output "dynamodb_table_name" {
  description = "Name of the account-requests table"
  value       = aws_dynamodb_table.requests.name
}

output "state_machine_arn" {
  description = "ARN of the provisioning state machine"
  value       = aws_sfn_state_machine.provisioning.arn
}

output "workflow_dlq_url" {
  description = "URL of the workflow dead-letter queue"
  value       = aws_sqs_queue.workflow_dlq.id
}

output "kms_key_arn" {
  description = "ARN of the control-plane KMS key"
  value       = aws_kms_key.control_plane.arn
}

output "sfn_role_arn" {
  description = "Execution role assumed by the state machine"
  value       = aws_iam_role.sfn.arn
}
