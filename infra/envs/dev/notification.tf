# Step 7: the notification service.
# SQS notification (CandidateMatched, strong matches only) -> Lambda -> SES email to the recruiter

locals {
  notification_service = "${local.name_prefix}-notification"
  recruiter_email      = coalesce(var.recruiter_email, var.alert_email)
}

resource "aws_dynamodb_table" "notifications" {
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
  email = local.recruiter_email
}

# ---------------------------------------------------------------- function

data "archive_file" "notification" {
  type        = "zip"
  source_dir  = "${path.root}/../../../services/notification/src"
  output_path = "${path.root}/build/notification.zip"
  excludes    = ["**/__pycache__/**"]
}

resource "aws_cloudwatch_log_group" "notification" {
  name              = "/aws/lambda/${local.notification_service}"
  retention_in_days = 14
}

resource "aws_lambda_function" "notification" {
  function_name    = local.notification_service
  role             = aws_iam_role.notification.arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = "notification_service.handler.handler"
  filename         = data.archive_file.notification.output_path
  source_code_hash = data.archive_file.notification.output_base64sha256
  memory_size      = 128
  timeout          = 30

  environment {
    variables = {
      TABLE_NAME      = aws_dynamodb_table.notifications.name
      SENDER_EMAIL    = aws_ses_email_identity.recruiter.email
      RECRUITER_EMAIL = local.recruiter_email
    }
  }

  tracing_config {
    mode = "Active"
  }

  depends_on = [aws_cloudwatch_log_group.notification]
}

resource "aws_lambda_event_source_mapping" "notification" {
  event_source_arn        = module.queue["notification"].queue_arn
  function_name           = aws_lambda_function.notification.arn
  batch_size              = 10
  function_response_types = ["ReportBatchItemFailures"]

  scaling_config {
    maximum_concurrency = 2
  }
}

# ---------------------------------------------------------------- permissions (least privilege)

resource "aws_iam_role" "notification" {
  name                 = local.notification_service
  assume_role_policy   = data.aws_iam_policy_document.lambda_assume.json
  permissions_boundary = var.lambda_permissions_boundary
}

data "aws_iam_policy_document" "notification" {
  statement {
    sid       = "ConsumeQueue"
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
    resources = [module.queue["notification"].queue_arn]
  }

  statement {
    sid       = "OwnTable"
    actions   = ["dynamodb:PutItem", "dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.notifications.arn]
  }

  statement {
    sid       = "SendFromVerifiedIdentity"
    actions   = ["ses:SendEmail"]
    resources = [aws_ses_email_identity.recruiter.arn]
  }

  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.notification.arn}:*"]
  }

  statement {
    sid       = "Tracing"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "notification" {
  name   = "notification-service"
  role   = aws_iam_role.notification.id
  policy = data.aws_iam_policy_document.notification.json
}
