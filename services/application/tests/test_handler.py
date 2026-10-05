import json
from unittest import mock

import pytest
from botocore.exceptions import ClientError

from application_service import domain, handler as handler_module
from application_service.handler import handler
from conftest import BUCKET, PDF_BYTES, api_event, make_body


def call(body, **kwargs):
    response = handler(api_event(body, **kwargs), None)
    return response["statusCode"], json.loads(response["body"])


def test_new_application_is_stored_and_published(aws, schemas):
    status, body = call(make_body())

    assert status == 201
    assert body["status"] == "submitted"
    application_id = domain.application_id_for("alex@example.com", "job_devops_munich_042")
    assert body["application_id"] == application_id

    item = aws["table"].get_item(Key={"application_id": application_id})["Item"]
    assert item["event_published"] is True
    assert item["email"] == "alex@example.com"

    cv = aws["s3"].get_object(Bucket=BUCKET, Key=f"applications/{application_id}/cv.pdf")
    assert cv["Body"].read() == PDF_BYTES
    assert cv["ContentType"] == "application/pdf"

    events = aws["published_events"]()
    assert len(events) == 1
    envelope = events[0]
    schemas["envelope"].validate(envelope)
    schemas["application-submitted"].validate(envelope["data"])
    assert envelope["event_type"] == "ApplicationSubmitted"
    assert envelope["correlation_id"] == application_id
    assert envelope["event_id"] == item["event_id"]


def test_duplicate_submission_returns_original_and_publishes_nothing(aws):
    first_status, first = call(make_body())
    aws["published_events"]()  # drain the first event

    second_status, second = call(make_body(email="alex@EXAMPLE.com", full_name="Alex Again"))

    assert (first_status, second_status) == (201, 200)
    assert second["status"] == "already_submitted"
    assert second["application_id"] == first["application_id"]
    assert second["submitted_at"] == first["submitted_at"]
    assert aws["published_events"]() == []
    item = aws["table"].get_item(Key={"application_id": first["application_id"]})["Item"]
    assert item["full_name"] == "Alex Example"  # the original is never overwritten


def test_same_candidate_can_apply_to_different_jobs(aws):
    _, first = call(make_body(job_id="job_a"))
    status, second = call(make_body(job_id="job_b"))

    assert status == 201
    assert first["application_id"] != second["application_id"]
    assert first["candidate_id"] == second["candidate_id"]
    assert len(aws["published_events"]()) == 2


def test_retry_after_crash_before_publish_publishes_once_with_same_event_id(aws, schemas):
    real = handler_module._aws()
    broken_sns = mock.Mock()
    broken_sns.publish.side_effect = ClientError({"Error": {"Code": "InternalError"}}, "Publish")
    with mock.patch.object(handler_module, "_aws", return_value={**real, "sns": broken_sns}):
        with pytest.raises(ClientError):
            call(make_body())

    application_id = domain.application_id_for("alex@example.com", "job_devops_munich_042")
    stored = aws["table"].get_item(Key={"application_id": application_id})["Item"]
    assert stored["event_published"] is False
    assert aws["published_events"]() == []

    status, body = call(make_body())

    assert status == 200
    assert body["status"] == "submitted"
    events = aws["published_events"]()
    assert len(events) == 1
    assert events[0]["event_id"] == stored["event_id"]
    assert events[0]["occurred_at"] == stored["submitted_at"]
    assert aws["table"].get_item(Key={"application_id": application_id})["Item"]["event_published"] is True


def test_text_cv_gets_txt_extension(aws):
    import base64

    status, body = call(
        make_body(cv={"content_type": "text/plain", "content_base64": base64.b64encode(b"Skills: AWS").decode()})
    )
    assert status == 201
    obj = aws["s3"].get_object(Bucket=BUCKET, Key=f"applications/{body['application_id']}/cv.txt")
    assert obj["Body"].read() == b"Skills: AWS"


def test_base64_encoded_api_gateway_body_is_accepted(aws):
    status, _ = call(make_body(), base64_encoded=True)
    assert status == 201


@pytest.mark.parametrize("raw", ["not json", "[1, 2]", ""])
def test_malformed_body_is_a_400_and_touches_nothing(aws, raw):
    status, body = call(raw)
    assert status == 400
    assert body["error"] == "validation_failed"
    assert aws["table"].scan()["Count"] == 0
    assert aws["published_events"]() == []


def test_invalid_fields_are_a_400_listing_each_problem(aws):
    status, body = call(make_body(email="nope", job_id=""))
    assert status == 400
    assert len(body["details"]) == 2
