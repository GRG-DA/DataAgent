# Frontends

DataAgent has three shipped frontends — the vanilla-JS **web SPA** (`web/`), the **marketing site** (`website/`) and the **desktop shell** (`shell/`, pywebview) — plus a fourth, the React marketing site (`website-react/`), that **does not exist on disk** yet. This document maps each one, the navigation, the auth gate, onboarding, the TypeScript BI layer, and the checkout wiring on the pricing page. Written for frontend contributors and for anyone changing the UI surface.

---

## 1. The three frontends

| Frontend | Path | Stack | Build step | Status |
|---|---|---|---|---|
| **Web SPA** (the app) | `web/` | Vanilla JS + Tailwind-style utility classes + vendored libraries, hash router | **None** | Shipped — this is the product |
| **Marketing site** | `website/` | Static HTML + CSS + Three.js / GSAP / Lenis | None (CDN + vendored) | Current — the static site |
| **React marketing site** | `website-react/` | React | `npm run dev` / `npm run build` | **Does not exist on disk.** `AGENTS.md` still lists it as the intended replacement; `config/settings.yaml` carries a note that the 8090 CORS entries are kept only for the billing dev mock. Until it lands, `website/` is the live marketing site. |
| **Desktop shell** | `shell/` | pywebview wrapper around the SPA | PyInstaller (`scripts/build_windows.ps1`) | Shipped — Windows exe |

The shell and the SPA are the **same** frontend: the shell starts the FastAPI backend and points a pywebview window at it. The marketing site is entirely separate and never talks to the API.

---

## 2. The web SPA (`web/`)

### Layout

```
web/
  index.html          # app shell: top nav, mobile nav, page container
  css/                # styles.css (357 KB), chat-pro.css (37 KB), enterprise.css,
                      # kpiStyles.css, propsPanel.css, fonts.css
  js/
    app.js            # hash router, page lifecycle, notifications, dark mode, top nav, onboarding
    api.js            # the API client (window.API)
    auth.js           # the login gate (window.Auth)
    charts.js         # shared chart helpers
    components/       # 31 shared components (rbac.js, dashboardGrid.js, widgetEditor.js, …)
    pages/            # 31 modules (29 registered routes)
  ts/                 # typed BI/chart layer (see §6)
  vendor/             # vendored third-party libraries
  lib/, static/
```

**No build step.** Pages are plain `<script>` files loaded on demand by the router. There is no bundler, no transpiler, no `package.json` under `web/`.

### Routing

`web/js/app.js` implements a hash router:

- `App.navigate('#page')` sets `window.location.hash`.
- `_handleRoute()` resolves the hash to a page module, calls `module.init()` → `module.render()`, and calls `module.destroy()` on the outgoing page.
- Pages register themselves as `window._PAGES['#name']`; the router lazy-loads `js/pages/<name>.js?v=<timestamp>` on first visit and caches the module.
- A monotonic `_routeToken` abandons a stale async route so a slow previous render cannot overwrite the next page's content.
- Legacy deep link `#bidashboard` (and `#bidashboard/<id>`) is rewritten to `#dashboardstudio` / `#blankdash/<id>` — the retired Power BI Studio page no longer ships.

### Page modules — 31 files, 29 registered routes

`web/js/pages/` holds 31 modules. **29** of them register a route in `window._PAGES`; `formula-bar.js` and `formula-engine.js` are shared Excel-workbench modules consumed by `#excel` rather than pages of their own.

`admin.js`, `analysis.js`, `anomalies.js`, `audit.js`, `automation.js`, `blankdash.js`, `catalog.js`, `chat-pro.js`, `chat.js`, `compare.js`, `connect.js`, `dashboard.js`, `dashboardStudio.js`, `excel.js`, `export.js`, `forecast.js`, `lineage.js`, `marketplace.js`, `notebook.js`, `notifications.js`, `org.js`, `quality.js`, `settings.js`, `sql.js`, `stats.js`, `subscription.js`, `upload.js`, `warehouse.js`, `workflows.js` — 29 routes.

Plus the two non-route modules: `formula-bar.js`, `formula-engine.js`.

