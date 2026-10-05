# One SNS topic per event type. Publishers only know the topic, never the consumers.

resource "aws_sns_topic" "this" {
  name              = "${var.name_prefix}-${var.event_name}"
  kms_master_key_id = "alias/aws/sns" # encrypted at rest with the AWS-managed key

  tags = {
    EventType = var.event_type
  }
}
