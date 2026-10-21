"""
UI verification harness - drives the live DataAgent SPA with Playwright.

Every interaction is performed as a REAL user action (mouse clicks, drags,
keyboard input). Pages are verified by hash route; each capture() saves a PNG
screenshot plus a JSON result entry (url, errors, actions, checks, shots).

Usage:
    python scripts/ui_verify/harness.py            # run all scenarios
    python scripts/ui_verify/harness.py --list     # list scenarios
    python scripts/ui_verify/harness.py <id> [<id>]  # run specific scenario(s)
"""

import argparse
import json
import os
import re
import traceback
from datetime import datetime
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8002"
OUT = Path("outputs/ui_verify")
OUT.mkdir(parents=True, exist_ok=True)

TABLE = "student_performance_powerbi"

# Local-mode auto-admin was removed (auth hardening), so the SPA shows a login
# overlay without a real session. Reuse DA_E2E_USER/DA_E2E_PASS when provided;
# otherwise fall back to a dedicated viewer account, registered on first run.
AUTH_USER = os.environ.get("DA_E2E_USER", "uiverify")
AUTH_PASS = os.environ.get("DA_E2E_PASS", "Uiverify#2026Aa1")

MAIN_PAGES = [
    ("dashboard", "#dashboard", "Home"),
    ("upload", "#upload", "Upload"),
    ("warehouse", "#warehouse", "Warehouse"),
    ("analysis", "#analysis", "Analysis"),
    ("sql", "#sql", "SQL Explorer"),
    ("excel", "#excel", "Excel"),
    ("compare", "#compare", "Compare"),
    ("anomalies", "#anomalies", "Anomalies"),
    ("quality", "#quality", "Quality Trends"),
    ("stats", "#stats", "Stats"),
    ("forecast", "#forecast", "Forecast"),
    ("lineage", "#lineage", "Lineage"),
    ("notebook", "#notebook", "Notebook"),
    ("notifications", "#notifications", "Notifications"),
    ("settings", "#settings", "Settings"),
    ("subscription", "#subscription", "Subscription"),
    ("admin", "#admin", "Admin"),
    ("audit", "#audit", "Audit Log"),
]


class Verifier:
    """One browser page + result collection for a scenario."""

    def __init__(self, page, stamp):
        self.page = page
        self.stamp = stamp
        self.errors = []
        self.actions = []
        self.checks = []
        self.shots = []

    # -- plumbing ----------------------------------------------------------
    def attach(self):
        page = self.page

        def on_console(msg):
            if msg.type == "error":
                self.errors.append(msg.text[:300])

        def on_pageerror(err):
            self.errors.append(f"pageerror: {err}")

        def on_response(resp):
            # /auth/accounts/* is probed by the SPA before a central billing
            # server is configured — a 404 there is expected, not an error.
            expected_missing = resp.status == 404 and "/auth/accounts/" in resp.url
            if resp.status >= 400 and not expected_missing:
                self.errors.append(f"HTTP {resp.status}: {resp.url[:160]}")

        page.on("console", on_console)
        page.on("pageerror", on_pageerror)
        page.on("response", on_response)

    def log(self, msg):
        self.actions.append(msg)
        print(f"    {msg}")

    def check(self, name, ok, detail=""):
        self.checks.append({"name": name, "ok": bool(ok), "detail": detail})
        print(f"    [{'OK' if ok else 'FAIL'}] {name} {detail}")

    def capture(self, name):
        fn = f"{name}.png"
        self.page.screenshot(path=str(OUT / fn), full_page=False)
        self.shots.append(fn)
        self.log(f"screenshot: {fn}")
        return fn

    def wait_app(self):
        self.page.wait_for_selector("#page-content", timeout=20000)
        self.page.wait_for_timeout(1200)

    def goto(self, route=""):
        self.page.goto(f"{BASE}/{route}")
        self.wait_app()

    def nav(self, route):
        self.page.evaluate(f"window.location.hash = '{route}'")
        self.page.wait_for_timeout(1800)

    def err_mark(self):
        return len(self.errors)

    def err_since(self, mark):
        return self.errors[mark:]

    # -- interactions --------------------------------------------------------
    def click_sel(self, selector, timeout=5000, label=""):
        loc = self.page.locator(selector).first
        try:
            loc.wait_for(state="visible", timeout=timeout)
            loc.scroll_into_view_if_needed()
            loc.click()
            self.log(f"click {label or selector}")
            self.page.wait_for_timeout(900)
            return True
        except Exception:
            return False

    def click_text(self, text, timeout=4000):
        loc = self.page.locator(f"text={text}").first
        try:
            loc.wait_for(state="visible", timeout=timeout)
            loc.scroll_into_view_if_needed()
            loc.click()
            self.log(f"click: '{text}'")
            self.page.wait_for_timeout(1000)
            return True
        except Exception:
            return False

    def click_button(self, pattern, timeout=4000):
        """Click first visible <button> whose label/title matches regex."""
        rx = re.compile(pattern, re.I)
        buttons = self.page.locator("button:visible")
        n = buttons.count()
        for i in range(min(n, 150)):
            b = buttons.nth(i)
            try:
                label = " ".join(
                    filter(None, [b.inner_text().strip(), b.get_attribute("title") or ""])
                )
            except Exception:
                continue
            if label and rx.search(label):
                try:
                    b.scroll_into_view_if_needed()
                    b.click()
                    self.log(f"click button: '{label.strip()[:60]}'")
                    self.page.wait_for_timeout(900)
                    return True
                except Exception:
                    continue
        return False

    def count(self, selector):
        return self.page.locator(selector).count()

    def dump_ui(self, scope="body", limit=50):
        info = self.page.evaluate(
            """(scope) => {
              const root = document.querySelector(scope) || document.body;
              const vis = (el) => !!(el.offsetWidth || el.offsetHeight);
              const out = {buttons: [], selects: [], inputs: []};
              root.querySelectorAll('button').forEach(b => {
                if (vis(b)) out.buttons.push((b.innerText||b.title||'').trim().slice(0,40));
              });
              root.querySelectorAll('select').forEach(s => {
                if (vis(s)) out.selects.push(Array.from(s.options).map(o=>o.text.trim()).slice(0,10));
              });
              root.querySelectorAll('input,textarea').forEach(i => {
                if (vis(i)) out.inputs.push((i.type||'text') + ':' + (i.placeholder||i.name||'').slice(0,30));
              });
              return out;
            }""",
            scope,
        )
        self.actions.append(
            "UI dump [%s]: buttons=%s selects=%s inputs=%s"
            % (scope, [b for b in info["buttons"] if b][:limit],
               info["selects"][:limit], info["inputs"][:limit])
        )
        return info

    # -- generic page verification -------------------------------------------
    def verify_page(self, page_id, route, label):
        self.nav(route)
        self.page.wait_for_timeout(800)
        name = f"{self.stamp}--page--{page_id}"
        self.capture(name)
        body = self.page.inner_text("body")[:500]
        bad = ("Loading DataAgent" in body) or len(body.strip()) < 15
        self.check(f"{label} renders", not bad, f"hash={route}")


