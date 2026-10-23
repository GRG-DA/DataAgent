"""DEV ONLY — billing server + mock Razorpay checkout for local walkthroughs.

Attaches dev routes to the REAL billing app (all /create-order, /verify,
/success, /v1/entitlements, /razorpay/webhook behavior is the production
code). Only the Razorpay popup itself is mocked:

  real: checkout.js popup → handler → POST /verify (HMAC computed by Razorpay)
  dev:  /dev storefront → POST /create-order → /dev/checkout page → POST /dev/pay
        (computes the same HMAC from RAZORPAY_KEY_SECRET) → POST /verify

Never deploy this file. Do not run with production secrets.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

import uvicorn
from fastapi import HTTPException
from fastapi.responses import HTMLResponse

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Local defaults BEFORE importing billing modules (env → config constants).
os.environ.setdefault("RAZORPAY_KEY_ID", "rzp_test_devmode")
os.environ.setdefault("RAZORPAY_KEY_SECRET", "devmode_secret")
os.environ.setdefault("RAZORPAY_WEBHOOK_SECRET", "devmode_whsec")
os.environ.setdefault("RAZORPAY_PLAN_ID_PRO", "plan_dev_pro_monthly")
os.environ.setdefault("ENTITLEMENTS_SYNC_SECRET", "devmode_sync_secret")
os.environ.setdefault("BILLING_EMAIL_FROM", "DataAgent Dev <dev@localhost>")
# DEV ONLY: token for the support view at /admin/registry?token=devmode_admin_token
os.environ.setdefault("BILLING_ADMIN_TOKEN", "devmode_admin_token")

# HARD GUARD: this file must never run against live Razorpay credentials.
# `setdefault` means a real RAZORPAY_KEY_ID/SECRET in the environment wins
# over the dev defaults — so with live keys present, /dev/pay would mint
# *valid* checkout HMACs for arbitrary order ids, and the default admin
# token would expose /admin/registry. Refuse to start instead.
_live_key = (os.environ.get("RAZORPAY_KEY_ID") or "").strip()
if _live_key.startswith("rzp_live_"):
    raise SystemExit(
        "Refusing to start: billing_server/dev_server.py was given a LIVE "
        f"Razorpay key id ({_live_key[:12]}…). The dev server stubs the "
        "Razorpay API and defaults the admin token to a public constant. "
        "Unset RAZORPAY_KEY_ID/SECRET or use test keys (rzp_test_…)."
    )

import billing_server.config as cfg  # noqa: E402
import billing_server.main as billing_main  # noqa: E402
from billing_server.main import CreateOrderRequest, create_order  # noqa: E402

# The setdefaults above only change the environment. If billing_server.config
# was ALREADY imported before this file (test-suite import order), its module
# constants never saw the dev defaults — a verifier keyed with an empty
# secret fails closed and the whole dev walkthrough dead-ends. Refresh the
# constants from the environment; non-empty values are never clobbered.
for _attr in ("RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", "RAZORPAY_WEBHOOK_SECRET",
              "RAZORPAY_PLAN_ID_PRO", "ENTITLEMENTS_SYNC_SECRET",
              "BILLING_EMAIL_FROM", "BILLING_ADMIN_TOKEN"):
    _val = (os.environ.get(_attr) or "").strip()
    if _val and not (getattr(cfg, _attr, "") or "").strip():
        setattr(cfg, _attr, _val)


# DEV: stub the Razorpay Orders API (the only thing real test keys replace).
# Everything downstream — /create-order, /verify HMAC, minting, store — stays
# the production code path.
def _fake_razorpay_create_order(amount, currency, receipt, notes):
    return {
        "id": "order_dev_" + hashlib.sha256(receipt.encode()).hexdigest()[:14],
        "amount": amount,
        "currency": currency,
        "status": "created",
        "notes": notes,
    }


billing_main.razorpay_client.create_order = _fake_razorpay_create_order


# DEV: stub the Razorpay Subscriptions API (monthly Pro) the same way. The
# real /create-subscription + /verify-subscription + webhook code paths stay
# production code; only the provider's server side is faked.
def _fake_razorpay_create_subscription(plan_id, notes, total_count=None):
    seed = f"{plan_id}:{(notes or {}).get('email', '')}"
    return {
        "id": "sub_dev_" + hashlib.sha256(seed.encode()).hexdigest()[:14],
        "plan_id": plan_id,
        "status": "created",
        "notes": notes,
    }


def _fake_razorpay_fetch_subscription(subscription_id):
    # Simulates a live subscription mid-period (30 days of Pro remaining).
    return {
        "id": subscription_id,
        "status": "active",
        "current_end": int(time.time()) + 30 * 86400,
    }


billing_main.razorpay_client.create_subscription = _fake_razorpay_create_subscription
billing_main.razorpay_client.fetch_subscription = _fake_razorpay_fetch_subscription

app = billing_main.app


# ── dev: mock payment — does exactly what Razorpay's server would do ──


class DevPayBody(dict):
    pass


@app.post("/dev/pay", openapi_extra=None)
async def dev_pay(req: dict):
    order_id = str(req.get("order_id", ""))
    if not order_id.startswith("order_"):
        raise HTTPException(status_code=400, detail="bad order id")
    payment_id = "pay_dev_" + hashlib.sha256(order_id.encode()).hexdigest()[:14]
    signature = hmac.new(cfg.RAZORPAY_KEY_SECRET.encode(),
                         f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()
    return {"payment_id": payment_id, "signature": signature}


@app.post("/dev/sub-pay", openapi_extra=None)
async def dev_sub_pay(req: dict):
    subscription_id = str(req.get("subscription_id", ""))
    if not subscription_id.startswith("sub_"):
        raise HTTPException(status_code=400, detail="bad subscription id")
    payment_id = "pay_dev_" + hashlib.sha256(subscription_id.encode()).hexdigest()[:14]
    signature = hmac.new(cfg.RAZORPAY_KEY_SECRET.encode(),
                         f"{payment_id}|{subscription_id}".encode(), hashlib.sha256).hexdigest()
    return {"payment_id": payment_id, "signature": signature}


# Replace the real /create-order route (FastAPI matches the FIRST registered
# route, so decorating again would be dead code). The wrapper keeps the real
# behavior and adds checkout_url for the mock popup. The URL is absolute to
# BILLING_PUBLIC_URL when configured, so the storefront (a different dev
# origin) still lands on the mock page; relative stays dev-server-local.
_real_create_order = create_order


def _dev_base() -> str:
    # Read at CALL time: tests monkeypatch cfg per-test; a module-level
    # constant bakes whatever the first import in the process saw.
    return (cfg.BILLING_PUBLIC_URL or "").rstrip("/")


async def _create_order_dev(req: dict):
    parsed = CreateOrderRequest(**req)
    out = await _real_create_order(parsed)
    qs = urlencode({"order_id": out["order_id"], "amount": out["amount"],
                    "email": parsed.email, "plan": parsed.plan})
    out["checkout_url"] = f"{_dev_base()}/dev/checkout?{qs}"
    return out


app.router.routes = [
    r for r in app.router.routes
    if not (getattr(r, "path", "") == "/create-order" and "POST" in getattr(r, "methods", set()))
]
app.add_api_route("/create-order", _create_order_dev, methods=["POST"])


# Same trick for /create-subscription (monthly Pro → mock subscription popup).
_real_create_subscription = billing_main.create_subscription


async def _create_subscription_dev(req: dict):
    parsed = billing_main.CreateSubscriptionRequest(**req)
    out = await _real_create_subscription(parsed)
    qs = urlencode({"subscription_id": out["subscription_id"], "amount": out["amount"],
                    "email": parsed.email})
    out["checkout_url"] = f"{_dev_base()}/dev/sub-checkout?{qs}"
    return out


app.router.routes = [
    r for r in app.router.routes
    if not (getattr(r, "path", "") == "/create-subscription"
            and "POST" in getattr(r, "methods", set()))
]
app.add_api_route("/create-subscription", _create_subscription_dev, methods=["POST"])


@app.get("/dev/checkout", response_class=HTMLResponse)
async def dev_checkout(order_id: str = "", amount: int = 0, email: str = "", plan: str = ""):
    if not order_id.startswith("order_"):
        raise HTTPException(status_code=400, detail="bad order id")
    rupees = amount / 100
    esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace('"', "&quot;")  # noqa: E731
    return HTMLResponse(f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Razorpay Checkout (DEV MOCK)</title>
<style>
 body{{font-family:Segoe UI,Arial,sans-serif;background:#0b1c3d;color:#e6ecf7;margin:0;
      display:grid;place-items:center;min-height:100vh}}
 .rzp{{background:#12305e;border-radius:14px;padding:28px;width:min(420px,92vw);
      box-shadow:0 18px 50px rgba(0,0,0,.5)}}
 h3{{margin:0 0 4px}} .sub{{color:#9db3d9;font-size:13px;margin-bottom:8px}}
 .pay{{background:#3395ff;color:#fff;border:none;border-radius:9px;padding:12px 22px;
      width:100%;font-size:15px;font-weight:600;cursor:pointer;margin-top:10px}}
 .pay:hover{{background:#1f7fe6}}
 .methods{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin:14px 0 4px;
      font-size:12px;text-align:center;color:#9db3d9}}
 .methods span{{border:1px solid #2a4a80;border-radius:8px;padding:8px 4px}}
 .banner{{background:#f59e0b;color:#3b2c00;font-size:11px;font-weight:700;letter-spacing:.5px;
      padding:6px 10px;border-radius:7px;display:inline-block;margin-bottom:12px}}
</style></head><body>
<div class="rzp">
  <span class="banner">⚠ DEV MOCK — NOT A REAL PAYMENT</span>
  <h3>DataAgent</h3>
  <div class="sub">{esc(plan)} · {esc(email)}</div>
  <div style="font-size:30px;font-weight:800;margin:6px 0 2px">₹{rupees:,.0f}</div>
  <div class="sub">order {esc(order_id)}</div>
  <div class="methods"><span>UPI</span><span>Card</span><span>Netbanking</span></div>
  <button class="pay" onclick="pay()">Pay ₹{rupees:,.0f} (simulated success)</button>
</div>
<script>
function pay() {{
  fetch('/dev/pay', {{method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{order_id: '{esc(order_id)}'}})}})
    .then(r => r.json())
    .then(v => fetch('/verify', {{method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{order_id: '{esc(order_id)}', payment_id: v.payment_id,
                            signature: v.signature}})}}))
    .then(r => r.json())
    .then(v => {{ if (v.ok) location = '/success?order_id={esc(order_id)}';
                 else alert('verify failed: ' + JSON.stringify(v)); }})
    .catch(e => alert('error: ' + e));
}}
</script></body></html>""")


