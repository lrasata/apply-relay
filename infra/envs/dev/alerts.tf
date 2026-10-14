# Readable alert emails.
# CloudWatch alarms -> SNS alarms -> Lambda alerts (rewrites them) -> SNS ops-alerts -> your email
#
# CloudWatch's own alarm email is a fixed format: mostly metric settings, nothing about what
# to do. The alerts function rewrites each one as "what happened / what to do / links".
# It isn't one of var.services: alarms exist from the first apply, so it always does too.

locals {
  alerts_service = "${local.name_prefix}-alerts"
}

# Where every alarm publishes. Not KMS-encrypted, for the same reason as ops-alerts.
resource "aws_sns_topic" "alarms" {
  name = "${local.name_prefix}-alarms"
}

data "archive_file" "alerts" {
  type        = "zip"
  source_dir  = "${path.root}/../../../services/alerts/src"
  output_path = "${path.root}/build/alerts.zip"
  excludes    = ["**/__pycache__/**"]
}

resource "aws_cloudwatch_log_group" "alerts" {
  name              = "/aws/lambda/${local.alerts_service}"
  retention_in_days = 14
}

resource "aws_lambda_function" "alerts" {
  function_name    = local.alerts_service
  role             = aws_iam_role.alerts.arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = "alerts_service.handler.handler"
  filename         = data.archive_file.alerts.output_path
  source_code_hash = data.archive_file.alerts.output_base64sha256
  memory_size      = 128
  timeout          = 10

  environment {
    variables = {
      OUTPUT_TOPIC_ARN = aws_sns_topic.ops_alerts.arn
      NAME_PREFIX      = local.name_prefix
      DASHBOARD_NAME   = try(aws_cloudwatch_dashboard.pipeline[0].dashboard_name, "") # once all services are deployed
    }
  }

  depends_on = [aws_cloudwatch_log_group.alerts]
}

resource "aws_lambda_permission" "alerts_from_sns" {
  statement_id  = "AllowAlarmsTopic"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.alerts.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.alarms.arn
}

resource "aws_sns_topic_subscription" "alerts" {
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.alerts.arn

  depends_on = [aws_lambda_permission.alerts_from_sns]
}

# If the formatter itself breaks, say so in CloudWatch's own format, straight to your email.
resource "aws_cloudwatch_metric_alarm" "alerts_errors" {
  alarm_name          = "${local.alerts_service}-errors"
  alarm_description   = "The alerts function failed, so other alarm emails may be missing. Check the CloudWatch alarms console and /aws/lambda/${local.alerts_service}."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.alerts.function_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.ops_alerts.arn]
}

# ---------------------------------------------------------------- permissions (least privilege)

resource "aws_iam_role" "alerts" {
  name                 = local.alerts_service
  assume_role_policy   = data.aws_iam_policy_document.lambda_assume.json
  permissions_boundary = var.lambda_permissions_boundary
}

data "aws_iam_policy_document" "alerts" {
  statement {
    sid       = "PublishOpsAlerts"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.ops_alerts.arn]
  }

  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.alerts.arn}:*"]
  }
}

resource "aws_iam_role_policy" "alerts" {
  name   = "alerts-service"
  role   = aws_iam_role.alerts.id
  policy = data.aws_iam_policy_document.alerts.json
}
