variable "name_prefix" {
  description = "Prefix for resource names, e.g. apply-relay-dev."
  type        = string
}

variable "service_name" {
  description = "Consuming service, e.g. cv-parser. Becomes the queue name."
  type        = string
}

variable "topic_arns" {
  description = "SNS topics this queue subscribes to, keyed by a stable name (e.g. application-submitted)."
  type        = map(string)
}

variable "alarm_topic_arn" {
  description = "SNS topic that receives the dead-letter-queue alarm."
  type        = string
}

variable "visibility_timeout_seconds" {
  description = "How long a message stays hidden while a consumer works on it. AWS recommends 6x the Lambda timeout."
  type        = number
  default     = 180
}

variable "max_receive_count" {
  description = "Failed attempts before a message is moved to the dead-letter queue."
  type        = number
  default     = 3

  validation {
    condition     = var.max_receive_count >= 1 && var.max_receive_count <= 10
    error_message = "max_receive_count must be between 1 and 10."
  }
}

variable "filter_policies" {
  description = "Optional SNS filter policy (JSON) per topic key, on message attributes."
  type        = map(string)
  default     = {}
}

variable "max_message_age_seconds" {
  description = "Alarm when the oldest message in the queue is older than this."
  type        = number
  default     = 900
}
