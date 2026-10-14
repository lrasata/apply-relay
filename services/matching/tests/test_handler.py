from unittest import mock

from botocore.exceptions import ClientError

from conftest import cv_parsed, sqs_event
from matching_service import handler as handler_module
from matching_service.handler import handler

STRONG_SKILLS = ["aws", "terraform", "python", "docker", "kubernetes", "github actions"]


def test_scores_candidate_stores_match_and_publishes(aws, schemas):
    incoming = cv_parsed("app_1", STRONG_SKILLS)

    assert handler(sqs_event(incoming), None) == {"batchItemFailures": []}

    events = aws["published_events"]()
    assert len(events) == 1
    envelope = events[0]
    schemas["envelope"].validate(envelope)
    schemas["candidate-matched"].validate(envelope["data"])
    assert envelope["event_type"] == "CandidateMatched"
    assert envelope["correlation_id"] == "app_1"
    assert envelope["data"]["score"] == 85  # 11 / 13
    assert envelope["data"]["recommendation"] == "strong_match"
    assert envelope["data"]["missing_skills"] == []
    # Context for the recruiter email: from the job catalogue and the parsed CV.
    assert envelope["data"]["job_title"] == "DevOps Engineer (AWS)"
    assert envelope["data"]["job_location"] == "Munich"
    assert envelope["data"]["years_of_experience"] == 5.0
    assert envelope["data"]["languages"] == ["english"]

    item = aws["matches"].get_item(Key={"application_id": "app_1"})["Item"]
    assert item["event_published"] is True
    assert item["source_event_id"] == incoming["event_id"]
    assert item["event_id"] == envelope["event_id"]


def test_weak_match_is_still_published(aws, schemas):
    handler(sqs_event(cv_parsed("app_weak", ["java"])), None)

    data = aws["published_events"]()[0]["data"]
    schemas["candidate-matched"].validate(data)
    assert data["score"] == 0
    assert data["recommendation"] == "weak_match"


def test_cv_without_years_of_experience_publishes_null(aws, schemas):
    incoming = cv_parsed("app_1", STRONG_SKILLS)
    incoming["data"]["years_of_experience"] = None
    handler(sqs_event(incoming), None)

    data = aws["published_events"]()[0]["data"]
    schemas["candidate-matched"].validate(data)
    assert data["years_of_experience"] is None


def test_each_job_uses_its_own_requirements(aws):
    skills = ["javascript", "typescript", "react"]
    handler(sqs_event(cv_parsed("app_dev", skills), cv_parsed("app_fe", skills, job_id="job_frontend_remote_008")), None)

    scores = {e["correlation_id"]: e["data"]["score"] for e in aws["published_events"]()}
    assert scores == {"app_dev": 0, "app_fe": 67}  # 6 / 9


def test_duplicate_delivery_publishes_once(aws):
    incoming = cv_parsed("app_1", STRONG_SKILLS)
    handler(sqs_event(incoming), None)
    assert handler(sqs_event(incoming), None) == {"batchItemFailures": []}
    assert len(aws["published_events"]()) == 1


def test_retry_after_crash_before_publish_reuses_event_id(aws):
    incoming = cv_parsed("app_1", STRONG_SKILLS)
    real = handler_module._aws()
    broken_sns = mock.Mock()
    broken_sns.publish.side_effect = ClientError({"Error": {"Code": "InternalError"}}, "Publish")
    with mock.patch.object(handler_module, "_aws", return_value={**real, "sns": broken_sns}):
        assert handler(sqs_event(incoming), None) == {"batchItemFailures": [{"itemIdentifier": "msg-0"}]}

    stored = aws["matches"].get_item(Key={"application_id": "app_1"})["Item"]
    assert stored["event_published"] is False

    assert handler(sqs_event(incoming), None) == {"batchItemFailures": []}
    events = aws["published_events"]()
    assert len(events) == 1
    assert events[0]["event_id"] == stored["event_id"]
    assert events[0]["occurred_at"] == stored["matched_at"]


def test_unknown_job_fails_only_its_message(aws):
    good = cv_parsed("app_good", STRONG_SKILLS)
    unknown = cv_parsed("app_unknown", STRONG_SKILLS, job_id="job_does_not_exist")

    result = handler(sqs_event(unknown, good), None)

    assert result == {"batchItemFailures": [{"itemIdentifier": "msg-0"}]}
    assert [e["correlation_id"] for e in aws["published_events"]()] == ["app_good"]
    assert "Item" not in aws["matches"].get_item(Key={"application_id": "app_unknown"})


def test_unknown_event_type_or_version_fails(aws):
    wrong_type = cv_parsed("app_1", STRONG_SKILLS, event_type="ApplicationSubmitted")
    newer_version = cv_parsed("app_1", STRONG_SKILLS, event_version=2)

    assert len(handler(sqs_event(wrong_type, newer_version), None)["batchItemFailures"]) == 2
    assert aws["published_events"]() == []
