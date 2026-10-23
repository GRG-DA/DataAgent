# DataAgent billing server — deployment guide

How to take the billing server from this repo to a public HTTPS host so the
storefront (`website/`), the desktop app, and Razorpay can all talk to it.

Designed for: one small **x86_64** VPS (Ubuntu/Debian) + Caddy + systemd.
No Docker required on the VPS (see [Appendix A](#appendix-a--docker--heroku)
for the container/Heroku variant).

> Context: architecture decisions live in `docs/decisions/ADR-004-keyless-subscriptions.md`.
> **Never** deploy the ARM (Oracle Ampere) free tier — `libsql==0.1.11` has
> no aarch64 wheels. Use x86_64 (Oracle AMD micro, GCP e2-micro, Hetzner…) .

## Architecture (single origin)

```
                        ┌───────────────────────────────┐
 yourapp.com ──► Caddy ─┤ /create-*, /verify*, /v1/* …  ├─► uvicorn 127.0.0.1:8090
 (TLS auto)             │ everything else → website/    │        billing_server/main.py
                        └───────────────────────────────┘              │
 Razorpay webhooks ──────────────────────────────► /razorpay/webhook    ├─► Turso (orders/accounts/grants)
                                                                        ├─► Razorpay API (orders, subscriptions)
                                                                        └─► Resend (key-delivery email)
```

Everything is served from **one origin** (`https://yourdomain.com`) because the
billing server has no CORS middleware: storefront pages and API share the
domain, so the browser needs no cross-origin permission. A subdomain split
(`billing.yourdomain.com`) would require adding `CORSMiddleware` first.

## 0. Prerequisites

| Need | Notes |
|---|---|
| x86_64 VPS, Ubuntu 22.04/24.04 | 1 GB RAM is plenty. Open ports 80 + 443. |
| Domain | Namecheap or any registrar. Apex + `www` A records → VPS IP. |
| Razorpay account | Test mode works before KYC. Live needs KYC + Subscriptions capability. |
| Resend account (optional) | Only for key-delivery email. Empty key = email disabled, everything else works. |
| This repo | Uploads: `billing_server/`, `keygen_app/`, `website/`, `deploy/`. |

## 1. DNS (Namecheap)

Domain List → Manage → **Advanced DNS** → delete parking records, add:

| Type | Host | Value |
|---|---|---|
| A Record | `@` | `<VPS IP>` |
| A Record | `www` | `<VPS IP>` |

## 2. Razorpay (test mode first)

1. Dashboard → Settings → API Keys → generate **test** keys (`rzp_test_…`).
2. Create the monthly plan (run on your dev machine from the repo root —
   the app venv already has the dependencies):

   ```powershell
   $env:RAZORPAY_KEY_ID="rzp_test_…"; $env:RAZORPAY_KEY_SECRET="…"
   python scripts\razorpay_create_plan.py
   ```

   Copy the printed `RAZORPAY_PLAN_ID_PRO=plan_…` line — it goes into the
   env file in step 6. (Re-run later with live keys to make the live plan.)
3. Webhook (after the server is up in step 8 — the URL must exist):
   Dashboard → Settings → Webhooks → Add:
   - URL: `https://yourdomain.com/razorpay/webhook`
   - Secret: generate one (`openssl rand -hex 32`) — becomes `RAZORPAY_WEBHOOK_SECRET`
   - Events: `payment.captured`, `payment_link.paid`, `refund.processed`,
     and all `subscription.*` events (authenticated, activated, charged,
     pending, halted, cancelled, completed, paused, resumed, expired).
     These are exactly the events `billing_server/main.py` handles.

> The Razorpay rail above is **dormant** on data-agent.me (ADR-016) — it stays
> here because it is the kill-switch fallback when the live rail is down.

## 2b. FamGateway (the live rail, ADR-016)

UPI-only, one-time payments, no KYC, 5-minute atomic checkout sessions.
Because FamGateway cannot auto-renew, **Pro = a 31-day window per payment**
(renew by paying again; `grants.py` folds the longest live window).

1. Merchant account on famgateway.in → copy the **API key**.
2. Environment (`/etc/dataagent/billing.env`):
   - `PAYMENTS_PROVIDER=famgateway`
   - `FAMGATEWAY_API_KEY=<api key>`
   - `BILLING_PUBLIC_URL=https://yourdomain.com`
3. After the server is up (step 8), register the webhook in the FamGateway
   dashboard:
   - webhook_url: `https://yourdomain.com/famgateway/webhook`
   - Signature: HMAC-SHA256 over the raw body, keyed with the API key
     (no separate secret — `X-FamGateway-Signature`, constant-time compare).
4. Sanity check: `curl -s https://yourdomain.com/v1/payments-info` must show
   `"provider":"famgateway"` — the storefront reads this and switches its
   copy ("Pay ₹499 for 31 days of Pro (UPI)").

Handling notes (mirrors the Razorpay webhook posture, `billing_server/main.py`):
signature → event/status allowlist → seen_event idempotency → order lookup +
provider check → underpay guard → UTR uniqueness across orders
(`store.resolve_utr_owner`) → defense-in-depth `GET /api/verify-order.php`
→ `mark_paid(order_id, utr|transaction_id)`. Unknown orders are ACKed with
`{"ok": true, "ignored": …}` so FamGateway stops retrying them.

## 3. VPS base setup

```bash
# as root over ssh
sudo adduser --system --group --home /opt/dataagent-billing dataagent
sudo apt update && sudo apt install -y python3-venv python3-pip
sudo ufw allow OpenSSH && sudo ufw allow 80,443/tcp && sudo ufw enable
```

Install Caddy (official apt repo):

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy
```

> **Oracle Cloud only:** the Ubuntu image ships an `iptables` REJECT rule that
> blocks 80/443 even after the ufw commands. Check with
> `sudo iptables -L INPUT --line-numbers -n`, then allow + persist:
> `sudo iptables -I INPUT 6 -p tcp -m multiport --dports 80,443 -j ACCEPT`
> `sudo netfilter-persistent save`
> Also open ports 80/443 in the OCI console **VPC security list**.

## 4. Upload the files

From the repo root on Windows (PowerShell):

```powershell
ssh root@SERVER_IP "mkdir -p /opt/dataagent-billing /srv/dataagent"
scp -r billing_server root@SERVER_IP:/opt/dataagent-billing/
scp -r keygen_app     root@SERVER_IP:/opt/dataagent-billing/
scp -r deploy         root@SERVER_IP:/opt/dataagent-billing/
scp -r website        root@SERVER_IP:/srv/dataagent/
```

Back on the server, remove local-only state (the private key never needs to
exist here if you use `LICENSE_PRIVATE_KEY` in the env file — step 6):

```bash
sudo rm -rf /opt/dataagent-billing/billing_server/{billing.sqlite3,keys,__pycache__} \
            /opt/dataagent-billing/keygen_app/__pycache__
```

## 5. Virtualenv + dependencies

```bash
cd /opt/dataagent-billing
sudo -u dataagent python3 -m venv .venv
sudo -u dataagent .venv/bin/pip install -r billing_server/requirements.txt
sudo chown -R dataagent:dataagent /opt/dataagent-billing
```

## 6. Environment file

```bash
sudo mkdir -p /etc/dataagent
sudo install -m 600 -o root -g root /opt/dataagent-billing/deploy/billing.env.example /etc/dataagent/billing.env
sudo nano /etc/dataagent/billing.env
```

Fill in (see comments inside the file):

| Variable | Value |
|---|---|
| `PAYMENTS_PROVIDER` | `famgateway` (live rail) / `razorpay` (dormant fallback) / `off` (kill-switch) |
| `FAMGATEWAY_API_KEY` | famgateway.in merchant API key (mode famgateway) |
| `BILLING_PUBLIC_URL` | `https://yourdomain.com` — builds the hosted-checkout redirect |
| `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` | test keys now, live after KYC (mode razorpay) |
| `RAZORPAY_PLAN_ID_PRO` | from step 2 |
| `RAZORPAY_WEBHOOK_SECRET` | the secret you set on the webhook |
| `ENTITLEMENTS_SYNC_SECRET` | `openssl rand -hex 32` — same value goes in the desktop app |
| `LICENSE_PRIVATE_KEY` | base64 of `billing_server/keys/ed25519_private.pem` (command in the file) |
| `BILLING_ADMIN_TOKEN` | `openssl rand -hex 24` |
| `TURSO_DATABASE_URL` / `TURSO_AUTH_TOKEN` | strongly recommended — survives rebuilds and avoids SQLite file locks |
| `RESEND_API_KEY` / `BILLING_EMAIL_FROM` | optional email delivery |

## 7. systemd service

```bash
sudo cp /opt/dataagent-billing/deploy/dataagent-billing.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now dataagent-billing
sudo systemctl status dataagent-billing
curl -s http://127.0.0.1:8090/healthz
```

`/healthz` is your readiness checklist — it reports each component
(`razorpay`, `webhook`, `license_key`, `sync`, `email`, `subscription_plan`).
`"ok": true` once the required three (razorpay, webhook, license_key) are set.

## 8. Caddy + TLS

```bash
sudo cp /opt/dataagent-billing/deploy/Caddyfile /etc/caddy/Caddyfile
sudo nano /etc/caddy/Caddyfile            # replace yourdomain.com (apex + www, one line)
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
curl -I https://yourdomain.com/healthz    # 200 + valid certificate
curl -I https://yourdomain.com/           # storefront loads
```

Then create the Razorpay webhook from step 2 and click **Send test webhook**
in the dashboard — the server should log a `200` (see
`journalctl -u dataagent-billing -f`).

## 9. Point the storefront and the app here

**Storefront** — `website/billing.html` (line ~185, commented example):

```html
window.BILLING_API_URL = 'https://yourdomain.com';
```

Same-origin absolute URL on purpose (the default `http://127.0.0.1:8090`
fallback in `website/js/billing.js` is for local dev only). Re-upload
`website/` after editing.

**Desktop app** — set in the app's environment (or `config/settings.yaml`
under `billing.sync_url`):

