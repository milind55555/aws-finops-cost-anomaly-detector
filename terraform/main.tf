data "archive_file" "lambda_package" {
  type        = "zip"
  source_file = "${path.module}/../lambda/handler.py"
  output_path = "${path.module}/lambda_package.zip"
}

resource "aws_sns_topic" "finops_alerts" {
  name = "aws-finops-cost-anomaly-alerts"
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.notification_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.finops_alerts.arn
  protocol  = "email"
  endpoint  = var.notification_email
}

resource "aws_iam_role" "lambda_role" {
  name = "aws-finops-cost-anomaly-lambda-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "basic_logs" {
  role       = aws_iam_role.lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "finops_permissions" {
  name = "finops-read-cost-tag-notify"
  role = aws_iam_role.lambda_role.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "CostExplorerRead"
        Effect = "Allow"
        Action = ["ce:GetCostAndUsage"]
        Resource = "*"
      },
      {
        Sid    = "EC2InventoryAndReviewTags"
        Effect = "Allow"
        Action = [
          "ec2:DescribeInstances",
          "ec2:DescribeVolumes",
          "ec2:CreateTags"
        ]
        Resource = "*"
      },
      {
        Sid    = "CloudWatchRead"
        Effect = "Allow"
        Action = ["cloudwatch:GetMetricStatistics"]
        Resource = "*"
      },
      {
        Sid    = "PublishNotifications"
        Effect = "Allow"
        Action = ["sns:Publish"]
        Resource = [aws_sns_topic.finops_alerts.arn]
      },
      {
        Sid    = "ReadSlackWebhook"
        Effect = "Allow"
        Action = ["ssm:GetParameter"]
        Resource = "arn:aws:ssm:${var.aws_region}:*:parameter${var.slack_parameter_name}"
      }
    ]
  })
}

resource "aws_cloudwatch_log_group" "lambda_logs" {
  name              = "/aws/lambda/aws-finops-cost-anomaly-detector"
  retention_in_days = 30
}

resource "aws_lambda_function" "finops" {
  function_name    = "aws-finops-cost-anomaly-detector"
  role             = aws_iam_role.lambda_role.arn
  runtime          = "python3.12"
  handler          = "handler.lambda_handler"
  filename         = data.archive_file.lambda_package.output_path
  source_code_hash = data.archive_file.lambda_package.output_base64sha256
  timeout          = 300
  memory_size      = 256

  environment {
    variables = {
      COST_EXPLORER_REGION   = var.cost_explorer_region
      ANOMALY_THRESHOLD      = tostring(var.anomaly_threshold)
      BASELINE_DAYS          = tostring(var.baseline_days)
      IDLE_LOOKBACK_DAYS     = tostring(var.idle_lookback_days)
      IDLE_CPU_THRESHOLD     = tostring(var.idle_cpu_threshold)
      SLACK_PARAMETER_NAME   = var.slack_parameter_name
      SNS_TOPIC_ARN          = aws_sns_topic.finops_alerts.arn
      COST_CURRENCY          = "USD"
    }
  }

  depends_on = [
    aws_iam_role_policy_attachment.basic_logs,
    aws_iam_role_policy.finops_permissions,
    aws_cloudwatch_log_group.lambda_logs
  ]
}

resource "aws_iam_role" "scheduler_role" {
  name = "aws-finops-eventbridge-scheduler-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
      Action = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "scheduler_invoke" {
  name = "invoke-finops-lambda"
  role = aws_iam_role.scheduler_role.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["lambda:InvokeFunction"]
      Resource = aws_lambda_function.finops.arn
    }]
  })
}

resource "aws_scheduler_schedule" "daily" {
  name                         = "aws-finops-daily-cost-check"
  description                  = "Daily AWS cost anomaly and idle resource review"
  schedule_expression          = var.schedule_expression
  schedule_expression_timezone = "UTC"
  flexible_time_window {
    mode = "OFF"
  }
  target {
    arn      = aws_lambda_function.finops.arn
    role_arn = aws_iam_role.scheduler_role.arn
    input    = jsonencode({ source = "eventbridge-scheduler" })
    retry_policy {
      maximum_event_age_in_seconds = 3600
      maximum_retry_attempts        = 2
    }
  }
}

output "lambda_function_name" {
  value = aws_lambda_function.finops.function_name
}
output "sns_topic_arn" {
  value = aws_sns_topic.finops_alerts.arn
}
output "schedule_name" {
  value = aws_scheduler_schedule.daily.name
}
