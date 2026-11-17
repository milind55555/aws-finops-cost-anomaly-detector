# Evidence checklist

Capture real results from your AWS account and store them here.

Recommended evidence:
- EventBridge Scheduler showing the daily schedule enabled.
- Lambda CloudWatch log showing Cost Explorer results and analysis.
- Slack message received for a test anomaly.
- EC2 instance tagged with `FinOpsReview=IdleCandidate`.
- Unattached EBS volume tagged for review.
- Completed before/after cost comparison with exact dates and currency.

Do not commit Slack webhook URLs, account secrets, or fabricated screenshots.
