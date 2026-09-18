# FIFO with one message group. Order matters more than throughput here: "T7
# yes" must never be processed before the message that created T7, and an
# approval must never overtake a STOP ALL.

resource "aws_sqs_queue" "inbound_dlq" {
  name                      = "errand-inbound-dlq.fifo"
  fifo_queue                = true
  kms_master_key_id         = aws_kms_key.errand.arn
  message_retention_seconds = 1209600 # 14 days
}

resource "aws_sqs_queue" "inbound" {
  name                        = "errand-inbound.fifo"
  fifo_queue                  = true
  content_based_deduplication = false
  kms_master_key_id           = aws_kms_key.errand.arn
  visibility_timeout_seconds  = 180

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.inbound_dlq.arn
    # Three attempts. A message that fails three times is not going to succeed
    # on the fourth, and replaying an approval is worse than dropping it.
    maxReceiveCount = 3
  })
}
