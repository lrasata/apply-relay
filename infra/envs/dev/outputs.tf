output "topic_arns" {
  description = "SNS topic per event type. Services publish here."
  value       = { for name, topic in module.topic : name => topic.arn }
}

output "queue_urls" {
  description = "SQS queue per consuming service."
  value       = { for name, queue in module.queue : name => queue.queue_url }
}

output "dead_letter_queue_urls" {
  description = "Where messages end up after 3 failed attempts."
  value       = { for name, queue in module.queue : name => queue.dlq_url }
}

output "ops_alerts_topic_arn" {
  value = aws_sns_topic.ops_alerts.arn
}

output "api_url" {
  description = "Base URL of the public API. Submit with POST <api_url>/applications."
  value       = one(aws_apigatewayv2_stage.default[*].invoke_url)
}

output "applications_table" {
  value = one(aws_dynamodb_table.applications[*].name)
}

output "cvs_bucket" {
  value = one(aws_s3_bucket.cvs[*].bucket)
}

output "dashboard_url" {
  value = length(aws_cloudwatch_dashboard.pipeline) > 0 ? "https://${var.region}.console.aws.amazon.com/cloudwatch/home?region=${var.region}#dashboards/dashboard/${aws_cloudwatch_dashboard.pipeline[0].dashboard_name}" : null
}

output "audit_table" {
  description = "Read one application's history: python scripts/trace_application.py <application_id>"
  value       = one(aws_dynamodb_table.audit_log[*].name)
}
