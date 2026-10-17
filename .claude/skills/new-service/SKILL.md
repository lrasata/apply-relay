---
name: new-service
description: Add a new event-consuming microservice to apply-relay (code, tests, Terraform, CI and docs), following the pattern of the existing services. Use when asked to add, scaffold or create a service, consumer or Lambda that reacts to an event.
---

# Add a service to apply-relay

A service is wired into many places. Work through every step; a missed one usually shows up only in CI or in AWS.

## 0. Pin down the design first

Agree these with the user before writing code:

- **Name**: `kebab-case` in Terraform and AWS (`cv-parser`), `snake_case` for the folder and package (`services/cv_parser`, `cv_parser_service`).
- **Consumes**: which event types and versions (the keys of `local.events` in `infra/envs/dev/main.tf`).
- **Publishes**: nothing, or a new event (then do step 3).
- **Owns**: its tables or buckets. It may not read another service's data; anything it needs must be in the events it consumes.
- **Personal data**: does it store candidate names or emails? (Steps 4 and 6.)

Pick the closest existing service as the template and read it fully:

| New service... | Copy from |
|---|---|
| consumes, stores, publishes | `matching` (cleanest example of the store -> publish -> flag pattern) |
| consumes, calls an external API, publishes nothing | `notification` |
| consumes many event types, append-only | `audit` |
| needs third-party packages | `cv_parser` (has `requirements.txt` and a build step) |

## 1. Code: `services/<name>/`

```
README.md                 # same sections as the others: Listens to / Publishes / Owns, What it does, Code, Run the tests
pyproject.toml            # copy as is: pythonpath = ["src"], testpaths = ["tests"]
requirements-dev.txt      # boto3, moto[<services used>], pytest (+ jsonschema[format] if it publishes)
requirements.txt          # only if it needs packages beyond boto3
src/<name>_service/__init__.py
src/<name>_service/handler.py
src/<name>_service/<logic>.py   # pure logic, no AWS calls
tests/conftest.py               # moto fixture + an envelope factory for the consumed event
tests/test_<name>.py
```

The handler keeps the shared shape: `CONSUMES` check raising `UnsupportedEvent`, `lru_cache`d `_aws()`, JSON `_log(level, message, **fields)` with `correlation_id`, idempotency through its own table (or a conditional write), per-message `batchItemFailures`.

Tests to include: happy path, duplicate delivery is a no-op, unknown event type/version fails only that message, a failure of the external call is retried, and (if it publishes) the event validates against its schema.

Run: `pip install -r services/<name>/requirements-dev.txt` and `pytest services/<name>`.

## 2. Terraform: `infra/envs/dev/`

1. **`main.tf`**: add the service to `local.consumers` (the events it listens to), to `local.all_services` in pipeline order, and to `local.consumer_filters` if it should only get some messages (the publisher must then set that message attribute).
2. **`variables.tf`**: add it to the `services` default, description and both lists in the validation. Add a dependency validation if it needs another service deployed first.
3. **`<name>.tf`**: copy the template service's file. Gate everything with `count = local.deploy.<name> ? 1 : 0`. Keep the 14-day log group, `timeout` of 30 s (the queue's visibility timeout is 6x that), `maximum_concurrency = 2`, `ReportBatchItemFailures`, X-Ray `Active`, and a least-privilege role with `permissions_boundary = var.lambda_permissions_boundary`.
4. **`observability.tf`**: add the function to `local.functions` and the log group to `local.all_log_groups`.
5. **`infra/envs/dev/example.tfvars`**: add it to the deployment order comment.

The queue, DLQ and both alarms come from `modules/consumer_queue` automatically. The alerts service names the service from the queue name, so keep queue and function both `apply-relay-<env>-<name>`.

## 3. A new event (only if it publishes one)

- `docs/events/<event-name>.schema.json` for the payload, plus a row in the Events table of `README.md`.
- `local.events` in `main.tf` (`<event-name> = "<EventType>"`) to create its topic.
- Publish with `event_type` and `event_version` message attributes, as the others do.

## 4. CI and permissions: `.github/workflows/`, `infra/ci/main.tf`

- `ci.yml`: add the folder name to the test `matrix.service`.
- If it has a `requirements.txt`: add `python scripts/build_lambda.py <name>` next to `cv_parser` in `ci.yml` (plan job), `deploy.yml` and `destroy.yml`.
- New AWS actions for the Lambda: they must be in the permissions boundary (`lambda_boundary` in `infra/ci/main.tf`). New resource types Terraform creates: the deploy role must allow them. Both need `terraform apply` in `infra/ci` by the user before CI can deploy.
- Personal data in a new table: add it to the plan role's `NoApplicationData` deny.

## 5. Docs

- `README.md`: Mermaid diagram, Services table, and the "Why SNS + SQS fan-out" text if the fan-out changes.
- `infra/README.md`: the wiring map, the deploy-one-at-a-time table, and the `Plan: N to add` count.
- `CLAUDE.md`: the service list in the test command.

## 6. Verify

```bash
pytest services/<name>
terraform fmt -recursive infra
terraform -chdir=infra/envs/dev validate
terraform -chdir=infra/envs/dev plan -var-file=dev.tfvars
```

The plan should only add the new service's resources (queue, DLQ, 2 alarms, subscriptions, Lambda, log group, event source mapping, role, policy and its own data). Then hand over: the user commits, pushes and approves the deploy. Never apply from here.
