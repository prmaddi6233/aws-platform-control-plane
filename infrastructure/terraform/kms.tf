# Customer-managed KMS key encrypting the request table, the workflow logs,
# and the DLQ. A single control-plane key keeps the key policy auditable.

resource "aws_kms_key" "control_plane" {
  description             = "Encryption key for the AWS Platform Control Plane"
  deletion_window_in_days = 30
  enable_key_rotation     = true

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "EnableRootAccountAdmin"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${local.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        Sid       = "AllowCloudWatchLogs"
        Effect    = "Allow"
        Principal = { Service = "logs.${local.region}.amazonaws.com" }
        Action = [
          "kms:Encrypt",
          "kms:Decrypt",
          "kms:ReEncrypt*",
          "kms:GenerateDataKey*",
          "kms:Describe*",
        ]
        Resource = "*"
        Condition = {
          ArnLike = {
            "kms:EncryptionContext:aws:logs:arn" = "arn:aws:logs:${local.region}:${local.account_id}:*"
          }
        }
      },
    ]
  })

  tags = local.common_tags
}

resource "aws_kms_alias" "control_plane" {
  name          = "alias/${var.name_prefix}-${var.environment}"
  target_key_id = aws_kms_key.control_plane.key_id
}
