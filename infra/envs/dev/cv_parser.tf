# Step 4: the CV parser service.
# SQS cv-parser (ApplicationSubmitted) -> Lambda -> reads CV from S3 -> DynamoDB -> SNS cv-parsed
#
# The package bundles pypdf, so build it before plan/apply:
#   python scripts/build_lambda.py cv_parser

locals {
  cv_parser_service = "${local.name_prefix}-cv-parser"
}

resource "aws_dynamodb_table" "parsed_cvs" {
  name         = "${local.name_prefix}-parsed-cvs"
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

# ---------------------------------------------------------------- function

data "archive_file" "cv_parser" {
  type        = "zip"
  source_dir  = "${path.root}/../../../services/cv_parser/build/package"
  output_path = "${path.root}/build/cv_parser.zip"
  excludes    = ["**/__pycache__/**"]
}

resource "aws_cloudwatch_log_group" "cv_parser" {
  name              = "/aws/lambda/${local.cv_parser_service}"
  retention_in_days = 14
}

resource "aws_lambda_function" "cv_parser" {
  function_name    = local.cv_parser_service
  role             = aws_iam_role.cv_parser.arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = "cv_parser_service.handler.handler"
  filename         = data.archive_file.cv_parser.output_path
  source_code_hash = data.archive_file.cv_parser.output_base64sha256
  memory_size      = 512 # PDF parsing is CPU-bound; more memory also means more CPU
  timeout          = 30  # the queue's 180 s visibility timeout is 6x this, as AWS recommends

  environment {
    variables = {
      TABLE_NAME = aws_dynamodb_table.parsed_cvs.name
      TOPIC_ARN  = module.topic["cv-parsed"].arn
    }
  }

  tracing_config {
    mode = "Active"
  }

  depends_on = [aws_cloudwatch_log_group.cv_parser]
}

resource "aws_lambda_event_source_mapping" "cv_parser" {
  event_source_arn        = module.queue["cv-parser"].queue_arn
  function_name           = aws_lambda_function.cv_parser.arn
  batch_size              = 10
  function_response_types = ["ReportBatchItemFailures"] # retry only the messages that failed

  scaling_config {
    maximum_concurrency = 2 # the minimum; plenty for a portfolio and caps the blast radius
  }
}

# ---------------------------------------------------------------- permissions (least privilege)

resource "aws_iam_role" "cv_parser" {
  name                 = local.cv_parser_service
  assume_role_policy   = data.aws_iam_policy_document.lambda_assume.json
  permissions_boundary = var.lambda_permissions_boundary
}

data "aws_iam_policy_document" "cv_parser" {
  statement {
    sid       = "ConsumeQueue"
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
    resources = [module.queue["cv-parser"].queue_arn]
  }

  statement {
    sid       = "OwnTable"
    actions   = ["dynamodb:PutItem", "dynamodb:GetItem", "dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.parsed_cvs.arn]
  }

  statement {
    sid       = "ReadCvs"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.cvs.arn}/applications/*"]
  }

  statement {
    sid       = "PublishCvParsed"
    actions   = ["sns:Publish"]
    resources = [module.topic["cv-parsed"].arn]
  }

  statement {
    sid       = "UseSnsKey"
    actions   = ["kms:GenerateDataKey*", "kms:Decrypt"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["sns.${var.region}.amazonaws.com"]
    }
  }

  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.cv_parser.arn}:*"]
  }

  statement {
    sid       = "Tracing"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "cv_parser" {
  name   = "cv-parser-service"
  role   = aws_iam_role.cv_parser.id
  policy = data.aws_iam_policy_document.cv_parser.json
}