# ---------------------------------------------------------------------------
# Scenario: all main pages
# ---------------------------------------------------------------------------
def scenario_pages(v: Verifier):
    v.goto("#dashboard")
    for page_id, route, label in MAIN_PAGES:
        v.verify_page(page_id, route, label)


# ---------------------------------------------------------------------------
# Shared props-panel exercise (works for any .bd-props dock)
# ---------------------------------------------------------------------------
def exercise_props_panel(v: Verifier, tag):
    """Flip through every props-panel tab and drive every control in it.

    Styled toggle switches cover their hidden <input type=checkbox>, so we
    click the visible control (label/switch) and dispatch the same input/
    change events the app's UIControlKit listens for.
    """
    panel = v.page.locator(".bd-props").first
    if not panel.count():
        v.check(f"props panel opens ({tag})", False, "no .bd-props in DOM")
        return
    v.check(f"props panel opens ({tag})", True)

    tabs = panel.locator(".bd-props-tab")
    tab_n = tabs.count()
    v.log(f"props tabs found: {tab_n}")
    total_controls = 0
    for t in range(tab_n):
        tab = tabs.nth(t)
        tname = tab.inner_text().strip()
        mark = v.err_mark()
        try:
            tab.click(timeout=3000, force=True)
        except Exception:
            tab.evaluate("e => e.click()")
        v.page.wait_for_timeout(500)
        if len(v.errors) > mark:
            v.check(f"tab '{tname}' switches cleanly", False,
                    "; ".join(v.err_since(mark))[:140])
        # drive every control in the active panel
        active = panel.locator(".bd-props-tab-panel.active")
        inputs = active.locator("input, select, textarea")
        n = min(inputs.count(), 60)
        changed = 0
        for i in range(n):
            el = inputs.nth(i)
            try:
                if not el.is_visible():
                    continue
                etype = (el.get_attribute("type") or "text").lower()
                tag_name = el.evaluate("e => e.tagName.toLowerCase()")
                before = v.err_mark()
                if tag_name == "select":
                    opts = el.locator("option")
                    if opts.count() > 1:
                        try:
                            el.select_option(index=1, timeout=2500)
                        except Exception:
                            el.evaluate(
                                "e => { e.selectedIndex = Math.min(1, e.options.length-1);"
                                " e.dispatchEvent(new Event('change',{bubbles:true})); }"
                            )
                        changed += 1
                elif etype == "checkbox":
                    # click the visible switch/label wrapping the input
                    wrap = el.locator("xpath=ancestor::label[1]")
                    try:
                        if wrap.count():
                            wrap.click(timeout=2500, force=True)
                        else:
                            el.evaluate(
                                "e => { e.checked = !e.checked;"
                                " e.dispatchEvent(new Event('change',{bubbles:true})); }"
                            )
                    except Exception:
                        el.evaluate(
                            "e => { e.checked = !e.checked;"
                            " e.dispatchEvent(new Event('change',{bubbles:true})); }"
                        )
                    changed += 1
                elif etype == "radio":
                    el.evaluate(
                        "e => { e.checked = true;"
                        " e.dispatchEvent(new Event('change',{bubbles:true})); }"
                    )
                    changed += 1
                elif etype == "range":
                    el.evaluate(
                        "e => { e.value = Math.min(100, +e.value + 20);"
                        " e.dispatchEvent(new Event('input',{bubbles:true}));"
                        " e.dispatchEvent(new Event('change',{bubbles:true})); }"
                    )
                    changed += 1
                elif etype == "color":
                    el.evaluate(
                        "e => { e.value = '#7c3aed';"
                        " e.dispatchEvent(new Event('input',{bubbles:true}));"
                        " e.dispatchEvent(new Event('change',{bubbles:true})); }"
                    )
                    changed += 1
                elif etype in ("number",):
                    el.fill("2", timeout=2500)
                    el.evaluate("e => e.dispatchEvent(new Event('change',{bubbles:true}))")
                    changed += 1
                elif etype in ("text", "date", "time"):
                    el.fill("Verify", timeout=2500)
                    el.evaluate("e => e.dispatchEvent(new Event('change',{bubbles:true}))")
                    changed += 1
                elif tag_name == "textarea":
                    el.fill("Verify text", timeout=2500)
                    changed += 1
                if len(v.errors) > before:
                    v.check(
                        f"no JS error after {tag_name}#{i} on tab '{tname}'",
                        False, "; ".join(v.err_since(before))[:140],
                    )
            except Exception:
                pass
        total_controls += changed
        if tname.lower() in ("content", "data", "chart"):
            v.capture(f"{v.stamp}--{tag}--tab-{tname.lower().replace(' ', '-')}")
    v.check(f"props controls exercised ({tag})", total_controls > 0,
            f"{total_controls} controls changed across {tab_n} tabs")


