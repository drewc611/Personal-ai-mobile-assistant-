# The secret containers are managed here; the values are not. Terraform state
# is a file, and a bot token in a state file is a bot token on disk
# (hard rule 6).
#
#   aws secretsmanager put-secret-value --secret-id errand/telegram \
#     --secret-string '{"bot_token":"123:ABC...","webhook_secret":"<32+ random chars>"}'
#
# The webhook_secret is the value passed to setWebhook as `secret_token`.
# Telegram echoes it back in the X-Telegram-Bot-Api-Secret-Token header on
# every update, and the ingress Lambda compares it in constant time.

resource "aws_secretsmanager_secret" "telegram" {
  name       = "errand/telegram"
  kms_key_id = aws_kms_key.errand.arn
}

resource "aws_secretsmanager_secret" "google_oauth" {
  name        = "errand/google-oauth"
  description = "OAuth client id and secret. User tokens go through AgentCore Identity."
  kms_key_id  = aws_kms_key.errand.arn
}
