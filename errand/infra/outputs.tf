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

output "twilio_secret_id" {
  description = "Put the account sid and auth token here; Terraform never sees them."
  value       = aws_secretsmanager_secret.twilio.name
}

output "spend_cap_warning" {
  description = "Tier 3 is refused entirely while this is zero."
  value       = var.tier3_cap_cents == 0 ? "No tier 3 spend cap set; purchases will be refused." : "Tier 3 cap: ${var.tier3_cap_cents} cents."
}
