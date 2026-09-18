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

# ---------------------------------------------------------------------------
# Ingress: the internet-facing function. It reads one secret, writes one audit
# partition, and puts on one queue. It cannot read a task, an approval, or any
# cached content, so a compromise of the webhook does not read the mailbox.
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
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.twilio.arn]
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
# Dispatcher: reads the queue, talks to AgentCore Runtime, sends SMS.
# It does not hold Bedrock model permissions - the model runs inside AgentCore,
# not here.
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
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
    ]
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
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.twilio.arn]
  }

  statement {
    actions   = ["s3:PutObject", "s3:GetObject"]
    resources = ["${aws_s3_bucket.voice.arn}/*", "${aws_s3_bucket.receipts.arn}/*"]
  }

  statement {
    actions = [
      "transcribe:StartTranscriptionJob",
      "transcribe:GetTranscriptionJob",
    ]
    resources = ["*"] # Transcribe job ARNs are not knowable before creation.
  }

  statement {
    actions   = ["bedrock-agentcore:InvokeAgentRuntime"]
    resources = ["*"] # Narrow to the runtime ARN once it exists.
  }

  statement {
    actions = ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey"]
    resources = [aws_kms_key.errand.arn]
  }
}

resource "aws_iam_role_policy" "dispatcher" {
  role   = aws_iam_role.dispatcher.id
  policy = data.aws_iam_policy_document.dispatcher.json
}