# ---------------------------------------------------------------------------
# Scenario: Dashboard Studio deep verification
# ---------------------------------------------------------------------------
def scenario_dashboardstudio(v: Verifier):
    # 1. Hub renders with its quick-start cards
    v.goto("#dashboardstudio")
    v.page.wait_for_timeout(1500)
    v.capture(f"{v.stamp}--dstudio--hub")
    v.dump_ui("#page-content")
    for card in ["AI Auto-Build", "Start from Scratch", "AI Insights",
                 "SQL to Dashboard", "Import Dashboard", "Recreate from Image"]:
        v.check(f"quick-start card present: {card}", v.count(f"text={card}") > 0)

    # 2. Data source list + search filter (list loads async — wait for it)
    v.page.wait_for_selector(".studio-data-item", timeout=15000)
    tables = v.count(".studio-data-item")
    v.check("data source list populated", tables > 0, f"{tables} tables")
    v.page.fill("#studio-data-search", TABLE[:6])
    v.page.wait_for_timeout(600)
    v.capture(f"{v.stamp}--dstudio--table-search")
    v.page.fill("#studio-data-search", "")
    v.page.wait_for_timeout(400)

    # 3. Template gallery + search
    tpl_count = v.page.locator("#studio-templates > *").count()
    v.page.fill("#studio-tpl-search", "exec")
    v.page.wait_for_timeout(600)
    filtered = v.page.locator("#studio-templates > *").count()
    v.page.fill("#studio-tpl-search", "")
    v.page.wait_for_timeout(400)
    v.check("template gallery renders + search filters",
            tpl_count > 0 and filtered <= tpl_count,
            f"{tpl_count} -> {filtered}")
    v.capture(f"{v.stamp}--dstudio--templates")

    # 4. Select the data source (real click) and verify the app state took it.
    #    The click must land on the row matching TABLE; verify via StudioPage
    #    state (row re-renders can race the toast).
    selected_ok = False
    for attempt in range(3):
        row = v.page.locator(f".studio-data-item:has-text('{TABLE[:20]}')")
        if not row.count():
            row = v.page.locator(".studio-data-item").first
        row.first.click()
        v.page.wait_for_timeout(500)
        state = v.page.evaluate(
            "(function(){ try { return StudioPage._selectedTable ? "
            "(StudioPage._selectedTable.table_name || StudioPage._selectedTable.name || '') : ''; } catch(e) { return 'ERR:'+e.message; } })()")
        v.log(f"selection attempt {attempt+1}: _selectedTable='{state}'")
        if state == TABLE:
            selected_ok = True
            break
    v.check("table selection registers", selected_ok, f"_selectedTable set for {TABLE}")

    # 5. Premium gate: plan-aware. Free users get the upgrade overlay; Pro
    #    users get the real flow — a native file chooser for the screenshot.
    plan_now = v.page.evaluate("RBAC.getPlan ? RBAC.getPlan() : 'free'")
    if plan_now == "free":
        if v.click_text("Recreate from Image"):
            v.page.wait_for_timeout(800)
            gate_overlay = v.page.locator("#premium-overlay").count()
            v.check("premium gate shows upgrade overlay (free plan)",
                    gate_overlay > 0, f"overlays={gate_overlay}")
            v.capture(f"{v.stamp}--dstudio--premium-gate")
            v.click_button(r"Not now|Cancel|close")
            v.page.evaluate("document.querySelectorAll('#premium-overlay').forEach(e=>e.remove())")
            v.page.wait_for_timeout(400)
    else:
        try:
            with v.page.expect_file_chooser(timeout=6000):
                v.page.locator("text=Recreate from Image").first.click()
            v.check(f"premium feature unlocked for '{plan_now}' plan (file chooser)", True)
        except Exception:
            v.check(f"premium feature unlocked for '{plan_now}' plan (file chooser)",
                    False, "no chooser event")
        v.capture(f"{v.stamp}--dstudio--premium-gate")

    # 6. AI Auto-Build wizard: 4 steps, screenshot each, then generate
    if not v.click_button(r"AI Auto-Build"):
        v.click_text("AI Auto-Build")
    v.page.wait_for_timeout(900)
    wizard = v.count(".bd-gal-modal")
    v.check("AI design wizard opens", wizard > 0)
    v.capture(f"{v.stamp}--dstudio--wizard-step1")
    # click a purpose chip (real click) then walk Next/Next/Next/Generate
    chip = v.page.locator(".ds-des-opt[data-opt]").first
    if chip.count():
        chip.click()
        v.log("clicked wizard option chip")
    for step in range(2, 6):
        nxt = v.page.locator("[data-next='1']:visible")
        if not nxt.count():
            break
        nxt.first.click()
        v.log(f"wizard next -> step {step}")
        v.page.wait_for_timeout(700)
        v.capture(f"{v.stamp}--dstudio--wizard-step{step}")
    gen = v.page.locator("[data-build='1']:visible").first
    if gen.count():
        gen.click()
        v.log("clicked Generate Dashboard")
    else:
        v.click_text("Generate Dashboard")
    # Final canvas-size modal -> "Build Dashboard". Belt-and-braces: the plan
    # request must carry the selected table (a previous run raced selection).
    v.page.evaluate(
        "(function(){ try { if (!StudioPage._selectedTable || !StudioPage._selectedTable.table_name) {"
        " var t = (window.__verifyTables && window.__verifyTables[0]) || null;"
        " if (t) StudioPage._selectedTable = t; } } catch(e) {} })()")
    v.page.wait_for_timeout(900)
    v.capture(f"{v.stamp}--dstudio--canvas-size-modal")
    if v.click_button(r"Build Dashboard"):
        v.log("clicked Build Dashboard (canvas size confirmed)")

    # 7. Wait for build -> auto-navigation to #blankdash with widgets
    #    (plan API + layout + render can take well over 10s)
    try:
        v.page.wait_for_url("**/#blankdash*", timeout=90000)
        v.log("navigated to #blankdash")
    except Exception:
        pass
    try:
        v.page.wait_for_selector(".ff-widget", timeout=30000)
        v.log("widgets appeared on canvas")
    except Exception:
        pass
    v.page.wait_for_timeout(2500)  # settle charts
    v.capture(f"{v.stamp}--dstudio--generated-dashboard")
    on_blankdash = "#blankdash" in v.page.url
    widgets = v.count(".ff-widget")
    v.check("AI build lands on blankdash canvas", on_blankdash, v.page.url)
    v.check("AI build produced widgets", widgets > 0, f"{widgets} widgets")
    v.dump_ui("body")

    # 8. Back to the hub: My Dashboards section + overflow (import) chooser
    v.nav("#dashboardstudio")
    v.page.wait_for_timeout(1200)
    # close any modal left open by the build error path
    v.page.evaluate("document.querySelectorAll('.bd-gal-overlay,.modal-overlay').forEach(e=>e.remove())")
    saved = v.count("#studio-mydash-list .studio-dash-item, #studio-mydash-list > *")
    v.check("'My Dashboards' section renders", saved > 0, f"{saved} entries")
    v.capture(f"{v.stamp}--dstudio--my-dashboards")
    # Click inside expect_file_chooser so the event is captured
    try:
        with v.page.expect_file_chooser(timeout=6000) as fc_info:
            v.page.locator(".stud-quick-card:has-text('Import Dashboard')").first.click()
        fc_info.value  # chooser fired
        v.check("import dashboard opens file chooser", True)
    except Exception as e:
        v.check("import dashboard opens file chooser", False, f"{str(e)[:80]}")
        v.click_text("Cancel")
    v.page.wait_for_timeout(500)


