locals {
  name_prefix = "apply-relay-${var.environment}"

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

  # Only strong matches reach the notification queue; SNS drops the rest for free.
  consumer_filters = {
    notification = { candidate-matched = jsonencode({ recommendation = ["strong_match"] }) }
  }

  # In deployment order: each service's events feed the next one.
  all_services = ["application", "cv-parser", "matching", "notification", "audit"]

  # Which services are deployed (var.services). A service's queue is created with it, so
  # events never pile up in a queue nobody reads yet.
  deploy             = { for service in local.all_services : service => contains(var.services, service) }
  deployed_consumers = { for service, events in local.consumers : service => events if local.deploy[service] }
}

data "aws_caller_identity" "current" {}

# Shared by every service's Lambda role.
data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
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
  for_each = local.deployed_consumers

  name_prefix     = local.name_prefix
  service_name    = each.key
  topic_arns      = { for event in each.value : event => module.topic[event].arn }
  alarm_topic_arn = aws_sns_topic.alarms.arn # rewritten into a readable email, see alerts.tf
  filter_policies = lookup(local.consumer_filters, each.key, {})
}

# ---------------------------------------------------------------- alerts

# Where alert emails go: the alerts function (alerts.tf) publishes readable alarm emails here.
# Not KMS-encrypted on purpose: CloudWatch can't publish to a topic encrypted with the
# AWS-managed SNS key (it would need a customer-managed key), and one alarm still does.
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
