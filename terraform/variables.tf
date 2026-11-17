variable "aws_region" {
  type        = string
  description = "AWS region for Lambda, EC2 inventory and notifications"
  default     = "ap-south-1"
}
variable "notification_email" {
  type        = string
  description = "Optional email endpoint for SNS subscription"
  default     = ""
}
variable "slack_parameter_name" {
  type        = string
  description = "SSM SecureString parameter containing Slack webhook"
  default     = "/finops/slack-webhook"
}
variable "schedule_expression" {
  type        = string
  description = "EventBridge Scheduler schedule expression (UTC)"
  default     = "cron(0 2 * * ? *)"
}
variable "anomaly_threshold" {
  type        = number
  default     = 0.20
}
variable "baseline_days" {
  type        = number
  default     = 7
}
variable "idle_lookback_days" {
  type        = number
  default     = 7
}
variable "idle_cpu_threshold" {
  type        = number
  default     = 5
}
variable "cost_explorer_region" {
  type        = string
  default     = "us-east-1"
}
