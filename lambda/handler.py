import json
import logging
import os
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

import boto3

LOG = logging.getLogger()
LOG.setLevel(logging.INFO)

AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")
COST_EXPLORER_REGION = os.environ.get("COST_EXPLORER_REGION", "us-east-1")
ANOMALY_THRESHOLD = float(os.environ.get("ANOMALY_THRESHOLD", "0.20"))
BASELINE_DAYS = int(os.environ.get("BASELINE_DAYS", "7"))
IDLE_LOOKBACK_DAYS = int(os.environ.get("IDLE_LOOKBACK_DAYS", "7"))
IDLE_CPU_THRESHOLD = float(os.environ.get("IDLE_CPU_THRESHOLD", "5"))
SLACK_PARAMETER_NAME = os.environ.get("SLACK_PARAMETER_NAME", "/finops/slack-webhook")
SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN", "")
TAG_KEY = os.environ.get("REVIEW_TAG_KEY", "FinOpsReview")
TAG_VALUE = os.environ.get("REVIEW_TAG_VALUE", "IdleCandidate")

ce = boto3.client("ce", region_name=COST_EXPLORER_REGION)
ec2 = boto3.client("ec2", region_name=AWS_REGION)
cloudwatch = boto3.client("cloudwatch", region_name=AWS_REGION)
ssm = boto3.client("ssm", region_name=AWS_REGION)
sns = boto3.client("sns", region_name=AWS_REGION)


def _date_range():
    today = datetime.now(timezone.utc).date()
    end = today
    start = end - timedelta(days=BASELINE_DAYS + 1)
    return start.isoformat(), end.isoformat()


def get_daily_costs():
    start, end = _date_range()
    response = ce.get_cost_and_usage(
        TimePeriod={"Start": start, "End": end},
        Granularity="DAILY",
        Metrics=["UnblendedCost"],
    )
    costs = {}
    for item in response.get("ResultsByTime", []):
        amount = float(item["Total"]["UnblendedCost"]["Amount"])
        costs[item["TimePeriod"]["Start"]] = amount
    return costs


def analyze_cost(costs):
    today = datetime.now(timezone.utc).date()
    yesterday = (today - timedelta(days=1)).isoformat()
    baseline_dates = [
        (today - timedelta(days=offset)).isoformat()
        for offset in range(2, BASELINE_DAYS + 2)
    ]
    baseline_values = [costs[d] for d in baseline_dates if d in costs]
    current = costs.get(yesterday)
    baseline = (sum(baseline_values) / len(baseline_values)) if baseline_values else None
    if current is None or baseline is None or baseline <= 0:
        return {
            "yesterday": yesterday,
            "current_spend": current,
            "baseline_daily_average": baseline,
            "change_percent": None,
            "is_anomaly": False,
            "note": "Insufficient completed daily cost data for comparison",
        }
    change = ((current - baseline) / baseline) * 100.0
    return {
        "yesterday": yesterday,
        "current_spend": current,
        "baseline_daily_average": baseline,
        "change_percent": round(change, 2),
        "is_anomaly": current > baseline * (1 + ANOMALY_THRESHOLD),
        "note": None,
    }


def _average_cpu(instance_id, end_time):
    start_time = end_time - timedelta(days=IDLE_LOOKBACK_DAYS)
    response = cloudwatch.get_metric_statistics(
        Namespace="AWS/EC2",
        MetricName="CPUUtilization",
        Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
        StartTime=start_time,
        EndTime=end_time,
        Period=3600,
        Statistics=["Average"],
    )
    points = response.get("Datapoints", [])
    if not points:
        return None
    return sum(p["Average"] for p in points) / len(points)


def find_idle_ec2():
    now = datetime.now(timezone.utc)
    candidates = []
    paginator = ec2.get_paginator("describe_instances")
    for page in paginator.paginate(
        Filters=[{"Name": "instance-state-name", "Values": ["running"]}]
    ):
        for reservation in page.get("Reservations", []):
            for instance in reservation.get("Instances", []):
                iid = instance["InstanceId"]
                avg_cpu = _average_cpu(iid, now)
                if avg_cpu is not None and avg_cpu < IDLE_CPU_THRESHOLD:
                    candidates.append({
                        "instance_id": iid,
                        "instance_type": instance.get("InstanceType", "unknown"),
                        "average_cpu_percent": round(avg_cpu, 2),
                        "recommendation": "Review workload and right-size or schedule stop after owner approval",
                    })
    return candidates


