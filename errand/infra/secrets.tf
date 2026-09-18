# The secret containers are managed here; the values are not. Terraform state
# is a file, and a Twilio auth token in a state file is a Twilio auth token on
# disk (hard rule 7).
#
#   aws secretsmanager put-secret-value --secret-id errand/twilio \
#     --secret-string '{"account_sid":"AC...","auth_token":"..."}'
#
# The auth token is both the REST credential and the HMAC key Twilio signs
# webhooks with, which is exactly why it never becomes an env var.

resource "aws_secretsmanager_secret" "twilio" {
  name       = "errand/twilio"
  kms_key_id = aws_kms_key.errand.arn
}

resource "aws_secretsmanager_secret" "google_oauth" {
  name        = "errand/google-oauth"
  description = "OAuth client id and secret. User tokens go through AgentCore Identity."
  kms_key_id  = aws_kms_key.errand.arn
}
