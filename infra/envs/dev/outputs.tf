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