### The API client — ~340 methods

`web/js/api.js` defines a single global `window.API` with **338 methods** (counted from the `window.API = { … }` object literal). It wraps `fetch()` with:

- a single `_request(method, path, {body, params, headers, silent})` core;
- the API origin resolved from `window.API_BASE_URL` or `window.API_PORT` (default `8002`) — the SPA may be served from another port;
- the app JWT attached as `Authorization: Bearer <token>` when one is present;
- a global loading overlay with an epoch counter (so a route change cannot leave it stuck) and a 15-second failsafe;
- **upgrade-overlay handling**: a 403 carrying `X-Upgrade-Required: true` (or a detail starting with `Upgrade required`) is turned into a single `window.RBAC.showUpgradeOverlay(feature)` toast instead of one error toast per doomed API call.

Representative groups: health/upload, tables/merge/SQL/schema, dashboard KPIs, profiling & quality, the whole `/analysis/*` surface (forecast, decomposition, survival, anomalies, insights, storytelling, segmentation, correlation, normality, hypothesis, regression, feature importance, A/B tests, power analysis, …), dashboards & BI, exports, enterprise (catalog, SLA, cost, budgets, workflows, masking, compliance, org, API keys, audit, SSO), notifications, subscription & trial, LLM dependency management, and the streaming chat.

### Styling and vendored libraries

Tailwind-style utility classes are used directly in the markup (`flex`, `items-center`, `gap-2`, `rounded-lg`, `text-sm`, `bg-indigo-600`, `dark:bg-gray-800`, …) with the custom properties and component styles in `web/css/styles.css`. There is no Tailwind build step — the classes are hand-written and the handful of custom ones (`bg-gradient-to-br`, `shadow-md shadow-indigo-500/25`) are defined in the stylesheet.

`web/vendor/` holds the third-party libraries, all offline:

| Library | Files |
|---|---|
| Chart.js | `chart.umd.min.js`, `chartjs-plugin-datalabels.min.js` |
| D3 | `d3.v7.min.js` |
| Plotly | `plotly.min.js` |
| Leaflet | `leaflet.js`, `leaflet.css` |
| MapLibre GL | `maplibre-gl.js`, `maplibre-gl.css` |
| HyperFormula | `hyperformula.full.min.js` |
| SheetJS (xlsx) | `xlsx.full.min.js` |
| x-spreadsheet | `xspreadsheet.js`, `xspreadsheet.css` |
| Font Awesome | `fontawesome/` (CSS + woff2/ttf) |
| React (dev build) | `react/react.development.js`, `react/react-dom.development.js`, `react/babel.min.js` |
| Icons | `58eaeb4e52248a5c75936c6f4c33a370.svg` |

---

## 3. Navigation map

The top nav (`web/index.html`, `#topnav-nav`) is **Home / Data / Analyze / Tools / Excel / Settings**. Items marked `data-feature="…"` are hidden or locked by `window.RBAC.applyToNav()`; items marked `data-role="admin"` require the admin role.

| Nav item | Pages |
|---|---|
| **Home** | `#dashboard` — landing page (`web/js/pages/dashboard.js`): hero, KPI row (tables / rows / API status / queries), quick-action cards, recent activity |
| **Data** | `#upload` (CSV/XLSX/Parquet ingest), `#warehouse` (table browser, schema, profiling) |
| **Analyze** | `#dashboardstudio` (Power BI-style studio), `#analysis` (the statistical workbench), `#chat` (legacy AI Chat), `#chat-pro` (Chat Pro), `#sql` (SQL Explorer) |
| **Tools** | `#export`, `#compare`, `#anomalies`, `#quality`, `#stats`, `#forecast`, `#connect`, `#lineage`, `#notebook`, `#automation`, `#admin`, `#audit`, `#workflows`, `#marketplace`, `#catalog`, `#org`, `#notifications` |
| **Excel** | `#excel` — the spreadsheet workbench (formula bar, formula engine, x-spreadsheet / HyperFormula) |
| **Settings** | `#settings` — LLM provider, connections, users, appearance |