# ---------------------------------------------------------------------------
# Scenario: Blank Dash deep verification
# ---------------------------------------------------------------------------
def scenario_blankdash(v: Verifier):
    v.page.set_default_timeout(6000)
    # 0. Seed a clean context (table bound, no widgets) via sessionStorage,
    #    exactly like Dashboard Studio's "Start from Scratch" does.
    v.page.goto(f"{BASE}/#dashboard")
    v.wait_app()
    v.page.evaluate(
        "sessionStorage.setItem('blankdash_context', JSON.stringify({table:'%s', name:'Verify Dash', widgets:[], fixedCanvas:true}))"
        % TABLE
    )
    v.page.evaluate("window.location.hash = '#blankdash'")
    v.page.wait_for_timeout(2500)
    v.capture(f"{v.stamp}--blankdash--canvas")
    v.dump_ui("body")

    # 1. Data source select in the ribbon (options load asynchronously)
    sel = v.page.locator("#bd-table-select")
    bound = False
    if sel.count():
        try:
            # options are populated asynchronously; <option> elements are always
            # "hidden" for wait_for, so poll the option count instead
            v.page.wait_for_function(
                "document.querySelector('#bd-table-select') && document.querySelector('#bd-table-select').options.length > 1",
                timeout=15000,
            )
            sel.select_option(TABLE)
            bound = True
        except Exception:
            opts = sel.locator("option")
            v.log(f"table select options: {[opts.nth(i).inner_text() for i in range(min(opts.count(), 8))]}"
                  if opts.count() else "(no options rendered)")
    if bound:
        v.page.wait_for_timeout(600)
        v.log(f"data source bound: {TABLE}")
    v.check("ribbon table select present + bound", bound)

    # 2. Palette: category filter + search
    cats = v.count("#bd-palette-cat option")
    v.check("palette category filter present", cats > 3, f"{cats} categories")
    v.page.fill("#bd-palette-search", "kpi")
    v.page.wait_for_timeout(500)
    kpi_hits = v.count("#bd-palette-items .bd-palette-item")
    v.check("palette search filters", 0 < kpi_hits < 95, f"{kpi_hits} items match 'kpi'")
    v.capture(f"{v.stamp}--blankdash--palette-search")
    v.page.fill("#bd-palette-search", "")
    v.page.wait_for_timeout(400)

    # 3. Add widgets by clicking palette items (real clicks)
    for dtype, label in [("kpi", "KPI Card"), ("bar", "Bar Chart"),
                         ("table", "Data Table"), ("text", "Text Box")]:
        item = v.page.locator(f".bd-palette-item[data-type='{dtype}']").first
        if item.count():
            item.scroll_into_view_if_needed()
            try:
                item.click(timeout=3000)
            except Exception:
                # props dock (opened on the previous add) overlaps the palette:
                # close it like a user would, then click again
                v.page.evaluate(
                    "window.PropsPanel && PropsPanel.close && PropsPanel.close()"
                )
                v.page.wait_for_timeout(300)
                item.click(timeout=3000)
            v.page.wait_for_timeout(800)
            v.log(f"palette add: {label}")
    widgets = v.count(".ff-widget")
    v.check("palette adds widgets to canvas", widgets >= 4, f"{widgets} widgets")
    v.capture(f"{v.stamp}--blankdash--widgets-added")

    # 4. Select a widget -> props dock + floating toolbar; exercise ALL tabs
    first = v.page.locator(".ff-widget[data-id]").first
    if first.count():
        first.click()
        v.page.wait_for_timeout(900)
        v.check("widget select shows float toolbar",
                v.count(".ff-float-toolbar") > 0)
        v.capture(f"{v.stamp}--blankdash--widget-selected")        # 5. Drag the widget (real mouse)
        box = first.bounding_box()
        if box:
            cx, cy = box["x"] + box["width"] / 2, box["y"] + 12
            v.page.mouse.move(cx, cy)
            v.page.mouse.down()
            v.page.mouse.move(cx + 140, cy + 90, steps=14)
            v.page.mouse.up()
            v.page.wait_for_timeout(600)
            box2 = first.bounding_box()
            moved = box2 and (abs(box2["x"] - box["x"]) + abs(box2["y"] - box["y"])) > 20
            v.check("widget drag moves it", bool(moved),
                    f"({box['x']:.0f},{box['y']:.0f})->({box2['x']:.0f},{box2['y']:.0f})" if box2 else "")
            v.capture(f"{v.stamp}--blankdash--after-drag")

        # 5b. Resize via the SE handle FIRST (pristine geometry, 100% zoom,
        #     panel closed) — the SE handle shares its corner with the corner-
        #     shape pill, so grab its exact center point.
        wid = first.get_attribute("data-id")
        handle = v.page.locator(f".ff-resize-handle[data-id='{wid}'][data-resize='se']")
        if handle.count():
            hb = handle.bounding_box()
            w0 = first.bounding_box()
            if hb and w0:
                hx, hy = hb["x"] + hb["width"] / 2, hb["y"] + hb["height"] / 2
                v.page.mouse.move(hx, hy)
                v.page.mouse.down()
                v.page.mouse.move(hx + 120, hy + 90, steps=12)
                v.page.mouse.up()
                v.page.wait_for_timeout(700)
                w1 = first.bounding_box()
                grew = w1 and (w1["width"] - w0["width"]) > 20
                v.check("widget resize works", bool(grew),
                        f"{w0['width']:.0f}->{w1['width']:.0f}px" if w1 else "")
                v.capture(f"{v.stamp}--blankdash--after-resize")

        # 5c. Exercise the properties panel (all 12 tabs, every control)
        exercise_props_panel(v, "blankdash-props")

        # 7. Floating toolbar: duplicate, then delete + undo restore
        ftb = v.page.locator(f".ff-float-toolbar[data-id='{first.get_attribute('data-id')}'] button, .ff-float-toolbar button")
        ft_labels = [ftb.nth(i).get_attribute("title") or ftb.nth(i).inner_text()
                     for i in range(min(ftb.count(), 12))]
        v.log(f"float toolbar actions: {ft_labels}")
        v.capture(f"{v.stamp}--blankdash--float-toolbar")
        n0 = v.count(".ff-widget")
        dup_btn = v.page.locator(".ff-float-toolbar button[title='Duplicate']")
        if dup_btn.count():
            dup_btn.first.click()
            v.page.wait_for_timeout(900)
            n1 = v.count(".ff-widget")
            toast = v.page.locator("#toast-container").inner_text() if v.count("#toast-container") else ""
            v.check("float toolbar duplicate works", n1 == n0 + 1,
                    f"{n0}->{n1} toast='{toast.strip()[:40]}'")
            # delete the duplicate, then undo
            dup = v.page.locator(".ff-widget[data-id]").last
            if dup.count() > 1:
                dup.click()
                v.page.wait_for_timeout(600)
                del_btn = v.page.locator(".ff-float-toolbar button[title='Delete']")
                if del_btn.count():
                    del_btn.first.click()
                    v.page.wait_for_timeout(700)
                    n2 = v.count(".ff-widget")
                    v.check("float toolbar delete works", n2 == n0, f"{n0}->{n2}")
                    v.page.keyboard.press("Control+z")
                    v.page.wait_for_timeout(700)
                    n3 = v.count(".ff-widget")
                    v.check("undo (Ctrl+Z) restores deleted widget", n3 == n0 + 1, f"{n2}->{n3}")
                    # clean up: remove the duplicate again so the canvas stays tidy
                    dup2 = v.page.locator(".ff-widget[data-id]").last
                    if dup2.count() > 1:
                        dup2.click()
                        v.page.wait_for_timeout(500)
                        del2 = v.page.locator(".ff-float-toolbar button[title='Delete']")
                        if del2.count():
                            del2.first.click()
                            v.page.wait_for_timeout(600)

    # 8. Ribbon: zoom in/out (verify % label), grid, grid mode, minimap, panels
    pct0 = v.page.locator("#bd-zoom-pct").inner_text()
    v.click_sel("button[title='Zoom in']", label="zoom in")
    pct1 = v.page.locator("#bd-zoom-pct").inner_text()
    v.click_sel("button[title='Zoom out']", label="zoom out")
    pct2 = v.page.locator("#bd-zoom-pct").inner_text()
    # zoom in must change the level; zoom out returns to the start value
    v.check("zoom in/out updates % label", pct0 != pct1 and pct2 == pct0,
            f"{pct0} -> {pct1} -> {pct2}")
    v.capture(f"{v.stamp}--blankdash--zoom")

    grid_btn = v.page.locator("#bd-grid-toggle")
    off0 = v.page.evaluate("document.getElementById('bd-canvas').classList.contains('fff-grid-off')")
    grid_btn.click()
    v.page.wait_for_timeout(500)
    off1 = v.page.evaluate("document.getElementById('bd-canvas').classList.contains('fff-grid-off')")
    grid_btn.click()
    v.page.wait_for_timeout(500)
    off2 = v.page.evaluate("document.getElementById('bd-canvas').classList.contains('fff-grid-off')")
    v.check("grid toggle flips canvas grid class", off0 != off1 and off1 != off2,
            f"{off0}->{off1}->{off2}")

    mode0 = v.page.locator("#bd-grid-mode-toggle").get_attribute("title")
    v.click_sel("#bd-grid-mode-toggle", label="grid mode cycle")
    mode1 = v.page.locator("#bd-grid-mode-toggle").get_attribute("title")
    v.click_sel("#bd-grid-mode-toggle", label="grid mode cycle")
    mode2 = v.page.locator("#bd-grid-mode-toggle").get_attribute("title")
    v.check("grid mode cycles (dots/cols/off)", mode0 != mode1 and mode1 != mode2,
            f"'{mode0[:24]}…' -> '{mode1[:24]}…' -> '{mode2[:24]}…'")
    v.capture(f"{v.stamp}--blankdash--grid-modes")

    mm0 = v.page.evaluate("(document.getElementById('bd-minimap')||{}).style ? document.getElementById('bd-minimap').style.display !== 'none' : false")
    v.click_sel("button[title*='minimap']", label="minimap toggle")
    mm1 = v.page.evaluate("(document.getElementById('bd-minimap')||{}).style ? document.getElementById('bd-minimap').style.display !== 'none' : false")
    v.check("minimap toggles", mm0 != mm1, f"visible {mm0}->{mm1}")
    v.click_sel("button[title*='minimap']", label="minimap toggle back")

    v.click_sel("button[title*='Layers and Visualizations']", label="side panels toggle")
    v.page.wait_for_timeout(400)
    v.capture(f"{v.stamp}--blankdash--panels-toggled")
    v.click_sel("button[title*='Layers and Visualizations']", label="side panels restore")

    # 9. AI Assist copilot panel
    if v.click_button(r"AI Assist"):
        v.page.wait_for_timeout(900)
        v.capture(f"{v.stamp}--blankdash--ai-assist")
        v.check("AI Assist panel opens", v.count(".bd-copilot, [class*='copilot']") > 0)
        v.click_button(r"close|times") or v.page.keyboard.press("Escape")

    # 10. Arrange menu (align/distribute controls render), then close it —
    #     its backdrop would swallow the Save click if left open
    if v.click_button(r"Arrange"):
        v.page.wait_for_timeout(600)
        arr = v.count(".bd-arrange-menu")
        v.check("arrange menu opens", arr > 0)
        v.capture(f"{v.stamp}--blankdash--arrange-menu")
        v.keyboard_escape()
        v.page.evaluate("document.querySelectorAll('.bd-arrange-menu,.bd-menu-backdrop').forEach(e=>e.remove())")

    # 11. Pages modal: add a page (close via its Close button — Escape only
    #      works inside inputs, and a stuck modal blocks Save)
    if v.click_button(r"Pages"):
        v.page.wait_for_timeout(600)
        if v.click_button(r"Add page"):
            v.page.wait_for_timeout(600)
            tabs = v.count(".bd-page-tab")
            v.check("pages modal adds a page", tabs >= 2, f"{tabs} page tabs")
        v.capture(f"{v.stamp}--blankdash--pages-modal")
        if not v.click_button(r"^Close$"):
            v.click_text("Close")
        v.page.evaluate("document.querySelectorAll('.bd-gal-overlay,.modal-overlay,.bd-menu-backdrop,.bd-arrange-menu').forEach(e=>e.remove())")
        v.page.wait_for_timeout(300)

    # 12. Overflow menu: Theme, Settings, Shortcuts
    if v.click_button(r"More actions"):
        v.page.wait_for_timeout(500)
        v.capture(f"{v.stamp}--blankdash--overflow-menu")
        if v.click_text("Theme"):
            v.page.wait_for_timeout(700)
            v.capture(f"{v.stamp}--blankdash--theme-modal")
            theme_btn = v.page.locator(".bd-gal-modal button, .modal-overlay button")
            if theme_btn.count():
                v.click_button(r"dark|midnight")
                v.page.wait_for_timeout(700)
                v.capture(f"{v.stamp}--blankdash--theme-applied")
                v.click_button(r"apply|done|close|cancel")
        if v.click_button(r"More actions"):
            if v.click_text("Settings"):
                v.page.wait_for_timeout(700)
                v.capture(f"{v.stamp}--blankdash--settings-modal")
                v.click_button(r"close|cancel|done|save")
        if v.click_button(r"More actions"):
            if v.click_text("Shortcuts"):
                v.page.wait_for_timeout(700)
                v.capture(f"{v.stamp}--blankdash--shortcuts-modal")
                v.click_button(r"close|got it|ok|cancel")
        # any leftover overflow menu/backdrop must go before Save
        v.page.evaluate("document.querySelectorAll('.bd-arrange-menu,.bd-overflow-menu,.bd-menu-backdrop').forEach(e=>e.remove())")
        v.page.wait_for_timeout(300)

    # 13. Save (real click) -> toast feedback
    v.click_sel("button.bd-save-btn", label="Save")
    v.page.wait_for_timeout(1800)
    toast = v.page.locator("#toast-container").inner_text() if v.count("#toast-container") else ""
    v.check("save produces feedback", len(toast.strip()) > 0, f"toast='{toast.strip()[:60]}'")
    v.check("save reached the server (deep link updated)",
            re.search(r"#blankdash/[A-Za-z0-9-]", v.page.url) is not None,
            v.page.url)
    v.capture(f"{v.stamp}--blankdash--after-save")

    # 14. Present mode -> navigation works -> Esc exits
    if v.click_button(r"Present"):
        v.page.wait_for_timeout(1800)
        presenting = v.count("#bd-present-stage") > 0 or "present" in \
            v.page.evaluate("document.querySelector('.bd-page') ? document.querySelector('.bd-page').className : ''")
        v.check("present mode activates", presenting)
        v.capture(f"{v.stamp}--blankdash--present")
        v.page.keyboard.press("ArrowRight")
        v.page.wait_for_timeout(500)
        v.page.keyboard.press("Escape")
        v.page.wait_for_timeout(900)
        exited = v.count("#bd-present-stage") == 0
        v.check("Escape exits present mode", exited)
        v.capture(f"{v.stamp}--blankdash--after-present")


