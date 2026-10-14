import json

import boto3
import pytest
from moto import mock_aws

from alerts_service import handler as handler_module

REGION = "eu-central-1"
ACCOUNT = "123456789012"
PREFIX = "apply-relay-test"


@pytest.fixture
def aws(monkeypatch):
    """Fake AWS (moto): the ops-alerts topic, with a queue subscribed to see what's published."""
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    monkeypatch.setenv("AWS_REGION", REGION)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.delenv("AWS_PROFILE", raising=False)

    with mock_aws():
        sns = boto3.client("sns")
        topic_arn = sns.create_topic(Name=f"{PREFIX}-ops-alerts")["TopicArn"]
        sqs = boto3.client("sqs")
        queue_url = sqs.create_queue(QueueName="inbox")["QueueUrl"]
        queue_arn = sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
        sns.subscribe(TopicArn=topic_arn, Protocol="sqs", Endpoint=queue_arn)

        monkeypatch.setenv("OUTPUT_TOPIC_ARN", topic_arn)
        monkeypatch.setenv("NAME_PREFIX", PREFIX)
        monkeypatch.setenv("DASHBOARD_NAME", f"{PREFIX}-pipeline")
        handler_module._sns.cache_clear()

        def sent():
            """(subject, text) of every message published to ops-alerts."""
            messages = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10).get("Messages", [])
            return [(json.loads(m["Body"]).get("Subject"), json.loads(m["Body"])["Message"]) for m in messages]

        yield {"sent": sent}

    handler_module._sns.cache_clear()


def alarm_notification(name, namespace, metric, dimensions, state="ALARM", value=1.0, description="An alarm."):
    """The JSON CloudWatch publishes to SNS when an alarm changes state."""
    return {
        "AlarmName": name,
        "AlarmDescription": description,
        "AWSAccountId": ACCOUNT,
        "AlarmConfigurationUpdatedTimestamp": "2026-10-14T10:00:00.000+0000",
        "NewStateValue": state,
        "NewStateReason": (
            f"Threshold Crossed: 1 out of the last 1 datapoints [{value} (14/10/26 19:44:00)] "
            "was greater than the threshold (0.0) (minimum 1 datapoint for OK -> ALARM transition)."
        ),
        "StateChangeTime": "2026-10-14T19:49:12.345+0000",
        "Region": "EU (Frankfurt)",
        "AlarmArn": f"arn:aws:cloudwatch:{REGION}:{ACCOUNT}:alarm:{name}",
        "OldStateValue": "OK" if state == "ALARM" else "ALARM",
        "Trigger": {
            "MetricName": metric,
            "Namespace": namespace,
            "StatisticType": "Statistic",
            "Statistic": "MAXIMUM",
            "Dimensions": [{"value": v, "name": k} for k, v in dimensions.items()],
            "Period": 300,
            "EvaluationPeriods": 1,
            "ComparisonOperator": "GreaterThanThreshold",
            "Threshold": 0.0,
        },
    }


def dlq_alarm(service="matching", **kwargs):
    return alarm_notification(
        f"{PREFIX}-{service}-dlq-not-empty",
        "AWS/SQS",
        "ApproximateNumberOfMessagesVisible",
        {"QueueName": f"{PREFIX}-{service}-dlq"},
        **kwargs,
    )


def sns_event(*messages):
    return {
        "Records": [
            {"EventSource": "aws:sns", "Sns": {"Subject": "ALARM: something", "Message": m if isinstance(m, str) else json.dumps(m)}}
            for m in messages
        ]
    }
