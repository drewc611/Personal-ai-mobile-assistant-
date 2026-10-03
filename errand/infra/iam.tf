data "aws_caller_identity" "current" {}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

# Hard rule 6: every component gets its own least privilege role. The point of
# splitting these is that the internet-facing function cannot read a task, an
# approval, or any cached content - so a compromise of the webhook does not
# read the mailbox.

# ---------------------------------------------------------------------------
# Ingress: reads one secret, writes one audit partition, puts on one queue.
# ---------------------------------------------------------------------------

resource "aws_iam_role" "ingress" {
  name               = "errand-ingress"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "ingress" {
  statement {
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:*"]
  }

  statement {
    actions = ["secretsmanager:GetSecretValue"]
    # Both channels, because ERRAND_CHANNEL decides which is live at runtime
    # and an IAM policy cannot read an environment variable. Named explicitly
    # rather than widened to a wildcard over every secret in the account.
    resources = [
      aws_secretsmanager_secret.telegram.arn,
      aws_secretsmanager_secret.twilio.arn,
    ]
  }

  statement {
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.inbound.arn]
  }

  statement {
    actions   = ["dynamodb:PutItem"]
    resources = [aws_dynamodb_table.errand["audit"].arn]
  }

  statement {
    actions   = ["kms:Encrypt", "kms:GenerateDataKey", "kms:Decrypt"]
    resources = [aws_kms_key.errand.arn]
  }
}

resource "aws_iam_role_policy" "ingress" {
  role   = aws_iam_role.ingress.id
  policy = data.aws_iam_policy_document.ingress.json
}

# ---------------------------------------------------------------------------
# Dispatcher: reads the queue, calls AgentCore Runtime, replies over Twilio,
# transcribes voice memos. It holds no Bedrock model permissions - the model
# runs inside AgentCore, not here.
# ---------------------------------------------------------------------------

resource "aws_iam_role" "dispatcher" {
  name               = "errand-dispatcher"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "dispatcher" {
  statement {
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:*"]
  }

  statement {
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
    resources = [aws_sqs_queue.inbound.arn]
  }

  statement {
    actions = [
      "dynamodb:PutItem",
      "dynamodb:GetItem",
      "dynamodb:Query",
      "dynamodb:Scan",
      "dynamodb:DeleteItem",
      "dynamodb:BatchWriteItem",
    ]
    resources = [for t in aws_dynamodb_table.errand : t.arn]
  }

  statement {
    actions = ["secretsmanager:GetSecretValue"]
    # Both channels, because ERRAND_CHANNEL decides which is live at runtime
    # and an IAM policy cannot read an environment variable. Named explicitly
    # rather than widened to a wildcard over every secret in the account.
    resources = [
      aws_secretsmanager_secret.telegram.arn,
      aws_secretsmanager_secret.twilio.arn,
    ]
  }

  statement {
    actions   = ["s3:PutObject", "s3:GetObject"]
    resources = ["${aws_s3_bucket.voice.arn}/*", "${aws_s3_bucket.receipts.arn}/*"]
  }

  statement {
    actions = ["transcribe:StartTranscriptionJob"]
    # AWS's service reference lists no resource types for this action, so it
    # cannot be scoped by ARN.
    resources = ["*"]
  }

  statement {
    actions = ["transcribe:GetTranscriptionJob"]
    # dispatcher/voice.py names every job "errand-<run id>".
    resources = ["arn:aws:transcribe:${var.region}:${data.aws_caller_identity.current.account_id}:transcription-job/errand-*"]
  }

  statement {
    actions = ["bedrock-agentcore:InvokeAgentRuntime"]
    # The runtime is created outside this Terraform, so its id is not known
    # here. Confined to runtimes and their endpoints in this account and region.
    resources = [
      "arn:aws:bedrock-agentcore:${var.region}:${data.aws_caller_identity.current.account_id}:runtime/*",
      "arn:aws:bedrock-agentcore:${var.region}:${data.aws_caller_identity.current.account_id}:runtime/*/runtime-endpoint/*",
    ]
  }

  statement {
    actions   = ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey"]
    resources = [aws_kms_key.errand.arn]
  }

  statement {
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
    resources = ["*"] # X-Ray write actions do not support resource-level permissions.
  }
}

resource "aws_iam_role_policy" "dispatcher" {
  role   = aws_iam_role.dispatcher.id
  policy = data.aws_iam_policy_document.dispatcher.json
}

# ---------------------------------------------------------------------------
# Agent runtime: the only principal that may invoke a model. It reaches the
# same tables (the gate and the audit log run inside it) but never the queue
# and never Twilio.
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "agent_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["bedrock-agentcore.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "agent" {
  name               = "errand-agent"
  assume_role_policy = data.aws_iam_policy_document.agent_assume.json
}

data "aws_iam_policy_document" "agent" {
  statement {
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:*"]
  }

  statement {
    actions = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
    # Exactly the three models in config. A model that is not one of these is
    # not one the budget knows how to price.
    resources = distinct([
      "arn:aws:bedrock:${var.region}::foundation-model/${var.default_model_id}",
      "arn:aws:bedrock:${var.region}::foundation-model/${var.escalation_model_id}",
      "arn:aws:bedrock:${var.region}::foundation-model/${var.reader_model_id}",
    ])
  }

  statement {
    actions = [
      "dynamodb:PutItem",
      "dynamodb:GetItem",
      "dynamodb:Query",
      "dynamodb:Scan",
      "dynamodb:DeleteItem",
      "dynamodb:BatchWriteItem",
    ]
    resources = [for t in aws_dynamodb_table.errand : t.arn]
  }

  statement {
    actions = ["bedrock-agentcore:GetResourceOauth2Token"]
    # The token vault, credential providers and workload identities are created
    # outside this Terraform, so their ids are not known here. Confined to this
    # account and region, using the four resource types the action authorizes on.
    resources = [
      "arn:aws:bedrock-agentcore:${var.region}:${data.aws_caller_identity.current.account_id}:token-vault/*",
      "arn:aws:bedrock-agentcore:${var.region}:${data.aws_caller_identity.current.account_id}:token-vault/*/oauth2credentialprovider/*",
      "arn:aws:bedrock-agentcore:${var.region}:${data.aws_caller_identity.current.account_id}:workload-identity-directory/*",
      "arn:aws:bedrock-agentcore:${var.region}:${data.aws_caller_identity.current.account_id}:workload-identity-directory/*/workload-identity/*",
    ]
  }

  statement {
    actions   = ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey"]
    resources = [aws_kms_key.errand.arn]
  }

  statement {
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
    resources = ["*"] # X-Ray write actions do not support resource-level permissions.
  }
}

resource "aws_iam_role_policy" "agent" {
  role   = aws_iam_role.agent.id
  policy = data.aws_iam_policy_document.agent.json
}

# ---------------------------------------------------------------------------
# Scheduler: may invoke the two scheduled functions and nothing else.
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "errand-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
}

data "aws_iam_policy_document" "scheduler" {
  statement {
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.releaser.arn, aws_lambda_function.digest.arn]
  }
}

resource "aws_iam_role_policy" "scheduler" {
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.scheduler.json
}