`#chat` and `#chat-pro` carry the `feature-llm` class: they are hidden entirely when the LLM is disabled (`app.js::_applyFeatureFlags` redirects to `#sql`).

Other routes reachable but not in the top nav: `#subscription` (plan + trial) and `#blankdash` (the surviving dashboard builder, deep-linkable as `#blankdash/<dashId>`).

The mobile nav (`#mobile-nav`) is a reduced subset: Home, Upload, Warehouse, Dashboard Studio, Analysis, AI Chat, Chat Pro, SQL Explorer, Export, Compare, Anomalies, Audit Log, Settings.

---

## 4. `#chat-pro` — Chat Pro

`web/js/pages/chat-pro.js` (1,917 lines) is a **separate, Claude-style conversational analyst UI** that lives beside the legacy `#chat` page (which is untouched). It registers as `window._PAGES['#chat-pro']` and is lazy-loaded by the same `init()` → `render()` → `destroy()` contract as every other page.

**Own stylesheet and namespace.** `web/css/chat-pro.css` (37 KB) is injected on boot with a cache-busting query (`css/chat-pro.css?v=6`) under the element id `cpx-stylesheet`. Every class it defines is prefixed `.cpx-*` (`cpx-app`, `cpx-thread`, `cpx-sidebar`, `cpx-code`, `cpx-icon-btn`, `cpx-new`, `cpx-host`, `cpx-inline`, `cpx-steps`, …). It does **not** touch `styles.css`, `chat.js` or `index.html`.

**What it does**

- Streams from `/llm/chat/stream` (SSE) with a hard fallback to `/ask`, so a question is never left unanswered by a dead stream.
- **Live step events (ADR-006).** The client opts into the `stream_steps` flag on `/llm/chat/stream`; the server then relays orchestrator progress as `{"step": …}` / `{"viz": …}` SSE events *before* the tokens, and **skips** the final `visualizations` batch (charts already streamed — sending both would render every chart twice). The flag absent or false keeps the legacy byte-identical contract for every other consumer.
- A steps panel (`.cpx-steps`) above the charts: one row per step with running / done / failed states, collapsing at the end to a `✓ N steps · M charts · X.Xs` summary.
- Plotly visualizations from `{__visualizations}` stream events and the non-streaming `/ask` response, with a render-retry until Plotly loads. Charts **append** (`vizSpecs.concat(...)`), never replace — replacing `innerHTML` would wipe the existing Plotly canvases.
- A conversation sidebar: date-grouped, searchable, rename/delete inline, collapsed state and scope selection persisted per login.
- A multi-select table scope picker mirrored into the request `tables`.
- A markdown-ish renderer (fenced code with copy, tables, lists, quotes, headings, links) that HTML-escapes before injection.
- Stop, regenerate, edit-and-resend, copy message / whole conversation.
- Best-effort server sync via `/user/conversations`, staying local-only when the API is unavailable.
- A non-string stream object can never concatenate into the answer text (`typeof token !== 'string'` guard).

> **Reachability.** Chat Pro used to be reachable only by typing `#chat-pro` into the URL bar. **It is now in the nav** — `web/index.html` lists it under *Analyze* ("Chat Pro") and in the mobile nav ("Chat Pro"), both with the `feature-llm` class.

Reference: `docs/decisions/ADR-006-live-agentic-chat-stream-steps.md` and `docs/decisions/ADR-007-chat-pro-qa-hardening.md`.

---

## 5. The auth gate

`web/js/auth.js` (414 lines) gates the whole SPA.

**Flow**

1. `Auth.init()` reads the token and user from storage and verifies them with `GET /auth/verify`. Stale tokens are **cleared, never trusted**.
2. Without a valid session it builds a login overlay with up to two paths:
   - **"Continue with Google"** — native OAuth popup (`GET /auth/google/login` → `postMessage` → `GET /auth/google/login/result`). No Firebase. The button only appears when `GET /auth/google/login/status` reports the provider is enabled.
   - **Email / password** — **central accounts** when the billing server is configured (`POST /auth/accounts/login`, `POST /auth/accounts/register`; the availability comes from `GET /auth/accounts/status`), otherwise **local username/password** (`POST /auth/login`, `POST /auth/register`).
