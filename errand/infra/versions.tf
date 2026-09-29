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
      # Single user, personal account, no work data. The tag is here so that
      # is visible in the console and in Cost Explorer.
      Scope = "personal"
    }
  }
}