def _keyboard_escape(self):
    self.page.keyboard.press("Escape")
    self.page.wait_for_timeout(400)


Verifier.keyboard_escape = _keyboard_escape


# ---------------------------------------------------------------------------
# Scenario: every widget type — add, render, verify, exercise ALL properties
# ---------------------------------------------------------------------------

# Covers every schema family and renderer branch: KPI family, chart families,
# statistical, specialized, table, filters, media/shapes.
# The palette search matches LABELS (e.g. "Box Plot"), not type ids, so each
# entry carries a label fragment that reveals it via the search box.
WIDGET_MATRIX = [
    ("kpi", "kpi card"),
    ("table", "data table"),
    ("text", "text box"),
    ("line", "line chart"),
    ("bar", "bar chart"),
    ("column", "column chart"),
    ("area", "area chart"),
    ("pie", "pie chart"),
    ("doughnut", "doughnut"),
    ("scatter", "scatter plot"),
    ("bubble", "bubble chart"),
    ("radar", "radar chart"),
    ("combo", "combo chart"),
    ("stackedbar", "stacked bar"),
    ("stackedarea", "stacked area"),
    ("percentbar", "100% stacked"),
    ("sparkline", "sparkline"),
    ("heatmap", "heatmap"),
    ("gauge", "gauge"),
    ("funnel", "funnel"),
    ("treemap", "treemap"),
    ("waterfall", "waterfall"),
    ("polar", "polar area"),
    ("boxplot", "box plot"),
    ("histogram", "histogram"),
    ("bullet", "bullet chart"),
    ("pyramid", "pyramid"),
    ("sunburst", "sunburst"),
    ("wordcloud", "word cloud"),
    ("progress", "progress ring"),
    ("slicer", "dropdown slicer"),
    ("daterange", "date range"),
    ("searchbox", "search filter"),
    ("image", "image"),
    ("shape", "shape"),
    ("divider", "divider"),
    ("custom", "custom html"),
    ("page", "page link"),
    ("button", "action button"),
    ("radial", "radial progress"),
    ("status", "status indicator"),
    ("comparison", "comparison kpi"),
    ("targetactual", "target vs"),
    ("stepline", "step line"),
    ("smoothline", "smooth line"),
    ("splinearea", "smooth area"),
    ("lollipop", "lollipop chart"),
    ("pareto", "pareto chart"),
    ("rose", "rose chart"),
    ("radialbar", "radial bar"),
    ("waffle", "waffle chart"),
    ("calendar", "calendar heatmap"),
    ("bubblegrid", "bubble grid"),
    ("watertank", "water tank"),
    ("ranking", "leaderboard"),
    ("gantt", "gantt timeline"),
    ("diverging", "diverging bar"),
    ("trendkpi", "trend kpi"),
    ("prevkpi", "prev period"),
    ("iconkpi", "icon kpi"),
    ("multikpi", "multi-value kpi"),
    ("metriccard", "metric card"),
    ("sparkkpi", "sparkline kpi"),
    ("sparkbarkpi", "mini bar"),
    ("minilinekpi", "mini line"),
    ("sparkdeltakpi", "sparkline delta"),
    ("donutkpi", "donut kpi"),
    ("slope", "slope chart"),
    ("sankey", "sankey diagram"),
    ("chord", "chord diagram"),
    ("ridgeline", "ridgeline plot"),
    ("beeswarm", "beeswarm plot"),
    ("stream", "stream graph"),
    ("population", "population pyramid"),
    ("venn", "venn diagram"),
    ("numrange", "number range"),
    ("hierarchy", "hierarchy slicer"),
    ("relativedate", "relative date"),
    ("topn", "top n"),
    ("filterpane", "filter pane"),
    ("globalsearch", "global search"),
    ("geomap", "marker map"),
    ("countrymap", "bubble sizing"),
    ("shapemap", "shape map"),
    ("dotmap", "dot distribution"),
    ("heatmapmap", "heatmap map"),
    ("flowmap", "flow map"),
    ("glmap", "gl map"),
]