3. The token, user and plan are persisted via `window.RBAC`, then the page reloads.
4. `Auth.signOut()` clears the local session (`auth_token`, `session`, `plan` from `localStorage`).

**The three identity paths** are therefore: Google OAuth, central (server-side, bcrypt-hashed) accounts, and local username/password.

**Entitlements** live in `web/js/components/rbac.js`: `RBAC.setEntitlements()`, `getEntitlements()`, `hasFeature(feature)`, `applyToNav()` (hides/locks `data-feature` elements), `gateElement()`, and `showUpgradeOverlay(feature)`. `app.js` fetches `/config` on boot and on window focus, so a Pro unlock lands without a reload after returning from an external payment browser.

---

## 6. `web/ts/` — the typed BI layer

`web/ts/` is a **separate, typed layer** for BI dashboards and charts. It is not part of the shipped bundle (the SPA has no build step) — it is the typed reference implementation of the same behaviour.

```
web/ts/
  bidashboard/
    types.ts        # DashboardState, LayoutConfig, Widget, WidgetType (19 widget kinds), …
    state.ts        # dashboard state store
    grid.ts         # collision-free grid geometry (band-packing, explicit col/row, clamping)
    renderer.ts     # widget rendering
    widgetEditor.ts # the widget property editor (37 KB)
    filters.ts      # slicer / filter model
    templates.ts    # dashboard templates
    utils.ts        # genWidgetId, esc, …
  charts/
    index.ts        # registry: widget type → renderer name on window.D3Charts
    theme.ts        # light/dark palette (Power BI colours: #7B61FF / #00D4AA / #FF6B6B)
    bar.ts, line.ts, area.ts, pie.ts, doughnut.ts, scatter.ts, bubble.ts, radar.ts,
    heatmap.ts, gauge.ts, funnel.ts, treemap.ts, waterfall.ts, combo.ts, kpi.ts,
    sparkline.ts, table.ts, text.ts, crossfilter.ts, tooltip.ts
```

Each chart module registers itself on the `window.D3Charts` global at bundle time; `charts/index.ts` maps the widget type to a renderer name and looks it up there. The types mirror the shapes that flow through `API.biQuery()`, `DashboardGrid`, `DashboardRenderer` and the template engine — the vanilla-JS originals live in `web/js/components/` (`dashboardGrid.js`, `dashboardRenderer.js`, `widgetEditor.js`), and the shipped `blankdash.js` notes that its palette "mirrors `web/ts/charts/theme.ts`".

**Why it exists:** the D3 chart family and the dashboard builder are the most intricate part of the front end; TypeScript gives them a real type contract (widget types, layout config, filter maps) without forcing a build step on the rest of the app. Keep changes to the two layers in sync — the TS files are the specification, the JS files are what ships.

---

## 7. Onboarding

`web/js/app.js::_checkOnboarding()` runs on boot and shows a **four-step wizard** unless `localStorage.onboarding_completed === "true"` (or the URL carries a `?dash=` deep link, which skips it — sharing and screenshots).

| Step | Title | What it does |
|---|---|---|
| 1 | Welcome to DataAgent | Orientation |
| 2 | **Set up your local AI** | Runs a **live `/llm/dependencies` check** and offers a one-click Ollama install + model pull |
| 3 | Upload Your Data | Deep-links to `#upload` |
| 4 | Ask Questions | Deep-links to `#chat` |

**Step 2** (`_runLlmSetupStep`) calls `GET /llm/dependencies` and reads `deps.ollama`:

- `server_reachable` **and** at least one model → "✓ Local model ready (<model>)".
- Not installed → an **"Install Ollama for me"** button → `POST /llm/ollama/install` returns a `job_id`; the wizard polls `GET /llm/ollama/install/{job_id}` every 1.5s and, on `done`/`completed`/`success`, continues to the start step. A failed job shows the error and points the user at Settings.
- Installed but not running (or no model) → a **"Start Ollama"** button → `POST /llm/ollama/start`, then `POST /llm/ollama/pull` with the model from `window.ONBOARDING_MODEL` (default `qwen2.5:7b`, ~4.7 GB).

