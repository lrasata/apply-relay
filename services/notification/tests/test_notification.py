from unittest import mock

from botocore.exceptions import ClientError

from conftest import RECRUITER, SENDER, candidate_matched, sqs_event
from notification_service import handler as handler_module
from notification_service.handler import handler
from notification_service.message import build_email, console_links


SUBJECT = "Strong match 85/100: DevOps Engineer (AWS), Munich"


def test_email_content():
    email = build_email(candidate_matched("app_1")["data"])
    assert email.subject == SUBJECT
    assert "app_1" in email.text
    assert "Required skills: Missing 1 required skill: kubernetes" in email.text
    assert "Matched skills:  aws, terraform, python, docker" in email.text
    assert "Experience:      6 years" in email.text
    assert "Languages:       English, German" in email.text
    assert "DevOps Engineer (AWS), Munich" in email.html


def test_email_with_no_missing_skills_says_so():
    data = {**candidate_matched("app_1")["data"], "missing_skills": []}
    assert "Required skills: Has every required skill." in build_email(data).text


def test_email_from_an_event_without_the_optional_fields():
    data = candidate_matched("app_1")["data"]
    for field in ("job_title", "job_location", "years_of_experience", "languages"):
        del data[field]
    email = build_email(data)
    assert email.subject == "Strong match 85/100: job_devops_munich_042"
    assert "Experience:      not stated in the CV" in email.text
    assert "Languages:       none" in email.text


def test_email_links_to_the_cv_and_the_application():
    links = console_links("app_1", "eu-central-1", "apply-relay-dev-cvs-123", "apply-relay-dev-applications")
    email = build_email(candidate_matched("app_1")["data"], links)
    assert links.cv == (
        "https://s3.console.aws.amazon.com/s3/buckets/apply-relay-dev-cvs-123"
        "?region=eu-central-1&prefix=applications/app_1/"
    )
    assert "table=apply-relay-dev-applications" in links.application and "pk=app_1" in links.application
    assert f"CV:           {links.cv}" in email.text
    assert "Open the CV" in email.html and "Open the application" in email.html


def test_html_escapes_values():
    data = {**candidate_matched("app_1")["data"], "job_title": "<script>x</script>"}
    assert "<script>" not in build_email(data).html


def test_strong_match_emails_the_recruiter_once_and_logs_it(aws):
    assert handler(sqs_event(candidate_matched("app_1")), None) == {"batchItemFailures": []}

    emails = aws["sent_emails"]()
    assert len(emails) == 1
    assert emails[0].source == SENDER
    assert emails[0].destinations["ToAddresses"] == [RECRUITER]
    assert emails[0].subject == SUBJECT
    assert "Strong match: 85/100" in emails[0].body  # moto keeps the HTML part when there is one

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
