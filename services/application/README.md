# Application service

| | |
|---|---|
| Listens to | HTTP `POST /applications` (via API Gateway) |
| Publishes | `ApplicationSubmitted` |
| Owns | `applications` DynamoDB table, `cvs` S3 bucket |

## What it does

1. Validate the request (name, email, job ID, CV file).
2. Derive a deterministic `application_id` from candidate email + job ID.
3. Write the application with a conditional put; if it already exists, return the original (HTTP 200) and publish nothing.
4. Store the CV in S3 and publish `ApplicationSubmitted` (HTTP 201).

The stored application carries its `event_id` and an `event_published` flag. If the function crashes after saving but before publishing, the next retry of the same request finishes the job with the **same** `event_id`, so the event is never lost and consumers can still de-duplicate it.

## API

`POST /applications`

```json
{
  "full_name": "Alex Example",
  "email": "alex@example.com",
  "job_id": "job_devops_munich_042",
  "cv": { "content_type": "application/pdf", "content_base64": "JVBERi0xLjcK..." }
}
```

| Response | When |
|---|---|
| `201` `{"application_id", "job_id", "candidate_id", "submitted_at", "status": "submitted"}` | New application, event published |
| `200` same fields, `"status": "already_submitted"` | Same email + job ID was already submitted |
| `400` `{"error": "validation_failed", "details": [...]}` | Every problem with the request, listed at once |

CVs can be PDF or plain text, up to 2 MB. The CV is sent base64-encoded in the JSON body to keep the API to one call; a production version would hand out a pre-signed S3 upload URL instead.

## Code

```
src/application_service/
├── domain.py    # validation, IDs, event envelope (pure Python, no AWS)
└── handler.py   # Lambda entry point: DynamoDB, S3, SNS
tests/           # pytest + moto (fake AWS); events are checked against docs/events/*.schema.json
```

The Lambda has no dependencies beyond `boto3`, which the Python runtime already includes, so the deployment zip is just `src/`.

## Run the tests

From the repo root:

```bash
python -m venv .venv
.venv/Scripts/activate            # Windows; on macOS/Linux: source .venv/bin/activate
pip install -r services/application/requirements-dev.txt
pytest services/application
```

No AWS account or credentials are needed: every AWS call goes to [moto](https://github.com/getmoto/moto).

## Try it after deploying

```bash
API=$(terraform -chdir=infra/envs/dev output -raw api_url)
CV=$(base64 -w0 my-cv.pdf)
curl -s -X POST "$API/applications" -H "Content-Type: application/json" \
  -d "{\"full_name\":\"Alex Example\",\"email\":\"alex@example.com\",\"job_id\":\"job_1\",\"cv\":{\"content_type\":\"application/pdf\",\"content_base64\":\"$CV\"}}"
```

Run the same command twice: the first returns `201`, the second `200` with `"already_submitted"`.