The step is **skippable** — SQL, Excel, dashboards and exports all work with no model at all, and the app falls back to rule-based answers.

Endpoints backing it (router prefix `/llm`, `api/llm_deps.py`):

| Endpoint | Purpose |
|---|---|
| `GET /llm/dependencies` | provider/Ollama/LM Studio/WARP status + installed models |
| `POST /llm/ollama/install` | start the Ollama installer (returns a `job_id`) |
| `GET /llm/ollama/install/{job_id}` | installer job status |
| `POST /llm/ollama/start` | start the Ollama server |
| `POST /llm/ollama/pull` | pull a model (returns a `job_id`) |
| `GET /llm/ollama/pull/{job_id}` | pull progress |

---

## 8. The desktop shell (`shell/`)

`python run.py shell` → `shell/app.py::run_shell()`.

| Module | Responsibility |
|---|---|
| `shell/app.py` | Orchestration: preflight → single-instance → port → backend thread → window → tray → `webview.start()` |
| `shell/runtime_check.py` | Preflight: writable per-user data dir + Edge **WebView2** runtime (Windows) |
| `shell/single_instance.py` | Windows named mutex `Local\DataAgentShell`; POSIX `fcntl.flock` fallback |
| `shell/port.py` | Port resolution — pins `settings.app.port` (8002); reuses an existing DataAgent backend on that port; refuses a foreign process |
| `shell/server.py` | `BackendThread` — uvicorn in a thread, `wait_ready()`, `url` |
| `shell/window.py` | pywebview window (1400×900, min 1024×700) + the splash → app navigation watcher |
| `shell/tray.py` | pystray icon (Windows only) with Open in browser / Open logs folder / About / Quit |
| `shell/bridge.py` | `window.pywebview.api` — `get_info()`, `open_browser()`, `quit()`, `tail_log(n)` |
| `shell/update.py` | Update check against `https://api.github.com/repos/DataAgent/DataAgent/releases/latest` |
| `shell/paths.py` | `resource_path()` — resolves bundled resources in frozen builds |
| `shell/splash.html` | The splash screen |

**Dynamic port allocation.** Strictly speaking the shell *pins* the port rather than allocating one dynamically — the SPA hardcodes the API origin to 8002 (`web/js/api.js` reads `window.API_PORT`, default `"8002"`), so drifting to a free port would break the client. The escape hatch: if the backend ends up on a different port, the window navigates to `/?api_port=<port>` and the SPA reads it.

**Preflight.** `shell/runtime_check.py::preflight()` returns `(ok, messages)`. A failure exits 1 with a Windows MessageBox ("Microsoft Edge WebView2 runtime not found…").

**Update check.** `shell/update.py::update_available()` compares `app.version` from `config/settings.yaml` against the latest GitHub release tag (5s timeout, `User-Agent: DataAgent`). It never raises and never blocks startup; offline it reports no update.

**Frozen builds.** `run.py::_frozen_bootstrap()` chdirs to `%LOCALAPPDATA%\DataAgent` (or `$XDG_DATA_HOME/DataAgent`) so the installer-installed exe under Program Files can write `data/`, `logs/` and `outputs/`; bundled config files are copied from `_MEIPASS` on first run so user edits survive a reinstall. In windowed builds `sys.stdout` is `None`, so stdout/stderr are redirected to `logs/runtime-stdout.log`.

---

## 9. The marketing site (`website/`)

Static HTML — no build step, no framework. 14 pages: `index.html`, `about.html`, `billing.html`, `blog.html`, `blog-post.html`, `changelog.html`, `comparison.html`, `contact.html`, `docs.html`, `download.html`, `faq.html`, `features.html`, `security.html`, `usecases.html`.

**Libraries** (`website/js/` + `website/vendor/`):

