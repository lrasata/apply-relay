# Notification service

| | |
|---|---|
| Listens to | `CandidateMatched`, strong matches only (SQS queue `notification`) |
| Publishes | nothing |
| Owns | `notifications` DynamoDB table (log of sent emails) |

## What it does

1. Ignore anything that isn't a `strong_match`. The SNS subscription already filters on the `recommendation` message attribute, so weak matches never reach the queue; the handler checks again in case the filter is ever misconfigured.
2. Record the notification as `sending` (one item per application, conditional write).
3. Send the recruiter an email with SES, then record it as `sent` with the SES message ID.

An application already marked `sent` is skipped, so a duplicate delivery never sends a second email. If the function crashes after sending but before recording `sent`, the retry sends again: email is at-least-once, the safe side for "the recruiter must hear about this".

The email contains the application ID, job, score and matched/missing skills, but no candidate name or email: the event doesn't carry them, and keeping personal data out of inboxes is a feature.

## Code

```
src/notification_service/
├── message.py   # the email text (no AWS)
└── handler.py   # SQS batch handler: DynamoDB, SES
tests/           # pytest + moto (fake DynamoDB and SES)
```

## Run the tests

```bash
pip install -r services/notification/requirements-dev.txt
pytest services/notification
```
