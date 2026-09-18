output "webhook_url" {
  description = "Set this as the messaging webhook on the Twilio number."
  value       = "${aws_apigatewayv2_api.sms.api_endpoint}/sms"
}

output "queue_url" {
  value = aws_sqs_queue.inbound.url
}

output "tables" {
  value = { for k, t in aws_dynamodb_table.errand : k => t.name }
}

output "receipts_bucket" {
  value = aws_s3_bucket.receipts.bucket
}

output "kms_key_arn" {
  value = aws_kms_key.errand.arn
}

output "agent_role_arn" {
  description = "Attach this to the AgentCore Runtime."
  value       = aws_iam_role.agent.arn
}

output "twilio_secret_id" {
  description = "Put the account sid and auth token here; Terraform never sees them."
  value       = aws_secretsmanager_secret.twilio.name
}

output "configuration_warnings" {
  description = "Settings that refuse to act until Andrew chooses a number."
  value = compact([
    var.monthly_budget_usd == 0 ? "No monthly budget set; every model call will be refused." : "",
    var.tier3_cap_cents == 0 ? "No tier 3 spend cap set; purchases will be refused." : "",
    var.model_rates_json == "{}" ? "No model rates set; the budget cannot price a call, so calls will be refused." : "",
  ])
}
