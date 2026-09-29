# One table per entity, as PLAN.md sets out, plus `content`. All pk/sk, all on
# demand: this is a single-user system and the volume does not justify
# provisioned capacity or any thought about partition design beyond keeping
# one task's rows together.
#
# `content` is the extra one. Hard rule 7 says a disconnect deletes every
# stored copy of an account's data and reports what it deleted; that needs a
# table that can be enumerated by connection, or the receipt is a promise
# rather than a count.

locals {
  tables = {
    tasks       = "errand-tasks"
    approvals   = "errand-approvals"
    audit       = "errand-audit"
    rules       = "errand-rules"
    receipts    = "errand-receipts"
    connections = "errand-connections"
    budget      = "errand-budget"
    content     = "errand-content"
    recipes     = "errand-recipes"
  }

  # Only the content table expires rows on its own. Everything else is either
  # the record of what happened (audit, receipts) or current state.
  ttl_tables = ["content"]
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
    enabled = true
  }

  dynamic "ttl" {
    for_each = contains(local.ttl_tables, each.key) ? [1] : []
    content {
      attribute_name = "expires_at"
      enabled        = true
    }
  }
}
