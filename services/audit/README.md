# Audit service

| | |
|---|---|
| Listens to | all events (SQS queue `audit`) |
| Publishes | nothing |
| Owns | `audit-log` DynamoDB table |

## What it does

Store every event, keyed so the full history of one application reads in order:

| Key | Value |
|---|---|
| `correlation_id` (partition) | the application ID |
| `sk` (sort) | `<occurred_at>#<event_id>`: sorted by time, unique per event |

The original envelope is kept as JSON text next to a few searchable fields (`event_type`, `producer`, ...). Writes are conditional on the key not existing, so a duplicate delivery is a no-op. Events of types the service doesn't know yet are still recorded; only messages that aren't a valid envelope fail (and end in the dead-letter queue).

The service's IAM role can only `PutItem`: it can add to the log but never change or delete it. Items expire through DynamoDB TTL after `audit_retention_days` (365 by default), since `ApplicationSubmitted` carries candidate names and emails.

## Read an application's history

```bash
python scripts/trace_application.py app_5f1d7a0c9e2b          # add --full for payloads
```

```
2026-10-05T18:40:00Z  ApplicationSubmitted   application-service  0b7c5a52-...
2026-10-05T18:40:03Z  CvParsed               cv-parser-service    4d1e...
2026-10-05T18:40:05Z  CandidateMatched       matching-service     9a3f...
```

## Run the tests

```bash
pip install -r services/audit/requirements-dev.txt
pytest services/audit
```
