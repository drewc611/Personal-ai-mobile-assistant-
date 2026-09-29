resource "aws_s3_bucket" "receipts" {
  bucket_prefix = "errand-receipts-"
}

resource "aws_s3_bucket_server_side_encryption_configuration" "receipts" {
  bucket = aws_s3_bucket.receipts.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.errand.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "receipts" {
  bucket                  = aws_s3_bucket.receipts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "receipts" {
  bucket = aws_s3_bucket.receipts.id
  versioning_configuration {
    status = "Enabled"
  }
}

# Voice memos: transient by design. The audio is transcribed and then it is of
# no further use, so it expires in a day rather than sitting in a bucket.
resource "aws_s3_bucket" "voice" {
  bucket_prefix = "errand-voice-"
}

resource "aws_s3_bucket_server_side_encryption_configuration" "voice" {
  bucket = aws_s3_bucket.voice.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.errand.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "voice" {
  bucket                  = aws_s3_bucket.voice.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "voice" {
  bucket = aws_s3_bucket.voice.id

  rule {
    id     = "expire-memos"
    status = "Enabled"

    filter {
      prefix = "voice/"
    }

    expiration {
      days = 1
    }
  }
}
