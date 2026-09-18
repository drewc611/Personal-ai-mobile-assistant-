# One customer managed key over every store that holds Andrew's content.
# The AWS managed key would work, but a CMK means the key policy is something
# that can be read and changed, and it means a single disable turns everything
# off at once.

resource "aws_kms_key" "errand" {
  description             = "Errand: tasks, approvals, audit, cached content, receipts"
  enable_key_rotation     = true
  deletion_window_in_days = 30
}

resource "aws_kms_alias" "errand" {
  name          = "alias/errand"
  target_key_id = aws_kms_key.errand.key_id
}
