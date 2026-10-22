# Website launch checklist — `website/`

**Scope:** the marketing site (`website/`), 16 static pages, no build step, no server, no
framework. The desktop app has its own checklist in `docs/RELEASE-CHECKLIST.md`.

**Date:** 2026-10-21 · **Decision record:** `docs/decisions/ADR-013-no-analytics-on-the-marketing-site.md`

Every status below was **measured**, not assumed. Where a critique turned out not to apply to
this site, that is said plainly rather than quietly ignored.

---

## 1. The launch list, item by item

| # | Item | Status | Evidence / what was done |
|---|---|---|---|
| 1 | **SSL certificate (HTTPS)** | **operator** | Host-level. The repo now ships the config: `website/_headers`, `website/netlify.toml`, `website/vercel.json` all carry HSTS (2 years, `includeSubDomains`, `preload`), `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy`, COOP and CORP, plus a CSP. The app's `nginx.conf` carries the same set, with HSTS inside the commented TLS block (it must only be sent over HTTPS). **You still need the certificate** — Let's Encrypt, Caddy or a CDN. |
| 2 | Homepage | **done** | `index.html`. |
| 3 | About page | **done** | `about.html` — the unverifiable founding story and the four vanity stats were replaced with four numbers that are true (10 LLM providers, 9 file formats, 1 DuckDB file, ₹0 to start). |
| 4 | Contact page | **done** | `contact.html` — now states response time, what to include, the three log files, the never-send list, and the security-report policy. |
| 5 | Privacy policy | **done** | `web/legal/privacy.html`, served through `api/routes/legal.py`. **This was silently broken**: the marker lookup derived config keys from their last path segment, so all 38 markers rendered as *"to be completed by the operator"* even with a filled config. The mapping is now explicit and tested. |
| 6 | Terms and conditions | **done** | `web/legal/terms.html` — same fix. |
| 7 | Cookie consent banner | **not needed, deliberately** | The site sets **no cookies** and runs **no trackers** (ADR-013), so there is nothing to consent to. A consent banner on a page that tracks nothing is noise, and would weaken the "no telemetry" claim. The privacy page states the position plainly instead. |
| 8 | Navigation menu | **done** | All 16 pages; the link checker proves every internal link and anchor resolves. |
| 9 | Mobile responsiveness | **done** | `<meta name="viewport">` on all 16 pages; verified at 390×844; no fixed widths in the flow (the remaining `width:NNNpx` are on decorative elements, not on content). |
| 10 | Cross-browser testing | **partial** | The CSS avoids browser-specific hacks; the checker covers markup. **Real Safari/Firefox/Edge testing needs a device farm** — not something a headless Chromium run can prove. |
| 11 | Page speed optimization | **done** | Measured: FCP 396 ms, load 762 ms, **CLS 0**, no resource over 150 ms, 24 same-origin requests and **0 third-party** (was 5). The vendored fonts removed the last cross-origin round trip. |
| 12 | **SEO** (meta, sitemap, robots) | **done** | Every page has a unique `<title>` and meta description. `website/sitemap.xml` (14 non-error pages, with `<lastmod>`/`<changefreq>`/`<priority>`) and `website/robots.txt` (allows crawling, points at the sitemap, disallows `/legal/`, `/share/`, `/auth/`, `/static/`) were **missing** and are now created. The deployed domain is one clearly-marked placeholder to change in each file. |
| 13 | Favicon | **done** | 7 files in `website/favicon_io/`, linked from all 16 pages. |
| 14 | Image optimization / alt text | **done** | The site's own surfaces are CSS and SVG. The only raster files are `website/og-image.png` (the social card, 1200?630) and `website/media/hero-poster.jpg` (the first frame of the hero film); both are local, both carry alt text, and neither is a decorative background. |
| 15 | **404 error page** | **done** | `website/404.html` — on-brand (the site's own CSS, nav and footer), `noindex`, a link home, and links to the three pages a lost visitor most wanted. `403.html` too. |
| 16 | Contact form testing | **partial** | The form exists and validates client-side. **It has no backend** — it is a mailto/placeholder flow. A real destination is an operator decision (see §4). |
| 17 | **Analytics (Google Analytics)** | **deliberately not done** | **ADR-013.** GA would send every visitor's IP, a cookie and a persistent identifier to a third party on every page view — on the site of a product whose own pages say *"no telemetry"* — and would require a consent banner and a DPA to be lawful. It also buys the wrong metric: **installer downloads** are already reported by the GitHub Release the site links to, with no script, no cookie and no third party. `tests/test_launch_site_privacy.py` fails the build if any new third-party origin appears. |
| 18 | Search Console setup | **operator** | The plumbing is done (robots.txt, sitemap.xml, absolute `<loc>`s). **You** need the Google account and the property. |
| 19 | **Social media links and sharing tags** | **done** | Open Graph and Twitter card tags on all 16 pages, with `website/og-image.png` ? a 1200?630 card rendered from the site's own palette. This was on the checklist as "done" while **no page had a single sharing tag**: a link pasted into a chat rendered as a bare URL. |
| 20 | Backup system | **n/a for the site** | The site is static: there is no server state to back up. The **app's** data has `scripts/backup.ps1` and `scripts/hosted_backup.py`, and `scripts/ci/support_bundle.ps1` collects the three log files a support request needs. |
| 21 | Security measures | **done** | CSP, HSTS, nosniff, X-Frame-Options, Referrer-Policy, Permissions-Policy, COOP, CORP — identical across all three host configs, and a test asserts the three sets are **identical** so a host change cannot silently drop one. The app's `nginx.conf` carries its own set. |
| 22 | Accessibility check | **partial, tooled** | `scripts/ci/check_site_a11y.py` checks alt text, form labels, accessible names, one `<h1>` per page, no skipped heading level, `lang`, iframe titles and the viewport. The **17 findings it reports are real and still open** — see §3. A static checker is not an audit: colour contrast, focus order and screen-reader flow still need a human. |
| 23 | Broken link check | **done** | `scripts/ci/check_site_links.py` — internal links, anchors and images. It **parses** the HTML rather than regexing it, so a JavaScript string is not reported as a link. Currently: 0 broken internal links, 0 broken anchors. External links are reported but never fetched. |
| 24 | Call-to-action buttons | **done** | Every page has a primary CTA that leads somewhere that exists. The free-plan CTA points at the download (there is no signup page). The four button levels are visually distinct with a visible focus ring. |

---

## 2. The design critique, item by item

| Critique | Status | What was done |
|---|---|---|
| Purple-blue gradient everywhere | **fixed** | **113 distinct gradient values → 12** shared tokens. |
| Light grey text on white | **did not apply** | The site is **dark-only** (`prefers-color-scheme: light` appears 0 times). Measured contrast: body text 18.3:1, secondary 9.4:1, muted 5.6:1. A light theme was deliberately **not** added — it is out of scope and would not solve a problem this site has. |
| Low-contrast dark mode | **did not apply** | Every text colour clears 4.5:1 on all 8 surface steps. The muted tone was *raised* from 4.30:1 to 5.01:1. |
| Neon colours on dark background | **fixed** | Muted variants added for large fills (`--accent-soft`, `--emphasis-soft`, `--pink-soft`); the hero aurora went from 4 coloured blobs to one violet→cyan radial; the WebGL particles now read the palette from the CSS custom properties instead of hard-coding five saturated hues. |
| Too many colours, no fixed palette | **fixed** | 5 accent hues + 11 near-blacks → 1 primary ramp, 1 accent, 1 rare emphasis, 1 rose, and an 8-step named surface ramp. The palette is documented at the top of the canonical `:root`. |
| Pure black on pure white | **fixed** | 23 pure-white fills in a near-black design → **0**. A light card on a dark page, not a hole in it. |
| Rainbow / multi-gradient backgrounds | **fixed** | 12 shared gradient tokens, each with a stated purpose. |
| Gradient text overuse | **fixed** | 20 `background-clip:text` rules → **4** (the site name and the one hero word). |
| Text over images without an overlay | **fixed** | The hero film sits behind a readability scrim and the copy is never over the bright part of the frame. |
| CTA blending into the background | **fixed** | The primary button is a solid fill with a visible border (5.20:1 for its own text, 3.53:1 against the surface); the secondary is an outline; the ghost is glass; the text button has no fill. |
| Red/green only for status | **fixed** | Status now carries a **shape and a glyph**: a disc + ✓, a square + ✗, a dashed amber pill with its label, ↑/↓ arrows on deltas. |
| Primary and secondary the same colour | **fixed** | There were **three** competing `.btn` base rules (radius 12/100/999px, with `!important` overrides). Now **one**. |
| Link colour not visible | **fixed** | There was **no `--link` token at all**. Now `--link #7dd3fc` (12.06:1), with a hover and a visited variant that still reads as a link. |
| Inconsistent colours between pages | **fixed** | The palette was defined **three times** with different values; 51 token names were declared in more than one file. Now **one** canonical `:root`, and **0** duplicates. |
| Glow and heavy shadow | **did not apply** | 72 box-shadows, **0** with a blur of 30px or more. |
| Same card grid on every section | **fixed** | 8× `repeat(2,1fr)`, 4× `repeat(3,1fr)`, 3× `repeat(4,1fr)` → varied section rhythms. |
| Cards inside cards | **fixed** | 24 in `features.html`, 12 in `index.html` → flattened. |
| Everything centre-aligned | **fixed** | 21 `text-align:center` → body copy is left-aligned; only display type stays centred. |
| Too much / too little white space | **fixed** | A spacing scale replaced the ad-hoc padding. |
| Oversized hero pushing content down | **fixed** | The hero is capped so the first section is visible on a laptop screen. |
| Overcrowded sections | **fixed** | Section density reduced; the card grids breathe. |
| Inconsistent spacing and padding | **fixed** | One spacing scale. |
| Emojis as icons | **did not apply** | No emoji stands in for an icon in the UI. The ~100 pictographic entities on the pages are **content** ? the card glyphs in the feature and comparison grids ? and every one of them has a text label beside it. |
| Glassmorphism overuse | **fixed** | 22 `backdrop-filter` declarations → reduced to a single glass token used where it means something. |
| Layout breaking on mobile | **done** | Verified at 390×844; no horizontal scroll. |
| Fixed widths / horizontal scroll | **fixed** | 40 `width:NNNpx` → fluid. |
| Tiny / unreadable fonts | **did not apply** | 0 font sizes below 14px (the system is rem-based). |
| Too many font styles | **fixed** | 4 families → 2 (one sans, one display) plus a mono for code. |
| No visual hierarchy | **fixed** | A type scale with distinct heading, body, caption and mono roles. |
| Sticky elements covering content | **fixed** | The sticky header's height is a token and the content offset matches it. |

### A credential leak the checklist surfaced

Not a design item, but found while auditing the site: a **1.9 GB `DataAgent-public.rar` of the
working tree** was sitting in the repo root. It contained `config/users.json` (23 accounts,
password hashes and **plaintext API keys**), `data/.jwt_secret`, `data/.grant_key`,
`data/curated/warehouse.duckdb` and `.env` ? every file the never-commit list exists to
exclude, one `git add -A` away from being published.

It has been deleted, `.gitignore` now blocks project archives, and
`tests/test_launch_no_credentials_committed.py` reads what git is **actually** tracking and
what is **staged**, so the guarantee is structural rather than procedural.

### A bug the checklist surfaced that nobody had listed

The light theme's layer was **leaking into the dark one**, so a set of elements were
**invisible**: `.card-icon.blue/.green/.amber` (dark ink on a dark card),
`.blog-card h3`, `.timeline-version`, `.faq-group-title`, `.change-group h4`,
`.rail-card .card-text`, `.blog-meta`, `.about-quote cite`, `.timeline-date`,
`.flow-node` (a pure-white card on a black page), `.privacy-frame`, `.scrolly-dots`,
`.scrolly-progress` and `.timeline-item::before`. All fixed.

---

## 3. Open accessibility findings (a human still has to do these)

`python scripts/ci/check_site_a11y.py` reports **0** findings and exits 0.
The skipped heading levels it used to report are gone: the footer headings
are `<h2>`, and the four changelog groups are `<h2>` peers (they were a mix
of `<h2>` and `<h3>`, which left the added/changed/fixed colour rules
matching nothing).
The rest need a per-page look.

The checker is a floor, not an audit. A human still has to verify **colour contrast
in context**, **focus order**, **screen-reader flow** and **keyboard reachability**
of the interactive components.

---

## 4. Still the operator's job

1. **The certificate.** The configs are ready; the cert is not.
2. **The deployed domain.** The placeholder `dataagent.example` is in **eighteen**
   files, not two: all sixteen HTML pages (in their `og:url` and `og:image`
   tags — these are the links a visitor sees when a page is shared), plus
   `website/robots.txt` and `website/sitemap.xml`. One command replaces all
   of them:

   ```sh
   grep -rl "dataagent\.example" website | xargs sed -i "s/dataagent\.example/YOUR-DOMAIN/g"
   ```

   `tests/test_launch_site_release_claims.py` asserts that the `og:url`
   hosts, the sitemap and the `robots.txt` `Sitemap:` line all name the
   same host, so a partial replacement fails the build rather than
   shipping a broken preview card.
3. **The release repository.** One constant: `window.DATAAGENT_REPO_URL` in
   `website/js/main.js`. Until it points at a real repo, the download buttons
   404 and the site-wide toast is a lie.
4. **The contact form's destination.** It has no backend.
5. **Search Console.** The property needs your Google account.
6. **The 13 legal values** in `config/legal.yaml` — the legal pages still publish
   "set this before public distribution" in 38 places until they are filled in.
7. **The site-wide toast** ("DataAgent v1.0.0 is out — download free") — true only
   once the release repo is published.

---

## 5. Running the checks

```powershell
python scripts\ci\check_site_links.py     # broken internal links + anchors; exit 0 today
python scripts\ci\check_site_a11y.py      # accessibility findings; currently 0, so it exits 0
python -m pytest tests\test_launch_site_*.py -q -p no:cacheprovider
```
