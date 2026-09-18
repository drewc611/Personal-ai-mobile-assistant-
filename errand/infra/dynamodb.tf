# Four tables, all pk/sk, all on-demand. This is a single-user system; the
# read and write volume does not justify provisioned capacity or a moment's
# thought about partition design beyond keeping one task's rows together.

locals {
  tables = {
    tasks     = "errand-tasks"
    approvals = "errand-approvals"
    audit     = "errand-audit"
    content   = "errand-content"
  }
}

resource "aws_dynamodb_table" "errand" {
  for_each = local.tables

  name         = each.value
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = aws_kms_key.errand.arn
  }

  point_in_time_recovery {
    # On for the audit log because it is the record of what happened. On for
    # the others because a single-user table is cheap to protect.
    enabled = true
  }

  # The content table is the only one that caches third-party data, and
  # "disconnect gmail" deletes from it explicitly. The TTL is a backstop for
  # anything a disconnect never covered.
  dynamic "ttl" {
    for_each = each.key == "content" ? [1] : []
    content {
      attribute_name = "expires_at"
      enabled        = true
    }
  }
}
