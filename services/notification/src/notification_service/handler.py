"""Lambda entry point: SQS queue `notification` (subscribed to CandidateMatched).

The queue's SNS subscription already filters on `recommendation = strong_match`; the
handler checks again so a misconfigured filter can never email about weak matches.

For each strong match:

1. Record the notification as `sending` (conditional write, one per application).
2. Send the email with SES.
3. Record it as `sent` with the SES message ID.

An application already marked `sent` is skipped, so a duplicate delivery never sends a
second email. If the function crashes between 2 and 3, the retry sends again: email is
at-least-once, which is the safe side for "a recruiter must hear about this".
"""

import json
import logging
import os
from datetime import datetime, timezone
from functools import lru_cache

import boto3
from botocore.exceptions import ClientError

from notification_service.message import build_email

SERVICE = "notification-service"
CONSUMES = ("CandidateMatched", 1)
NOTIFY_ON = "strong_match"

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


@lru_cache(maxsize=1)
def _aws():
    return {
        "table": boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"]),
        "ses": boto3.client("ses"),
    }


def _log(level: int, message: str, **fields) -> None:
    logger.log(level, json.dumps({"message": message, "service": SERVICE, **fields}))


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class UnsupportedEvent(Exception):
    """Wrong event type or a newer version than this service understands."""


def process(envelope: dict) -> str:
    """Handle one CandidateMatched envelope. Returns what happened, for logs and tests."""
    if (envelope.get("event_type"), envelope.get("event_version")) != CONSUMES:
        raise UnsupportedEvent(f"{envelope.get('event_type')} v{envelope.get('event_version')}")

    match = envelope["data"]
    if match["recommendation"] != NOTIFY_ON:
        return "not_notified"

    aws = _aws()
    application_id = match["application_id"]
    try:
        aws["table"].put_item(
            Item={
                "application_id": application_id,
                "job_id": match["job_id"],
                "status": "sending",
                "source_event_id": envelope["event_id"],
                "created_at": _utc_now(),
            },
            ConditionExpression="attribute_not_exists(application_id) OR #status = :sending",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":sending": "sending"},
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return "duplicate"  # already sent
        raise

    email = build_email(match)
    recipient = os.environ["RECRUITER_EMAIL"]
    sent = aws["ses"].send_email(
        Source=os.environ["SENDER_EMAIL"],
        Destination={"ToAddresses": [recipient]},
        Message={
            "Subject": {"Data": email.subject, "Charset": "UTF-8"},
            "Body": {"Text": {"Data": email.text, "Charset": "UTF-8"}},
        },
    )
    aws["table"].update_item(
        Key={"application_id": application_id},
        UpdateExpression="SET #status = :sent, ses_message_id = :id, sent_at = :at",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={":sent": "sent", ":id": sent["MessageId"], ":at": _utc_now()},
    )
    _log(logging.INFO, "recruiter notified", correlation_id=application_id, ses_message_id=sent["MessageId"])
    return "sent"


def handler(event: dict, context: object) -> dict:
    failures = []
    for record in event.get("Records", []):
        correlation_id = None
        try:
            envelope = json.loads(record["body"])
            correlation_id = envelope.get("correlation_id")
            outcome = process(envelope)
            if outcome != "sent":
                _log(logging.INFO, f"skipped: {outcome}", correlation_id=correlation_id)
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