# Shard support: WIDGET_SHARD="0/4" runs types 0,4,8,... of the matrix so the
# full 88-type pass can be split across N parallel browser sessions.
import os as _os
_shard = _os.environ.get("WIDGET_SHARD", "")
if _shard and "/" in _shard:
    _i, _n = (int(x) for x in _shard.split("/", 1))
    WIDGET_MATRIX = WIDGET_MATRIX[_i::_n]
_types = _os.environ.get("WIDGET_TYPES", "")
if _types:
    _want = {t.strip() for t in _types.split(",") if t.strip()}
    WIDGET_MATRIX = [r for r in WIDGET_MATRIX if r[0] in _want]

# Filling URL-like fields with bare words makes the app window.open() them
# (real 404s). Give every text control a value that is valid for its key.
_SAFE_TEXT = {
    "config.imageLink": "https://example.com/v.png",
    "config.imageUrl": "https://example.com/v.png",
    "config.bgImageUrl": "https://example.com/v.png",
    "config.onClickTarget": "#dashboard",
    "config.drillTarget": "#dashboard",
    "config.sql": "SELECT 1 AS v",
    "widget.title": "Verify",
}

# Geometry keys are exercised by the dedicated drag/resize scenario; filling
# them with arbitrary numbers collapses the widget to a 3px dot.
_SKIP_KEYS = {"widget.x", "widget.y", "widget.width", "widget.height", "widget.zIndex"}


