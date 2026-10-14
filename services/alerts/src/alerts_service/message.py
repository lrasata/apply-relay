"""Builds a readable alert email from a CloudWatch alarm notification. No AWS calls here.

The project's alarms are recognised from their metric (see `classify`): a dead-letter queue
that isn't empty, a queue backlog, or API server errors. Any other alarm still gets a short,
generic email.
"""

import re
from dataclasses import dataclass
from urllib.parse import quote


@dataclass(frozen=True)
class Alert:
    subject: str
    text: str


@dataclass(frozen=True)
class Alarm:
    name: str
    description: str
    state: str  # ALARM, OK or INSUFFICIENT_DATA
    reason: str
    changed_at: str
    region: str
    account: str
    namespace: str
    metric: str
    dimensions: dict
    value: float | None


def parse(notification: dict, default_region: str = "") -> Alarm:
    """The JSON CloudWatch publishes to SNS when an alarm changes state."""
    trigger = notification.get("Trigger", {})
    arn = notification.get("AlarmArn", "").split(":")  # arn:aws:cloudwatch:<region>:<account>:alarm:<name>
    reason = notification.get("NewStateReason", "")
    # "Threshold Crossed: 1 out of the last 1 datapoints [3.0 (14/10/26 19:44:00)] was greater than ..."
    value = re.search(r"\[(\d+(?:\.\d+)?) \(", reason)
    return Alarm(
        name=notification.get("AlarmName", "unknown alarm"),
        description=notification.get("AlarmDescription") or "",
        state=notification.get("NewStateValue", "UNKNOWN"),
        reason=reason,
        changed_at=notification.get("StateChangeTime", ""),
        region=arn[3] if len(arn) > 3 and arn[3] else default_region,
        account=notification.get("AWSAccountId") or (arn[4] if len(arn) > 4 else ""),
        namespace=trigger.get("Namespace", ""),
        metric=trigger.get("MetricName", ""),
        dimensions={d.get("name"): d.get("value") for d in trigger.get("Dimensions", [])},
        value=float(value.group(1)) if value else None,
    )


def classify(alarm: Alarm, name_prefix: str) -> tuple[str, str]:
    """(kind, service): kind is dlq, backlog, api or other."""
    queue = alarm.dimensions.get("QueueName", "")
    service = queue.removeprefix(f"{name_prefix}-").removesuffix("-dlq")
    if alarm.namespace == "AWS/SQS" and alarm.metric == "ApproximateNumberOfMessagesVisible" and queue.endswith("-dlq"):
        return "dlq", service
    if alarm.namespace == "AWS/SQS" and alarm.metric == "ApproximateAgeOfOldestMessage":
        return "backlog", service
    if alarm.namespace == "AWS/ApiGateway" and alarm.metric == "5xx":
        return "api", "application"
    return "other", ""


# ---------------------------------------------------------------- console links


def _console(region: str) -> str:
    return f"https://{region}.console.aws.amazon.com"


def queue_link(region: str, account: str, queue: str) -> str:
    url = f"https://sqs.{region}.amazonaws.com/{account}/{queue}"
    return f"{_console(region)}/sqs/v3/home?region={region}#/queues/{quote(url, safe='')}"


def logs_link(region: str, log_group: str) -> str:
    # The CloudWatch console encodes the group name twice, with $ instead of %.
    encoded = quote(quote(log_group, safe=""), safe="").replace("%", "$")
    return f"{_console(region)}/cloudwatch/home?region={region}#logsV2:log-groups/log-group/{encoded}"


def alarm_link(region: str, name: str) -> str:
    return f"{_console(region)}/cloudwatch/home?region={region}#alarmsV2:alarm/{quote(name, safe='')}"


def dashboard_link(region: str, name: str) -> str:
    return f"{_console(region)}/cloudwatch/home?region={region}#dashboards/dashboard/{name}"


# ---------------------------------------------------------------- email


def _when(timestamp: str) -> str:
    # "2026-10-14T19:44:12.345+0000" -> "2026-10-14 19:44 UTC"
    return f"{timestamp[:10]} {timestamp[11:16]} UTC" if len(timestamp) >= 16 else timestamp


