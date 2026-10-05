"""Lambda entry point: SQS queue `cv-parser` (subscribed to ApplicationSubmitted).

For each message:

1. Skip it if this application's CvParsed event was already published (SQS can deliver
   the same message more than once).
2. Read the CV from S3 and parse it.
3. Store the result with `event_published = false`, publish CvParsed, flip the flag.

A crash between 3's steps is retried by SQS and republishes with the *stored* event_id,
so consumers can de-duplicate. Failures are reported per message (ReportBatchItemFailures):
only the failing message is retried, and after 3 attempts it moves to the dead-letter queue.
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

from cv_parser_service.extraction import parse_cv

EVENT_TYPE = "CvParsed"
EVENT_VERSION = 1
PRODUCER = "cv-parser-service"
CONSUMES = ("ApplicationSubmitted", 1)

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


@lru_cache(maxsize=1)
def _aws():
    return {
        "table": boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"]),
        "s3": boto3.client("s3"),
        "sns": boto3.client("sns"),
    }


def _log(level: int, message: str, **fields) -> None:
    logger.log(level, json.dumps({"message": message, "service": PRODUCER, **fields}))


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class UnsupportedEvent(Exception):
    """Wrong event type or a newer version than this service understands."""


def build_event(item: dict) -> dict:
    years = item["years_of_experience"]
    return {
        "event_id": item["event_id"],
        "event_type": EVENT_TYPE,
        "event_version": EVENT_VERSION,
        "occurred_at": item["parsed_at"],
        "correlation_id": item["application_id"],
        "producer": PRODUCER,
        "data": {
            "application_id": item["application_id"],
            "job_id": item["job_id"],
            "skills": list(item["skills"]),
            "years_of_experience": None if years is None else float(years),
            "languages": list(item["languages"]),
            "parsed_at": item["parsed_at"],
        },
    }


def process(envelope: dict) -> str:
    """Handle one ApplicationSubmitted envelope. Returns what happened, for logs and tests."""
    if (envelope.get("event_type"), envelope.get("event_version")) != CONSUMES:
        raise UnsupportedEvent(f"{envelope.get('event_type')} v{envelope.get('event_version')}")

    aws = _aws()
    data = envelope["data"]
    application_id = data["application_id"]

    existing = aws["table"].get_item(Key={"application_id": application_id}, ConsistentRead=True).get("Item")
    if existing and existing["event_published"]:
        return "duplicate"

    cv = aws["s3"].get_object(Bucket=data["cv"]["bucket"], Key=data["cv"]["key"])
    parsed = parse_cv(cv["Body"].read(), data["cv"]["content_type"])

    item = {
        "application_id": application_id,
        "job_id": data["job_id"],
        "skills": parsed.skills,
        "years_of_experience": None if parsed.years_of_experience is None else Decimal(str(parsed.years_of_experience)),
        "languages": parsed.languages,
        "source_event_id": envelope["event_id"],
        # Reuse the identity of an earlier, unpublished attempt so the event stays the same.
        "event_id": existing["event_id"] if existing else str(uuid.uuid4()),
        "parsed_at": existing["parsed_at"] if existing else _utc_now(),
        "event_published": False,
    }
    try:
        aws["table"].put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(application_id) OR event_published = :false",
            ExpressionAttributeValues={":false": False},
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return "duplicate"  # a concurrent delivery of the same message won the race
        raise

    aws["sns"].publish(
        TopicArn=os.environ["TOPIC_ARN"],
        Message=json.dumps(build_event(item)),
        MessageAttributes={
            "event_type": {"DataType": "String", "StringValue": EVENT_TYPE},
            "event_version": {"DataType": "Number", "StringValue": str(EVENT_VERSION)},
        },
    )
    aws["table"].update_item(
        Key={"application_id": application_id},
        UpdateExpression="SET event_published = :true",
        ExpressionAttributeValues={":true": True},
    )
    _log(
        logging.INFO,
        "cv parsed",
        correlation_id=application_id,
        event_id=item["event_id"],
        skills=len(parsed.skills),
        years_of_experience=parsed.years_of_experience,
    )
    return "published"


def handler(event: dict, context: object) -> dict:
    failures = []
    for record in event.get("Records", []):
        correlation_id = None
        try:
            envelope = json.loads(record["body"])
            correlation_id = envelope.get("correlation_id")
            outcome = process(envelope)
            if outcome == "duplicate":
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
