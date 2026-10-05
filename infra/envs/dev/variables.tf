variable "region" {
  description = "AWS region to deploy to."
  type        = string
  default     = "eu-central-1" # Frankfurt
}

variable "environment" {
  description = "Environment name, used in resource names (jae-<environment>-...)."
  type        = string
  default     = "dev"

  validation {
    condition     = can(regex("^[a-z0-9-]{2,10}$", var.environment))
    error_message = "environment must be 2-10 lower-case letters, digits or dashes."
  }
}

variable "alert_email" {
  description = "Email address for dead-letter-queue alarms and budget warnings."
  type        = string

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.alert_email))
    error_message = "alert_email must be a valid email address."
  }
}

variable "monthly_budget_usd" {
  description = "Monthly spend limit for the whole AWS account. You get an email at 80% (forecast) and 100% (actual)."
  type        = number
  default     = 5
}
