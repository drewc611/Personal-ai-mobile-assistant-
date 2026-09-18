variable "region" {
  description = "Nova Sonic is in us-east-1 and v2 needs it, so everything lives there."
  type        = string
  default     = "us-east-1"
}

variable "owner_number" {
  description = "Andrew's mobile in E.164. The only number the system will answer."
  type        = string

  validation {
    condition     = can(regex("^\\+[1-9]\\d{6,14}$", var.owner_number))
    error_message = "owner_number must be E.164, for example +15555550123."
  }
}

variable "twilio_from_number" {
  description = "The Twilio number Errand texts from, E.164."
  type        = string
}

variable "planner_model_id" {
  description = <<-EOT
    Bedrock model id for the planner. Look this up in the Bedrock console for
    this account and region - there is deliberately no default, because a
    stale id committed to a repo is a silent downgrade.
  EOT
  type        = string
}

variable "reader_model_id" {
  description = "Bedrock model id for the quarantined reader. Look it up too."
  type        = string
}

variable "tier3_cap_cents" {
  description = <<-EOT
    Per-task spend cap for tier 3, in cents. Zero means no cap has been chosen
    yet, and every tier 3 approval is refused until one is. That is the
    intended behaviour, not a placeholder to work around.
  EOT
  type        = number
  default     = 0
}

variable "undo_seconds" {
  description = "How long an approved send waits before it goes out."
  type        = number
  default     = 60
}

variable "approval_ttl_seconds" {
  description = "How long a pending approval stays answerable."
  type        = number
  default     = 3600
}

variable "digest_cron" {
  description = "When the batched-approval digest goes out (UTC)."
  type        = string
  default     = "cron(0 14 * * ? *)"
}

variable "log_retention_days" {
  description = "CloudWatch retention. Audit rows live in DynamoDB, not here."
  type        = number
  default     = 30
}

variable "lambda_artifact" {
  description = "Path to the built Lambda zip."
  type        = string
  default     = "../../build/errand.zip"
}
