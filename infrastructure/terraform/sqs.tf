# Dead-letter queue for workflow task events that exhaust retries. A redrive
# runbook (docs/failure-recovery.md) drains this back into the workflow after
# the root cause is fixed.

resource "aws_sqs_queue" "workflow_dlq" {
  name                              = "${var.name_prefix}-workflow-dlq-${var.environment}"
  message_retention_seconds         = 1209600 # 14 days — max, to preserve evidence
  kms_master_key_id                 = aws_kms_key.control_plane.arn
  kms_data_key_reuse_period_seconds = 300

  tags = local.common_tags
}

resource "aws_sqs_queue_policy" "workflow_dlq" {
  queue_url = aws_sqs_queue.workflow_dlq.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "sqs:*"
        Resource  = aws_sqs_queue.workflow_dlq.arn
        Condition = {
          Bool = { "aws:SecureTransport" = "false" }
        }
      },
    ]
  })
}
