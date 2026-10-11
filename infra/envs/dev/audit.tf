# Step 7: the audit service.
# SQS audit (every event) -> Lambda -> DynamoDB audit-log, one item per event, ordered per application

locals {
  audit_service = "${local.name_prefix}-audit"
  audit_count   = local.deploy.audit ? 1 : 0
}

resource "aws_dynamodb_table" "audit_log" {
  count = local.audit_count

  name         = "${local.name_prefix}-audit-log"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "correlation_id"
  range_key    = "sk" # "<occurred_at>#<event_id>"

  attribute {
    name = "correlation_id"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = var.audit_retention_days > 0
  }

  server_side_encryption {
    enabled = true
  }
}

# ---------------------------------------------------------------- function

data "archive_file" "audit" {
  count = local.audit_count

  type        = "zip"
  source_dir  = "${path.root}/../../../services/audit/src"
  output_path = "${path.root}/build/audit.zip"
  excludes    = ["**/__pycache__/**"]
}

resource "aws_cloudwatch_log_group" "audit" {
  count = local.audit_count

  name              = "/aws/lambda/${local.audit_service}"
  retention_in_days = 14
}

resource "aws_lambda_function" "audit" {
  count = local.audit_count

  function_name    = local.audit_service
  role             = aws_iam_role.audit[0].arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = "audit_service.handler.handler"
  filename         = data.archive_file.audit[0].output_path
  source_code_hash = data.archive_file.audit[0].output_base64sha256
  memory_size      = 128
  timeout          = 30

  environment {
    variables = {
      TABLE_NAME     = aws_dynamodb_table.audit_log[0].name
      RETENTION_DAYS = tostring(var.audit_retention_days)
    }
  }

  tracing_config {
    mode = "Active"
  }

  depends_on = [aws_cloudwatch_log_group.audit]
}

resource "aws_lambda_event_source_mapping" "audit" {
  count = local.audit_count

  event_source_arn        = module.queue["audit"].queue_arn
  function_name           = aws_lambda_function.audit[0].arn
  batch_size              = 10
  function_response_types = ["ReportBatchItemFailures"]

  scaling_config {
    maximum_concurrency = 2
  }
}

# ---------------------------------------------------------------- permissions (least privilege)

resource "aws_iam_role" "audit" {
  count = local.audit_count

  name                 = local.audit_service
  assume_role_policy   = data.aws_iam_policy_document.lambda_assume.json
  permissions_boundary = var.lambda_permissions_boundary
}

data "aws_iam_policy_document" "audit" {
  count = local.audit_count

  statement {
    sid       = "ConsumeQueue"
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
    resources = [module.queue["audit"].queue_arn]
  }

  statement {
    sid       = "AppendOnly" # can add events, never change or delete them
    actions   = ["dynamodb:PutItem"]
    resources = [aws_dynamodb_table.audit_log[0].arn]
  }

  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.audit[0].arn}:*"]
  }

  statement {
    sid       = "Tracing"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "audit" {
  count = local.audit_count

  name   = "audit-service"
  role   = aws_iam_role.audit[0].id
  policy = data.aws_iam_policy_document.audit[0].json
}
