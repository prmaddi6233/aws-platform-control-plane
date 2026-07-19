resource "aws_dynamodb_table" "requests" {
  name         = "${var.dynamodb_table_name}-${var.environment}"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "PK"
  range_key    = "SK"

  attribute {
    name = "PK"
    type = "S"
  }

  attribute {
    name = "SK"
    type = "S"
  }

  # Encrypt at rest with the customer-managed control-plane key.
  server_side_encryption {
    enabled     = true
    kms_key_arn = aws_kms_key.control_plane.arn
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = local.common_tags
}
