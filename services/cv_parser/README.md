# CV parser service

| | |
|---|---|
| Listens to | `ApplicationSubmitted` (SQS queue `cv-parser`) |
| Publishes | `CvParsed` |
| Owns | `parsed-cvs` DynamoDB table |

## What it does

1. Skip the message if its `event_id` was already processed.
2. Read the CV from S3 and extract skills, years of experience and spoken languages (keyword-based in v1).
3. Store the result and publish `CvParsed`.

_Code and tests arrive in the build step for this service._
