variable "name_prefix" {
  description = "Prefix for resource names, e.g. apply-relay-dev."
  type        = string
}

variable "event_name" {
  description = "Kebab-case topic name, e.g. application-submitted."
  type        = string
}

variable "event_type" {
  description = "Event type as it appears in the envelope, e.g. ApplicationSubmitted."
  type        = string
}