def find_unattached_volumes():
    result = []
    paginator = ec2.get_paginator("describe_volumes")
    for page in paginator.paginate(Filters=[{"Name": "status", "Values": ["available"]}]):
        for volume in page.get("Volumes", []):
            result.append({
                "volume_id": volume["VolumeId"],
                "size_gib": volume.get("Size"),
                "volume_type": volume.get("VolumeType"),
                "recommendation": "Confirm no snapshot/restore dependency, then review for deletion",
            })
    return result


def tag_review_candidates(idle_instances, unattached_volumes):
    timestamp = datetime.now(timezone.utc).isoformat()
    resources = [x["instance_id"] for x in idle_instances]
    resources.extend(x["volume_id"] for x in unattached_volumes)
    # EC2 CreateTags supports resource IDs for both instances and EBS volumes.
    for offset in range(0, len(resources), 1000):
        ec2.create_tags(
            Resources=resources[offset:offset + 1000],
            Tags=[
                {"Key": TAG_KEY, "Value": TAG_VALUE},
                {"Key": "FinOpsReviewAt", "Value": timestamp},
            ],
        )
    return len(resources)


def _slack_webhook():
    if not SLACK_PARAMETER_NAME:
        return None
    try:
        response = ssm.get_parameter(Name=SLACK_PARAMETER_NAME, WithDecryption=True)
        return response["Parameter"]["Value"]
    except ssm.exceptions.ParameterNotFound:
        LOG.warning("Slack webhook parameter %s not found; Slack alert skipped", SLACK_PARAMETER_NAME)
        return None


def build_slack_payload(cost, idle_instances, unattached_volumes, tagged_count):
    status = "COST ANOMALY" if cost["is_anomaly"] else "Daily FinOps summary"
    current = cost.get("current_spend")
    baseline = cost.get("baseline_daily_average")
    change = cost.get("change_percent")
    currency = os.environ.get("COST_CURRENCY", "USD")
    current_text = f"{current:,.2f} {currency}" if current is not None else "not available"
    baseline_text = f"{baseline:,.2f} {currency}" if baseline is not None else "not available"
    change_text = f"{change:+.2f}%" if change is not None else "not available"
    lines = [
        f"*{status}*",
        f"Yesterday spend: *{current_text}*",
        f"Trailing {BASELINE_DAYS}-day daily average: *{baseline_text}*",
        f"Change vs baseline: *{change_text}* (threshold {ANOMALY_THRESHOLD * 100:.0f}%)",
        f"Idle EC2 candidates: *{len(idle_instances)}*",
        f"Unattached EBS volumes: *{len(unattached_volumes)}*",
        f"Resources tagged for review: *{tagged_count}*",
        "_No resources were stopped, resized, or deleted. Review candidates with service owners before remediation._",
    ]
    return {
        "text": status,
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": status}},
            {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}},
        ],
    }


def send_slack(payload):
    webhook = _slack_webhook()
    if not webhook:
        return False
    data = json.dumps(payload).encode("utf-8")
    request = Request(webhook, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=8) as response:
            LOG.info("Slack webhook returned HTTP %s", response.status)
            return 200 <= response.status < 300
    except (HTTPError, URLError, TimeoutError) as exc:
        LOG.exception("Slack notification failed: %s", exc)
        return False


def publish_sns(subject, message):
    if not SNS_TOPIC_ARN:
        return False
    sns.publish(TopicArn=SNS_TOPIC_ARN, Subject=subject[:100], Message=message)
    return True


def lambda_handler(event, context):
    LOG.info("Starting daily FinOps scan")
    costs = get_daily_costs()
    cost_analysis = analyze_cost(costs)
    idle_instances = find_idle_ec2()
    unattached_volumes = find_unattached_volumes()
    tagged_count = tag_review_candidates(idle_instances, unattached_volumes)

    report = {
        "cost": cost_analysis,
        "idle_ec2": idle_instances,
        "unattached_ebs": unattached_volumes,
        "tagged_resource_count": tagged_count,
    }
    payload = build_slack_payload(cost_analysis, idle_instances, unattached_volumes, tagged_count)
    slack_sent = send_slack(payload)
    sns_sent = publish_sns(
        "AWS FinOps cost anomaly detected" if cost_analysis["is_anomaly"] else "AWS FinOps daily summary",
        json.dumps(report, indent=2),
    )
    result = {
        "status": "ok",
        "report": report,
        "notifications": {"slack_sent": slack_sent, "sns_sent": sns_sent},
    }
    LOG.info("FinOps scan completed: %s", json.dumps(result))
    return result
