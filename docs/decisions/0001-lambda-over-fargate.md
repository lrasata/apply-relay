# 0001: Run services on AWS Lambda instead of ECS Fargate

**Status:** accepted · 2026-10-05

## Context

Each of the five services does a short piece of work per message (parse a CV, compute a score, send an email). Traffic is bursty: nothing for hours, then many applications when a job is published. This is a portfolio project running in a personal AWS account, so idle cost matters.

## Options

| | Lambda | ECS Fargate |
|---|---|---|
| Idle cost | None (pay per invocation, mostly free tier) | Tasks run 24/7, plus a load balancer |
| Scaling | Automatic per message batch | Needs autoscaling policies |
| SQS integration | Native event source mapping, partial batch failures | Service must poll the queue itself |
| Limits | 15 min per run, cold starts | No time limit, warm containers |

## Decision

Lambda. Every task finishes in seconds, the SQS integration handles polling, batching and retries for us, and the project costs close to nothing when idle.

## Consequences

* Cold starts add latency to the first message after an idle period. That's acceptable because nothing here is user-facing except the submit API.
* If a step ever needs more than 15 minutes (e.g. heavy ML parsing), that one service can move to Fargate without changing the others, since they only share events.
