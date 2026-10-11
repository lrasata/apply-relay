import base64
import json
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from application_service import handler as handler_module

REGION = "eu-central-1"
TABLE = "apply-relay-test-applications"
BUCKET = "apply-relay-test-cvs"
EVENTS_DIR = Path(__file__).resolve().parents[3] / "docs" / "events"

PDF_BYTES = b"%PDF-1.7\n1 0 obj << >> endobj\ntrailer << >>\n%%EOF\n"


@pytest.fixture
def aws(monkeypatch):
    """Fake AWS (moto): the table, the CV bucket, the topic, and a queue subscribed to the
    topic so tests can read exactly what consumers would receive. Nothing leaves the machine."""
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.delenv("AWS_PROFILE", raising=False)

    with mock_aws():
        dynamodb = boto3.resource("dynamodb")
        table = dynamodb.create_table(
            TableName=TABLE,
            KeySchema=[{"AttributeName": "application_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "application_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        s3 = boto3.client("s3")
        s3.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": REGION})

        sns = boto3.client("sns")
        topic_arn = sns.create_topic(Name="apply-relay-test-application-submitted")["TopicArn"]
        sqs = boto3.client("sqs")
        queue_url = sqs.create_queue(QueueName="apply-relay-test-consumer")["QueueUrl"]
        queue_arn = sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
        sns.subscribe(
            TopicArn=topic_arn, Protocol="sqs", Endpoint=queue_arn, Attributes={"RawMessageDelivery": "true"}
        )

        monkeypatch.setenv("TABLE_NAME", TABLE)
        monkeypatch.setenv("BUCKET_NAME", BUCKET)
        monkeypatch.setenv("TOPIC_ARN", topic_arn)
        handler_module._aws.cache_clear()

        def published_events():
            messages = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10).get("Messages", [])
            return [json.loads(m["Body"]) for m in messages]

        yield {
            "table": table,
            "s3": s3,
            "sns": sns,
            "topic_arn": topic_arn,
            "published_events": published_events,
        }

    handler_module._aws.cache_clear()


def make_body(**overrides):
    body = {
        "full_name": "Alex Example",
        "email": "Alex@Example.com",
        "job_id": "job_devops_munich_042",
        "cv": {"content_type": "application/pdf", "content_base64": base64.b64encode(PDF_BYTES).decode()},
    }
    body.update(overrides)
    return body


def api_event(body, *, base64_encoded=False):
    """Minimal API Gateway HTTP API (payload 2.0) event."""
    raw = body if isinstance(body, str) else json.dumps(body)
    if base64_encoded:
        raw = base64.b64encode(raw.encode()).decode()
    return {
        "version": "2.0",
        "routeKey": "POST /applications",
        "rawPath": "/applications",
        "body": raw,
        "isBase64Encoded": base64_encoded,
    }


@pytest.fixture
def schemas():
    from jsonschema import Draft202012Validator

    def load(name):
        schema = json.loads((EVENTS_DIR / name).read_text())
        return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)

    return {
        "envelope": load("envelope.schema.json"),
        "application-submitted": load("application-submitted.schema.json"),
    }
