# Step 7: the notification service.
# SQS notification (CandidateMatched, strong matches only) -> Lambda -> SES email to the recruiter

locals {
  notification_service = "${local.name_prefix}-notification"
  notification_count   = local.deploy.notification ? 1 : 0
  recruiter_email      = coalesce(var.recruiter_email, var.alert_email)
}

resource "aws_dynamodb_table" "notifications" {
  count = local.notification_count

  name         = "${local.name_prefix}-notifications"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "application_id"

  attribute {
    name = "application_id"
    type = "S"
  }

  server_side_encryption {
    enabled = true
  }
}

# A new SES account is in the sandbox: it can only send to verified addresses, which is
# exactly what a portfolio needs. AWS emails a verification link on apply; click it once.
resource "aws_ses_email_identity" "recruiter" {
  count = local.notification_count

  email = local.recruiter_email
}

# ---------------------------------------------------------------- function

data "archive_file" "notification" {
  count = local.notification_count

  type        = "zip"
  source_dir  = "${path.root}/../../../services/notification/src"
  output_path = "${path.root}/build/notification.zip"
  excludes    = ["**/__pycache__/**"]
}

resource "aws_cloudwatch_log_group" "notification" {
  count = local.notification_count

  name              = "/aws/lambda/${local.notification_service}"
  retention_in_days = 14
}

resource "aws_lambda_function" "notification" {
  count = local.notification_count

  function_name    = local.notification_service
  role             = aws_iam_role.notification[0].arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = "notification_service.handler.handler"
  filename         = data.archive_file.notification[0].output_path
  source_code_hash = data.archive_file.notification[0].output_base64sha256
  memory_size      = 128
  timeout          = 30

  environment {
    variables = {
      TABLE_NAME      = aws_dynamodb_table.notifications[0].name
      SENDER_EMAIL    = aws_ses_email_identity.recruiter[0].email
      RECRUITER_EMAIL = local.recruiter_email
      # Only to build console links in the email; the function has no access to either.
      CVS_BUCKET         = try(aws_s3_bucket.cvs[0].bucket, "")
      APPLICATIONS_TABLE = try(aws_dynamodb_table.applications[0].name, "")
    }
  }

  tracing_config {
    mode = "Active"
  }

  depends_on = [aws_cloudwatch_log_group.notification]
}

resource "aws_lambda_event_source_mapping" "notification" {
  count = local.notification_count

  event_source_arn        = module.queue["notification"].queue_arn
  function_name           = aws_lambda_function.notification[0].arn
  batch_size              = 10
  function_response_types = ["ReportBatchItemFailures"]

  scaling_config {
    maximum_concurrency = 2
  }
}

# ---------------------------------------------------------------- permissions (least privilege)

resource "aws_iam_role" "notification" {
  count = local.notification_count

  name                 = local.notification_service
  assume_role_policy   = data.aws_iam_policy_document.lambda_assume.json
  permissions_boundary = var.lambda_permissions_boundary
}

data "aws_iam_policy_document" "notification" {
  count = local.notification_count

  statement {
    sid       = "ConsumeQueue"
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
    resources = [module.queue["notification"].queue_arn]
  }

  statement {
    sid       = "OwnTable"
    actions   = ["dynamodb:PutItem", "dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.notifications[0].arn]
  }

  statement {
    sid       = "SendFromVerifiedIdentity"
    actions   = ["ses:SendEmail"]
    resources = [aws_ses_email_identity.recruiter[0].arn]
  }

  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.notification[0].arn}:*"]
  }

  statement {
    sid       = "Tracing"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "notification" {
  count = local.notification_count

  name   = "notification-service"
  role   = aws_iam_role.notification[0].id
  policy = data.aws_iam_policy_document.notification[0].json
}
