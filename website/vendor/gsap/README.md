# Vendored GSAP 3.13.0

These files are byte-for-byte copies of the GSAP 3.13.0 distribution that the
marketing site used to load from `cdn.jsdelivr.net`. They are served from this
folder so that **no page view makes a request to a third-party origin** — the
product is local-first and its own site must not leak a visitor's IP to a CDN.

## Licence

GSAP and its plugins are distributed under the **GreenSock Standard License**
(<https://gsap.com/standard-license>), copyright 2025 GreenSock. The banner is
reproduced verbatim at the top of each file:

```
@license Copyright 2025, GreenSock. All rights reserved.
Subject to the terms at https://gsap.com/standard-license.
```

GSAP 3.13 (May 2025) made the former "Club" plugins — including **SplitText** —
free under that licence, so all three files below are redistributable.

## Files

| File | Bytes | SHA256 | Upstream |
|---|---:|---|---|
| `gsap.min.js` | 72435 | `96c01b81f44a3290e2b4532f55e2c9534b2adc43273a19f3756b2cb41f0fd0b6` | <https://cdn.jsdelivr.net/npm/gsap@3.13.0/dist/gsap.min.js> |
| `ScrollTrigger.min.js` | 44157 | `308219390e5e3b84cda0c481e70caa9820883ae10bda44e6e9a149a81aac4b3f` | <https://cdn.jsdelivr.net/npm/gsap@3.13.0/dist/ScrollTrigger.min.js> |
| `SplitText.min.js` | 7247 | `5e519ea2470faa15ca4d4f27e9263e5906c07c034a9b4771164dee108136563a` | <https://cdn.jsdelivr.net/npm/gsap@3.13.0/dist/SplitText.min.js> |

Upstream package: <https://www.npmjs.com/package/gsap> (version `3.13.0`).

`SplitText.min.js` is vendored so that the local mirror of the old CDN set is
complete and nothing 404s, but the motion engine (`website/js/fx-core.js`) does
**not** use `window.SplitText` — it splits headings with its own
`.fx-line` masking. The file is kept only so the vendored set matches what the
pages referenced before.

## How the pages load them

Every page in `website/` loads the three files with plain (non-module) script
tags, in this order, immediately before `js/main.js`:

```html
<script src="vendor/gsap/gsap.min.js"></script>
<script src="vendor/gsap/ScrollTrigger.min.js"></script>
<script src="vendor/gsap/SplitText.min.js"></script>
```

`ScrollTrigger.min.js` registers itself on `window.ScrollTrigger` and calls
`gsap.registerPlugin`, so `window.gsap` and `window.ScrollTrigger` are both
truthy by the time `js/fx-core.js` runs.

## Verifying the hashes

```powershell
Get-FileHash -Algorithm SHA256 website\vendor\gsap\*.js
```

The guard in `tests/test_launch_site_motion.py` re-computes these digests on
every test run and fails if a vendored file is edited, truncated or replaced.
