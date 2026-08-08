"""Bedrock usage reporting script.

Queries CloudWatch for near-real-time token/invocation metrics
and AWS Cost Explorer for billed costs.

Usage:
  python scripts/bedrock_usage.py --profile intelligence-dev --hours 24
  python scripts/bedrock_usage.py --profile intelligence-dev --days 7
  python scripts/bedrock_usage.py --profile intelligence-dev --cost --month-to-date
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

import boto3

OPUS_MODELS = [
    "us.anthropic.claude-opus-4-6-v1",
    "us.anthropic.claude-opus-4-5-20251101-v1:0",
    "us.anthropic.claude-opus-4-1-20250805-v1:0",
    "us.anthropic.claude-opus-4-7",
    "us.anthropic.claude-opus-4-8",
    "us.anthropic.claude-opus-5",
]

TOKEN_METRICS = [
    "Invocations",
    "InputTokenCount",
    "OutputTokenCount",
    "CacheReadInputTokenCount",
    "CacheWriteInputTokenCount",
    "InvocationClientErrors",
    "InvocationServerErrors",
    "InvocationThrottles",
]


def get_usage(session, hours: int | None = None, days: int | None = None):
    """Query CloudWatch for Bedrock Opus usage metrics."""
    cw = session.client("cloudwatch")
    end = datetime.now(timezone.utc)

    if hours:
        start = end - timedelta(hours=hours)
        period_label = f"Last {hours} hours"
    elif days:
        start = end - timedelta(days=days)
        period_label = f"Last {days} days"
    else:
        start = end - timedelta(hours=24)
        period_label = "Last 24 hours"

    total_seconds = int((end - start).total_seconds())

    print(f"\n{'=' * 70}")
    print(f"  Bedrock Claude Opus Usage — {period_label}")
    print(f"  {start.strftime('%Y-%m-%d %H:%M UTC')} to {end.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'=' * 70}\n")

    header = f"{'Model':<45} {'Invoc':>7} {'Input':>10} {'Output':>10} {'CacheRd':>10} {'CacheWr':>10} {'Errs':>5} {'Throt':>5}"
    print(header)
    print("-" * len(header))

    totals = {m: 0 for m in TOKEN_METRICS}
    any_data = False

    for model_id in OPUS_MODELS:
        row = {}
        has_data = False
        for metric_name in TOKEN_METRICS:
            try:
                resp = cw.get_metric_statistics(
                    Namespace="AWS/Bedrock",
                    MetricName=metric_name,
                    Dimensions=[{"Name": "ModelId", "Value": model_id}],
                    StartTime=start,
                    EndTime=end,
                    Period=total_seconds,
                    Statistics=["Sum"],
                )
                val = sum(dp["Sum"] for dp in resp["Datapoints"])
                row[metric_name] = val
                totals[metric_name] += val
                if val > 0:
                    has_data = True
            except Exception:
                row[metric_name] = 0

        if has_data:
            any_data = True
            short_name = model_id.replace("us.anthropic.claude-", "")
            print(
                f"{short_name:<45} "
                f"{row['Invocations']:>7,.0f} "
                f"{row['InputTokenCount']:>10,.0f} "
                f"{row['OutputTokenCount']:>10,.0f} "
                f"{row['CacheReadInputTokenCount']:>10,.0f} "
                f"{row['CacheWriteInputTokenCount']:>10,.0f} "
                f"{row.get('InvocationClientErrors', 0):>5,.0f} "
                f"{row.get('InvocationThrottles', 0):>5,.0f}"
            )

    if not any_data:
        print("  (no data for this time period)")

    print("-" * len(header))
    print(
        f"{'TOTAL':<45} "
        f"{totals['Invocations']:>7,.0f} "
        f"{totals['InputTokenCount']:>10,.0f} "
        f"{totals['OutputTokenCount']:>10,.0f} "
        f"{totals['CacheReadInputTokenCount']:>10,.0f} "
        f"{totals['CacheWriteInputTokenCount']:>10,.0f} "
        f"{totals.get('InvocationClientErrors', 0):>5,.0f} "
        f"{totals.get('InvocationThrottles', 0):>5,.0f}"
    )
    print()
    print("Note: InputTokenCount excludes cache-read tokens. CacheReadInputTokenCount")
    print("and CacheWriteInputTokenCount are billed at different rates than standard input.")
    print()


def get_cost(session):
    """Query AWS Cost Explorer for month-to-date Bedrock costs."""
    ce = session.client("ce")
    now = datetime.now(timezone.utc)
    start_of_month = now.replace(day=1).strftime("%Y-%m-%d")
    today = now.strftime("%Y-%m-%d")

    print(f"\n{'=' * 70}")
    print(f"  AWS Bedrock Cost — Month to Date")
    print(f"  {start_of_month} to {today}")
    print(f"  (Cost Explorer data is delayed 24-48h from real-time)")
    print(f"{'=' * 70}\n")

    # Cost Explorer uses exclusive end date; use tomorrow
    end_date = (now + timedelta(days=1)).strftime("%Y-%m-%d")

    try:
        resp = ce.get_cost_and_usage(
            TimePeriod={"Start": start_of_month, "End": end_date},
            Granularity="DAILY",
            Metrics=["UnblendedCost", "UsageQuantity"],
            Filter={
                "Dimensions": {
                    "Key": "SERVICE",
                    "Values": ["Amazon Bedrock"],
                }
            },
            GroupBy=[{"Type": "DIMENSION", "Key": "USAGE_TYPE"}],
        )
    except Exception as e:
        print(f"  Cost Explorer error: {e}")
        print("  (This may happen if no billing data has propagated yet.)")
        return

    total_cost = 0.0
    opus_cost = 0.0
    rows = []

    for result in resp.get("ResultsByTime", []):
        date = result["TimePeriod"]["Start"]
        for group in result.get("Groups", []):
            usage_type = group["Keys"][0]
            cost = float(group["Metrics"]["UnblendedCost"]["Amount"])
            quantity = float(group["Metrics"]["UsageQuantity"]["Amount"])
            unit = group["Metrics"]["UnblendedCost"]["Unit"]
            if cost > 0 or quantity > 0:
                rows.append((date, usage_type, quantity, cost, unit))
                total_cost += cost
                if "opus" in usage_type.lower() or "claude" in usage_type.lower():
                    opus_cost += cost

    if not rows:
        print("  No Bedrock billing data available for this period.")
        print("  (Data may take 24-48 hours to appear in Cost Explorer.)")
        return

    header = f"{'Date':<12} {'Usage Type':<45} {'Quantity':>12} {'Cost':>10}"
    print(header)
    print("-" * len(header))

    for date, usage_type, quantity, cost, unit in sorted(rows):
        if cost > 0.001:
            print(f"{date:<12} {usage_type:<45} {quantity:>12,.2f} ${cost:>9.4f}")

    print("-" * len(header))
    print(f"\n  Total Amazon Bedrock MTD cost:  ${total_cost:.4f}")
    if opus_cost > 0:
        print(f"  Identifiable Opus-related cost: ${opus_cost:.4f}")
    else:
        print("  Note: Could not isolate Opus-specific cost from usage types.")
        print("  Inspect usage type names above for Claude/Opus identifiers.")
    print()


def main():
    parser = argparse.ArgumentParser(description="Bedrock Opus usage report")
    parser.add_argument("--profile", default="intelligence-dev", help="AWS profile")
    parser.add_argument("--region", default="us-east-1", help="AWS region")
    parser.add_argument("--hours", type=int, help="Report for last N hours")
    parser.add_argument("--days", type=int, help="Report for last N days")
    parser.add_argument("--cost", action="store_true", help="Show Cost Explorer data")
    parser.add_argument("--month-to-date", action="store_true", help="MTD cost report")
    args = parser.parse_args()

    session = boto3.Session(profile_name=args.profile, region_name=args.region)

    if args.cost or args.month_to_date:
        get_cost(session)
    elif args.hours:
        get_usage(session, hours=args.hours)
    elif args.days:
        get_usage(session, days=args.days)
    else:
        get_usage(session, hours=24)


if __name__ == "__main__":
    main()
