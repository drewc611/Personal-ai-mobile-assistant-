variable "region" {
  description = "Andrew's personal account, us-east-1 as CLAUDE.md fixes it."
  type        = string
  default     = "us-east-1"
}

variable "owner_telegram_id" {
  description = "Andrew's Telegram user id. The only sender the bot answers."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{5,15}$", var.owner_telegram_id))
    error_message = "owner_telegram_id must be a numeric Telegram user id."
  }
}

variable "default_model_id" {
  description = <<-EOT
    Bedrock model id for the default planner, Claude Haiku 4.5. Look it up in
    the Bedrock console for this account and region. There is deliberately no
    default here: CLAUDE.md forbids hardcoding a model id, and a stale one
    would be a silent downgrade rather than an error.
  EOT
  type        = string
}

variable "escalation_model_id" {
  description = "Bedrock model id for escalation, Claude Sonnet 5. Look it up too."
  type        = string
}

variable "reader_model_id" {
  description = "Bedrock model id for the quarantined reader, Haiku. Look it up too."
  type        = string
}

variable "model_rates_json" {
  description = <<-EOT
    USD per million tokens per model, as JSON:
    {"<model id>": {"in": 0.80, "out": 4.00}}
    Taken from the Bedrock pricing page. A model with no rate here blocks
    rather than being counted as free, because a cap that can be walked past
    is not a cap.
  EOT
  type        = string
  default     = "{}"
}

variable "monthly_budget_usd" {
  description = <<-EOT
    Hard rule 8. Warn at 80 percent, stop at 100. Zero means no budget has
    been chosen and every model call is refused, which is the intended
    behaviour rather than a placeholder to work around.
  EOT
  type        = number
  default     = 0
}

variable "tier3_cap_cents" {
  description = <<-EOT
    Per task spend cap for tier 3, in cents. Zero refuses every tier 3
    approval. Set it to what you are willing to lose to a bug.
  EOT
  type        = number
  default     = 0
}

variable "undo_seconds" {
  description = "How long an approved tier 2 or 3 action waits, with an Undo button."
  type        = number
  default     = 60
}

variable "approval_ttl_seconds" {
  description = "How long a pending approval stays answerable."
  type        = number
  default     = 3600
}

variable "digest_schedule" {
  description = "When the daily approval digest goes out. EventBridge Scheduler syntax."
  type        = string
  default     = "cron(0 14 * * ? *)"
}

variable "log_retention_days" {
  description = "CloudWatch retention, set on day one per CLAUDE.md."
  type        = number
  default     = 30
}

variable "trace_sampling_rate" {
  description = "X-Ray sampling for AgentCore Observability, set on day one."
  type        = number
  default     = 0.1
}

variable "lambda_artifact" {
  description = "Path to the built Lambda zip."
  type        = string
  default     = "../../build/errand.zip"
}
