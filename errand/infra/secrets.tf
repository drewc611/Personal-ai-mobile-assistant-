# The secret containers are managed here; the values are not. Terraform state
# is a file, and a Twilio auth token in a state file is a Twilio auth token on
# disk (hard rule 7).
#
#   umask 077
#   read -rsp "SID: " SID; echo; read -rsp "token: " TOKEN; echo
#   jq -nc --arg s "$SID" --arg t "$TOKEN" '{account_sid:$s,auth_token:$t}' > twilio.json
#   unset SID TOKEN
#   aws secretsmanager put-secret-value --secret-id errand/twilio \
#     --secret-string file://twilio.json
#   shred -u twilio.json
#
# Read interactively rather than pasted into the command, so the token does
# not land in shell history or, if an agent is driving the terminal, in its
# transcript.
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