```
BILLING_SYNC_URL=https://yourdomain.com
BILLING_SYNC_SECRET=<same value as ENTITLEMENTS_SYNC_SECRET>
```

Both are required together; with either missing the app silently stays local.
The app calls `{BILLING_SYNC_URL}/v1/entitlements` with an HMAC signature.

## 10. Test-mode end-to-end check

1. Open `https://yourdomain.com/billing.html` → buy **Pro** with Razorpay's
   test card `4111 1111 1111 1111` (any future expiry, any CVV).
2. Success page says the plan auto-renews and shows the renewal date.
3. `https://yourdomain.com/admin/registry?token=<BILLING_ADMIN_TOKEN>` →
   order + subscription rows exist.
4. Razorpay Dashboard → Subscriptions → the subscription is `active`.
5. Desktop app → subscription page shows Pro + renewal date (proves
   `BILLING_SYNC_URL`/`SECRET` are wired).
6. Buy **Lifetime** → success page → app folds it to lifetime (no renewal).
7. Cancel from the app/site → `cancel_at_period_end` shows in the app.

## 11. Go-live checklist

**FamGateway rail (live rail on data-agent.me):**
- [ ] `PAYMENTS_PROVIDER=famgateway` + `FAMGATEWAY_API_KEY` set; Razorpay vars left empty (dormant).
- [ ] FamGateway webhook registered → a test payment grants the order; `/healthz` shows the `famgateway` component green.
- [ ] `GET /v1/payments-info` returns `"provider":"famgateway"` (storefront copy follows it).
- [ ] Test ₹10 payment: webhook logs a 200, order `status=paid`, UTR recorded, entitlement visible via the app's "Sync now".

