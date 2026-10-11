# Step 7: alarms, dashboard and saved log queries.
#
# Alarms (all to the ops-alerts topic, i.e. your email), 9 in total so they stay in the free tier:
#   4x DLQ not empty         a message failed 3 times          (modules/consumer_queue)
#   4x queue backlog         messages waiting > 15 minutes     (modules/consumer_queue)
#   1x API 5xx               the public API is failing         (below)
#
# Tracing: every Lambda and SNS topic has X-Ray active tracing, so one application shows up
# as one connected trace: API -> application -> SNS -> SQS -> cv-parser -> ... -> notification.

locals {
  functions = {
    application  = one(aws_lambda_function.application[*].function_name)
    cv-parser    = one(aws_lambda_function.cv_parser[*].function_name)
    matching     = one(aws_lambda_function.matching[*].function_name)
    notification = one(aws_lambda_function.notification[*].function_name)
    audit        = one(aws_lambda_function.audit[*].function_name)
  }
}

resource "aws_cloudwatch_metric_alarm" "api_5xx" {
  count = local.application_count

  alarm_name          = "${local.name_prefix}-api-5xx"
  alarm_description   = "POST /applications returned server errors."
  namespace           = "AWS/ApiGateway"
  metric_name         = "5xx"
  dimensions          = { ApiId = aws_apigatewayv2_api.public[0].id, Stage = aws_apigatewayv2_stage.default[0].name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.ops_alerts.arn]
  ok_actions          = [aws_sns_topic.ops_alerts.arn]
}

# ---------------------------------------------------------------- dashboard

# Created once every service is deployed, so no widget points at a missing function.
resource "aws_cloudwatch_dashboard" "pipeline" {
  count = alltrue(values(local.deploy)) ? 1 : 0

  dashboard_name = "${local.name_prefix}-pipeline"

  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric", x = 0, y = 0, width = 12, height = 6
        properties = {
          title  = "API: requests and errors"
          region = var.region
          stat   = "Sum"
          period = 300
          metrics = [
            ["AWS/ApiGateway", "Count", "ApiId", aws_apigatewayv2_api.public[0].id, "Stage", "$default"],
            [".", "4xx", ".", ".", ".", "."],
            [".", "5xx", ".", ".", ".", "."],
          ]
        }
      },
      {
        type = "metric", x = 12, y = 0, width = 12, height = 6
        properties = {
          title   = "Events published"
          region  = var.region
          stat    = "Sum"
          period  = 300
          metrics = [for name, topic in module.topic : ["AWS/SNS", "NumberOfMessagesPublished", "TopicName", topic.name, { label = name }]]
        }
      },
      {
        type = "metric", x = 0, y = 6, width = 12, height = 6
        properties = {
          title   = "Lambda errors"
          region  = var.region
          stat    = "Sum"
          period  = 300
          metrics = [for name, fn in local.functions : ["AWS/Lambda", "Errors", "FunctionName", fn, { label = name }]]
        }
      },
      {
        type = "metric", x = 12, y = 6, width = 12, height = 6
        properties = {
          title   = "Lambda duration (p95, ms)"
          region  = var.region
          stat    = "p95"
          period  = 300
          metrics = [for name, fn in local.functions : ["AWS/Lambda", "Duration", "FunctionName", fn, { label = name }]]
        }
      },
      {
        type = "metric", x = 0, y = 12, width = 12, height = 6
        properties = {
          title   = "Queue backlog (messages waiting)"
          region  = var.region
          stat    = "Maximum"
          period  = 300
          metrics = [for name, q in module.queue : ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", q.queue_name, { label = name }]]
        }
      },
      {
        type = "metric", x = 12, y = 12, width = 12, height = 6
        properties = {
          title   = "Dead-letter queues (should be 0)"
          region  = var.region
          stat    = "Maximum"
          period  = 300
          metrics = [for name, q in module.queue : ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", q.dlq_name, { label = name }]]
        }
      },
    ]
  })
}

# ---------------------------------------------------------------- saved Logs Insights queries

locals {
  all_log_groups = concat(
    aws_cloudwatch_log_group.application[*].name,
    aws_cloudwatch_log_group.cv_parser[*].name,
    aws_cloudwatch_log_group.matching[*].name,
    aws_cloudwatch_log_group.notification[*].name,
    aws_cloudwatch_log_group.audit[*].name,
  )
}

# Every service logs JSON with correlation_id = the application ID.
resource "aws_cloudwatch_query_definition" "trace_application" {
  count = length(local.all_log_groups) > 0 ? 1 : 0

  name            = "${local.name_prefix}/trace-one-application"
  log_group_names = local.all_log_groups
  query_string    = <<-EOT
    fields @timestamp, service, message, @log
    | filter correlation_id = "app_REPLACE_ME"
    | sort @timestamp asc
  EOT
}

resource "aws_cloudwatch_query_definition" "errors" {
  count = length(local.all_log_groups) > 0 ? 1 : 0

  name            = "${local.name_prefix}/errors"
  log_group_names = local.all_log_groups
  query_string    = <<-EOT
    fields @timestamp, service, correlation_id, error, message_id
    | filter message = "processing failed" or @message like /ERROR/
    | sort @timestamp desc
    | limit 100
  EOT
}
