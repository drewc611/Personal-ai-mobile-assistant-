# The secret container is managed here; the value is not. Terraform state is a
# file, and a Twilio auth token in a state file is a Twilio auth token on disk.
# Set it once with:
#   aws secretsmanager put-secret-value --secret-id errand/twilio \
#     --secret-string '{"account_sid":"AC...","auth_token":"..."}'

resource "aws_secretsmanager_secret" "twilio" {
  name       = "errand/twilio"
  kms_key_id = aws_kms_key.errand.arn
}