**Razorpay fallback (only if you ever flip back):**
- [ ] Razorpay KYC complete + **Subscriptions** capability enabled.
- [ ] Live keys (`rzp_live_…`) in the env file → `systemctl restart dataagent-billing`.
- [ ] Live plan created (`scripts/razorpay_create_plan.py` with live keys) → `RAZORPAY_PLAN_ID_PRO` updated.
- [ ] Live-mode webhook created (test and live webhooks are separate) → `RAZORPAY_WEBHOOK_SECRET` updated.

**Common:**
- [ ] Resend domain verified (SPF/DKIM DNS records) → `RESEND_API_KEY` + `BILLING_EMAIL_FROM` set.
- [ ] Rotate any credentials that were ever shared/pasted: Turso tokens, `ENTITLEMENTS_SYNC_SECRET`, `BILLING_ADMIN_TOKEN`.
- [ ] `scripts/migrate_keys_to_email_access.py --apply` run against the production Turso DB (from a dev machine with prod `TURSO_*` env).
- [ ] One real live purchase (small amount) → webhook logged → refund → grant revoked.
- [ ] `/healthz` all-green + an external uptime monitor pointed at it.

## 12. Operations

```bash
# logs
sudo journalctl -u dataagent-billing -f
sudo journalctl -u caddy -f

# update code: re-scp billing_server/ (and keygen_app/ if changed), then:
sudo -u dataagent .venv/bin/pip install -r billing_server/requirements.txt   # only if requirements changed
sudo systemctl restart dataagent-billing

# backups
# Turso: use the Turso dashboard/CLI backups (hosted default).
# SQLite fallback only: stop the service first (one writer!), copy
# billing_server/billing.sqlite3, start again.
```

