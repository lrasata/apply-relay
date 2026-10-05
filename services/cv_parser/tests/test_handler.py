from unittest import mock

from botocore.exceptions import ClientError

from conftest import application_submitted, sqs_event
from cv_parser_service import handler as handler_module
from cv_parser_service.handler import handler
from pdfs import make_pdf

CV_TEXT = b"DevOps engineer, 5 years of experience. AWS, Terraform, Python. Languages: English, German."


def test_parses_cv_stores_result_and_publishes_cv_parsed(aws, schemas):
    key = aws["upload_cv"]("app_1", CV_TEXT)
    incoming = application_submitted("app_1", key)

    result = handler(sqs_event(incoming), None)

    assert result == {"batchItemFailures": []}
    events = aws["published_events"]()
    assert len(events) == 1
    envelope = events[0]
    schemas["envelope"].validate(envelope)
    schemas["cv-parsed"].validate(envelope["data"])
    assert envelope["event_type"] == "CvParsed"
    assert envelope["correlation_id"] == "app_1"
    assert envelope["data"]["job_id"] == "job_devops_munich_042"
    assert envelope["data"]["skills"] == ["aws", "python", "terraform"]
    assert envelope["data"]["years_of_experience"] == 5.0
    assert envelope["data"]["languages"] == ["english", "german"]

    item = aws["table"].get_item(Key={"application_id": "app_1"})["Item"]
    assert item["event_published"] is True
    assert item["source_event_id"] == incoming["event_id"]
    assert item["event_id"] == envelope["event_id"]


def test_pdf_cv(aws, schemas):
    pdf = make_pdf(["Kubernetes and Docker", "Languages: French"])
    key = aws["upload_cv"]("app_pdf", pdf, "application/pdf")

    handler(sqs_event(application_submitted("app_pdf", key, "application/pdf")), None)

    data = aws["published_events"]()[0]["data"]
    schemas["cv-parsed"].validate(data)
    assert data["skills"] == ["docker", "kubernetes"]
    assert data["years_of_experience"] is None
    assert data["languages"] == ["french"]


def test_duplicate_delivery_publishes_once(aws):
    key = aws["upload_cv"]("app_1", CV_TEXT)
    incoming = application_submitted("app_1", key)

    handler(sqs_event(incoming), None)
    result = handler(sqs_event(incoming), None)

    assert result == {"batchItemFailures": []}
    assert len(aws["published_events"]()) == 1


def test_retry_after_crash_before_publish_reuses_event_id(aws):
    key = aws["upload_cv"]("app_1", CV_TEXT)
    incoming = application_submitted("app_1", key)

    real = handler_module._aws()
    broken_sns = mock.Mock()
    broken_sns.publish.side_effect = ClientError({"Error": {"Code": "InternalError"}}, "Publish")
    with mock.patch.object(handler_module, "_aws", return_value={**real, "sns": broken_sns}):
        assert handler(sqs_event(incoming), None) == {"batchItemFailures": [{"itemIdentifier": "msg-0"}]}

    stored = aws["table"].get_item(Key={"application_id": "app_1"})["Item"]
    assert stored["event_published"] is False

    assert handler(sqs_event(incoming), None) == {"batchItemFailures": []}
    events = aws["published_events"]()
    assert len(events) == 1
    assert events[0]["event_id"] == stored["event_id"]
    assert events[0]["occurred_at"] == stored["parsed_at"]


def test_only_failing_messages_are_reported(aws):
    good_key = aws["upload_cv"]("app_good", CV_TEXT)
    good = application_submitted("app_good", good_key)
    missing_cv = application_submitted("app_missing", "applications/app_missing/cv.txt")
    corrupt_key = aws["upload_cv"]("app_corrupt", b"%PDF-1.4 garbage", "application/pdf")
    corrupt = application_submitted("app_corrupt", corrupt_key, "application/pdf")

    result = handler(sqs_event(missing_cv, good, corrupt), None)

    assert result == {"batchItemFailures": [{"itemIdentifier": "msg-0"}, {"itemIdentifier": "msg-2"}]}
    events = aws["published_events"]()
    assert [e["correlation_id"] for e in events] == ["app_good"]


def test_unknown_event_type_or_version_fails_instead_of_guessing(aws):
    key = aws["upload_cv"]("app_1", CV_TEXT)
    wrong_type = application_submitted("app_1", key, event_type="CvParsed")
    newer_version = application_submitted("app_1", key, event_version=2)
    not_json = {"Records": [{"messageId": "msg-x", "body": "not json"}]}

    assert len(handler(sqs_event(wrong_type, newer_version), None)["batchItemFailures"]) == 2
    assert handler(not_json, None) == {"batchItemFailures": [{"itemIdentifier": "msg-x"}]}
    assert aws["published_events"]() == []
