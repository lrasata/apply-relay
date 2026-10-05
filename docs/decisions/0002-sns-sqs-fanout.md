# 0002: SNS + SQS fan-out for events

**Status:** accepted · 2026-10-05

## Context

One event must reach several independent consumers (for example, `ApplicationSubmitted` goes to the CV parser and to the audit service). Each consumer needs its own retries and failure handling, so a slow consumer never delays another.

## Options

* **SNS topic per event type, one SQS queue per consumer (fan-out).** The publisher doesn't know who listens; each consumer has its own buffer, retry policy and dead-letter queue.
* **EventBridge.** Content-based routing rules and a schema registry; still needs SQS behind it for buffering, and is less commonly asked about in AWS interviews.
* **RabbitMQ (Amazon MQ).** Flexible exchanges and routing, but a broker instance runs (and bills) all the time and has to be sized and patched.
* **Lambda calling Lambda directly.** Simple, but tightly coupled: if one service is down, the caller fails too.

## Decision

SNS + SQS fan-out. It's fully managed, costs nothing when idle, and gives each consumer isolation through its own queue and DLQ.

## Consequences

* Delivery is *at least once*, so every consumer must be idempotent (see `event_id` in the envelope).
* Ordering is not guaranteed. The pipeline is a chain of distinct events per application, so ordering across applications doesn't matter.
* RabbitMQ could be added later for one consumer to compare the two approaches.
