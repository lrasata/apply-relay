import json
import uuid

import boto3
import pytest
from moto import mock_aws
from moto.core import DEFAULT_ACCOUNT_ID
from moto.ses.models import ses_backends

from notification_service import handler as handler_module

REGION = "eu-central-1"
SENDER = "jobs@example.com"
RECRUITER = "recruiter@example.com"


@pytest.fixture
def aws(monkeypatch):
    """Fake AWS (moto): the notifications table and SES with a verified sender."""
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.delenv("AWS_PROFILE", raising=False)

    with mock_aws():
        table = boto3.resource("dynamodb").create_table(
            TableName="jae-test-notifications",
            KeySchema=[{"AttributeName": "application_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "application_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        boto3.client("ses").verify_email_identity(EmailAddress=SENDER)

        monkeypatch.setenv("TABLE_NAME", "jae-test-notifications")
        monkeypatch.setenv("SENDER_EMAIL", SENDER)
        monkeypatch.setenv("RECRUITER_EMAIL", RECRUITER)
        handler_module._aws.cache_clear()

        def sent_emails():
            return list(ses_backends[DEFAULT_ACCOUNT_ID][REGION].sent_messages)

        yield {"table": table, "sent_emails": sent_emails}

    handler_module._aws.cache_clear()


def candidate_matched(application_id, recommendation="strong_match", score=85, **overrides):
    """A CandidateMatched envelope as the matching service publishes it."""
    envelope = {
        "event_id": str(uuid.uuid4()),
        "event_type": "CandidateMatched",
        "event_version": 1,
        "occurred_at": "2026-10-05T18:40:05Z",
        "correlation_id": application_id,
        "producer": "matching-service",
        "data": {
            "application_id": application_id,
            "job_id": "job_devops_munich_042",
            "score": score,
            "matched_skills": ["aws", "terraform", "python", "docker"],
            "missing_skills": ["kubernetes"],
            "recommendation": recommendation,
            "matched_at": "2026-10-05T18:40:05Z",
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