| Library | Where | Purpose |
|---|---|---|
| **Three.js 0.186.1** | `website/vendor/three/build/three.module.js` + `three.core.js` + `examples/jsm/postprocessing/*` | WebGL hero background (`js/fx-webgl.js` — `WebGLRenderer`, `EffectComposer`, `RenderPass`, `UnrealBloomPass`, `OutputPass`). Loaded through an import map: `{"three": "vendor/three/build/three.module.js", "three/addons/": "vendor/three/examples/jsm/"}` |
| **GSAP 3.13.0** | CDN (`gsap.min.js`, `ScrollTrigger.min.js`, `SplitText.min.js`) | Scroll animations, text splitting, counters |
| **Lenis 1.3.4** | `website/vendor/lenis.min.js` (vendored offline, CDN fallback) | Smooth scroll, wired into GSAP's ticker and `ScrollTrigger.update()` in `js/fx-core.js` |

`js/fx-core.js` owns the shared motion layer (Lenis ⇄ ScrollTrigger bridge, reduced-motion handling, `FX.scrollTo`, `FX.gsap`/`FX.st` capability flags); `js/fx-webgl.js` is the Three.js module.

### The pricing page and checkout wiring

`website/billing.html` + `website/js/billing.js` implement the storefront.

```html
window.RAZORPAY_PAYMENT_LINKS = { lifetime: '' };
// window.BILLING_API_URL = 'https://billing.yourdomain.com';
```

| Variable | Meaning |
|---|---|
| `window.BILLING_API_URL` | The deployed billing server. Defaults to `http://127.0.0.1:8090` (local dev only). |
| `window.RAZORPAY_PAYMENT_LINKS.lifetime` | Optional no-code fallback — a Razorpay Payment Link for **Lifetime only** (a Payment Link cannot create an auto-renewing subscription). |
| `window.EXTERNAL_BILLING_URL` | Optional "buy on our billing site" link. |

**When `BILLING_API_URL` is unset or points at localhost** (`http://localhost…`, `http://127.0.0.1…`, `http://[::1]…`), the page detects the loopback URL, sets `BILLING_READY = false`, and renders a **"Checkout is not enabled on this deployment — no billing server is configured, so no payment can be taken here"** notice instead of a broken Pay button. The notice points the visitor at the external billing site (if configured) or at the Docker self-host download. Clicking Buy on an unconfigured deployment shows a toast to the same effect. This is deliberate: a checkout POST to a loopback address never reaches a real billing server — it just fails in the visitor's browser.

**When it is configured**, the flows are:

- **Pro** → `POST {API}/create-subscription` → Razorpay Checkout in *subscription* mode → handler → `POST {API}/verify-subscription` → `{API}/success?subscription_id=…`. Keyless: the plan follows the buyer's email, renews monthly, cancel anytime.
- **Lifetime** → `POST {API}/create-order` → Razorpay Checkout popup → handler → `POST {API}/verify` → `{API}/success?order_id=…`. Keyless: entitlement follows the buyer's email.

Both handlers show a `payment.failed` toast ("Payment failed — no money was taken") and a dismiss toast. A server may also return a `checkout_url` (a hosted checkout page), in which case the browser is redirected instead of opening the popup.

**Everything is same-origin.** The billing server has no CORS middleware, so the storefront and the API must share a domain — see `docs/deployment/billing-server.md`.

---

## Quick reference

| Thing | Where |
|---|---|
| SPA shell / nav | `web/index.html` |
| Router, onboarding, feature flags | `web/js/app.js` |
| API client (`window.API`) | `web/js/api.js` |
| Login gate (`window.Auth`) | `web/js/auth.js` |
| Entitlements + upgrade overlay (`window.RBAC`) | `web/js/components/rbac.js` |
| Chat Pro page | `web/js/pages/chat-pro.js` |
| Chat Pro styles | `web/css/chat-pro.css` (`.cpx-*`) |
| Typed BI/chart layer | `web/ts/bidashboard/`, `web/ts/charts/` |
| Desktop shell | `shell/` |
| Marketing site | `website/` |
| Marketing checkout | `website/js/billing.js`, `website/billing.html` |
| Live chat contract | `docs/decisions/ADR-006-live-agentic-chat-stream-steps.md` |
