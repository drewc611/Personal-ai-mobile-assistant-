# Remote state, because a deploy that runs in GitHub Actions has nowhere to
# keep local state -- the runner is destroyed afterwards. Versioned so a bad
# apply can be rolled back to the previous state file, and locked so two runs
# cannot corrupt each other.
#
# Terraform 1.9 does not have S3 native locking (that landed in 1.10), so the
# lock lives in DynamoDB.

resource "random_id" "state_suffix" {
  byte_length = 6
}

locals {
  state_bucket = coalesce(
    var.state_bucket_name,
    "errand-tfstate-${data.aws_caller_identity.current.account_id}-${random_id.state_suffix.hex}"
  )
}

resource "aws_s3_bucket" "state" {
  bucket = local.state_bucket

  # State describes the whole system. Losing it is worse than almost any
  # other accident here, so it does not get destroyed by a stray apply.
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_dynamodb_table" "state_lock" {
  name         = "errand-tfstate-lock"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }
}
