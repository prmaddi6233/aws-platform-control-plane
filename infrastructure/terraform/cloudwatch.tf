# Alarm on any failed provisioning execution. Supports the reliability SLO
# (docs/slos.md): a failed execution consumes error budget and must page.

resource "aws_cloudwatch_metric_alarm" "provisioning_failures" {
  alarm_name          = "${var.name_prefix}-provisioning-failures-${var.environment}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ExecutionsFailed"
  namespace           = "AWS/States"
  period              = 300
  statistic           = "Sum"
  threshold           = 0
  treat_missing_data  = "notBreaching"
  alarm_description   = "A provisioning workflow execution failed; investigate per docs/failure-recovery.md."

  dimensions = {
    StateMachineArn = aws_sfn_state_machine.provisioning.arn
  }

  alarm_actions = var.alarm_sns_topic_arn == "" ? [] : [var.alarm_sns_topic_arn]
  ok_actions    = var.alarm_sns_topic_arn == "" ? [] : [var.alarm_sns_topic_arn]

  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "dlq_not_empty" {
  alarm_name          = "${var.name_prefix}-dlq-not-empty-${var.environment}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 300
  statistic           = "Maximum"
  threshold           = 0
  treat_missing_data  = "notBreaching"
  alarm_description   = "Messages present in the workflow DLQ; a redrive may be required."

  dimensions = {
    QueueName = aws_sqs_queue.workflow_dlq.name
  }

  alarm_actions = var.alarm_sns_topic_arn == "" ? [] : [var.alarm_sns_topic_arn]

  tags = local.common_tags
}
