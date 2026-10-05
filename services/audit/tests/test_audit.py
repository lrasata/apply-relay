import json

from boto3.dynamodb.conditions import Key

from audit_service import handler as handler_module
from audit_service.handler import handler
from conftest import envelope, sqs_event


def history(table, correlation_id):
    return table.query(KeyConditionExpression=Key("correlation_id").eq(correlation_id))["Items"]


def test_full_history_of_one_application_reads_in_order(aws):
    submitted = envelope("ApplicationSubmitted", "app_1", "2026-10-05T18:40:00Z", "application-service")
    parsed = envelope("CvParsed", "app_1", "2026-10-05T18:40:03Z", "cv-parser-service")
    matched = envelope("CandidateMatched", "app_1", "2026-10-05T18:40:05Z", "matching-service")
    other = envelope("ApplicationSubmitted", "app_2", "2026-10-05T18:40:01Z")

    # Delivered out of order and mixed with another application, as SQS may do.
    assert handler(sqs_event(matched, other, submitted, parsed), None) == {"batchItemFailures": []}

    items = history(aws["table"], "app_1")
    assert [i["event_type"] for i in items] == ["ApplicationSubmitted", "CvParsed", "CandidateMatched"]
    assert [i["producer"] for i in items] == ["application-service", "cv-parser-service", "matching-service"]
    assert json.loads(items[0]["envelope"]) == submitted


def test_duplicate_delivery_is_stored_once(aws):
    event = envelope("CvParsed", "app_1", "2026-10-05T18:40:03Z")
    handler(sqs_event(event), None)
    assert handler(sqs_event(event), None) == {"batchItemFailures": []}
    assert len(history(aws["table"], "app_1")) == 1


def test_same_timestamp_different_events_are_both_kept(aws):
    a = envelope("CvParsed", "app_1", "2026-10-05T18:40:03Z")
    b = envelope("CandidateMatched", "app_1", "2026-10-05T18:40:03Z")
    handler(sqs_event(a, b), None)
    assert len(history(aws["table"], "app_1")) == 2


def test_unknown_event_types_are_still_recorded(aws):
    handler(sqs_event(envelope("InterviewScheduled", "app_1", "2026-10-06T09:00:00Z", event_version=3)), None)
    assert history(aws["table"], "app_1")[0]["event_type"] == "InterviewScheduled"


def test_items_expire_after_the_retention_period(aws):
    handler_module.process(json.dumps(envelope("CvParsed", "app_1", "2026-10-05T18:40:03Z")), received_at=1_000_000)
    item = history(aws["table"], "app_1")[0]
    assert item["recorded_at"] == 1_000_000
    assert item["expires_at"] == 1_000_000 + 365 * 86400


def test_no_expiry_when_retention_is_off(aws, monkeypatch):
    monkeypatch.setenv("RETENTION_DAYS", "0")
    handler(sqs_event(envelope("CvParsed", "app_1", "2026-10-05T18:40:03Z")), None)
    assert "expires_at" not in history(aws["table"], "app_1")[0]


def test_invalid_messages_fail_individually(aws):
    good = envelope("CvParsed", "app_1", "2026-10-05T18:40:03Z")
    missing_fields = {"event_type": "CvParsed"}
    result = handler(sqs_event("not json", good, missing_fields, "[1, 2]"), None)
    assert result == {
        "batchItemFailures": [{"itemIdentifier": "msg-0"}, {"itemIdentifier": "msg-2"}, {"itemIdentifier": "msg-3"}]
    }
    assert len(history(aws["table"], "app_1")) == 1
