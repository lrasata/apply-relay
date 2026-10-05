locals {
  name_prefix = "jae-${var.environment}"

  # Event type (as in the envelope) => topic name.
  events = {
    application-submitted = "ApplicationSubmitted"
    cv-parsed             = "CvParsed"
    candidate-matched     = "CandidateMatched"
  }

  # Which consumer listens to which events. This map is the whole wiring diagram.
  consumers = {
    cv-parser    = ["application-submitted"]
    matching     = ["cv-parsed"]
    notification = ["candidate-matched"]
    audit        = ["application-submitted", "cv-parsed", "candidate-matched"]
  }
}

# ---------------------------------------------------------------- events

module "topic" {
  source   = "../../modules/event_topic"
  for_each = local.events

  name_prefix = local.name_prefix
  event_name  = each.key
  event_type  = each.value
}

module "queue" {
  source   = "../../modules/consumer_queue"
  for_each = local.consumers

  name_prefix     = local.name_prefix
  service_name    = each.key
  topic_arns      = { for event in each.value : event => module.topic[event].arn }
  alarm_topic_arn = aws_sns_topic.ops_alerts.arn
}

# ---------------------------------------------------------------- alerts

# Where alarms go. Not KMS-encrypted on purpose: CloudWatch can't publish to a topic
# encrypted with the AWS-managed SNS key (it would need a customer-managed key).
resource "aws_sns_topic" "ops_alerts" {
  name = "${local.name_prefix}-ops-alerts"
}

resource "aws_sns_topic_subscription" "ops_alerts_email" {
  topic_arn = aws_sns_topic.ops_alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email # AWS sends a confirmation email that must be clicked once
}

# ---------------------------------------------------------------- cost guardrail

# Account-wide on purpose: in a personal account it also catches anything created outside Terraform.

resource "aws_budgets_budget" "monthly" {
  name         = "${local.name_prefix}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }
}
