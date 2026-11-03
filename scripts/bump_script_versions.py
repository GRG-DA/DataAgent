"""
Bump the `?v=` cache-busting parameter of every script AND stylesheet tag
whose file changed, so a browser with a cached copy actually loads the new
code.

The app is served statically with no build step — the `?v=` query is the
only cache-busting mechanism, so it must be bumped when a file changes.

Only the STATIC `<script src="js/...">` tags in index.html need this.
Page modules under `js/pages/` are loaded on demand by app.js with
`?v=' + Date.now()`, so they are always fetched fresh.
"""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "web" / "index.html"

text = INDEX.read_text(encoding="utf-8", newline="")

changed = subprocess.run(
    ["git", "diff", "--name-only", "--", "web/"],
    cwd=ROOT, capture_output=True, text=True, check=False,
).stdout.split()
changed_set = {c.replace("\\", "/") for c in changed}

# The version already recorded in HEAD. A tag whose working-tree version
# still equals the HEAD version has not been bumped for this change yet, so
# bumping it again would needlessly invalidate everyone's cache.
head_text = subprocess.run(
    ["git", "show", "HEAD:web/index.html"],
    cwd=ROOT, capture_output=True, check=False,
).stdout.decode("utf-8", errors="replace")


def _head_version(tag_pattern: str) -> int | None:
    m = re.search(tag_pattern, head_text)
    return int(m.group(1)) if m else None


bumped = []
for m in re.finditer(r'<script src="(js/[^"?]+)\?v=(\d+)"', text):
    rel = "web/" + m.group(1)
    if rel not in changed_set:
        continue
    current = int(m.group(2))
    head = _head_version(r'<script src="' + re.escape(m.group(1)) + r'\?v=(\d+)"')
    if head is not None and current != head:
        continue  # already bumped for this change
    bumped.append((m.group(1), current + 1))

for path, version in bumped:
    text = re.sub(
        r'(<script src="' + re.escape(path) + r'\?v=)\d+(")',
        r"\g<1>" + str(version) + r"\g<2>",
        text,
    )

# Stylesheets.
css_bumped = []
for m in re.finditer(r'<link rel="stylesheet" href="(css/[^"?]+)\?v=(\d+)"', text):
    rel = "web/" + m.group(1)
    if rel not in changed_set:
        continue
    current = int(m.group(2))
    head = _head_version(
        r'<link rel="stylesheet" href="' + re.escape(m.group(1)) + r'\?v=(\d+)"')
    if head is not None and current != head:
        continue
    css_bumped.append((m.group(1), current + 1))

for path, version in css_bumped:
    text = re.sub(
        r'(<link rel="stylesheet" href="' + re.escape(path) + r'\?v=)\d+(")',
        r"\g<1>" + str(version) + r"\g<2>",
        text,
    )

if bumped or css_bumped:
    INDEX.write_text(text, encoding="utf-8", newline="")

print(f"bumped {len(bumped)} script and {len(css_bumped)} stylesheet version(s):")
for path, version in sorted(bumped + css_bumped):
    print(f"  {path} -> v{version}")
