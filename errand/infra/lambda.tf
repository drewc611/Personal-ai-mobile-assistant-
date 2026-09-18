locals {
  common_env = {
    ERRAND_REGION               = var.region
    ERRAND_BACKEND              = "dynamodb"
    ERRAND_TASKS_TABLE          = aws_dynamodb_table.errand["tasks"].name
    ERRAND_APPROVALS_TABLE      = aws_dynamodb_table.errand["approvals"].name
    ERRAND_AUDIT_TABLE          = aws_dynamodb_table.errand["audit"].name
    ERRAND_CONTENT_TABLE        = aws_dynamodb_table.errand["content"].name
    ERRAND_RECEIPTS_BUCKET      = aws_s3_bucket.receipts.bucket
    ERRAND_TRANSCRIBE_BUCKET    = aws_s3_bucket.voice.bucket
    ERRAND_QUEUE_URL            = aws_sqs_queue.inbound.url
    ERRAND_TWILIO_SECRET_ID     = aws_secretsmanager_secret.twilio.name
    ERRAND_TWILIO_FROM          = var.twilio_from_number
    ERRAND_OWNER_NUMBER         = var.owner_number
    ERRAND_PLANNER_MODEL_ID     = var.planner_model_id
    ERRAND_READER_MODEL_ID      = var.reader_model_id
    ERRAND_TIER3_CAP_CENTS      = tostring(var.tier3_cap_cents)
    ERRAND_UNDO_SECONDS         = tostring(var.undo_seconds)
    ERRAND_APPROVAL_TTL_SECONDS = tostring(var.approval_ttl_seconds)
  }
}

resource "aws_lambda_function" "ingress" {
  function_name = "errand-ingress"
  role          = aws_iam_role.ingress.arn
  runtime       = "python3.12"
  handler       = "errand.ingress.handler.handler"
  filename      = var.lambda_artifact
  timeout       = 10
  memory_size   = 256

  environment {
    variables = merge(local.common_env, {
      # The URL Twilio actually calls. Signature validation needs the exact
      # string, and rebuilding it from the Lambda event gives the internal
      # host, which fails every signature.
      ERRAND_WEBHOOK_URL = "${aws_apigatewayv2_api.sms.api_endpoint}/sms"
      ERRAND_NUMBER_SALT = random_id.number_salt.hex
    })
  }
}

resource "random_id" "number_salt" {
  byte_length = 16
}

resource "aws_lambda_function" "dispatcher" {
  function_name = "errand-dispatcher"
  role          = aws_iam_role.dispatcher.arn
  runtime       = "python3.12"
  handler       = "errand.dispatcher.handler.handler"
  filename      = var.lambda_artifact
  timeout       = 120
  memory_size   = 512

  environment {
    variables = local.common_env
  }
}

resource "aws_lambda_function" "releaser" {
  function_name = "errand-releaser"
  role          = aws_iam_role.dispatcher.arn
  runtime       = "python3.12"
  handler       = "errand.dispatcher.handler.releaser"
  filename      = var.lambda_artifact
  timeout       = 60
  memory_size   = 512

  environment {
    variables = local.common_env
  }
}

resource "aws_lambda_function" "digest" {
  function_name = "errand-digest"
  role          = aws_iam_role.dispatcher.arn
  runtime       = "python3.12"
  handler       = "errand.dispatcher.handler.digest"
  filename      = var.lambda_artifact
  timeout       = 60
  memory_size   = 512

  environment {
    variables = local.common_env
  }
}

resource "aws_lambda_event_source_mapping" "inbound" {
  event_source_arn = aws_sqs_queue.inbound.arn
  function_name    = aws_lambda_function.dispatcher.arn
  batch_size       = 1

  # Only failed messages come back for redelivery. Replaying a whole batch
  # would re-run approvals that already went through.
  function_response_types = ["ReportBatchItemFailures"]
}

# The undo window closes on a schedule. Once a minute is the coarsest tick
# that still makes a 60 second window feel like 60 seconds.
resource "aws_cloudwatch_event_rule" "release" {
  name                = "errand-release-due"
  schedule_expression = "rate(1 minute)"
}

resource "aws_cloudwatch_event_target" "release" {
  rule = aws_cloudwatch_event_rule.release.name
  arn  = aws_lambda_function.releaser.arn
}

resource "aws_lambda_permission" "release" {
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.releaser.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.release.arn
}

resource "aws_cloudwatch_event_rule" "digest" {
  name                = "errand-morning-digest"
  schedule_expression = var.digest_cron
}

resource "aws_cloudwatch_event_target" "digest" {
  rule = aws_cloudwatch_event_rule.digest.name
  arn  = aws_lambda_function.digest.arn
}

resource "aws_lambda_permission" "digest" {
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.digest.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.digest.arn
}

resource "aws_cloudwatch_log_group" "lambdas" {
  for_each = toset([
    aws_lambda_function.ingress.function_name,
    aws_lambda_function.dispatcher.function_name,
    aws_lambda_function.releaser.function_name,
    aws_lambda_function.digest.function_name,
  ])

  name              = "/aws/lambda/${each.value}"
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.errand.arn
}