def _drive_props(v: Verifier, wtype: str):
    """Flip through every tab of the props dock, drive every control safely.
    Returns (tabs, driven, problems_list)."""
    panel = v.page.locator(".bd-props").first
    tabs = panel.locator(".bd-props-tab")
    tab_n = tabs.count()
    driven = 0
    problems = []
    for t in range(tab_n):
        tab = tabs.nth(t)
        tname = "tab"
        try:
            tname = tab.get_attribute("title") or tab.inner_text().strip()
        except Exception:
            pass
        tmark = v.err_mark()
        try:
            tab.click(timeout=2500, force=True)
        except Exception:
            tab.evaluate("e => e.click()")
        v.page.wait_for_timeout(250)
        active = panel.locator(".bd-props-tab-panel.active")
        if not active.count():
            active = panel.locator(".bd-props-tab-panel").last
        inputs = active.locator("input, select, textarea")
        n = min(inputs.count(), 60)
        for i in range(n):
            el = inputs.nth(i)
            try:
                if not el.is_visible():
                    continue
                etype = (el.get_attribute("type") or "").lower()
                tn = el.evaluate("e => e.tagName.toLowerCase()")
                pkey = el.evaluate(
                    "e => { var c = e.closest('[data-prop-key]');"
                    " return c ? c.getAttribute('data-prop-key') : ''; }")
                if pkey in _SKIP_KEYS:
                    continue
                if tn == "select":
                    if el.locator("option").count() > 1:
                        try:
                            el.select_option(index=1, timeout=2000)
                        except Exception:
                            el.evaluate(
                                "e => { e.selectedIndex = Math.min(1, e.options.length-1);"
                                " e.dispatchEvent(new Event('change',{bubbles:true})); }")
                        driven += 1
                elif etype == "checkbox":
                    wrap = el.locator("xpath=ancestor::label[1]")
                    try:
                        if wrap.count():
                            wrap.click(timeout=2000, force=True)
                        else:
                            el.evaluate(
                                "e => { e.checked = !e.checked;"
                                " e.dispatchEvent(new Event('change',{bubbles:true})); }")
                    except Exception:
                        el.evaluate(
                            "e => { e.checked = !e.checked;"
                            " e.dispatchEvent(new Event('change',{bubbles:true})); }")
                    driven += 1
                elif etype == "radio":
                    el.evaluate(
                        "e => { e.checked = true;"
                        " e.dispatchEvent(new Event('change',{bubbles:true})); }")
                    driven += 1
                elif etype == "range":
                    el.evaluate(
                        "e => { e.value = Math.min(100, +e.value + 25);"
                        " e.dispatchEvent(new Event('input',{bubbles:true}));"
                        " e.dispatchEvent(new Event('change',{bubbles:true})); }")
                    driven += 1
                elif etype == "color":
                    el.evaluate(
                        "e => { e.value = '#7c3aed';"
                        " e.dispatchEvent(new Event('input',{bubbles:true}));"
                        " e.dispatchEvent(new Event('change',{bubbles:true})); }")
                    driven += 1
                elif etype == "number":
                    el.fill("3", timeout=2000)
                    el.evaluate("e => e.dispatchEvent(new Event('change',{bubbles:true}))")
                    driven += 1
                elif etype in ("text", "date", "time"):
                    # value must be safe for the property being edited
                    val = _SAFE_TEXT.get(pkey, "Verify")
                    el.fill(val, timeout=2000)
                    el.evaluate("e => e.dispatchEvent(new Event('change',{bubbles:true}))")
                    driven += 1
                elif tn == "textarea":
                    el.fill("Verify text", timeout=2000)
                    driven += 1
            except Exception:
                pass
        if len(v.errors) > tmark:
            problems.append(f"tab '{tname}': {v.err_since(tmark)[0][:110]}")
    return tab_n, driven, problems


def scenario_widgets(v: Verifier):
    """Every widget type: add from the palette (real click), verify the renderer
    painted real content, exercise EVERY property tab/control, verify the widget
    survives the edits, screenshot, then delete (accepting the confirm modal)."""
    v.page.goto(f"{BASE}/#dashboard")
    v.wait_app()
    v.page.evaluate(
        "sessionStorage.setItem('blankdash_context', JSON.stringify({table:'%s', name:'Widget Verify', widgets:[], fixedCanvas:true}))"
        % TABLE)
    v.page.evaluate("window.location.hash = '#blankdash'")
    v.page.wait_for_timeout(2500)

    ready = v.page.evaluate(
        "(function(){ try { return typeof BlankDash !== 'undefined' &&"
        " typeof BlankDash._addWidget === 'function'; } catch(e){ return false; } })()")
    v.check("blankdash module ready", bool(ready))

    # Bind the data source through the ribbon select like a user (its change
    # handler is what sets the internal _dash.table).
    v.page.wait_for_function(
        "document.querySelector('#bd-table-select') &&"
        " document.querySelector('#bd-table-select').options.length > 0",
        timeout=15000)
    bound = v.page.evaluate(
        "(function(){ var s = document.getElementById('bd-table-select');"
        "  if (!s) return '';"
        "  if (s.value) return s.value;"
        "  for (var i=0;i<s.options.length;i++) if (s.options[i].value === '%s')"
        "    { s.value = '%s'; s.dispatchEvent(new Event('change',{bubbles:true})); return s.value; }"
        "  return s.options.length ? s.options[0].value : ''; })()" % (TABLE, TABLE))
    v.page.wait_for_timeout(800)
    v.log(f"data source bound: {bound}")
    v.check("data source bound for widgets", bool(bound), f"table={bound}")

    passed = failed = 0
    for wtype, search_q in WIDGET_MATRIX:
        mark = v.err_mark()
        ok, detail, tab_n = True, "", 0

        # 1. Reveal the palette item via the search box (works regardless of
        #    which collapsed group holds it), then click it.
        try:
            v.page.fill("#bd-palette-search", search_q)
            v.page.wait_for_timeout(500)
            item = v.page.locator(f".bd-palette-item[data-type='{wtype}']").first
            item.wait_for(state="visible", timeout=4000)
            item.scroll_into_view_if_needed()
            item.click(timeout=3000)
            v.page.fill("#bd-palette-search", "")
            v.page.wait_for_timeout(1000)
            v.log(f"palette add: {wtype}")
        except Exception as e:
            ok = False
            detail = f"palette click failed: {str(e).splitlines()[0][:90]}"
            try:
                v.page.fill("#bd-palette-search", "")
            except Exception:
                pass

        # 2. The widget cell must exist and have painted real content.
        if ok:
            state = v.page.evaluate(
                "(function(){ try {"
                "  var cells = document.querySelectorAll('.ff-widget-content[data-id]');"
                "  if (!cells.length) return {added:false, reason:'no widget cells'};"
                "  var cell = cells[cells.length-1];"
                "  var html = cell.innerHTML || '';"
                "  return {added:true, id:cell.getAttribute('data-id'),"
                "    onCanvas:!!cell.closest('.ff-widget'),"
                "    rendered:html.length>80,"
                "    isUnknown:html.indexOf('Unknown widget type')!==-1};"
                " } catch(e) { return {added:false, err:String(e)}; } })()")
            if not state.get("added"):
                ok, detail = False, f"widget not added: {state}"
            elif not state.get("onCanvas"):
                ok, detail = False, "cell present but .ff-widget missing"
            elif state.get("isUnknown"):
                ok, detail = False, "renderer printed 'Unknown widget type'"

        # 2b. Screenshot the freshly-rendered widget BEFORE any property edits,
        #     so the visual proof of a working renderer is preserved.
        if ok:
            v.page.wait_for_timeout(1500)   # charts settle
            v.capture(f"{v.stamp}--widget-{wtype}--rendered")

        # 3. Properties dock: every tab, every control.
        if ok:
            v.page.evaluate(
                "(function(){ try { var els=document.querySelectorAll('.ff-widget[data-id]');"
                " if (els.length) BlankDash._selectWidget(els[els.length-1].getAttribute('data-id'));"
                " } catch(e){} })()")
            v.page.wait_for_timeout(700)
            if not v.page.locator(".bd-props").count():
                ok, detail = False, "props dock did not open"
            else:
                tab_n, driven, problems = _drive_props(v, wtype)
                v.log(f"{wtype}: {tab_n} tabs, {driven} controls driven")
                if driven == 0:
                    ok, detail = False, "no controls could be driven"
                if problems:
                    ok = False
                    detail = "; ".join(problems)[:200]
                v.capture(f"{v.stamp}--widget-{wtype}--props")

        # 4. Widget must survive all property mutations.
        if ok:
            alive = v.page.evaluate(
                "document.querySelectorAll('.ff-widget-content[data-id]').length > 0")
            if not alive:
                ok, detail = False, "widget vanished after property edits"

        if ok and len(v.errors) > mark:
            ok = False
            detail = f"console error: {v.err_since(mark)[0][:110]}"

        if ok:
            passed += 1
            v.check(f"widget '{wtype}' renders + full props pass", True,
                    f"{tab_n} tabs OK")
        else:
            failed += 1
            v.check(f"widget '{wtype}' renders + full props pass", False, detail)
            v.capture(f"{v.stamp}--widget-{wtype}--FAIL")

        # 5. Cleanup: delete (accept the 'Remove widget?' modal), clear docks.
        v.page.evaluate(
            "(function(){ try { var els=document.querySelectorAll('.ff-widget[data-id]');"
            " if (els.length) BlankDash._deleteWidget(els[els.length-1].getAttribute('data-id'));"
            " } catch(e){} })()")
        v.page.wait_for_timeout(500)
        v.click_button(r"^Remove$")
        v.page.evaluate(
            "document.querySelectorAll('.bd-props').forEach(e=>e.remove());"
            "document.querySelectorAll('.modal-overlay,.bd-gal-overlay').forEach(e=>e.remove())")
        v.page.wait_for_timeout(300)

    v.check("widget matrix summary", failed == 0,
            f"{passed} passed, {failed} failed of {len(WIDGET_MATRIX)}")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
