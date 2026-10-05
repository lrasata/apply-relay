import json
import uuid
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from matching_service import handler as handler_module

REGION = "eu-central-1"
SERVICE_DIR = Path(__file__).resolve().parents[1]
EVENTS_DIR = SERVICE_DIR.parents[1] / "docs" / "events"
JOBS = json.loads((SERVICE_DIR / "jobs.json").read_text())


@pytest.fixture
def aws(monkeypatch):
    """Fake AWS (moto): matches and jobs tables (jobs seeded from jobs.json, as Terraform
    does), the candidate-matched topic, and a queue subscribed to it."""
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.delenv("AWS_PROFILE", raising=False)

    with mock_aws():
        dynamodb = boto3.resource("dynamodb")

        def create_table(name, key):
            return dynamodb.create_table(
                TableName=name,
                KeySchema=[{"AttributeName": key, "KeyType": "HASH"}],
                AttributeDefinitions=[{"AttributeName": key, "AttributeType": "S"}],
                BillingMode="PAY_PER_REQUEST",
            )

        matches = create_table("jae-test-matches", "application_id")
        jobs = create_table("jae-test-jobs", "job_id")
        for job in JOBS:
            jobs.put_item(Item=job)

        sns = boto3.client("sns")
        topic_arn = sns.create_topic(Name="jae-test-candidate-matched")["TopicArn"]
        sqs = boto3.client("sqs")
        queue_url = sqs.create_queue(QueueName="jae-test-notification")["QueueUrl"]
        queue_arn = sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
        sns.subscribe(
            TopicArn=topic_arn, Protocol="sqs", Endpoint=queue_arn, Attributes={"RawMessageDelivery": "true"}
        )

        monkeypatch.setenv("MATCHES_TABLE", "jae-test-matches")
        monkeypatch.setenv("JOBS_TABLE", "jae-test-jobs")
        monkeypatch.setenv("TOPIC_ARN", topic_arn)
        handler_module._aws.cache_clear()

        def published_events():
            messages = sqs.receive_message(
                QueueUrl=queue_url, MaxNumberOfMessages=10, MessageAttributeNames=["All"]
            ).get("Messages", [])
            return [json.loads(m["Body"]) for m in messages]

        yield {"matches": matches, "published_events": published_events}

    handler_module._aws.cache_clear()


def cv_parsed(application_id, skills, job_id="job_devops_munich_042", **overrides):
    """A CvParsed envelope as the CV parser publishes it."""
    envelope = {
        "event_id": str(uuid.uuid4()),
        "event_type": "CvParsed",
        "event_version": 1,
        "occurred_at": "2026-10-05T18:40:03Z",
        "correlation_id": application_id,
        "producer": "cv-parser-service",
        "data": {
            "application_id": application_id,
            "job_id": job_id,
            "skills": skills,
            "years_of_experience": 5.0,
            "languages": ["english"],
            "parsed_at": "2026-10-05T18:40:03Z",
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

    return {"envelope": load("envelope.schema.json"), "candidate-matched": load("candidate-matched.schema.json")}
