"""Lambda entry point: SNS topic `alarms` (every CloudWatch alarm in the project).

For each alarm notification, build a short email that says what happened and what to do,
and publish it to the `ops-alerts` topic, whose email subscription delivers it.

A notification that can't be parsed is forwarded as it is, so an alert is never lost to a
formatting bug. If publishing fails, the error is raised and Lambda retries the invocation
(SNS invokes it asynchronously: 2 retries).
"""

import json
import logging
import os
from functools import lru_cache

import boto3

from alerts_service.message import build_alert, fallback_alert, parse

SERVICE = "alerts-service"
SUBJECT_MAX = 99  # SNS email subjects: ASCII, one line, under 100 characters

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


@lru_cache(maxsize=1)
def _sns():
    return boto3.client("sns")


def _log(level: int, message: str, **fields) -> None:
    logger.log(level, json.dumps({"message": message, "service": SERVICE, **fields}))


def _subject(text: str) -> str:
    one_line = " ".join(text.encode("ascii", "ignore").decode().split())
    return one_line if len(one_line) <= SUBJECT_MAX else one_line[: SUBJECT_MAX - 3] + "..."


def format_notification(raw_message: str, raw_subject: str = ""):
    try:
        alarm = parse(json.loads(raw_message), default_region=os.environ.get("AWS_REGION", ""))
        return build_alert(alarm, os.environ["NAME_PREFIX"], os.environ.get("DASHBOARD_NAME", ""))
    except Exception as exc:
        _log(logging.WARNING, "could not format alarm, forwarding it as is", error=f"{type(exc).__name__}: {exc}")
        return fallback_alert(raw_message, raw_subject)


def handler(event: dict, context: object) -> None:
    for record in event.get("Records", []):
        sns = record["Sns"]
        alert = format_notification(sns.get("Message", ""), sns.get("Subject") or "")
        _sns().publish(TopicArn=os.environ["OUTPUT_TOPIC_ARN"], Subject=_subject(alert.subject), Message=alert.text)
        _log(logging.INFO, "alert sent", subject=alert.subject)