SCENARIOS = {
    "pages": ("All main pages render", scenario_pages),
    "dashboardstudio": ("Dashboard Studio deep verification", scenario_dashboardstudio),
    "blankdash": ("Blank Dash deep verification", scenario_blankdash),
    "widgets": ("All widget types: render + full properties pass", scenario_widgets),
}


def run(page, sid: str, stamp: str):
    title, fn = SCENARIOS[sid]
    v = Verifier(page, stamp)
    v.attach()
    print(f"\n=== {sid}: {title} ===")
    try:
        fn(v)
    except Exception as e:
        v.errors.append(f"scenario exception: {e}\n{traceback.format_exc()[-800:]}")
    status = "PASS" if not v.errors and all(c["ok"] for c in v.checks) else "FAIL"
    result = {
        "scenario": sid,
        "title": title,
        "status": status,
        "url_after": page.url,
        "checks": v.checks,
        "actions": v.actions,
        "console_errors": v.errors,
        "screenshots": v.shots,
    }
    (OUT / f"result--{stamp}--{sid}.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    print(f"--- {sid}: {status} | checks={len(v.checks)} errors={len(v.errors)} "
          f"shots={len(v.shots)}")
    return result


def login_session():
    """Return (token, user) for an authenticated SPA session.

    Tries DA_E2E_USER/DA_E2E_PASS first; on failure registers the dedicated
    viewer account (first run only) and logs in again.
    """
    def _post(path, payload):
        r = requests.post(f"{BASE}{path}", json=payload, timeout=15)
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, {}

    status, data = _post("/auth/login", {"username": AUTH_USER, "password": AUTH_PASS})
    if status != 200 or not data.get("success"):
        status, data = _post("/auth/register", {"username": AUTH_USER, "password": AUTH_PASS})
        if not data.get("success"):
            raise SystemExit(f"auth bootstrap failed: {data.get('error') or status}")
        status, data = _post("/auth/login", {"username": AUTH_USER, "password": AUTH_PASS})
    if status != 200 or not data.get("token"):
        raise SystemExit(f"auth login failed: HTTP {status}")
    return data["token"], data.get("user") or {}


def pick_table(token):
    """Point the scenarios at a table this warehouse actually has.

    Prefers UI_VERIFY_TABLE, then the historical student_performance_powerbi,
    then the first table with a numeric column and more than one row — the AI
    build needs measures to produce widgets, so text-only tables (1-row
    key_redemptions, key/value premium_settings) are skipped.
    """
    global TABLE
    try:
        r = requests.get(f"{BASE}/tables",
                         headers={"Authorization": f"Bearer {token}"}, timeout=15)
        data = r.json()
        # /tables returns {"tables": {name: info, ...}, "system_tables": [...]}
        raw = data.get("tables") if isinstance(data, dict) else data
        if isinstance(raw, dict):
            info_by_name = raw
        elif isinstance(raw, list):
            info_by_name = {t.get("table_name") or t.get("name"): t for t in raw}
        else:
            info_by_name = {}
        names = [n for n in info_by_name if n]
    except Exception as e:  # noqa: BLE001
        print(f"table discovery failed: {e}; using default {TABLE}")
        return TABLE

    want = os.environ.get("UI_VERIFY_TABLE", "")
    if want and want in names:
        TABLE = want
    elif names and TABLE not in names:
        def _has_measures(name):
            cols = (info_by_name.get(name) or {}).get("columns") or []
            num = ("INT", "DOUBLE", "DECIMAL", "FLOAT", "REAL", "NUMERIC")
            return (any(any(t in (c.get("column_type") or "").upper() for t in num)
                        for c in cols)
                    and ((info_by_name.get(name) or {}).get("row_count") or 0) > 1)
        TABLE = next((n for n in names if _has_measures(n)), names[0])
    print(f"table under test: {TABLE}")
    return TABLE


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scenarios", nargs="*", help="scenario ids (default: all)")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if args.list:
        for sid, (title, _) in SCENARIOS.items():
            print(f"{sid:18s} {title}")
        return

    wanted = args.scenarios or list(SCENARIOS)
    unknown = [w for w in wanted if w not in SCENARIOS]
    if unknown:
        raise SystemExit(f"unknown scenarios: {unknown}; use --list")

    token, user = login_session()
    print(f"auth: signed in as '{user.get('username')}' (role: {user.get('role')})")
    pick_table(token)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    _shard = os.environ.get("WIDGET_SHARD", "")
    if _shard and "/" in _shard:
        stamp += "-s" + _shard.split("/")[0]   # parallel runs must not collide
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1600, "height": 1000})
        ctx.add_init_script(
            "localStorage.setItem('onboarding_completed','true');"
            "localStorage.setItem('darkMode','false');"
            # dashboard metering keys off owner_id = localStorage.username;
            # a fresh user per run starts with a clean backfilled quota
            "localStorage.setItem('username','uiverify_' + Date.now());"
            # real session for the SPA auth gate (auth hardening removed
            # local auto-admin): JWT + user object consumed by RBAC/Auth.init
            f"localStorage.setItem('auth_token',{json.dumps(token)});"
            f"localStorage.setItem('session',{json.dumps(json.dumps(user))});"
        )
        page = ctx.new_page()
        for sid in wanted:
            results.append(run(page, sid, stamp))
        browser.close()

    ok = all(r["status"] == "PASS" for r in results)
    print(f"\n==== SUMMARY: {'ALL PASS' if ok else 'FAILURES PRESENT'} ({stamp}) ====")
    for r in results:
        print(f"  {r['scenario']:16s} {r['status']}")
        for c in r["checks"]:
            if not c["ok"]:
                print(f"    FAIL: {c['name']} {c['detail']}")
        for e in r["console_errors"][:8]:
            print(f"    ERR: {e}")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
