"""Print the full event history of one application from the audit log.

    python scripts/trace_application.py app_5f1d7a0c9e2b [--table jae-dev-audit-log]

Read-only. Uses your normal AWS credentials and region (AWS_PROFILE, AWS_REGION, ...).
"""

import argparse
import json

import boto3
from boto3.dynamodb.conditions import Key


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("application_id")
    parser.add_argument("--table", default="jae-dev-audit-log")
    parser.add_argument("--region", default=None)
    parser.add_argument("--full", action="store_true", help="also print each event's payload")
    args = parser.parse_args()

    table = boto3.resource("dynamodb", region_name=args.region).Table(args.table)
    items, start = [], None
    while True:
        page = table.query(
            KeyConditionExpression=Key("correlation_id").eq(args.application_id),
            **({"ExclusiveStartKey": start} if start else {}),
        )
        items += page["Items"]
        start = page.get("LastEvaluatedKey")
        if not start:
            break

    if not items:
        print(f"No events for {args.application_id} in {args.table}.")
        return

    for item in items:
        print(f"{item['occurred_at']}  {item['event_type']:<22} {item['producer']:<20} {item['event_id']}")
        if args.full:
            print(json.dumps(json.loads(item["envelope"])["data"], indent=2))


if __name__ == "__main__":
    main()
