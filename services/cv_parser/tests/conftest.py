import json
import uuid
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from cv_parser_service import handler as handler_module

REGION = "eu-central-1"
TABLE = "apply-relay-test-parsed-cvs"
BUCKET = "apply-relay-test-cvs"
EVENTS_DIR = Path(__file__).resolve().parents[3] / "docs" / "events"


@pytest.fixture
def aws(monkeypatch):
    """Fake AWS (moto): the service's table, the CV bucket, the cv-parsed topic, and a queue
    subscribed to it so tests read exactly what the matching service would receive."""
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.delenv("AWS_PROFILE", raising=False)

    with mock_aws():
        table = boto3.resource("dynamodb").create_table(
            TableName=TABLE,
            KeySchema=[{"AttributeName": "application_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "application_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        s3 = boto3.client("s3")
        s3.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": REGION})

        sns = boto3.client("sns")
        topic_arn = sns.create_topic(Name="apply-relay-test-cv-parsed")["TopicArn"]
        sqs = boto3.client("sqs")
        queue_url = sqs.create_queue(QueueName="apply-relay-test-matching")["QueueUrl"]
        queue_arn = sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
        sns.subscribe(
            TopicArn=topic_arn, Protocol="sqs", Endpoint=queue_arn, Attributes={"RawMessageDelivery": "true"}
        )

        monkeypatch.setenv("TABLE_NAME", TABLE)
        monkeypatch.setenv("TOPIC_ARN", topic_arn)
        handler_module._aws.cache_clear()

        def published_events():
            messages = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10).get("Messages", [])
            return [json.loads(m["Body"]) for m in messages]

        def upload_cv(application_id, content, content_type="text/plain"):
            key = f"applications/{application_id}/cv.{'pdf' if content_type == 'application/pdf' else 'txt'}"
            s3.put_object(Bucket=BUCKET, Key=key, Body=content, ContentType=content_type)
            return key

        yield {"table": table, "published_events": published_events, "upload_cv": upload_cv}

    handler_module._aws.cache_clear()


def application_submitted(application_id, cv_key, content_type="text/plain", **overrides):
    """An ApplicationSubmitted envelope as the application service publishes it."""
    envelope = {
        "event_id": str(uuid.uuid4()),
        "event_type": "ApplicationSubmitted",
        "event_version": 1,
        "occurred_at": "2026-10-05T18:40:00Z",
        "correlation_id": application_id,
        "producer": "application-service",
        "data": {
            "application_id": application_id,
            "job_id": "job_devops_munich_042",
            "candidate": {"candidate_id": "cand_1", "full_name": "Alex Example", "email": "alex@example.com"},
            "cv": {"bucket": BUCKET, "key": cv_key, "content_type": content_type},
            "submitted_at": "2026-10-05T18:40:00Z",
        },
    }
    envelope.update(overrides)
    return envelope


def sqs_event(*envelopes):
    return {
        "Records": [
            {"messageId": f"msg-{i}", "body": json.dumps(envelope), "eventSource": "aws:sqs"}
            for i, envelope in enumerate(envelopes)
        ]
    }


@pytest.fixture
def schemas():
    from jsonschema import Draft202012Validator

    def load(name):
        schema = json.loads((EVENTS_DIR / name).read_text())
        return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)

    return {"envelope": load("envelope.schema.json"), "cv-parsed": load("cv-parsed.schema.json")}
