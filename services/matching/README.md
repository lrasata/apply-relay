# Matching service

| | |
|---|---|
| Listens to | `CvParsed` (SQS queue `matching`) |
| Publishes | `CandidateMatched` |
| Owns | `matches` DynamoDB table, `jobs` table (job requirements) |

## What it does

1. Skip the message if this application's `CandidateMatched` was already published.
2. Look up the job's required and nice-to-have skills in its own `jobs` table.
3. Compute a 0-100 score and a recommendation, store it and publish `CandidateMatched`.

Every match is published, weak ones included, so the audit trail is complete. The event also carries `recommendation` as an SNS message attribute, so the notification queue can later filter for strong matches without reading the body.

Reliability is the same as the CV parser: duplicate deliveries are skipped, a crash before publishing is finished by the retry with the same `event_id`, and a message for an unknown job or an unsupported event version fails on its own and ends up in the dead-letter queue.

## Scoring

In [`scoring.py`](src/matching_service/scoring.py). Required skills count twice, nice-to-haves once:

```
score = 100 * (2 * required_matched + nice_matched) / (2 * required_total + nice_total)
```

| Score | Recommendation |
|---|---|
| 75-100 | `strong_match` (notifies the recruiter) |
| 50-74 | `possible_match` |
| 0-49 | `weak_match` |

For the DevOps job (5 required, 3 nice-to-have): every required skill and nothing else scores 77, a strong match; one required skill missing and no extras scores 62; the three nice-to-haves alone score 23. `missing_skills` lists only required skills, since that's what a recruiter acts on.

## Jobs

The job catalogue is [`jobs.json`](jobs.json). Terraform loads it into the `jobs` table, so adding a job is an edit and an apply. A test checks that every skill in it is one the CV parser can actually produce, so a typo like `k8s` fails the build instead of silently never matching.

## Code

```
jobs.json                 # job catalogue
src/matching_service/
├── scoring.py            # the score (no AWS)
└── handler.py            # SQS batch handler: DynamoDB, SNS
tests/                    # pytest + moto
```

## Run the tests

From the repo root:

```bash
pip install -r services/matching/requirements-dev.txt
pytest services/matching
```

The Lambda has no dependencies beyond `boto3`, so Terraform zips `src/` directly; no build step.
