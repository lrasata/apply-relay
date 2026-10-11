# Step 3: the application service.
# POST /applications (API Gateway) -> Lambda -> DynamoDB + S3 -> SNS application-submitted

locals {
  application_service = "${local.name_prefix}-application"
  application_count   = local.deploy.application ? 1 : 0
}

# ---------------------------------------------------------------- data owned by the service

resource "aws_dynamodb_table" "applications" {
  count = local.application_count

  name         = "${local.name_prefix}-applications"
  billing_mode = "PAY_PER_REQUEST" # no idle cost
  hash_key     = "application_id"

  attribute {
    name = "application_id"
    type = "S"
  }

  server_side_encryption {
    enabled = true
  }
}

# Bucket names are global, so the account ID keeps this one unique.
resource "aws_s3_bucket" "cvs" {
  count = local.application_count

  bucket        = "${local.name_prefix}-cvs-${data.aws_caller_identity.current.account_id}"
  force_destroy = true # dev only: lets `terraform destroy` remove uploaded CVs
}

resource "aws_s3_bucket_public_access_block" "cvs" {
  count = local.application_count

  bucket                  = aws_s3_bucket.cvs[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "cvs" {
  count = local.application_count

  bucket = aws_s3_bucket.cvs[0].id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# CVs are personal data: don't keep them longer than needed.
resource "aws_s3_bucket_lifecycle_configuration" "cvs" {
  count = local.application_count

  bucket = aws_s3_bucket.cvs[0].id

  rule {
    id     = "expire-cvs"
    status = "Enabled"

    filter {
      prefix = "applications/"
    }

    expiration {
      days = var.cv_retention_days
    }
  }
}

# ---------------------------------------------------------------- function

data "archive_file" "application" {
  count = local.application_count

  type        = "zip"
  source_dir  = "${path.root}/../../../services/application/src"
  output_path = "${path.root}/build/application.zip"
  excludes    = ["**/__pycache__/**"]
}

resource "aws_cloudwatch_log_group" "application" {
  count = local.application_count

  name              = "/aws/lambda/${local.application_service}"
  retention_in_days = 14
}

resource "aws_lambda_function" "application" {
  count = local.application_count

  function_name    = local.application_service
  role             = aws_iam_role.application[0].arn
  runtime          = "python3.12"
  architectures    = ["arm64"] # cheaper per GB-second than x86
  handler          = "application_service.handler.handler"
  filename         = data.archive_file.application[0].output_path
  source_code_hash = data.archive_file.application[0].output_base64sha256
  memory_size      = 256
  timeout          = 10

  environment {
    variables = {
      TABLE_NAME  = aws_dynamodb_table.applications[0].name
      BUCKET_NAME = aws_s3_bucket.cvs[0].bucket
      TOPIC_ARN   = module.topic["application-submitted"].arn
    }
  }

  tracing_config {
    mode = "Active" # X-Ray
  }

  depends_on = [aws_cloudwatch_log_group.application]
}

# ---------------------------------------------------------------- permissions (least privilege)

resource "aws_iam_role" "application" {
  count = local.application_count

  name                 = local.application_service
  assume_role_policy   = data.aws_iam_policy_document.lambda_assume.json
  permissions_boundary = var.lambda_permissions_boundary
}

data "aws_iam_policy_document" "application" {
  count = local.application_count

  statement {
    sid       = "OwnTable"
    actions   = ["dynamodb:PutItem", "dynamodb:GetItem", "dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.applications[0].arn]
  }

  statement {
    sid       = "StoreCvs"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.cvs[0].arn}/applications/*"]
  }

  statement {
    sid       = "PublishApplicationSubmitted"
    actions   = ["sns:Publish"]
    resources = [module.topic["application-submitted"].arn]
  }

  # The topic is encrypted with the AWS-managed SNS key; publishing needs to use it via SNS.
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
    resources = ["${aws_cloudwatch_log_group.application[0].arn}:*"]
  }

  statement {
    sid       = "Tracing"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "application" {
  count = local.application_count

  name   = "application-service"
  role   = aws_iam_role.application[0].id
  policy = data.aws_iam_policy_document.application[0].json
}

# ---------------------------------------------------------------- HTTP API

resource "aws_apigatewayv2_api" "public" {
  count = local.application_count

  name          = "${local.name_prefix}-api"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "application" {
  count = local.application_count

  api_id                 = aws_apigatewayv2_api.public[0].id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.application[0].invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "submit_application" {
  count = local.application_count

  api_id    = aws_apigatewayv2_api.public[0].id
  route_key = "POST /applications"
  target    = "integrations/${aws_apigatewayv2_integration.application[0].id}"
}

resource "aws_apigatewayv2_stage" "default" {
  count = local.application_count

  api_id      = aws_apigatewayv2_api.public[0].id
  name        = "$default"
  auto_deploy = true

  # A public endpoint on a personal account: cap traffic so nobody can run up a bill.
  default_route_settings {
    throttling_burst_limit = var.api_burst_limit
    throttling_rate_limit  = var.api_rate_limit
  }
}

resource "aws_lambda_permission" "api" {
  count = local.application_count

  statement_id  = "AllowApiGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.application[0].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.public[0].execution_arn}/*/*/applications"
}
