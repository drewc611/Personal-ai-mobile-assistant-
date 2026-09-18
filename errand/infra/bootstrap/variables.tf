variable "region" {
  type    = string
  default = "us-east-1"
}

variable "github_repository" {
  description = "owner/repo. Only workflows in this repository may assume the deploy role."
  type        = string
  default     = "drewc611/Personal-ai-mobile-assistant-"

  validation {
    condition     = can(regex("^[^/]+/[^/]+$", var.github_repository))
    error_message = "github_repository must be owner/repo."
  }
}

variable "github_environment" {
  description = <<-EOT
    The GitHub Environment a deploy must run in. This is the tightest of the
    available trust conditions: a workflow can only get credentials if it is
    running in this environment, and the environment can require your manual
    approval before the job starts.

    Create it at Settings > Environments, add yourself as a required reviewer,
    and nothing deploys without a click.
  EOT
  type        = string
  default     = "production"
}

variable "state_bucket_name" {
  description = "Remote terraform state. Must be globally unique; leave empty for a generated name."
  type        = string
  default     = ""
}
