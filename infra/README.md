# Infrastructure

All AWS resources are defined in Terraform (>= 1.10, AWS provider 5.x).

```
infra/
├── bootstrap/            # one-time: S3 bucket + DynamoDB lock table for Terraform state
├── ci/                   # one-time: GitHub OIDC provider and the CI roles
├── envs/dev/             # the dev environment (wires the modules together)
└── modules/
    ├── event_topic/      # one SNS topic per event type
    └── consumer_queue/   # SQS queue + DLQ + SNS subscriptions + DLQ alarm, per consuming service
```

## What step 2 creates (32 resources)

| | Resources |
|---|---|
| Event topics | `apply-relay-dev-application-submitted`, `apply-relay-dev-cv-parsed`, `apply-relay-dev-candidate-matched` (SNS, encrypted at rest) |
| Consumer queues | `apply-relay-dev-cv-parser`, `apply-relay-dev-matching`, `apply-relay-dev-notification`, `apply-relay-dev-audit` (SQS, encrypted, long polling) |
| Dead-letter queues | one `-dlq` per queue, 14-day retention; a message moves there after 3 failed attempts |
| Wiring | SNS → SQS subscriptions with raw delivery, and queue policies that only accept messages from the subscribed topics |
| Alerts | a CloudWatch alarm per DLQ ("anything landed here?") → `apply-relay-dev-ops-alerts` SNS topic → your email |
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
| Public API | `apply-relay-dev-api` HTTP API with one route, `POST /applications`, throttled to 5 requests/s (burst 10) |
| Function | `apply-relay-dev-application` Lambda (Python 3.12, arm64), X-Ray tracing, logs kept 14 days |
| Data | `apply-relay-dev-applications` DynamoDB table (on-demand) and `apply-relay-dev-cvs-<account-id>` S3 bucket (private, encrypted, CVs deleted after 30 days) |
| Permissions | one IAM role that can only write its own table, put CVs, and publish to `application-submitted` |

Terraform zips `services/application/src` itself (via the `archive` provider), so there is no separate build step.

## What step 4 adds (6 resources)

| | Resources |
|---|---|
| Function | `apply-relay-dev-cv-parser` Lambda (Python 3.12, arm64, 512 MB), triggered by the `apply-relay-dev-cv-parser` queue in batches of 10, at most 2 running at once |
| Data | `apply-relay-dev-parsed-cvs` DynamoDB table (on-demand) |
| Permissions | one IAM role that can read its queue and CVs, write its own table, and publish to `cv-parsed` |

This function bundles `pypdf`, so its package is built first with `python scripts/build_lambda.py cv_parser` (see "Deploy it yourself").

## What step 5 adds (10 resources)

| | Resources |
|---|---|
| Function | `apply-relay-dev-matching` Lambda (Python 3.12, arm64), triggered by the `apply-relay-dev-matching` queue in batches of 10, at most 2 running at once |
| Data | `apply-relay-dev-matches` table, and `apply-relay-dev-jobs` table seeded with the 3 jobs in [`services/matching/jobs.json`](../services/matching/jobs.json) |
| Permissions | one IAM role that can read its queue and the jobs, write its own matches, and publish to `candidate-matched` |

To add or change a job, edit `jobs.json` and apply; Terraform updates the table items.

## What step 7 adds (21 resources)

| | Resources |
|---|---|
| Notification | `apply-relay-dev-notification` Lambda, `apply-relay-dev-notifications` table (sent log), an SES email identity. The queue's SNS subscription has a filter policy, so only `strong_match` events reach it |
| Audit | `apply-relay-dev-audit` Lambda (append-only: it can `PutItem` and nothing else), `apply-relay-dev-audit-log` table keyed by application and time, with a TTL (365 days by default, `audit_retention_days`) |
| Alarms | a backlog alarm per queue (oldest message > 15 min) and an API 5xx alarm, on top of the 4 DLQ alarms: 9 in total, inside the 10 free alarms |
| Dashboard | `apply-relay-dev-pipeline`: API traffic, events published, Lambda errors and p95 duration, queue and DLQ depth |
| Queries | saved Logs Insights queries `apply-relay-dev/trace-one-application` and `apply-relay-dev/errors` |
| Tracing | X-Ray active tracing on the three SNS topics (all Lambdas already had it) |

Strong-match emails go to `recruiter_email` (defaults to `alert_email`) and are sent from that same address. AWS emails it a verification link on apply; click it once, or the notification messages will fail and land in the DLQ. A new account's SES sandbox only sends to verified addresses, which is all this project needs.

**Expected cost:** effectively $0 while idle. SNS, SQS, Lambda and DynamoDB on-demand are within the free tier at portfolio traffic, API Gateway HTTP APIs cost about $1 per million requests, the first 10 CloudWatch alarms and the first 2 budgets are free, and the state bucket holds a few KB.

