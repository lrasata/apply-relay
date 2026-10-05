# Infrastructure

All AWS resources are defined in Terraform (>= 1.10, AWS provider 5.x).

```
infra/
├── bootstrap/            # one-time: S3 bucket for Terraform state
├── envs/dev/             # the dev environment (wires the modules together)
└── modules/
    ├── event_topic/      # one SNS topic per event type
    └── consumer_queue/   # SQS queue + DLQ + SNS subscriptions + DLQ alarm, per consuming service
```

## What step 2 creates (32 resources)

| | Resources |
|---|---|
| Event topics | `jae-dev-application-submitted`, `jae-dev-cv-parsed`, `jae-dev-candidate-matched` (SNS, encrypted at rest) |
| Consumer queues | `jae-dev-cv-parser`, `jae-dev-matching`, `jae-dev-notification`, `jae-dev-audit` (SQS, encrypted, long polling) |
| Dead-letter queues | one `-dlq` per queue, 14-day retention; a message moves there after 3 failed attempts |
| Wiring | SNS → SQS subscriptions with raw delivery, and queue policies that only accept messages from the subscribed topics |
| Alerts | a CloudWatch alarm per DLQ ("anything landed here?") → `jae-dev-ops-alerts` SNS topic → your email |
| Cost guardrail | an account-wide monthly AWS budget (default $5) that emails you at 80% forecast and 100% actual |

The wiring lives in one map in [`envs/dev/main.tf`](envs/dev/main.tf):

```hcl
consumers = {
  cv-parser    = ["application-submitted"]
  matching     = ["cv-parsed"]
  notification = ["candidate-matched"]
  audit        = ["application-submitted", "cv-parsed", "candidate-matched"]
}
```

Adding a consumer, or subscribing one to another event, is a one-line change.

## What step 3 adds (14 resources)

| | Resources |
|---|---|
| Public API | `jae-dev-api` HTTP API with one route, `POST /applications`, throttled to 5 requests/s (burst 10) |
| Function | `jae-dev-application` Lambda (Python 3.12, arm64), X-Ray tracing, logs kept 14 days |
| Data | `jae-dev-applications` DynamoDB table (on-demand) and `jae-dev-cvs-<account-id>` S3 bucket (private, encrypted, CVs deleted after 30 days) |
| Permissions | one IAM role that can only write its own table, put CVs, and publish to `application-submitted` |

Terraform zips `services/application/src` itself (via the `archive` provider), so there is no separate build step.

## What step 4 adds (6 resources)

| | Resources |
|---|---|
| Function | `jae-dev-cv-parser` Lambda (Python 3.12, arm64, 512 MB), triggered by the `jae-dev-cv-parser` queue in batches of 10, at most 2 running at once |
| Data | `jae-dev-parsed-cvs` DynamoDB table (on-demand) |
| Permissions | one IAM role that can read its queue and CVs, write its own table, and publish to `cv-parsed` |

This function bundles `pypdf`, so its package is built first with `python scripts/build_lambda.py cv_parser` (see "Deploy it yourself").

**Expected cost:** effectively $0 while idle. SNS, SQS, Lambda and DynamoDB on-demand are within the free tier at portfolio traffic, API Gateway HTTP APIs cost about $1 per million requests, the first 10 CloudWatch alarms and the first 2 budgets are free, and the state bucket holds a few KB.

## Deploy it yourself

Prerequisites: Terraform >= 1.10, AWS CLI v2, and credentials for your account (`aws sts get-caller-identity` should work). Default region is `eu-central-1` (Frankfurt).

**1. Create the state bucket (once).**

```bash
cd infra/bootstrap
terraform init
terraform apply            # note the state_bucket output
```

**2. Configure the dev environment.**

```bash
cd ../envs/dev
cp backend.hcl.example backend.hcl     # put the bucket name from step 1 in it
cp example.tfvars dev.tfvars           # put your email in it
```

**3. Build, plan, check, apply.**

```bash
python ../../../scripts/build_lambda.py cv_parser   # bundles pypdf for the CV parser
terraform init -backend-config=backend.hcl
terraform plan -var-file=dev.tfvars    # should say: Plan: 52 to add
terraform apply -var-file=dev.tfvars
```

**4. Confirm the alert email.** AWS sends a "Subscription Confirmation" email to `alert_email`; click the link once, or alarms won't reach you.

## Try the fan-out without any code

Publish a test event to the first topic and watch it arrive in two queues (the CV parser's and the audit's):

```bash
TOPIC=$(terraform output -json topic_arns | jq -r '."application-submitted"')
aws sns publish --topic-arn "$TOPIC" --message '{"event_type":"ApplicationSubmitted","note":"hello"}'

for q in cv-parser audit matching; do
  URL=$(terraform output -json queue_urls | jq -r ".\"$q\"")
  echo "$q: $(aws sqs get-queue-attributes --queue-url "$URL" \
    --attribute-names ApproximateNumberOfMessages --query Attributes.ApproximateNumberOfMessages --output text)"
done
# cv-parser: 1, audit: 1, matching: 0  <- matching only listens to cv-parsed
```

Purge the queues afterwards (`aws sqs purge-queue --queue-url ...`) so the test message doesn't reach the services once they exist.

## Tear it down

```bash
cd infra/envs/dev && terraform destroy -var-file=dev.tfvars
```

The state bucket has `prevent_destroy` so it isn't deleted by accident. Remove that line first if you really want to delete it.
