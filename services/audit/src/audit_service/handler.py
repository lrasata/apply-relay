"""Lambda entry point: SQS queue `audit` (subscribed to every event topic).

Each event becomes one item in the `audit-log` table:

    correlation_id (partition key)   the application ID
    sk (sort key)                    "<occurred_at>#<event_id>": sorts by time, unique per event

so `Query(correlation_id = app_...)` returns the application's whole history in order.
Writes are conditional on the key not existing: a duplicate delivery is a no-op.

The audit log stores events as they were received, including event types it doesn't know
yet. It only rejects messages that aren't a valid envelope.
"""

import json
import logging
import os
import time
from functools import lru_cache

import boto3
from botocore.exceptions import ClientError

SERVICE = "audit-service"
REQUIRED = ("event_id", "event_type", "event_version", "occurred_at", "correlation_id", "producer", "data")

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


@lru_cache(maxsize=1)
def _table():
    return boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])


def _log(level: int, message: str, **fields) -> None:
    logger.log(level, json.dumps({"message": message, "service": SERVICE, **fields}))


class InvalidEnvelope(Exception):
    """The message isn't an event envelope; retrying won't fix it."""


def sort_key(envelope: dict) -> str:
    return f"{envelope['occurred_at']}#{envelope['event_id']}"


def process(raw_body: str, received_at: float | None = None) -> str:
    try:
        envelope = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise InvalidEnvelope("body is not JSON") from exc
    if not isinstance(envelope, dict):
        raise InvalidEnvelope("body is not a JSON object")
    missing = [field for field in REQUIRED if field not in envelope]
    if missing:
        raise InvalidEnvelope(f"missing {', '.join(missing)}")

    now = received_at if received_at is not None else time.time()
    item = {
        "correlation_id": envelope["correlation_id"],
        "sk": sort_key(envelope),
        "event_id": envelope["event_id"],
        "event_type": envelope["event_type"],
        "event_version": envelope["event_version"],
        "producer": envelope["producer"],
        "occurred_at": envelope["occurred_at"],
        # Stored as the original JSON text: exact, and no float/Decimal conversion surprises.
        "envelope": raw_body,
        "recorded_at": int(now),
    }
    retention_days = int(os.environ.get("RETENTION_DAYS", "0"))
    if retention_days > 0:
        item["expires_at"] = int(now) + retention_days * 86400  # DynamoDB TTL deletes it after this

    try:
        _table().put_item(Item=item, ConditionExpression="attribute_not_exists(sk)")
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return "duplicate"
        raise
    _log(logging.INFO, "event recorded", correlation_id=item["correlation_id"], event_type=item["event_type"])
    return "recorded"


def handler(event: dict, context: object) -> dict:
    failures = []
    for record in event.get("Records", []):
        try:
            process(record["body"])
        except Exception as exc:
            _log(
                logging.ERROR,
                "processing failed",
                message_id=record.get("messageId"),
                error=f"{type(exc).__name__}: {exc}",
            )
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}
