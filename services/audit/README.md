# Audit service

| | |
|---|---|
| Listens to | all events (SQS queue `audit`) |
| Publishes | nothing |
| Owns | `audit-log` DynamoDB table |

## What it does

1. Store every event keyed by `correlation_id` + `occurred_at`, so the full history of one application can be read in order.
2. Built in round 2.

_Code and tests arrive in the build step for this service._
