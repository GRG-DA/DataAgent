"""Materialize grant windows for legacy paid Pro orders (keyless migration).

Under the keyless model, entitlements follow the buyer's email. Legacy Pro
one-time purchases carry a 31-day grant window (``orders.grant_days``); the
grants engine already falls back to 31 days when the column is NULL, so this
script is a book-keeping backfill that records the window explicitly.

What it does (idempotent):
    UPDATE orders SET grant_days = 31
    WHERE plan = 'pro' AND status = 'paid' AND grant_days IS NULL

Usage:
    python scripts/migrate_keys_to_email_access.py           # dry run
    python scripts/migrate_keys_to_email_access.py --apply   # write
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from billing_server import store  # noqa: E402
from billing_server.grants import LEGACY_PRO_GRANT_DAYS  # noqa: E402

_WHERE = "plan = 'pro' AND status = 'paid' AND grant_days IS NULL"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="write changes (default: dry run)")
    args = parser.parse_args()

    print(f"target: {store._target()}")
    with store._lock, store._connect() as con:
        store._ensure_schema(con)
        rows = con.execute(f"SELECT order_id FROM orders WHERE {_WHERE}").fetchall()
        print(f"legacy paid pro orders missing grant_days: {len(rows)}")
        for row in rows[:10]:
            print(f"  {row['order_id']}")
        if len(rows) > 10:
            print(f"  … and {len(rows) - 10} more")

        if not args.apply:
            print("dry run — pass --apply to write")
            return 0
        if not rows:
            print("nothing to backfill")
            return 0

        con.execute(f"UPDATE orders SET grant_days = ? WHERE {_WHERE}",
                    [LEGACY_PRO_GRANT_DAYS])
        con.commit()
    print(f"backfilled {len(rows)} order(s) with grant_days={LEGACY_PRO_GRANT_DAYS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
