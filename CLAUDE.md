# Apply Relay

Event-driven job application pipeline on AWS: five services that talk only through events (SNS topic per event type -> SQS queue per consumer -> Lambda), plus an alerts service for CloudWatch alarms. Python 3.12 on Lambda, Terraform for all infrastructure, GitHub Actions with OIDC for CI/CD. Start with `README.md` (architecture, events, reliability) and `infra/README.md` (what each step deploys, how to deploy).

## Commands

Tests run one service at a time: each service has its own `pyproject.toml` that puts its `src/` on the path, so `pytest services` from the root fails.

```bash
pip install -r services/<service>/requirements-dev.txt
pytest services/<service>        # application, cv_parser, matching, notification, audit, alerts
```

Terraform:

```bash
python scripts/build_lambda.py cv_parser   # before any plan/apply: it bundles pypdf; the others are zipped from src/
terraform fmt -recursive infra             # CI fails on unformatted files
terraform -chdir=infra/envs/dev validate
terraform -chdir=infra/envs/dev plan -var-file=dev.tfvars
```

The user works in PowerShell: quote arguments that contain a dot after `=`, e.g. `terraform plan "-var-file=dev.tfvars"`, or PowerShell splits them.

## How changes reach AWS

Through GitHub, not from a laptop. A push to `main` runs `deploy.yml` (tests, then plan and apply in the GitHub environment `dev`, which needs the user's approval). Pull requests run `ci.yml` with a read-only plan. `destroy.yml` is manual. Local `terraform plan` is fine for checking a change; never run `apply` or `destroy`, and never `set-alarm-state`, purge queues or delete AWS resources without the user asking.

Stacks: `infra/bootstrap` (state bucket + lock table) and `infra/ci` (OIDC roles, permissions boundary) are applied by hand, once. `infra/envs/dev` is what CI deploys. `var.services` lists the deployed services; in CI it's unset, which means all of them.

## Conventions

Read an existing service before writing a new one; they all follow the same shape.

- **Events**: every message is the envelope in `docs/events/envelope.schema.json`. `correlation_id` is the application ID and must be in every log line. Payload schemas live in `docs/events/`.
- **Schema changes**: within a version, only add optional fields, and make consumers tolerate their absence. Anything else is a new `event_version`. Consumers reject versions they don't know.
- **Data ownership**: a service reads and writes only its own tables. What it needs from another service arrives in an event (see `job_title` in `CandidateMatched`).
- **Idempotency** (SQS is at least once): check the own table for an already-published result, store the result with `event_published = false` (conditional write), publish, then set the flag. A retry reuses the stored `event_id`.
- **Batches**: handlers return `batchItemFailures` so only failing messages are retried; after 3 failures a message goes to the service's DLQ and an alarm fires.
- **Code split**: AWS calls in `handler.py`, pure logic in its own module (`scoring.py`, `extraction.py`, `message.py`) so it tests without mocks. `boto3` clients are created once, in an `lru_cache`d function.
- **Logs**: one JSON object per line via the service's `_log` helper, with `message`, `service` and `correlation_id`.
- **Tests**: pytest + moto. Contract tests validate published events against the JSON Schemas.
- **IAM**: one role per Lambda, least privilege, scoped to exact ARNs, always with `permissions_boundary = var.lambda_permissions_boundary`. A new AWS action a Lambda needs must also be allowed by the boundary in `infra/ci/main.tf`, and new resource types by the deploy role there.
- **Naming**: every AWS resource is `apply-relay-<env>-...` (`local.name_prefix`); the CI roles can only manage `apply-relay-*`.
- **Comments** explain *why*, briefly, as in the existing code. READMEs are updated in the same change as the behaviour they describe.

## Don'ts

- Don't commit or print `*.tfvars` (except `example.tfvars`), `backend.hcl` or `.env`: they hold account-specific values.
- Don't put candidate names or emails in logs, events after `ApplicationSubmitted`, or emails.
- Don't call one service from another (HTTP or Lambda invoke); publish an event.
- Don't add a service without the checklist in the `new-service` skill (`/new-service`): the wiring spans Terraform, CI and docs.
