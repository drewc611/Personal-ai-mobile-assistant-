# The deploy role. GitHub Actions assumes this, and nothing else can.

resource "aws_iam_role" "deploy" {
  name               = "errand-deploy"
  description        = "Assumed by GitHub Actions via OIDC to run terraform apply."
  assume_role_policy = data.aws_iam_policy_document.deploy_assume.json

  # An apply that has gone wrong is easier to stop than to undo. One hour is
  # long enough for the slowest apply here and short enough that a leaked
  # session is not a standing problem.
  max_session_duration = 3600
}

data "aws_iam_policy_document" "deploy" {
  # Everything Errand is made of. Broad on purpose within these services --
  # terraform needs to read, tag, update and destroy as well as create, and
  # enumerating that per resource produces a policy nobody can audit.
  statement {
    sid    = "ManageTheStack"
    effect = "Allow"
    actions = [
      "apigateway:*",
      "dynamodb:*",
      "events:*",
      "kms:*",
      "lambda:*",
      "logs:*",
      "s3:*",
      "scheduler:*",
      "secretsmanager:*",
      "sqs:*",
      "xray:*",
    ]
    resources = ["*"]
  }

  # Reading its own identity, and reading IAM so terraform can refresh state.
  statement {
    sid    = "ReadOnlyIdentityAndIAM"
    effect = "Allow"
    actions = [
      "sts:GetCallerIdentity",
      "iam:Get*",
      "iam:List*",
      "iam:TagRole",
      "iam:UntagRole",
    ]
    resources = ["*"]
  }

  # Creating roles is allowed ONLY when the new role carries the boundary.
  # Without this condition the deploy credential is account admin wearing a
  # narrow-looking policy.
  statement {
    sid    = "CreateRolesOnlyInsideTheBoundary"
    effect = "Allow"
    actions = [
      "iam:CreateRole",
      "iam:PutRolePolicy",
      "iam:AttachRolePolicy",
    ]
    resources = ["arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:role/errand-*"]

    condition {
      test     = "StringEquals"
      variable = "iam:PermissionsBoundary"
      values   = [aws_iam_policy.boundary.arn]
    }
  }

  # Deleting and detaching need no boundary condition -- they only ever reduce
  # permissions -- but stay confined to Errand's own roles.
  statement {
    sid    = "TidyUpItsOwnRoles"
    effect = "Allow"
    actions = [
      "iam:DeleteRole",
      "iam:DeleteRolePolicy",
      "iam:DetachRolePolicy",
      "iam:UpdateAssumeRolePolicy",
    ]
    resources = ["arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:role/errand-*"]
  }

  statement {
    sid       = "PassRolesToTheServicesThatRunThem"
    effect    = "Allow"
    actions   = ["iam:PassRole"]
    resources = ["arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:role/errand-*"]

    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values = [
        "lambda.amazonaws.com",
        "scheduler.amazonaws.com",
        "bedrock-agentcore.amazonaws.com",
      ]
    }
  }

  # The three things that would let the deploy role escape its own fence:
  # taking a boundary off a role, rewriting the boundary policy, or editing
  # itself. An explicit Deny cannot be overridden by any Allow.
  statement {
    sid    = "NoEscapingTheFence"
    effect = "Deny"
    actions = [
      "iam:DeleteRolePermissionsBoundary",
      "iam:PutRolePermissionsBoundary",
      "iam:CreatePolicyVersion",
      "iam:DeletePolicyVersion",
      "iam:SetDefaultPolicyVersion",
      "iam:CreateUser",
      "iam:CreateAccessKey",
      "iam:DeleteOpenIDConnectProvider",
      "iam:UpdateOpenIDConnectProviderThumbprint",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "NorEditingItself"
    effect = "Deny"
    actions = [
      "iam:*",
    ]
    resources = [
      aws_iam_role.deploy.arn,
      aws_iam_policy.boundary.arn,
    ]
  }

  # The state bucket is how a deploy remembers what it built. It may read and
  # write state, and may not delete the bucket.
  statement {
    sid    = "ReadAndWriteItsOwnState"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:ListBucket",
    ]
    resources = [
      aws_s3_bucket.state.arn,
      "${aws_s3_bucket.state.arn}/*",
    ]
  }

  statement {
    sid       = "NotDeletingTheStateBucket"
    effect    = "Deny"
    actions   = ["s3:DeleteBucket"]
    resources = [aws_s3_bucket.state.arn]
  }
}

resource "aws_iam_role_policy" "deploy" {
  name   = "errand-deploy"
  role   = aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.deploy.json
}
