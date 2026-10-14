# Alerts service

| | |
|---|---|
| Listens to | every CloudWatch alarm (SNS topic `alarms`) |
| Publishes | readable alert emails (SNS topic `ops-alerts`, which emails `alert_email`) |
| Owns | nothing |

## What it does

CloudWatch's own alarm email is a fixed format: thirty lines of metric settings and nothing about what to do. This function rewrites each alarm notification as:

```
Subject: [ALARM] matching: 1 message failed and moved to the dead-letter queue

WHAT HAPPENED
1 message for the matching service failed every retry and
was moved to the dead-letter queue apply-relay-dev-matching-dlq.
Nothing processes it until you act.

WHAT TO DO
1. See why it failed:
   aws logs tail /aws/lambda/apply-relay-dev-matching --since 1h --filter-pattern "processing failed"
2. Fix the cause, then send it back to be processed:
   SQS console > the dead-letter queue > Start DLQ redrive.
   If it can never succeed, delete it instead (Purge).

LINKS
Dead-letter queue: https://...
Logs:              https://...
Dashboard:         https://...
Alarm:             https://...
```

It recognises the project's alarms from their metric: dead-letter queue not empty, queue backlog, API 5xx. Each gets its own wording, and a short `[OK]` email when it recovers. Any other alarm still gets a generic email with its description.

It never loses an alert:
- A notification it can't parse is forwarded as it is.
- If publishing fails, the invocation fails and Lambda retries it.
- If the function itself keeps failing, the `apply-relay-dev-alerts-errors` alarm emails you directly, in CloudWatch's own format.

## Code

```
src/alerts_service/
├── message.py   # parse the alarm, pick the wording, build console links (no AWS)
└── handler.py   # SNS handler: format, then publish to ops-alerts
tests/           # pytest + moto (fake SNS)
```

## Run the tests

```bash
pip install -r services/alerts/requirements-dev.txt
pytest services/alerts
```
