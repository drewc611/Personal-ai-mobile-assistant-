variable "region" {
  description = <<-EOT
    Andrew's personal account. us-east-2 by his call.

    This was us-east-1 because Nova Sonic launched there and v2 voice needs
    it. That tradeoff is accepted and recorded in CLAUDE.md: v2 will either
    call Sonic cross-region or use whatever is in us-east-2 by then.
  EOT
  type        = string
  default     = "us-east-2"
}

variable "channel" {
  description = <<-EOT
    Which Channel implementation is live. The rest of the system never reads
    this; only the ingress Lambda and channels.get_channel do.

    It decides which of the owner identities below is required: telegram needs
    owner_telegram_id, twilio needs owner_number and twilio_from_number. The
    unused pair stays empty rather than being filled with a placeholder.
  EOT
  type        = string
  default     = "telegram"

  validation {
    condition     = contains(["telegram", "twilio"], var.channel)
    error_message = "channel must be telegram or twilio."
  }
}

variable "owner_telegram_id" {
  description = <<-EOT
    Hard rule 3: the one Telegram user id the system will answer. A numeric id,
    not an @username -- a username can be given up and taken by someone else,
    an id cannot.

    Message the bot and read it out of the ingress log, or ask @userinfobot.
  EOT
  type        = string
  default     = ""

  validation {
    condition     = var.owner_telegram_id == "" || can(regex("^[0-9]+$", var.owner_telegram_id))
    error_message = "owner_telegram_id must be the numeric Telegram user id, digits only."
  }

  validation {
    condition     = var.channel != "telegram" || var.owner_telegram_id != ""
    error_message = "channel = telegram requires owner_telegram_id, or the bot answers nobody."
  }
}

variable "owner_number" {
  description = <<-EOT
    Hard rule 3 for the Twilio channel: Andrew's mobile in E.164, the only
    number the system will answer. Leave empty when channel = telegram.
  EOT
  type        = string
  default     = ""

  validation {
    condition     = var.owner_number == "" || can(regex("^\\+[1-9]\\d{6,14}$", var.owner_number))
    error_message = "owner_number must be E.164, for example +15555550123."
  }

  validation {
    condition     = var.channel != "twilio" || var.owner_number != ""
    error_message = "channel = twilio requires owner_number, or the system answers nobody."
  }
}

variable "twilio_from_number" {
  description = "The Twilio number Errand texts from, E.164. Empty when channel = telegram."
  type        = string
  default     = ""

  validation {
    condition     = var.twilio_from_number == "" || can(regex("^\\+[1-9]\\d{6,14}$", var.twilio_from_number))
    error_message = "twilio_from_number must be E.164, for example +15555550999."
  }

  validation {
    condition     = var.channel != "twilio" || var.twilio_from_number != ""
    error_message = "channel = twilio requires twilio_from_number."
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
