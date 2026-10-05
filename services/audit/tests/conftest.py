import json
import uuid

import boto3
import pytest
from moto import mock_aws

from audit_service import handler as handler_module

REGION = "eu-central-1"


@pytest.fixture
def aws(monkeypatch):
    """Fake AWS (moto): the audit-log table."""
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.delenv("AWS_PROFILE", raising=False)

    with mock_aws():
        table = boto3.resource("dynamodb").create_table(
            TableName="jae-test-audit-log",
            KeySchema=[
                {"AttributeName": "correlation_id", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "correlation_id", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        monkeypatch.setenv("TABLE_NAME", "jae-test-audit-log")
        monkeypatch.setenv("RETENTION_DAYS", "365")
        handler_module._table.cache_clear()
        yield {"table": table}

    handler_module._table.cache_clear()


def envelope(event_type, correlation_id, occurred_at, producer="some-service", **overrides):
    value = {
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "event_version": 1,
        "occurred_at": occurred_at,
        "correlation_id": correlation_id,
        "producer": producer,
        "data": {"application_id": correlation_id},
    }
    value.update(overrides)
    return value


def sqs_event(*bodies):
    return {
        "Records": [
            {"messageId": f"msg-{i}", "body": body if isinstance(body, str) else json.dumps(body)}
            for i, body in enumerate(bodies)
        ]
    }