Never run `uvicorn --reload` on the server, never expose 8090 publicly
(bind `127.0.0.1` only, Caddy fronts it), and never run two server processes
against the same SQLite file (that's why production uses Turso).

## Appendix A — Docker / Heroku

The image ships only `billing_server/` + `keygen_app/` (runtime state is
excluded via `billing_server/Dockerfile.dockerignore`). Build from the repo root:

```bash
docker build -f billing_server/Dockerfile -t dataagent-billing .
docker run -d --name dataagent-billing \
  --env-file /etc/dataagent/billing.env \
  -p 127.0.0.1:8090:8090 --restart unless-stopped dataagent-billing
docker exec dataagent-billing python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8090/healthz').read())"
```

**Heroku** (uses the `$13/month × 24 months` student credit): add `heroku.yml`
at the repo root, then set the same env vars as config vars:

```yaml
build:
  docker:
    web: billing_server/Dockerfile
```

```bash
heroku create <app-name> && heroku stack:set container -a <app-name>
heroku config:set RAZORPAY_KEY_ID=… RAZORPAY_KEY_SECRET=… RAZORPAY_WEBHOOK_SECRET=… \
  RAZORPAY_PLAN_ID_PRO=… ENTITLEMENTS_SYNC_SECRET=… LICENSE_PRIVATE_KEY=… \
  BILLING_ADMIN_TOKEN=… TURSO_DATABASE_URL=… TURSO_AUTH_TOKEN=… -a <app-name>
git push heroku main
```

Use a **Basic** dyno ($7/mo, always-on); Eco dynos sleep and would drop
webhooks. Heroku terminates TLS itself — skip Caddy, the webhook URL becomes
`https://<app-name>.herokuapp.com/razorpay/webhook`, and you must set
`TURSO_*` (the container filesystem is ephemeral).

## Appendix B — Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Caddy returns 502 | uvicorn down → `systemctl status dataagent-billing` + journal logs |
| `/healthz` `ok:false` | `components` names the missing secret — fix env file, restart |
| Storefront fetch fails / CORS error | `BILLING_API_URL` must be the **same origin** as the page |
| Webhook 400 invalid signature | `RAZORPAY_WEBHOOK_SECRET` mismatch (test webhook vs test secret; live has its own) |
| Webhook 404 | URL must be `https://…/razorpay/webhook` |
| Renewals never extend | webhook missing `subscription.*` events |
| "database is locked" (SQLite) | more than one process on the file → set `TURSO_*` |
| Certificate never issues | 80/443 blocked, DNS not pointing yet, or Oracle iptables rule |
| Checkout fails on live key | live plan id created with test keys (or vice versa) — they must match the key mode |
