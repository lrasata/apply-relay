"""Lambda entry point for POST /applications (API Gateway HTTP API, payload format 2.0).

Flow, designed so a retry or a crash at any point never produces a second application
and never loses the event:

1. Validate the request and derive the deterministic application ID.
2. Conditionally write the application (`attribute_not_exists`), with `event_published = false`.
3. Store the CV in S3.
4. Publish ApplicationSubmitted, then flip `event_published` to true.

If step 2 finds the application already there and its event was published, this is a
duplicate submission: return the original, publish nothing. If the event was *not*
published (an earlier attempt crashed between steps 2 and 4), finish steps 3-4 with the
stored event_id, so consumers see one event even if it is delivered twice.
"""

import base64
import json
import logging
import os
from functools import lru_cache

import boto3
from botocore.exceptions import ClientError

from application_service import domain

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


@lru_cache(maxsize=1)
def _aws():
    """Clients are created once per Lambda container and reused across invocations."""
    return {
        "table": boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"]),
        "s3": boto3.client("s3"),
        "sns": boto3.client("sns"),
    }


def _log(message: str, **fields) -> None:
    # One JSON object per line: searchable in CloudWatch Logs Insights by correlation_id.
    logger.info(json.dumps({"message": message, "service": domain.PRODUCER, **fields}))


def _response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def _parse_body(event: dict) -> object:
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise domain.ValidationError(["body must be valid JSON"]) from exc


def _public_view(application: dict) -> dict:
    return {
        "application_id": application["application_id"],
        "job_id": application["job_id"],
        "candidate_id": application["candidate_id"],
        "submitted_at": application["submitted_at"],
    }


def handler(event: dict, context: object) -> dict:
    try:
        request = domain.parse_request(_parse_body(event))
    except domain.ValidationError as exc:
        return _response(400, {"error": "validation_failed", "details": exc.errors})

    aws = _aws()
    bucket = os.environ["BUCKET_NAME"]
    topic_arn = os.environ["TOPIC_ARN"]

    application_id = domain.application_id_for(request.email, request.job_id)
    item = {
        "application_id": application_id,
        "job_id": request.job_id,
        "candidate_id": domain.candidate_id_for(request.email),
        "full_name": request.full_name,
        "email": request.email,
        "cv_bucket": bucket,
        "cv_key": domain.cv_key_for(application_id, request.cv_extension),
        "cv_content_type": request.cv_content_type,
        "submitted_at": domain.utc_now(),
        "event_id": domain.new_event_id(),
        "event_published": False,
    }

    try:
        aws["table"].put_item(Item=item, ConditionExpression="attribute_not_exists(application_id)")
        created = True
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        created = False
        item = aws["table"].get_item(Key={"application_id": application_id}, ConsistentRead=True)["Item"]
        if item["event_published"]:
            _log("duplicate submission ignored", correlation_id=application_id)
            return _response(200, {**_public_view(item), "status": "already_submitted"})
        _log("resuming unpublished application", correlation_id=application_id)

    # A stored-but-unpublished application has never been seen by consumers, so its CV can
    # safely be (re)written; once published, the CV is never overwritten (we returned above).
    aws["s3"].put_object(
        Bucket=item["cv_bucket"],
        Key=item["cv_key"],
        Body=request.cv_bytes,
        ContentType=item["cv_content_type"],
        ServerSideEncryption="AES256",
    )

    envelope = domain.build_event(item)
    aws["sns"].publish(
        TopicArn=topic_arn,
        Message=json.dumps(envelope),
        MessageAttributes={
            "event_type": {"DataType": "String", "StringValue": domain.EVENT_TYPE},
            "event_version": {"DataType": "Number", "StringValue": str(domain.EVENT_VERSION)},
        },
    )
    aws["table"].update_item(
        Key={"application_id": application_id},
        UpdateExpression="SET event_published = :true",
        ExpressionAttributeValues={":true": True},
    )

    _log("application submitted", correlation_id=application_id, event_id=item["event_id"], job_id=item["job_id"])
    return _response(201 if created else 200, {**_public_view(item), "status": "submitted"})
