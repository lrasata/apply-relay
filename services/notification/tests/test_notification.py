from unittest import mock

from botocore.exceptions import ClientError

from conftest import RECRUITER, SENDER, candidate_matched, sqs_event
from notification_service import handler as handler_module
from notification_service.handler import handler
from notification_service.message import build_email


def test_email_content():
    email = build_email(candidate_matched("app_1")["data"])
    assert email.subject == "Strong match (85/100) for job_devops_munich_042"
    assert "app_1" in email.text
    assert "Matched skills:  aws, terraform, python, docker" in email.text
    assert "Missing skills:  kubernetes" in email.text


def test_email_with_no_missing_skills_says_none():
    data = {**candidate_matched("app_1")["data"], "missing_skills": []}
    assert "Missing skills:  none" in build_email(data).text


def test_strong_match_emails_the_recruiter_once_and_logs_it(aws):
    assert handler(sqs_event(candidate_matched("app_1")), None) == {"batchItemFailures": []}

    emails = aws["sent_emails"]()
    assert len(emails) == 1
    assert emails[0].source == SENDER
    assert emails[0].destinations["ToAddresses"] == [RECRUITER]
    assert emails[0].subject == "Strong match (85/100) for job_devops_munich_042"

    item = aws["table"].get_item(Key={"application_id": "app_1"})["Item"]
    assert item["status"] == "sent"
    assert item["ses_message_id"] == emails[0].id


def test_duplicate_delivery_sends_one_email(aws):
    incoming = candidate_matched("app_1")
    handler(sqs_event(incoming), None)
    assert handler(sqs_event(incoming), None) == {"batchItemFailures": []}
    assert len(aws["sent_emails"]()) == 1


def test_possible_and_weak_matches_send_nothing(aws):
    result = handler(
        sqs_event(
            candidate_matched("app_possible", "possible_match", 60),
            candidate_matched("app_weak", "weak_match", 10),
        ),
        None,
    )
    assert result == {"batchItemFailures": []}
    assert aws["sent_emails"]() == []
    assert aws["table"].scan()["Count"] == 0


def test_ses_failure_is_retried_and_then_sends(aws):
    incoming = candidate_matched("app_1")
    real = handler_module._aws()
    broken_ses = mock.Mock()
    broken_ses.send_email.side_effect = ClientError({"Error": {"Code": "Throttling"}}, "SendEmail")
    with mock.patch.object(handler_module, "_aws", return_value={**real, "ses": broken_ses}):
        assert handler(sqs_event(incoming), None) == {"batchItemFailures": [{"itemIdentifier": "msg-0"}]}
    assert aws["table"].get_item(Key={"application_id": "app_1"})["Item"]["status"] == "sending"

    assert handler(sqs_event(incoming), None) == {"batchItemFailures": []}
    assert len(aws["sent_emails"]()) == 1
    assert aws["table"].get_item(Key={"application_id": "app_1"})["Item"]["status"] == "sent"


def test_unverified_sender_fails_the_message(aws, monkeypatch):
    monkeypatch.setenv("SENDER_EMAIL", "not-verified@example.com")
    result = handler(sqs_event(candidate_matched("app_1")), None)
    assert result == {"batchItemFailures": [{"itemIdentifier": "msg-0"}]}
    assert aws["sent_emails"]() == []


def test_unknown_event_type_or_version_fails(aws):
    result = handler(
        sqs_event(candidate_matched("a", event_type="CvParsed"), candidate_matched("b", event_version=2)), None
    )
    assert len(result["batchItemFailures"]) == 2
    assert aws["sent_emails"]() == []
