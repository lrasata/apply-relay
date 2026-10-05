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

**Expected cost:** effectively $0 while idle. SNS and SQS are free up to 1M requests a month, the first 10 CloudWatch alarms and the first 2 budgets are free, and the state bucket holds a few KB.

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

**3. Plan, check, apply.**

```bash
terraform init -backend-config=backend.hcl
terraform plan -var-file=dev.tfvars    # should say: Plan: 32 to add
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
