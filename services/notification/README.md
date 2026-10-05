# Notification service

| | |
|---|---|
| Listens to | `CandidateMatched` (SQS queue `notification`) |
| Publishes | nothing |
| Owns | `notifications` DynamoDB table (log of sent emails) |

## What it does

1. Ignore anything that isn't a `strong_match`.
2. Send the recruiter one email per application (the sent log prevents duplicates).
3. Built in round 2.

_Code and tests arrive in the build step for this service._
