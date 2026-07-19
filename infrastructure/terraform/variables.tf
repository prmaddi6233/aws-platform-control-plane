variable "aws_region" {
  description = "AWS region to deploy the control plane"
  type        = string
  default     = "ca-central-1"
}

variable "environment" {
  description = "Environment name (e.g. dev, prod)"
  type        = string
  default     = "dev"
}

variable "dynamodb_table_name" {
  description = "Name of the DynamoDB table for account requests"
  type        = string
  default     = "platform-control-plane-requests"
}

variable "name_prefix" {
  description = "Prefix applied to all control-plane resource names"
  type        = string
  default     = "platform-control-plane"
}

variable "log_retention_days" {
  description = "CloudWatch log retention for the workflow, in days"
  type        = number
  default     = 90

  validation {
    condition     = contains([7, 14, 30, 60, 90, 180, 365], var.log_retention_days)
    error_message = "log_retention_days must be one of 7, 14, 30, 60, 90, 180, 365."
  }
}

variable "task_function_arns" {
  description = <<-EOT
    ARNs of the Lambda functions that implement each workflow task. These are
    provided by the caller; this stack does NOT create the account-mutating
    Lambdas (see docs/production-boundaries.md). Placeholder ARNs let the stack
    validate and plan without deploying real account-creation logic.
  EOT
  type = object({
    create_account  = string
    apply_baseline  = string
    assign_identity = string
    create_budget   = string
    mark_completed  = string
    notify_owner    = string
    handle_failure  = string
  })
  default = {
    create_account  = "arn:aws:lambda:ca-central-1:111111111111:function:placeholder-create-account"
    apply_baseline  = "arn:aws:lambda:ca-central-1:111111111111:function:placeholder-apply-baseline"
    assign_identity = "arn:aws:lambda:ca-central-1:111111111111:function:placeholder-assign-identity"
    create_budget   = "arn:aws:lambda:ca-central-1:111111111111:function:placeholder-create-budget"
    mark_completed  = "arn:aws:lambda:ca-central-1:111111111111:function:placeholder-mark-completed"
    notify_owner    = "arn:aws:lambda:ca-central-1:111111111111:function:placeholder-notify-owner"
    handle_failure  = "arn:aws:lambda:ca-central-1:111111111111:function:placeholder-handle-failure"
  }

  validation {
    condition     = alltrue([for arn in values(var.task_function_arns) : can(regex("^arn:aws:lambda:", arn))])
    error_message = "Every task_function_arns value must be a Lambda ARN (arn:aws:lambda:...)."
  }
}

variable "alarm_sns_topic_arn" {
  description = "Optional SNS topic ARN for workflow-failure alarms. Empty disables alarm actions."
  type        = string
  default     = ""
}
