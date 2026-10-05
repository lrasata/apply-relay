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

_Code and tests arrive in the build step for this service._