## Deploy it yourself

Prerequisites: Terraform >= 1.10, AWS CLI v2, and credentials for your account (`aws sts get-caller-identity` should work). Default region is `eu-central-1` (Frankfurt).

**1. Create the state bucket and lock table (once).**

```bash
cd infra/bootstrap
terraform init
terraform apply            # note the state_bucket and lock_table outputs
```

**2. Configure the dev environment.**

```bash
cd ../envs/dev
cp backend.hcl.example backend.hcl     # put the bucket and lock_table names from step 1 in it
cp example.tfvars dev.tfvars           # put your email in it
```

**3. Deploy the services one at a time.**

`services` in `dev.tfvars` lists the services to deploy. The example starts with `["application"]`. Apply, check that the service works, then add the next one and apply again:

```bash
terraform init -backend-config=backend.hcl
terraform plan -var-file=dev.tfvars
terraform apply -var-file=dev.tfvars
```

| Add to `services` | Before applying | Check with a new test submission |
|---|---|---|
| `application` | | `POST <api_url>/applications` returns `201`; an item in `apply-relay-dev-applications`, the CV in the `cvs` bucket |
| `cv-parser` | `python ../../../scripts/build_lambda.py cv_parser` (bundles pypdf) | an item in `apply-relay-dev-parsed-cvs` |
| `matching` | | 3 jobs in `apply-relay-dev-jobs`, an item in `apply-relay-dev-matches` |
| `notification` | | strong matches email `recruiter_email` (after step 4) |
| `audit` | | one item per event in `apply-relay-dev-audit-log` |

A service's queue is created with the service, so nothing piles up in a queue no one reads; events published before their consumer exists are dropped, which is why each check needs a new submission (a new email or job ID). `cv-parser` requires `application`, since it reads CVs from that service's bucket. The dashboard is created once all five are deployed. With all five (or with `services` removed from `dev.tfvars`, as CI does), the plan says `Plan: 83 to add`.

**4. Confirm two emails.** AWS sends `alert_email` a "Subscription Confirmation" (for alarms) on the first apply, and an "Email Address Verification Request" (from SES, for recruiter emails) when `notification` is added. Click both links once.

## CI/CD with GitHub Actions

| Workflow | When | What |
|---|---|---|
| [`ci.yml`](../.github/workflows/ci.yml) | every pull request | tests for each service (Python 3.12), `terraform fmt` and `validate` for all three stacks, then a `terraform plan` for dev shown in the run summary |
| [`deploy.yml`](../.github/workflows/deploy.yml) | push to `main` | the same CI, then build, plan and apply to dev in the GitHub environment `dev` |

There are no AWS keys in GitHub. Jobs exchange GitHub's OIDC token for 1-hour credentials, and [`ci/`](ci/) defines who can get which:

| Role | Who can assume it | Can do |
|---|---|---|
| `apply-relay-github-plan` | pull requests and `main` of your repo | read-only (`ReadOnlyAccess`), minus CVs and application data; read/write the state lock |
| `apply-relay-github-deploy` | only jobs running in the GitHub environment `dev` | create and change `apply-relay-*` resources only; cannot touch the CI roles or delete the state bucket |

Every Lambda role CI creates must carry the `apply-relay-lambda-boundary` permissions boundary, so even a malicious change to a service's IAM policy in a pull request can't grant more than the services need.

Until the repository variables below exist, the AWS jobs are skipped and CI just runs the tests and checks.

**Set it up (once, after "Deploy it yourself" above, with all five services deployed):** CI doesn't set `services`, so its first deploy creates every service at once.

```bash
cd infra/ci
cp example.tfvars ci.tfvars                       # put your owner/repo in it
terraform init -backend-config=../envs/dev/backend.hcl
terraform apply -var-file=ci.tfvars               # 7 resources
```

If your account already has a GitHub OIDC provider, add `create_oidc_provider = false` to `ci.tfvars`.

Then in GitHub, under *Settings → Secrets and variables → Actions → Variables*, add:

| Variable | Value |
|---|---|
| `AWS_PLAN_ROLE_ARN` | `terraform output -raw plan_role_arn` |
| `AWS_DEPLOY_ROLE_ARN` | `terraform output -raw deploy_role_arn` |
| `LAMBDA_PERMISSIONS_BOUNDARY_ARN` | `terraform output -raw lambda_permissions_boundary_arn` |
| `TF_STATE_BUCKET` | the bucket from `infra/bootstrap` |
| `ALERT_EMAIL` | your email |
| `AWS_REGION` | optional, defaults to `eu-central-1` |

Finally create the environment `dev` under *Settings → Environments*. Adding yourself as a required reviewer there makes every deploy wait for your approval.

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