@app.get("/dev/sub-checkout", response_class=HTMLResponse)
async def dev_sub_checkout(subscription_id: str = "", amount: int = 0,
                           email: str = ""):
    if not subscription_id.startswith("sub_"):
        raise HTTPException(status_code=400, detail="bad subscription id")
    rupees = amount / 100
    esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace('"', "&quot;")  # noqa: E731
    return HTMLResponse(f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Razorpay Subscription (DEV MOCK)</title>
<style>
 body{{font-family:Segoe UI,Arial,sans-serif;background:#0b1c3d;color:#e6ecf7;margin:0;
      display:grid;place-items:center;min-height:100vh}}
 .rzp{{background:#12305e;border-radius:14px;padding:28px;width:min(420px,92vw);
      box-shadow:0 18px 50px rgba(0,0,0,.5)}}
 h3{{margin:0 0 4px}} .sub{{color:#9db3d9;font-size:13px;margin-bottom:8px}}
 .pay{{background:#3395ff;color:#fff;border:none;border-radius:9px;padding:12px 22px;
      width:100%;font-size:15px;font-weight:600;cursor:pointer;margin-top:10px}}
 .pay:hover{{background:#1f7fe6}}
 .methods{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin:14px 0 4px;
      font-size:12px;text-align:center;color:#9db3d9}}
 .methods span{{border:1px solid #2a4a80;border-radius:8px;padding:8px 4px}}
 .banner{{background:#f59e0b;color:#3b2c00;font-size:11px;font-weight:700;letter-spacing:.5px;
      padding:6px 10px;border-radius:7px;display:inline-block;margin-bottom:12px}}
</style></head><body>
<div class="rzp">
  <span class="banner">⚠ DEV MOCK — NOT A REAL PAYMENT</span>
  <h3>DataAgent Pro (monthly)</h3>
  <div class="sub">{esc(email)} · auto-renews monthly</div>
  <div style="font-size:30px;font-weight:800;margin:6px 0 2px">₹{rupees:,.0f}<span style="font-size:14px;color:#9db3d9">/mo</span></div>
  <div class="sub">subscription {esc(subscription_id)}</div>
  <div class="methods"><span>UPI</span><span>Card</span><span>Netbanking</span></div>
  <button class="pay" onclick="pay()">Authorize ₹{rupees:,.0f}/mo (simulated success)</button>
</div>
<script>
function pay() {{
  fetch('/dev/sub-pay', {{method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{subscription_id: '{esc(subscription_id)}'}})}})
    .then(r => r.json())
    .then(v => fetch('/verify-subscription', {{method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{subscription_id: '{esc(subscription_id)}', payment_id: v.payment_id,
                            signature: v.signature}})}}))
    .then(r => r.json())
    .then(v => {{ if (v.ok) location = '/success?subscription_id={esc(subscription_id)}';
                 else alert('verify failed: ' + JSON.stringify(v)); }})
    .catch(e => alert('error: ' + e));
}}
</script></body></html>""")


# ── dev: serve the storefront so the walkthrough runs on one origin ──


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
@app.get("/billing.html", response_class=HTMLResponse, include_in_schema=False)
async def index():
    return HTMLResponse((ROOT / "website" / "billing.html").read_text(encoding="utf-8"))


_MEDIA = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
          ".html": "text/html; charset=utf-8"}


@app.get("/js/{name}", include_in_schema=False)
@app.get("/css/{name}", include_in_schema=False)
async def static_files(name: str):
    for sub in ("js", "css"):
        p = ROOT / "website" / sub / name
        if p.exists():
            return HTMLResponse(p.read_text(encoding="utf-8"),
                                media_type=_MEDIA.get(p.suffix, "text/plain"))
    raise HTTPException(status_code=404)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8090
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
