# GitHub's OIDC provider. This is what replaces a stored AWS credential: a
# workflow presents a short-lived token that GitHub signed, AWS checks the
# signature and the claims, and hands back a session. There is no secret to
# leak, rotate, or find in a transcript.
#
# thumbprint_list is deliberately omitted. AWS stopped requiring thumbprint
# verification for providers whose host uses a well-known CA, and the provider
# schema marks the field optional and computed -- so pinning a thumbprint here
# would only create something that silently expires.

resource "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"

  # The audience the workflow requests. configure-aws-credentials asks for
  # exactly this.
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_policy_document" "deploy_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    # The tight one. `sub` identifies which workflow is asking, and pinning it
    # to an environment means a push to any branch cannot deploy: the job has
    # to be running in the named GitHub Environment, which can require a human
    # to approve it first.
    #
    # Pinning to the repository alone would let any workflow in it, including
    # one added by a pull request, assume this role.
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repository}:environment:${var.github_environment}"]
    }
  }
}
