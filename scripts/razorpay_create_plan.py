"""Create the Razorpay monthly plan for DataAgent Pro (one-time setup).

The plan id is per Razorpay account (test mode has its own ids). Set the
printed id as ``RAZORPAY_PLAN_ID_PRO`` in the billing server environment.

Usage:
    set RAZORPAY_KEY_ID=... && set RAZORPAY_KEY_SECRET=...    (Windows)
    python scripts/razorpay_create_plan.py                    # plan defaults
    python scripts/razorpay_create_plan.py --amount 49900 --name "DataAgent Pro"

Rerunning creates a new plan each time — subscriptions created against an
older plan id keep working, but new checkouts should use the latest id.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import billing_server.config as cfg  # noqa: E402
from billing_server.razorpay_client import RazorpayError, create_plan  # noqa: E402


def main() -> int:
    pro = cfg.PLANS.get("pro") or {}
    parser = argparse.ArgumentParser(description="Create the DataAgent Pro monthly plan")
    parser.add_argument("--amount", type=int, default=int(pro.get("amount") or 49900),
                        help="amount in paise (default: %(default)s)")
    parser.add_argument("--name", default=str(pro.get("label") or "DataAgent Pro"),
                        help="plan name shown at checkout")
    parser.add_argument("--interval", type=int, default=1,
                        help="billing interval count (default: %(default)s)")
    args = parser.parse_args()

    if not cfg.RAZORPAY_KEY_ID or not cfg.RAZORPAY_KEY_SECRET:
        print("error: set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET first", file=sys.stderr)
        return 1

    try:
        plan = create_plan(args.name, args.amount, period="monthly", interval=args.interval)
    except RazorpayError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    plan_id = plan.get("id", "")
    item = plan.get("item") or {}
    print(f"plan created: {plan_id}")
    print(f"  name:   {item.get('name', args.name)}")
    print(f"  amount: {item.get('amount', args.amount)} paise / month")
    print()
    print("Set this in the billing server environment:")
    print(f"  RAZORPAY_PLAN_ID_PRO={plan_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
