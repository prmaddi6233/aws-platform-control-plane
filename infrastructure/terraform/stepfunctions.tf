# Provisioning state machine. The definition is rendered from the versioned ASL
# file, substituting the caller-provided task Lambda ARNs. This stack does not
# create those Lambdas (see docs/production-boundaries.md).

resource "aws_cloudwatch_log_group" "workflow" {
  name              = "/aws/vendedlogs/states/${var.name_prefix}-provisioning-${var.environment}"
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.control_plane.arn
  tags              = local.common_tags
}

resource "aws_sfn_state_machine" "provisioning" {
  name     = "${var.name_prefix}-provisioning-${var.environment}"
  role_arn = aws_iam_role.sfn.arn
  type     = "STANDARD"

  definition = templatefile("${path.module}/../../statemachines/provisioning.asl.json", {
    CreateAccountFunction  = var.task_function_arns.create_account
    ApplyBaselineFunction  = var.task_function_arns.apply_baseline
    AssignIdentityFunction = var.task_function_arns.assign_identity
    CreateBudgetFunction   = var.task_function_arns.create_budget
    MarkCompletedFunction  = var.task_function_arns.mark_completed
    NotifyOwnerFunction    = var.task_function_arns.notify_owner
    HandleFailureFunction  = var.task_function_arns.handle_failure
  })

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.workflow.arn}:*"
    include_execution_data = false # request payloads may carry sensitive metadata
    level                  = "ERROR"
  }

  tracing_configuration {
    enabled = true
  }

  tags = local.common_tags
}
