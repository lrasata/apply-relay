# Matching service

| | |
|---|---|
| Listens to | `CvParsed` (SQS queue `matching`) |
| Publishes | `CandidateMatched` |
| Owns | `matches` DynamoDB table, job requirements |

## What it does

1. Skip the message if its `event_id` was already processed.
2. Look up the job's required and nice-to-have skills.
3. Compute a 0-100 score and a recommendation, store it and publish `CandidateMatched`.

_Code and tests arrive in the build step for this service._
