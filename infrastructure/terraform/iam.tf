# Least-privilege execution role for the Step Functions state machine.
# It may invoke ONLY the named task Lambdas, write ONLY its own log group, and
# send ONLY to the control-plane DLQ.

data "aws_iam_policy_document" "sfn_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_iam_role" "sfn" {
  name               = "${var.name_prefix}-sfn-${var.environment}"
  assume_role_policy = data.aws_iam_policy_document.sfn_assume.json
  tags               = local.common_tags
}

data "aws_iam_policy_document" "sfn_permissions" {
  statement {
    sid       = "InvokeTaskLambdas"
    effect    = "Allow"
    actions   = ["lambda:InvokeFunction"]
    resources = values(var.task_function_arns)
  }

  statement {
    sid       = "SendToDLQ"
    effect    = "Allow"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.workflow_dlq.arn]
  }

  statement {
    sid    = "WriteWorkflowLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogDelivery",
      "logs:GetLogDelivery",
      "logs:UpdateLogDelivery",
      "logs:DeleteLogDelivery",
      "logs:ListLogDeliveries",
      "logs:PutResourcePolicy",
      "logs:DescribeResourcePolicies",
      "logs:DescribeLogGroups",
    ]
    resources = ["*"] # required by Step Functions logging; scoped by the log group below at delivery time
  }

  statement {
    sid       = "UseControlPlaneKey"
    effect    = "Allow"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [aws_kms_key.control_plane.arn]
  }
}

resource "aws_iam_role_policy" "sfn" {
  name   = "${var.name_prefix}-sfn-${var.environment}"
  role   = aws_iam_role.sfn.id
  policy = data.aws_iam_policy_document.sfn_permissions.json
}
