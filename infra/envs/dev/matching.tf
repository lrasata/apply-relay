# Step 5: the matching service.
# SQS matching (CvParsed) -> Lambda -> job requirements (own table) -> DynamoDB -> SNS candidate-matched

locals {
  matching_service = "${local.name_prefix}-matching"

  # The job catalogue lives next to the service's code and is loaded into its own table.
  jobs = { for job in jsondecode(file("${path.root}/../../../services/matching/jobs.json")) : job.job_id => job }
}

# ---------------------------------------------------------------- data owned by the service

resource "aws_dynamodb_table" "matches" {
  name         = "${local.name_prefix}-matches"
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

resource "aws_dynamodb_table" "jobs" {
  name         = "${local.name_prefix}-jobs"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "job_id"

  attribute {
    name = "job_id"
    type = "S"
  }

  server_side_encryption {
    enabled = true
  }
}

resource "aws_dynamodb_table_item" "job" {
  for_each = local.jobs

  table_name = aws_dynamodb_table.jobs.name
  hash_key   = aws_dynamodb_table.jobs.hash_key
  item = jsonencode({
    job_id              = { S = each.value.job_id }
    title               = { S = each.value.title }
    location            = { S = each.value.location }
    recruiter_email     = { S = each.value.recruiter_email }
    required_skills     = { L = [for skill in each.value.required_skills : { S = skill }] }
    nice_to_have_skills = { L = [for skill in each.value.nice_to_have_skills : { S = skill }] }
  })
}

# ---------------------------------------------------------------- function

data "archive_file" "matching" {
  type        = "zip"
  source_dir  = "${path.root}/../../../services/matching/src"
  output_path = "${path.root}/build/matching.zip"
  excludes    = ["**/__pycache__/**"]
}

resource "aws_cloudwatch_log_group" "matching" {
  name              = "/aws/lambda/${local.matching_service}"
  retention_in_days = 14
}

resource "aws_lambda_function" "matching" {
  function_name    = local.matching_service
  role             = aws_iam_role.matching.arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = "matching_service.handler.handler"
  filename         = data.archive_file.matching.output_path
  source_code_hash = data.archive_file.matching.output_base64sha256
  memory_size      = 256
  timeout          = 30 # the queue's 180 s visibility timeout is 6x this

  environment {
    variables = {
      MATCHES_TABLE = aws_dynamodb_table.matches.name
      JOBS_TABLE    = aws_dynamodb_table.jobs.name
      TOPIC_ARN     = module.topic["candidate-matched"].arn
    }
  }

  tracing_config {
    mode = "Active"
  }

  depends_on = [aws_cloudwatch_log_group.matching]
}

resource "aws_lambda_event_source_mapping" "matching" {
  event_source_arn        = module.queue["matching"].queue_arn
  function_name           = aws_lambda_function.matching.arn
  batch_size              = 10
  function_response_types = ["ReportBatchItemFailures"]

  scaling_config {
    maximum_concurrency = 2
  }
}

# ---------------------------------------------------------------- permissions (least privilege)

resource "aws_iam_role" "matching" {
  name               = local.matching_service
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "matching" {
  statement {
    sid       = "ConsumeQueue"
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
    resources = [module.queue["matching"].queue_arn]
  }

  statement {
    sid       = "OwnMatchesTable"
    actions   = ["dynamodb:PutItem", "dynamodb:GetItem", "dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.matches.arn]
  }

  statement {
    sid       = "ReadJobs"
    actions   = ["dynamodb:GetItem"]
    resources = [aws_dynamodb_table.jobs.arn]
  }

  statement {
    sid       = "PublishCandidateMatched"
    actions   = ["sns:Publish"]
    resources = [module.topic["candidate-matched"].arn]
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
    resources = ["${aws_cloudwatch_log_group.matching.arn}:*"]
  }

  statement {
    sid       = "Tracing"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "matching" {
  name   = "matching-service"
  role   = aws_iam_role.matching.id
  policy = data.aws_iam_policy_document.matching.json
}
