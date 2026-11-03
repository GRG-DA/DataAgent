"""
Provision a clean probe account with a small dataset, for the AI feature
tests. Idempotent: reuses an existing account when it still has quota.

Usage:  python scripts/ai_probe_setup.py
Prints the username it settled on, so the probe/benchmark scripts can use it.
"""
from __future__ import annotations

import csv
import io
import json
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover
    pass

BASE = "http://127.0.0.1:8002"
STATE = Path(__file__).resolve().parent / ".ai_probe_user.json"
TABLE = "ai_probe_sales"

ROWS = [
    {"order_id": 1, "region": "North", "product": "Widget", "amount": 120.50, "qty": 3, "status": "paid", "ordered_on": "2026-01-05"},
    {"order_id": 2, "region": "South", "product": "Gadget", "amount": 89.00, "qty": 1, "status": "paid", "ordered_on": "2026-01-09"},
    {"order_id": 3, "region": "North", "product": "Gadget", "amount": 240.75, "qty": 4, "status": "pending", "ordered_on": "2026-01-14"},
    {"order_id": 4, "region": "East", "product": "Widget", "amount": 15.25, "qty": 1, "status": "refunded", "ordered_on": "2026-02-02"},
    {"order_id": 5, "region": "West", "product": "Doohickey", "amount": 310.00, "qty": 2, "status": "paid", "ordered_on": "2026-02-08"},
    {"order_id": 6, "region": "South", "product": "Widget", "amount": 75.00, "qty": 1, "status": "pending", "ordered_on": "2026-02-17"},
    {"order_id": 7, "region": "East", "product": "Gadget", "amount": 199.99, "qty": 3, "status": "paid", "ordered_on": "2026-03-01"},
    {"order_id": 8, "region": "North", "product": "Doohickey", "amount": 45.10, "qty": 1, "status": "paid", "ordered_on": "2026-03-06"},
    {"order_id": 9, "region": "West", "product": "Widget", "amount": 130.00, "qty": 2, "status": "refunded", "ordered_on": "2026-03-21"},
    {"order_id": 10, "region": "South", "product": "Gadget", "amount": 64.80, "qty": 1, "status": "paid", "ordered_on": "2026-04-02"},
    {"order_id": 11, "region": "East", "product": "Widget", "amount": 88.00, "qty": 2, "status": "paid", "ordered_on": "2026-04-11"},
    {"order_id": 12, "region": "North", "product": "Gadget", "amount": 410.60, "qty": 5, "status": "pending", "ordered_on": "2026-04-19"},
]


def request(method, path, body=None, token=None, headers=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if raw is None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            payload = r.read()
            try:
                return r.status, json.loads(payload)
            except json.JSONDecodeError:
                return r.status, payload.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        payload = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(payload)
        except json.JSONDecodeError:
            return e.code, payload
    except Exception as e:  # noqa: BLE001
        return 0, str(e)[:200]


def register(username, password):
    return request("POST", "/auth/register",
                    {"username": username, "password": password})


def login(username, password):
    return request("POST", "/auth/login",
                    {"username": username, "password": password})


def has_table(token):
    status, body = request("GET", "/tables", token=token)
    if status != 200 or not isinstance(body, dict):
        return False
    return TABLE in (body.get("tables") or {})


def upload_and_load(token):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(ROWS[0].keys()))
    writer.writeheader()
    writer.writerows(ROWS)
    payload = buf.getvalue().encode()

    boundary = "----probe" + uuid.uuid4().hex
    parts = [
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="file"; filename="ai_probe_sales.csv"\r\n',
        b"Content-Type: text/csv\r\n\r\n",
        payload,
        f"\r\n--{boundary}--\r\n".encode(),
    ]
    status, body = request("POST", "/upload", raw=b"".join(parts), token=token,
                           headers={"Content-Type":
                                    f"multipart/form-data; boundary={boundary}"})
    if status != 200:
        return False, f"upload failed: {status} {body}"

    status, body = request("POST", "/load", token=token, body={
        "filename": "ai_probe_sales.csv", "sheet": "default",
        "table_name": TABLE,
    })
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        return False, f"load failed: {status} {body}"
    return True, body.get("message", "")


def quota_left(token):
    """Ask the app how many AI requests this user has left today."""
    for path in ("/billing/usage", "/usage", "/billing/me/usage", "/billing/quota"):
        status, body = request("GET", path, token=token)
        if status == 200:
            blob = json.dumps(body, default=str).lower()
            for key in ("ai_requests", "ai_request"):
                m = re.search(rf'"{key}"\s*:\s*(\d+)', blob)
                if m:
                    return int(m.group(1))
    return None


def ai_requests_used(token):
    """The AI requests the user has consumed today (as reported by the app)."""
    for path in ("/billing/usage", "/usage", "/billing/me/usage", "/billing/quota"):
        status, body = request("GET", path, token=token)
        if status == 200:
            blob = json.dumps(body, default=str).lower()
            m = re.search(r'"(?:used|ai_requests_used|ai_used)"\s*:\s*(\d+)', blob)
            if m:
                return int(m.group(1))
    return None


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--fresh", action="store_true",
                    help="always create a new account (the free plan's daily "
                         "AI quota is small, and the probe burns it)")
    args = ap.parse_args()
    password = "UiTest!2345x"
    needed = 45  # the probe + benchmark burn ~40 AI requests between them

    # 1. Reuse a stored account when it still works AND has quota left.
    if STATE.exists() and not args.fresh:
        try:
            saved = json.loads(STATE.read_text(encoding="utf-8"))
            status, body = login(saved["username"], saved.get("password", password))
            if status == 200 and isinstance(body, dict) and body.get("token"):
                token = body["token"]
                used = ai_requests_used(token)
                if used is None or used < needed:
                    if has_table(token):
                        print(json.dumps({"username": saved["username"],
                                          "password": password,
                                          "table": TABLE,
                                          "reused": True}))
                        return 0
                    ok, msg = upload_and_load(token)
                    print(json.dumps({"username": saved["username"],
                                      "password": password, "table": TABLE,
                                      "provisioned": ok, "note": msg}))
                    return 0 if ok else 1
        except Exception:  # noqa: BLE001
            pass

    # 2. Fresh account.
    for attempt in range(4):
        username = "aiprobe" + uuid.uuid4().hex[:8]
        status, body = register(username, password)
        if status != 200:
            print(f"register failed: {status} {body}")
            time.sleep(2)
            continue
        status, body = login(username, password)
        if status != 200 or not isinstance(body, dict) or not body.get("token"):
            print(f"login after register failed: {status} {body}")
            time.sleep(2)
            continue
        token = body["token"]
        ok, msg = upload_and_load(token)
        STATE.write_text(json.dumps({"username": username, "password": password}),
                         encoding="utf-8")
        print(json.dumps({"username": username, "password": password,
                          "table": TABLE, "provisioned": ok, "note": msg}))
        return 0 if ok else 1

    print("could not provision a probe account")
    return 1


if __name__ == "__main__":
    sys.exit(main())
