# Billing & entitlements

How DataAgent decides what a user may do: the three plans and their prices, the 17 premium feature keys, the entitlement-resolution chain, the three grant sources (and why a hand-edited `warehouse.duckdb` grants nothing), the Pro trial, the external Razorpay checkout, and the product-key flow. Written for whoever operates the billing server, audits the entitlement code, or wants to sell a Pro seat.

---

## 1. Plans and prices

**One price list, one currency (INR).** The checkout already charged in paise
(Razorpay's smallest unit); the app used to display a *different* dollar list.
That is fixed: the app now shows exactly what the checkout charges.

Prices live in `billing_server/config.py` → `PLANS` (**what is charged**) and are
mirrored by `api/billing/seed.py` → `PRICES` (amounts in **paise**) and
`api/subscriptions.py` → `PLANS` (major units, for display). The three must
agree — `tests/test_launch_billing_legal.py` fails if they drift.

| Plan | `id` | Cycle | Amount (paise) | Displayed as | Notes |
|---|---|---|---|---|---|
| Free | `plan_free` | `MONTHLY` | `0` | ₹0 | 3 dashboards, 7-day history, 25 AI requests/day |
| Pro | `plan_pro` | `MONTHLY` | `49900` | **₹499/mo** | auto-renewing Razorpay subscription |
| Lifetime | `plan_lifetime` | `ONETIME` | `1000000` | **₹10,000** | one-time Razorpay order — **the headline product** |

```python
# billing_server/config.py
"pro":      {"amount": 49900,   "currency": "INR", "cycle": "MONTHLY", "days": 31,    "subscription": True},
"lifetime": {"amount": 1000000, "currency": "INR", "cycle": "ONETIME", "days": None, "subscription": False},
```

> **There is no yearly Pro row.** It used to exist as a *display-only* row
> (`6000` cents) for a SKU the checkout never sold — the second price list that
> made the app and the checkout disagree. It has been removed.

**Lifetime is the headline product.** ₹10,000 once beats a $14/user/mo Power BI
Pro seat in year one, needs no metering story, and is verifiable offline by the
product-key path. Pro survives as a capped secondary tier.

### Quotas (plan limits)

`api/billing/seed.py` → `LIMITS` (`-1` = unlimited):

| Metric | Free | Pro | Lifetime |
|---|---|---|---|
| `dashboards` | 3 | 50 | 1000 |
| `chat_history_days` | 7 | 365 | 3650 |
| `ai_requests` | **25/day** | **50/day** | **100/day** |

**No plan has unlimited AI.** The old `-1` on Pro and Lifetime was a
guaranteed negative margin on a ₹499/mo seat: `agent/sqlgen.py` injects the
schema and relationships for up to 25 tables into the prompt, so a request is
~10 K input + ~2 K output tokens ⇒ ≈ $0.04/request at Sonnet-class prices.
600 requests/month ≈ $30 of inference against ₹499 (~$6) of revenue.

The seed re-syncs these rows on **every** run (`api/billing/seed.py` →
limit re-sync), so an existing install with `ai_requests = -1` is repaired to
the new caps without a manual migration.

### The AI-request caps

| Env var | Default | Meaning |
|---|---|---|
| `BILLING_FREE_AI_DAILY_CAP` | **25** | AI requests per day for the Free plan. `-1` = unlimited. |
| `BILLING_PRO_AI_DAILY_CAP` | **50** | AI requests per day for Pro. |
| `BILLING_LIFETIME_AI_DAILY_CAP` | **100** | AI requests per day for Lifetime. |
| `BILLING_TENANT_AI_DAILY_CAP` | **100** | Aggregate AI budget per tenant, across every account. **This is the hard cost ceiling.** |

`ai_requests` is metered **per day** (`_DAILY_METRICS` in `api/billing/metering.py`);
every other metric is metered per calendar month. The cap is written into
`analytics.plan_limits` at seed time.

> **On the desktop build the marginal cost of an AI request is $0** — the LLM
> runs on the user's own machine (Ollama is the default provider). The caps
> there are product limits, not cost controls. They exist so that a *hosted*
> deployment has a finite ceiling.

When the cap is hit, `api/main.py::_meter_ai_request` raises **HTTP 429** with
the same upgrade headers the feature gate emits:

```
X-Upgrade-Required: true
X-Required-Feature: ai_unlimited
```

---

## 2. Premium feature keys and what they gate

17 premium feature keys are declared in `api/billing/schema.py` (`FEATURE_RECREATE_FROM_IMAGE` … `FEATURE_ORGANIZATION`). All 17 are disabled for Free and enabled for Pro and Lifetime (`api/billing/seed.py` → `FEATURES`). Three further flags complete the matrix: `ads_visible` (True on Free, False on paid), `ip_rotation` and `ai_unlimited` (False on Free, True on paid).

> **`ai_unlimited` is a legacy name, not a claim.** No plan has unlimited AI
> any more (see the caps above). The key survives because it is a declared
> schema column (`api/billing/schema.py` → `FEATURE_AI_UNLIMITED`) and because
> the 429 response labels the upgrade hint with it. **Marketing copy must not
> say "unlimited AI"** — the actual number is `max_ai_requests`.

| Feature key | Flag name | Endpoints gated (count) | Representative endpoints |
|---|---|---|---|
| `enterprise` | `FEATURE_ENTERPRISE` | **21** | `POST /catalog/sync`, `POST /auth/api-key`, `POST/GET /auth/api-keys`, `POST /auth/api-keys/{id}/rotate`, `/sla/definitions`, `/sla/status`, `/sla/breaches`, `/cost/record`, `/cost/queries`, `/cost/budgets` |
| `organization` | `FEATURE_ORGANIZATION` | **20** | `/org/tree`, `/org/departments`, `/org/teams`, `/org/groups`, `/org/groups/{id}/members`, `/org/users/{user_id}/groups` |
| `governance` | `FEATURE_GOVERNANCE` | **20** | `/masking/rules`, `/masking/scan/{table}`, `/compliance/privacy-request(s)`, `/compliance/retention-policies`, `/compliance/consent`, `/compliance/pii-scan` |
| `automation` | `FEATURE_AUTOMATION` | **19** | `/workflows/definitions`, `/workflows/start`, `/workflows/instances`, `/workflows/pending-approvals`, `/pipeline/health`, `/pipeline/run`, `/pipeline/validate`, `/pipeline/list` |
| `export_page` | `FEATURE_EXPORT_PAGE` | **13** | `GET /export/csv/{table_name}`, `GET /export/parquet/{table_name}`, `GET /export/xlsx/{table_name}`, `GET /export/markdown/{table_name}`, `GET /export/html/{table_name}`, `GET /export/pptx/{table_name}`, `GET /export/pdf/{table_name}`, `GET /export/zip`, `GET /export/bi/{table_name}`, `GET /export/bi-config`, `GET /export/schema-doc`, `POST /export/email`, `POST /analysis/export-model` |
| `scheduled_reports` | `FEATURE_SCHEDULED_REPORTS` | **8** | `POST /schedule/add`, `GET /schedule/list`, `DELETE /schedule/{name}`, `POST /schedule/start`, `POST /pipeline/schedule`, `GET /pipeline/schedules`, `DELETE /pipeline/schedule/{schedule_name}`, `POST /pipeline/schedule/{schedule_name}/toggle` |
| `dashboard_export` | `FEATURE_DASHBOARD_EXPORT` | **7** | `GET /{dash_id}/export/pbit`, `POST /{dash_id}/export/{fmt}`, `GET /export/{job_id}`, `GET /export/download/{job_id}`, `GET /export/pbit/{dash_id}`, `GET /export/csv/{table}`, `GET /export/xlsx/{table}` |
| `marketplace` | `FEATURE_MARKETPLACE` | **7** | `/marketplace/products`, `/marketplace/my-requests` |
| `alerts_thresholds` | `FEATURE_ALERTS_THRESHOLDS` | **7** | `POST /alerts/add`, `GET /alerts/list`, `POST /alerts/run`, `DELETE /alerts/{name}`, `GET /alerts/history`, `POST /alert/check`, `GET /alerts/recent` |
| `smart_monitoring` | `FEATURE_SMART_MONITORING` | **6** | `POST /watcher/start`, `POST /watcher/stop`, `GET /watcher/files`, `GET /watcher/history`, `POST /watcher/scan`, `POST /sla/monitor/start` |
| `connect_page` | `FEATURE_CONNECT_PAGE` | **4** | `POST /connect/database`, `POST /connect/database/import`, `POST /connect/api` |
| `recreate_from_image` | `FEATURE_RECREATE_FROM_IMAGE` | **3** | `POST /analyze-image`, `POST /build-from-image`, `POST /build-from-pbix` |
| `storytelling` | `FEATURE_STORYTELLING` | **2** | `POST /analysis/storytelling`, `GET /analysis/storytelling/status/{job_id}` |
| `reports` | `FEATURE_REPORTS` | **1** | `GET /report/{table_name}` |
| **Total** | | **138** | `require_feature(...)` bindings across `api/` |

Three further keys are **not** bound to `require_feature` — they are enforced elsewhere:

| Feature key | Enforced by |
|---|---|
| `ai_unlimited` | the metering cap (429 + `X-Upgrade-Required`) |
| `ip_rotation` | `api/billing/context.py` → the LLM client's WARP auto-reset, and the proxy relay (feature ticket) |
| `ads_visible` | the frontend. The seeded flag is `True` for Free, but ads are **removed from the product**: `config/settings.yaml` sets `ads.enabled: false` and `GET /config` always reports `ads.enabled = false`, so no client ever renders a slot. |

Also declared but currently unbound: `external_sources`, `live_monitor`, `api_connection` (present in `schema.py` and in the seeded feature matrix, but no `require_feature(...)` call sites yet).

### The gate itself

`api/billing/gate.py::require_feature(feature_key)` returns a FastAPI dependency. When the feature is not entitled it raises **HTTP 403** with:

```
X-Upgrade-Required: true
X-Required-Feature: <feature_key>
```

Two behaviours worth knowing:

- It always resolves the plan **server-side** (`has_feature(user.user_id, feature_key, None)`) instead of trusting `User.plan`, so a stale or forged plan cannot launder a tampered keygen grant.
- There is one dev backdoor: `DATAGENT_DEV_FEATURES=1` unlocks `recreate_from_image` only, and only when **not** a frozen PyInstaller build (`_is_frozen()`). A shipped `.exe` never honours the env var.

---

## 3. Entitlement resolution

The chain is `get_plan_for_user` → `find_effective_subscription` → `_verify_effective`.

### 3.1 `get_plan_for_user(user_id)` — `api/billing/entitlements.py`

1. `find_effective_subscription(user_id)` → the canonical `analytics.subscriptions` row (lazy expiry applied on read: an `ACTIVE`/`TRIALING`/`PAST_DUE` row whose `current_period_end` / `expires_at` / `trial_ends_at` has passed is transitioned to `EXPIRED` with an audit event and skipped).
2. If no row matches the local `user_id`, retry by Google identity (`email` / `sso_subject`) so a paid subscription follows the Google account even if the local user id changed.
3. If still nothing, fall back to the legacy `analytics.user_subscriptions` table — but only through `api/billing/license_guard.py::verify_legacy_fallback`, which requires a valid stored key bound to this account's email.
4. Any exception along the way → `free`.

### 3.2 `_verify_effective(user_id, sub)` — the grant-source switch

```python
provider = sub["payment_provider"].lower()
if provider == "keygen":     → license_guard.verified_keygen_plan(user_id, plan)
if provider == "cloud_sync": → license_guard.verified_cloud_sync_plan(user_id, plan)
if plan == "free":           → "free"
otherwise (trial / razorpay / admin / manual)
                            → grant_signature.verify_grant(sub) must be True
```

Every branch **fails closed to `free`**. Guard errors never grant.

### 3.3 The three grant sources

| `payment_provider` | Source of truth | Verified how |
|---|---|---|
| `keygen` | Ed25519 product key | Re-verified on **every** entitlement read: signature, expiry, plan match, and email binding to the purchasing account. |
| `cloud_sync` | Billing server | Server-signed Ed25519 entitlement token, verified against the baked public key. |
| anything else (`trial`, `razorpay`, `admin`, manual) | local row | Must carry a valid **local grant signature** over the row's own canonical value. |

#### `keygen` — Ed25519 product-key signature

`api/billing/license_guard.py::verified_keygen_plan`:

- No active `keygen` rows → the candidate plan is returned unchanged (this is the trial/admin path).
- Rows present → `verify_stored_license()` re-runs `validate_key()` on the stored `premium_settings.license_key` using the **baked-in public key**. Then:
  - the **signed plan must equal** the candidate plan (a stored `pro` key cannot justify a hand-edited `lifetime` row);
  - the key's `bound_email` must equal the signed-in account's email;
  - remote revocation (refund / share) is re-checked at most once per 24h when the billing server is configured;
  - the offline grace window (`LICENSE_GRACE_DAYS`, default 14, clamped 1–90) bounds how long a locally-valid key works without a server check. With no billing server configured there is **no** grace enforcement — offline-only deployments keep working.

Any failure calls `lapse_keygen_rows()` (status → `EXPIRED`, legacy table → `free`, `license_status` marked invalid) and returns `free`.

#### `cloud_sync` — server-signed entitlement token

The billing server mints a short-lived Ed25519 assertion (`billing_server/keymint.py::mint_entitlement_token`); the app verifies it with the baked public key (`api/billing/entitlement_token.py::verify_token`).

- Token format: `base64url(canonical_payload_json).base64url(signature)`.
- Payload: `{product: "dataagent-ent", ver: 1, email, plan, iat, exp, grant_expires_at}`.
- Verification checks the signature, product, version, plan ∈ {`pro`,`lifetime`}, email present and equal to the expected account email, and `exp` in the future.
- Tokens are stored per email in `analytics.premium_settings` under `entitlement_token:<email>`.
- A `cloud_sync` row without a valid token → rows are lapsed and the plan drops to `free`.

#### Everything else — the signed local grant (`api/billing/grant_signature.py`)

**This is the module that closes the "edit the DuckDB file" bypass.**

Previously, every `payment_provider` that was not `keygen` and not `cloud_sync` was treated as trusted local state and returned verbatim. That meant a hand-edited row with `payment_provider='razorpay'` and `plan_id='plan_lifetime'` granted a full paid entitlement with no key, no signature and no server round-trip.

Now every locally-issued grant carries an **HMAC-SHA256 signature over the row's canonical value**:

```
canonical = user_id | plan_id | payment_provider | grant_nonce |
            current_period_end | expires_at | trial_ends_at
signature = HMAC-SHA256(key, canonical)          # hex
```

- The signing key lives in **`data/.grant_key`** — outside the warehouse — created on first use with `0600` permissions and written as `secrets.token_hex(32)` (64 ASCII hex characters; a stored key shorter than 32 characters is rejected and replaced). It can be overridden with the `DATAAGENT_GRANT_KEY` env var.
- If the key cannot be persisted it is used in memory only; grants then do not survive a restart (a forced re-grant rather than a silent drop to free).
- Verification uses `hmac.compare_digest`.
- `status` is deliberately **not** covered: the lifecycle writes that change it only remove or defer entitlement, and the dates above are what actually bound access.
- The signature is (re)written by `api/billing/subs.py::_sign_row` / `_resign` after every write that changes what the row entitles the user to (create, plan change, period advance, cancel, status change), and by `keygen_app/storage.py::_sign_row` / `_resign` on the product-key activation path.

**Why this matters:** editing `warehouse.duckdb` to `plan_id='plan_lifetime'` changes the canonical value, so the stored signature no longer matches and the grant is refused. The attacker would need the signing key, which lives outside the database.

### 3.4 The one-time grant migration

Rows written before grant signing existed carry no `grant_signature` and would be refused — real customers would silently drop to free. `api/billing/schema.py::_migrate_unsigned_grants` signs them once, from their own current values, during `init_billing_tables()`.

The "already migrated" marker lives in **`data/.grant_migrated`** — a *file outside the database*, not a table. If the marker lived in the warehouse, an attacker could delete both a tampered row's signature and the marker, and the next start would re-sign the tampered row — turning the migration into a bypass. Keeping the marker next to the signing key means only the database can be edited, so the migration can never be re-triggered by them.

The marker is written **last**, so a crash mid-migration re-runs it (harmless: already-signed rows are skipped) rather than leaving rows unsigned.

---

## 4. The Pro trial

| Property | Value |
|---|---|
| Length | **14 days** (`BILLING_TRIAL_DAYS`, clamped 1–90) |
| Plan / status | `plan_pro` / `TRIALING` |
| Validity window | `trial_ends_at` and `current_period_end` both set to now + 14 days |
| Repeat use | **One per email** (and per `user_id` / `sso_subject`) |
| Enable/disable | `BILLING_TRIAL_ENABLED=0` disables it |

Endpoints (`api/subscriptions.py`):

- `POST /subscriptions/trial/start` → 403 when disabled, 409 when the account already had a paid or trial grant.
- `GET /subscriptions/trial/status` → `{eligible, active, ends_at, days, enabled}`.

The single-use guard matches on `user_id` **or** the Google identity (`email` / `sso_subject`), so the same person cannot claim a second trial by recreating the local account. A post-insert duplicate check rolls the insert back if a concurrent start won the race (DuckDB is single-writer, so the second committer always sees both rows).

Trials leave an `EXPIRED` row behind, so `_had_paid_or_trial()` keeps them one-shot forever.

---

## 5. Self-hosted deployments stay free

| Setting | Where | Effect |
|---|---|---|
| `BILLING_EXTERNAL_URL` | env (`BillingSettings`, `env_prefix="BILLING_"`) | URL of the external checkout site. Empty ⇒ the app renders no purchase path. |
| `BILLING_SYNC_URL` | env or `config/settings.yaml` → `billing.sync_url` | Base URL of the billing server serving `/v1/entitlements`. Empty ⇒ no cloud sync. |
| `BILLING_SYNC_SECRET` | env or `config/settings.yaml` → `billing.sync_secret` | HMAC secret for those requests. Empty ⇒ no cloud sync. |

`cloud_sync.is_configured()` returns True only when **both** `BILLING_SYNC_URL` and `BILLING_SYNC_SECRET` are set. With neither set:

- the app resolves plans locally only — Free plan + the built-in trial;
- `GET /config` always reports `billing.enabled: false` (in-app payments were removed; the external site owns checkout) and `billing.external_url` empty, so the frontend renders no purchase path;
- `billing.plans` still lists the seeded local prices (the pricing UI is static), and `billing.entitlements` still resolves for the signed-in user, so the Free-plan gating works exactly as it does on a paid deployment;
- `sync_user()` returns `None` immediately (no request is made), so nothing upgrades the local plan;

Self-hosters get the whole product for free: `scripts/build_secure_zip.py` strips every credential file from the shipped zip, and the marketing site renders a "checkout is not enabled on this deployment" notice rather than a broken Pay button.

---

## 6. External checkout (Razorpay) — end to end

Checkout does **not** happen inside the app. The app holds no payment material; the billing server owns the Razorpay keys and the license signing key.

### 6.1 Billing-server endpoints (`billing_server/main.py`)

| Endpoint | Purpose |
|---|---|
| `POST /create-order` | One-time Razorpay order (Lifetime). Pro is rejected here with 400 — it is a subscription. |
| `POST /verify` | Verify the checkout HMAC (`order_id\|payment_id`), mark the order paid. **Keyless** — no key is minted; the plan follows the buyer's email. |
| `POST /create-subscription` | Start the auto-renewing monthly Pro subscription. 503 when `RAZORPAY_PLAN_ID_PRO` is not set. |
| `POST /verify-subscription` | Verify the subscription checkout HMAC, activate the subscription, return the renewal date. |
| `POST /razorpay/webhook` | Signature-verified payment / subscription / refund events. |
| `GET /success` | Success page (`?order_id=` or `?subscription_id=`). |
| `GET /v1/entitlements` | HMAC-signed plan lookup by email — **the app's read path**. |
| `POST /v1/feature-ticket` | Mint a short-lived signed ticket authorizing one paid action (the flagship `ip_rotation` proxy relay). |
| `POST /v1/keys/redeem` | One-time product-key redemption registry. |
| `POST /v1/subscriptions/cancel` | Cancel-at-period-end for the session email's live subscription. |
| `GET /healthz` | Readiness probe (names the missing secret when not ready). |

### 6.2 Request signing

The app signs its calls to the billing server with `BILLING_SYNC_SECRET` (the server side is `ENTITLEMENTS_SYNC_SECRET` — the same value):

```
/v1/entitlements   GET  ?email=<email>&exp=<unix_ts>&sig=HMAC-SHA256(secret, "<email>|<exp>")
/v1/feature-ticket POST {"mode":"key"|"token", "email", "feature", "key"/"token", "user_id", "exp",
                        "sig": HMAC-SHA256(secret, "ticket|<key_hash>|<email>|<user_id>|<exp>")}
/v1/keys/redeem    POST {"key", "key_hash", "email", "user_id", "exp",
                         "sig": HMAC-SHA256(secret, "<key_hash>|<email>|<user_id>|<exp>")}
```

The server enforces a **300-second freshness window** on `exp` (`_verify_signed`) and compares with `hmac.compare_digest`.

### 6.3 Token minting

`GET /v1/entitlements` returns `plan`, `status`, `expires_at`, `auto_renew`, `cancel_at_period_end`, and — for paid plans — a **server-signed Ed25519 entitlement token** (`billing_server/keymint.py::mint_entitlement_token`). The app verifies that token with the baked public key; a forged response without the private key fails closed.

If the signing key is unavailable the server fails open to a tokenless response (old app behaviour) — but the app then writes the grant as a local row that carries a **local grant signature**, so a DB-only edit still grants nothing.

### 6.4 App side

`api/billing/cloud_sync.py`:

- `fetch_remote_plan(email)` → HMAC-signed `GET {BILLING_SYNC_URL}/v1/entitlements`, verifies any returned token before trusting it.
- `apply_remote_plan(user_id, email, remote)` → writes the plan through `api.subscriptions.set_plan`, which writes through to `analytics.subscriptions` (signed). A paid plan **with** a verified token is written with `payment_provider='cloud_sync'` (so the guard enforces the signature); a paid plan without a token is written as `razorpay` and then requires the local grant signature.
- Fail-secure: a network error or an unconfigured server **keeps the local plan** — a signed product key already on disk stays valid offline. A remote "free" never downgrades a local key.
- `sync_user(user_id, email)` → fetch + apply.

### 6.5 Manual steps to go live — **not yet done**

These are real and currently undone. Treat them as a checklist:

- [ ] **Razorpay KYC complete** and the **Subscriptions** capability enabled.
- [ ] Create the monthly Pro plan: `python scripts/razorpay_create_plan.py` with the Razorpay keys in the environment. Copy the printed `RAZORPAY_PLAN_ID_PRO=plan_…` into the billing-server environment. Rerunning creates a *new* plan — existing subscriptions keep working, but new checkouts must use the latest id.
- [ ] Set `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` (test first, `rzp_test_…`, then live `rzp_live_…`).
- [ ] Create the webhook in the Razorpay dashboard: URL `https://<host>/razorpay/webhook`, secret → `RAZORPAY_WEBHOOK_SECRET`, events `payment.captured`, `payment_link.paid`, `refund.processed` **plus** every `subscription.*` event (`authenticated`, `activated`, `charged`, `pending`, `halted`, `cancelled`, `completed`, `paused`, `resumed`, `expired`). Test and live webhooks are separate objects with separate secrets.
- [ ] Generate the license signing keypair once:
      `python -m keygen_app.keygen gen-keys --out billing_server/keys/ed25519_private.pem`
      Put the **private** key on the billing host only (`LICENSE_PRIVATE_KEY` = base64 of the PEM, or `LICENSE_PRIVATE_KEY_PATH`). Put the **public** key in `keygen_app/baked_public_key.py` and ship that with the app build.
- [ ] Set `ENTITLEMENTS_SYNC_SECRET` on the server and the **same value** as `BILLING_SYNC_SECRET` in the app.
- [ ] Set `BILLING_ADMIN_TOKEN` (otherwise `/admin/registry` is disabled — fail-closed) and optionally `RESEND_API_KEY` / `BILLING_EMAIL_FROM`.
- [ ] Point the storefront at the server: `window.BILLING_API_URL = 'https://yourdomain.com'` in `website/billing.html`.
- [ ] Point the app at the server: `BILLING_SYNC_URL` + `BILLING_SYNC_SECRET` in the app environment (or `billing.sync_url` / `billing.sync_secret` in `config/settings.yaml`).
- [ ] Confirm `/healthz` reports `ok: true` (razorpay + webhook + license_key present).
- [ ] Run one real (small-amount) live purchase → webhook logged → refund → grant revoked.

`docs/deployment/billing-server.md` is the full VPS/Caddy/systemd walkthrough.

---

## 7. Security notes

**The license guard fails closed.** `api/billing/license_guard.py` never returns an unverified plan. Every rejection path (bad signature, wrong plan, email mismatch, remote revocation, expired grace) lapses the rows and returns `free`.

**Webhook signatures are verified over the raw body.** `POST /razorpay/webhook` reads `await request.body()` and compares `HMAC-SHA256(RAZORPAY_WEBHOOK_SECRET, raw_body)` against the `X-Razorpay-Signature` header with `hmac.compare_digest`. The body is parsed *after* verification.

**Idempotency markers.** `store.seen_event(kind, ref)` records one delivery per `(event, payment)`; a repeat delivery returns `{"ok": true, "duplicate": true}` without re-applying the grant. Metering has its own idempotency log (`analytics.usage_events.idempotency_key`, unique) — `record_usage()` returns `{"status": "duplicate_ignored"}` for a repeated key instead of double-counting.

**The dev server refuses to start with a live Razorpay key.** `billing_server/dev_server.py` raises `SystemExit` when `RAZORPAY_KEY_ID` starts with `rzp_live_`: the dev server stubs the Razorpay API and defaults the admin token to a public constant, so with live keys present it could mint valid checkout HMACs for arbitrary order ids and expose `/admin/registry`.

**Checkout HMACs are constant-time.** `verify_checkout_signature` / `verify_subscription_checkout_signature` / `verify_webhook_signature` all use `hmac.compare_digest`.

**The signing key never ships.** `keygen_app/baked_public_key.py` holds only the public PEM. `scripts/build_windows.ps1` fails the build if any `*ed25519*`/`*private*` `.pem` is found in `dist/`. Frozen builds verify against the baked public key only and ignore `LICENSE_PUBLIC_KEY` / `LICENSE_PUBLIC_FINGERPRINT` env overrides, so an environment variable cannot redirect verification to an attacker keypair (`keygen_app/config.py::load_public_key`).

**Feature tickets.** `api/billing/feature_ticket.py` binds paid actions to server proof: the ticket (not the DB) is what the proxy relay accepts. The decision matrix in `access_decision()` is deny/direct/relay per case — free plan denies, unconfigured server stays direct (the offline product promise), a transport error falls back to direct only if fresh cryptographic proof exists, and a server verdict is authoritative.

---

## 8. The `keygen_app/` product-key flow

`keygen_app/keygen.py` mints and verifies offline product keys.

**Key format**

```
DATAAGENT-<base32(payload_json_bytes + 64-byte_signature), grouped in 5-char blocks>
```

**Payload** (compact JSON, canonical key order — `json.dumps(payload, sort_keys=True, separators=(",", ":"))`):

```json
{"product":"dataagent","plan":"pro|lifetime|trial","expiresAt":"ISO"|null,
 "nonce":"<16 hex>","ver":1,"email":"buyer@example.com"}
```

The signature covers the **exact payload bytes**, so validation must re-serialize with the same canonical form. `email` binds the key to the account that bought it — the activation endpoint refuses unbound keys.

**Generation**

```powershell
python -m keygen_app.keygen gen-keys --out billing_server/keys/ed25519_private.pem
python -m keygen_app.keygen make --plan pro --email buyer@example.com
python -m keygen_app.keygen check DATAAGENT-XXXXX-XXXXX-…
```

- `gen-keys` writes the private key PEM with `0600` and prints the public-key fingerprint.
- `make --email` is **required** for shippable keys (the app refuses to activate an unbound key).
- `assert_not_dev_key()` refuses to sign with the bundled development keypair.

**Verification** (`keygen_app/config.py::load_public_key`):

- **Frozen (`.exe`)**: the baked public key only, and its fingerprint must equal the hardcoded production fingerprint `deb8b1e5d88e42e4` — env/file overrides are ignored so `LICENSE_PUBLIC_KEY` cannot redirect verification. A fingerprint mismatch raises `RuntimeError("Baked public key fingerprint mismatch — build tampered.")`; a dev key raises `RuntimeError("Frozen build uses a development key. Refusing.")`.
- **Dev/test**: `LICENSE_PUBLIC_KEY` env → `keygen_app/keys/ed25519_public.pem` → the compiled constant.

**The private key never ships.** Only the public key is baked into the customer build; the admin build additionally loads the private key from a PEM file or the `LICENSE_PRIVATE_KEY` env var (base64 PEM).

---

## 9. `/premium/activate-key` and the keyless subscription model

`POST /premium/activate-key` (`api/premium.py`) activates a product key on the signed-in account:

1. Load the baked public key → `validate_key(key, public_key)` (400 on a bad signature).
2. The key must carry a **buyer email binding**; unbound (legacy) keys are refused with 400.
3. The signed-in account's email must equal the buyer email — 403 otherwise ("Product keys only work on the account that purchased them.").
4. **One-time use.** The local ledger `analytics.key_redemptions` records the first `user_id` that activated a key hash, so a second account on the same install is refused even offline. When the billing server is configured, `POST /v1/keys/redeem` claims the key globally and refuses unknown/refunded keys.
5. On success the plan is written to DuckDB (via the signed grant path) and the subscription page picks it up immediately.

**The keyless subscription model.** For the normal purchase path there is no key at all. The paid order or subscription itself grants the plan **by email**:

- The buyer checks out on the external site with their email.
- `GET /v1/entitlements` resolves the plan for that email.
- The app syncs on login/startup (`cloud_sync.sync_user`) and writes the plan locally, bound to the account's email.

Consequence: **the plan follows the buyer's email, not the machine.** Signing in with the same address on a second device gets the same plan — no key to copy, no activation step. See `docs/decisions/ADR-004-keyless-subscriptions.md`.

---

## Where things live

| Path | Role |
|---|---|
| `api/billing/schema.py` | DDL for plans/prices/features/limits/subscriptions/payments; feature-key constants; the one-time grant migration |
| `api/billing/seed.py` | `PLANS`, `PRICES`, `FEATURES`, `LIMITS`; the AI daily caps |
| `api/billing/entitlements.py` | `get_plan_for_user`, `_verify_effective`, `get_entitlements` |
| `api/billing/subs.py` | canonical subscription layer: lifecycle, lazy expiry, trial, signing |
| `api/billing/grant_signature.py` | signed local grants (HMAC-SHA256, `data/.grant_key`) |
| `api/billing/license_guard.py` | keygen / cloud_sync / legacy re-verification; fail-closed |
| `api/billing/entitlement_token.py` | app-side Ed25519 entitlement-token verification |
| `api/billing/cloud_sync.py` | HMAC pull from the billing server; plan application |
| `api/billing/gate.py` | `require_feature()` dependency + 403 upgrade headers |
| `api/billing/metering.py` | daily/monthly usage metering |
| `api/billing/feature_ticket.py` | per-request authorization for paid actions |
| `api/subscriptions.py` | `/subscriptions/*` routes; legacy `user_subscriptions` table |
| `billing_server/` | Razorpay checkout, entitlement lookup, ticket minting, store |
| `keygen_app/` | Ed25519 product keys (mint + verify) |
| `docs/deployment/billing-server.md` | taking the billing server to a public host |
| `docs/decisions/ADR-004-keyless-subscriptions.md` | why the plan follows the email |
