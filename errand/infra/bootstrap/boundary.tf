# The permissions boundary.
#
# The deploy role has to be able to create IAM roles -- Errand has four. That
# permission, with PutRolePolicy and PassRole beside it, is ordinarily a path
# straight to account admin: create a role with AdministratorAccess, attach it
# to a Lambda, invoke the Lambda.
#
# A boundary closes that. It is a ceiling: a role created with this boundary
# can never exceed it, no matter what policy is attached. The deploy role is
# then only allowed to create roles that carry it (see deploy_role.tf), and is
# denied the ability to take it off again.
#
# So this document is the real answer to "how much can the deploy credential
# ultimately do". Everything Errand's own roles need is here, and nothing else.

data "aws_iam_policy_document" "boundary" {
  statement {
    sid    = "ObservabilityAndTracing"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "xray:PutTraceSegments",
      "xray:PutTelemetryRecords",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "TheDataErrandOwns"
    effect = "Allow"
    actions = [
      "dynamodb:PutItem",
      "dynamodb:GetItem",
      "dynamodb:Query",
      "dynamodb:Scan",
      "dynamodb:DeleteItem",
      "dynamodb:BatchWriteItem",
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
      "sqs:SendMessage",
      "s3:GetObject",
      "s3:PutObject",
      "secretsmanager:GetSecretValue",
      "kms:Encrypt",
      "kms:Decrypt",
      "kms:GenerateDataKey",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "TheModelsAndTheAgent"
    effect = "Allow"
    actions = [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
      "bedrock-agentcore:InvokeAgentRuntime",
      "bedrock-agentcore:GetResourceOauth2Token",
      "transcribe:StartTranscriptionJob",
      "transcribe:GetTranscriptionJob",
      "lambda:InvokeFunction",
    ]
    resources = ["*"]
  }

  # The ceiling itself. Nothing created by the deploy role may touch IAM or
  # organisations, which is what stops a created role from being used to climb
  # out of the boundary.
  statement {
    sid    = "NoClimbingOut"
    effect = "Deny"
    actions = [
      "iam:*",
      "organizations:*",
      "account:*",
      "sts:AssumeRole",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_policy" "boundary" {
  name        = "errand-permissions-boundary"
  description = "Ceiling for every role the Errand deploy role is allowed to create."
  policy      = data.aws_iam_policy_document.boundary.json
}
