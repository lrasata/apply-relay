"""Lambda entry point: SQS queue `matching` (subscribed to CvParsed).

For each message:

1. Skip it if this application's CandidateMatched event was already published.
2. Look up the job's requirements in the service's own `jobs` table.
3. Score the candidate, store the match with `event_published = false`, publish
   CandidateMatched, flip the flag.

Same reliability pattern as the CV parser: a crash before publishing is finished by the
SQS retry with the stored event_id, and failures are reported per message.
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from functools import lru_cache

import boto3
from botocore.exceptions import ClientError

from matching_service.scoring import Job, score

EVENT_TYPE = "CandidateMatched"
EVENT_VERSION = 1
PRODUCER = "matching-service"
CONSUMES = ("CvParsed", 1)

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


@lru_cache(maxsize=1)
def _aws():
    dynamodb = boto3.resource("dynamodb")
    return {
        "matches": dynamodb.Table(os.environ["MATCHES_TABLE"]),
        "jobs": dynamodb.Table(os.environ["JOBS_TABLE"]),
        "sns": boto3.client("sns"),
    }


def _log(level: int, message: str, **fields) -> None:
    logger.log(level, json.dumps({"message": message, "service": PRODUCER, **fields}))


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class UnsupportedEvent(Exception):
    """Wrong event type or a newer version than this service understands."""


class UnknownJob(Exception):
    """The application refers to a job this service has no requirements for."""


def build_event(item: dict) -> dict:
    years = item["years_of_experience"]
    return {
        "event_id": item["event_id"],
        "event_type": EVENT_TYPE,
        "event_version": EVENT_VERSION,
        "occurred_at": item["matched_at"],
        "correlation_id": item["application_id"],
        "producer": PRODUCER,
        "data": {
            "application_id": item["application_id"],
            "job_id": item["job_id"],
            "job_title": item["job_title"],
            "job_location": item["job_location"],
            "years_of_experience": None if years is None else float(years),
            "languages": list(item["languages"]),
            "score": int(item["score"]),
            "matched_skills": list(item["matched_skills"]),
            "missing_skills": list(item["missing_skills"]),
            "recommendation": item["recommendation"],
            "matched_at": item["matched_at"],
        },
    }


def load_job(job_id: str) -> Job:
    item = _aws()["jobs"].get_item(Key={"job_id": job_id}).get("Item")
    if not item:
        raise UnknownJob(job_id)
    return Job(
        job_id=job_id,
        required_skills=tuple(item.get("required_skills", [])),
        nice_to_have_skills=tuple(item.get("nice_to_have_skills", [])),
        title=item.get("title", ""),
        location=item.get("location", ""),
    )


def process(envelope: dict) -> str:
    """Handle one CvParsed envelope. Returns what happened, for logs and tests."""
    if (envelope.get("event_type"), envelope.get("event_version")) != CONSUMES:
        raise UnsupportedEvent(f"{envelope.get('event_type')} v{envelope.get('event_version')}")

    aws = _aws()
    data = envelope["data"]
    application_id = data["application_id"]

    existing = aws["matches"].get_item(Key={"application_id": application_id}, ConsistentRead=True).get("Item")
    if existing and existing["event_published"]:
        return "duplicate"

    job = load_job(data["job_id"])
    match = score(data["skills"], job)
    years = data.get("years_of_experience")

    item = {
        "application_id": application_id,
        "job_id": data["job_id"],
        # Carried along so the recruiter email can say what the job is and who applied,
        # without the notification service reading other services' tables.
        "job_title": job.title,
        "job_location": job.location,
        "years_of_experience": None if years is None else Decimal(str(years)),
        "languages": data.get("languages", []),
        "score": match.score,
        "matched_skills": match.matched_skills,
        "missing_skills": match.missing_skills,
        "recommendation": match.recommendation,
        "source_event_id": envelope["event_id"],
        "event_id": existing["event_id"] if existing else str(uuid.uuid4()),
        "matched_at": existing["matched_at"] if existing else _utc_now(),
        "event_published": False,
    }
    try:
        aws["matches"].put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(application_id) OR event_published = :false",
            ExpressionAttributeValues={":false": False},
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return "duplicate"
        raise

    aws["sns"].publish(
        TopicArn=os.environ["TOPIC_ARN"],
        Message=json.dumps(build_event(item)),
        MessageAttributes={
            "event_type": {"DataType": "String", "StringValue": EVENT_TYPE},
            "event_version": {"DataType": "Number", "StringValue": str(EVENT_VERSION)},
            # Lets the notification queue filter on it later without parsing the body.
            "recommendation": {"DataType": "String", "StringValue": match.recommendation},
        },
    )
    aws["matches"].update_item(
        Key={"application_id": application_id},
        UpdateExpression="SET event_published = :true",
        ExpressionAttributeValues={":true": True},
    )
    _log(
        logging.INFO,
        "candidate matched",
        correlation_id=application_id,
        event_id=item["event_id"],
        job_id=data["job_id"],
        score=match.score,
        recommendation=match.recommendation,
    )
    return "published"


def handler(event: dict, context: object) -> dict:
    failures = []
    for record in event.get("Records", []):
        correlation_id = None
        try:
            envelope = json.loads(record["body"])
            correlation_id = envelope.get("correlation_id")
            if process(envelope) == "duplicate":
                _log(logging.INFO, "duplicate delivery skipped", correlation_id=correlation_id)
        except Exception as exc:
            _log(
                logging.ERROR,
                "processing failed",
                correlation_id=correlation_id,
                message_id=record.get("messageId"),
                error=f"{type(exc).__name__}: {exc}",
            )
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}