def _count(value: float | None, noun: str) -> str:
    if value is None:
        return f"{noun}s"
    n = int(value)
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def build_alert(alarm: Alarm, name_prefix: str, dashboard: str = "") -> Alert:
    kind, service = classify(alarm, name_prefix)
    function = f"{name_prefix}-{service}"
    log_group = f"/aws/lambda/{function}"
    region = alarm.region
    tail = f'aws logs tail {log_group} --since 1h --filter-pattern "processing failed"'
    links = []

    if kind == "dlq":
        dlq = alarm.dimensions["QueueName"]
        messages = _count(alarm.value, "message")
        one = alarm.value == 1
        it, was = ("it", "was") if one else ("they", "were")
        if alarm.state == "ALARM":
            subject = f"{service}: {messages} failed and moved to the dead-letter queue"
            happened = [
                f"{messages.capitalize()} for the {service} service failed every retry and",
                f"{was} moved to the dead-letter queue {dlq}.",
                f"Nothing processes {'it' if one else 'them'} until you act.",
            ]
            todo = [
                f"1. See why {it} failed:",
                f"   {tail}",
                f"2. Fix the cause, then send {'it' if one else 'them'} back to be processed:",
                "   SQS console > the dead-letter queue > Start DLQ redrive.",
                f"   If {it} can never succeed, delete {'it' if one else 'them'} instead (Purge).",
            ]
        else:
            subject = f"{service}: dead-letter queue is empty again"
            happened, todo = [f"The dead-letter queue {dlq} is empty again."], []
        links = [("Dead-letter queue", queue_link(region, alarm.account, dlq)), ("Logs", logs_link(region, log_group))]

    elif kind == "backlog":
        queue = alarm.dimensions["QueueName"]
        minutes = "" if alarm.value is None else f" for {int(alarm.value // 60)} minutes"
        if alarm.state == "ALARM":
            subject = f"{service}: messages waiting{minutes}, not being processed"
            happened = [
                f"The oldest message in the {queue} queue has been waiting{minutes}.",
                f"The {service} service isn't keeping up, or isn't being triggered.",
            ]
            todo = [
                "1. Is the trigger enabled?",
                f'   aws lambda list-event-source-mappings --function-name {function} --query "EventSourceMappings[].State"',
                "2. Is the function failing or timing out?",
                f"   aws logs tail {log_group} --since 1h",
            ]
        else:
            subject = f"{service}: queue is moving again"
            happened, todo = [f"Messages in the {queue} queue are being processed again."], []
        links = [("Queue", queue_link(region, alarm.account, queue)), ("Logs", logs_link(region, log_group))]

    elif kind == "api":
        if alarm.state == "ALARM":
            errors = _count(alarm.value, "server error")
            subject = f"API: {errors} (5xx) on POST /applications"
            happened = [
                f"POST /applications returned {errors} (HTTP 5xx) in the last 5 minutes.",
                "Candidates trying to apply are getting an error.",
            ]
            todo = ["See what went wrong:", f"   aws logs tail {log_group} --since 1h --filter-pattern ERROR"]
        else:
            subject = "API: no more server errors"
            happened, todo = ["POST /applications is answering without server errors again."], []
        links = [("Logs", logs_link(region, log_group))]

    else:
        subject = alarm.name
        happened = [line for line in (alarm.description, alarm.reason) if line]
        todo = []

    if dashboard and kind != "other":
        links.append(("Dashboard", dashboard_link(region, dashboard)))
    links.append(("Alarm", alarm_link(region, alarm.name)))

    lines = ["WHAT HAPPENED", *happened, ""]
    if todo:
        lines += ["WHAT TO DO", *todo, ""]
    lines += ["LINKS", *(f"{label + ':':<19}{url}" for label, url in links), ""]
    lines.append(f"Alarm {alarm.name} changed to {alarm.state} at {_when(alarm.changed_at)} ({region}).")

    return Alert(subject=f"[{alarm.state}] {subject}", text="\n".join(lines))


def fallback_alert(raw_message: str, subject: str = "") -> Alert:
    """Used when a notification can't be parsed: forward it as it is rather than lose it."""
    return Alert(
        subject=f"[ALERT] {subject or 'an alarm changed state'}",
        text="This alert couldn't be formatted, so here is the original message:\n\n" + raw_message,
    )
