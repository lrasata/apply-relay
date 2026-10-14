from unittest import mock

import pytest

from conftest import ACCOUNT, PREFIX, REGION, alarm_notification, dlq_alarm, sns_event
from alerts_service import handler as handler_module
from alerts_service.handler import handler
from alerts_service.message import build_alert, logs_link, parse, queue_link


def alert_for(notification, dashboard=""):
    return build_alert(parse(notification), PREFIX, dashboard)


def test_dlq_alarm_says_what_failed_and_what_to_do():
    alert = alert_for(dlq_alarm(value=3.0), dashboard=f"{PREFIX}-pipeline")

    assert alert.subject == "[ALARM] matching: 3 messages failed and moved to the dead-letter queue"
    assert f"moved to the dead-letter queue {PREFIX}-matching-dlq." in alert.text
    assert f'aws logs tail /aws/lambda/{PREFIX}-matching --since 1h --filter-pattern "processing failed"' in alert.text
    assert "Start DLQ redrive" in alert.text
    assert f"Dead-letter queue: {queue_link(REGION, ACCOUNT, f'{PREFIX}-matching-dlq')}" in alert.text
    assert f"#dashboards/dashboard/{PREFIX}-pipeline" in alert.text
    assert "changed to ALARM at 2026-10-14 19:49 UTC (eu-central-1)" in alert.text


def test_one_message_is_singular():
    alert = alert_for(dlq_alarm(value=1.0))
    assert alert.subject == "[ALARM] matching: 1 message failed and moved to the dead-letter queue"
    assert "was moved" in alert.text


def test_dlq_back_to_ok_is_short():
    alert = alert_for(dlq_alarm(service="cv-parser", state="OK", value=0.0))
    assert alert.subject == "[OK] cv-parser: dead-letter queue is empty again"
    assert "WHAT TO DO" not in alert.text


def test_backlog_alarm():
    notification = alarm_notification(
        f"{PREFIX}-audit-backlog", "AWS/SQS", "ApproximateAgeOfOldestMessage", {"QueueName": f"{PREFIX}-audit"}, value=1260.0
    )
    alert = alert_for(notification)
    assert alert.subject == "[ALARM] audit: messages waiting for 21 minutes, not being processed"
    assert f"--function-name {PREFIX}-audit" in alert.text


def test_api_alarm():
    notification = alarm_notification(
        f"{PREFIX}-api-5xx", "AWS/ApiGateway", "5xx", {"ApiId": "abc123", "Stage": "$default"}, value=4.0
    )
    alert = alert_for(notification)
    assert alert.subject == "[ALARM] API: 4 server errors (5xx) on POST /applications"
    assert f"aws logs tail /aws/lambda/{PREFIX}-application" in alert.text


def test_unknown_alarm_gets_a_generic_email():
    notification = alarm_notification("someone-elses-alarm", "AWS/EC2", "CPUUtilization", {}, description="CPU high.")
    alert = alert_for(notification)
    assert alert.subject == "[ALARM] someone-elses-alarm"
    assert "CPU high." in alert.text


def test_logs_link_uses_the_console_encoding():
    assert logs_link(REGION, "/aws/lambda/x").endswith("#logsV2:log-groups/log-group/$252Faws$252Flambda$252Fx")


def test_handler_publishes_the_formatted_alert(aws):
    handler(sns_event(dlq_alarm()), None)

    [(subject, text)] = aws["sent"]()
    assert subject == "[ALARM] matching: 1 message failed and moved to the dead-letter queue"
    assert text.startswith("WHAT HAPPENED")


def test_unparseable_message_is_forwarded_not_lost(aws):
    handler(sns_event("not json at all"), None)

    [(subject, text)] = aws["sent"]()
    assert subject == "[ALERT] ALARM: something"
    assert "not json at all" in text


def test_long_or_non_ascii_subjects_are_made_valid_for_sns(aws):
    notification = alarm_notification("é" + "x" * 150, "AWS/EC2", "CPUUtilization", {})
    handler(sns_event(notification), None)

    [(subject, _)] = aws["sent"]()
    assert subject.isascii() and len(subject) < 100 and subject.endswith("...")


def test_publish_failure_raises_so_lambda_retries(aws):
    broken = mock.Mock()
    broken.publish.side_effect = RuntimeError("SNS down")
    with mock.patch.object(handler_module, "_sns", return_value=broken), pytest.raises(RuntimeError):
        handler(sns_event(dlq_alarm()), None)
