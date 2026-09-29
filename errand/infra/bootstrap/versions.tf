# Bootstrap: run this ONCE, by hand, with your own admin session.
#
# It creates the things that must exist before anything can deploy itself:
# the GitHub OIDC trust, the deploy role, the permissions boundary that caps
# what that role can ever create, and the remote state backend.
#
#   cd errand/infra/bootstrap
#   terraform init
#   terraform apply
#
# Its own state is local and stays local -- there is no bucket to put it in
# until this has run. Keep terraform.tfstate here; it is small, and it is the
# only record of what was created. It contains no secrets.

terraform {
  required_version = ">= 1.9"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project = "errand"
      Owner   = "andrew"
      Scope   = "bootstrap"
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
