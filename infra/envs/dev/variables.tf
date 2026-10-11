variable "region" {
  description = "AWS region to deploy to."
  type        = string
  default     = "eu-central-1" # Frankfurt
}

variable "environment" {
  description = "Environment name, used in resource names (apply-relay-<environment>-...)."
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

variable "cv_retention_days" {
  description = "Days before uploaded CVs are deleted from S3 (they are personal data)."
  type        = number
  default     = 30
}

variable "api_rate_limit" {
  description = "Steady-state requests per second allowed on the public API."
  type        = number
  default     = 5
}

variable "api_burst_limit" {
  description = "Short burst of requests allowed on the public API."
  type        = number
  default     = 10
}

variable "lambda_permissions_boundary" {
  description = "ARN of the permissions boundary for Lambda roles. Set in CI (from infra/ci); leave null for local applies."
  type        = string
  default     = null
}

variable "recruiter_email" {
  description = "Where strong-match emails go, and the address they're sent from. Defaults to alert_email. Must be verified in SES (AWS emails a link on apply)."
  type        = string
  default     = null
}

variable "audit_retention_days" {
  description = "Days before audit events are deleted (they contain candidate names and emails). 0 keeps them forever."
  type        = number
  default     = 365
}
