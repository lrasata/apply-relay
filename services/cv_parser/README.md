# CV parser service

| | |
|---|---|
| Listens to | `ApplicationSubmitted` (SQS queue `cv-parser`) |
| Publishes | `CvParsed` |
| Owns | `parsed-cvs` DynamoDB table |

## What it does

1. Skip the message if this application's `CvParsed` was already published (SQS can deliver a message twice).
2. Read the CV from S3 and extract skills, years of experience and spoken languages (keyword-based in v1).
3. Store the result and publish `CvParsed`.

Like the application service, the stored result carries its `event_id` and an `event_published` flag, so a crash between storing and publishing is finished by the SQS retry with the same `event_id`.

Messages are processed in batches of up to 10 with partial batch failures: a missing or corrupt CV fails only its own message, which is retried and, after 3 attempts, moved to the dead-letter queue (raising the DLQ alarm). An event type or version this service doesn't understand fails the same way instead of being guessed at.

## How parsing works (v1)

All in [`extraction.py`](src/cv_parser_service/extraction.py), deliberately simple and explainable:

| Field | Rule |
|---|---|
| `skills` | ~35 canonical skills, each with aliases (`k8s`, `EKS` → `kubernetes`; `Amazon Web Services` → `aws`). Boundaries are strict, so `Java` doesn't match `JavaScript` and `GitHub Actions` doesn't count as `git`. |
| `years_of_experience` | An explicit "*N* years of experience" (also "*N* Jahre Berufserfahrung") wins; otherwise the span from the earliest job start to the latest end date (`2019 – present`); otherwise `null`. |
| `languages` | English and native names (`Deutsch`, `Français`, ...). |

Text comes from `pypdf` for PDFs. Scanned PDFs (images only) yield no text; Amazon Textract would be the upgrade path. The event contract stays the same if this module is later replaced by an LLM or Amazon Comprehend.

## Code

```
src/cv_parser_service/
├── extraction.py   # text extraction and keyword rules (no AWS)
└── handler.py      # SQS batch handler: S3, DynamoDB, SNS
tests/              # pytest + moto; test PDFs are generated in tests/pdfs.py
```

## Run the tests

From the repo root, with the venv from the application service:

```bash
pip install -r services/cv_parser/requirements-dev.txt
pytest services/cv_parser
```

## Build the Lambda package

This service bundles `pypdf`, so build it before `terraform plan`:

```bash
python scripts/build_lambda.py cv_parser
```

It downloads Linux arm64 / Python 3.12 wheels into `services/cv_parser/build/package/`, which Terraform zips.
