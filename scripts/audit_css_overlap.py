"""
Find utility classes declared in BOTH styles.css and utilities.css.

`utilities.css` is linked AFTER `styles.css`, so at equal specificity the
later file wins. Any class where the two declarations disagree is a silent
override - the class that was reviewed in styles.css is not the one that
renders.

Only BARE single-class declarations count as "declared". A contextual
override such as `#bidash-grid .widget-container .bg-gray-50 { ... }` is a
DIFFERENT selector with higher specificity: it wins by design and is not a
duplicate declaration. Those are listed separately below so nothing is
hidden.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STYLES = ROOT / "web" / "css" / "styles.css"
UTILS = ROOT / "web" / "css" / "utilities.css"


def split_commas(sel: str) -> list[str]:
    parts, depth, cur = [], 0, ""
    for ch in sel:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur.strip())
    return parts


def strip_pseudo(sel: str) -> str:
    """Drop pseudo-classes/elements, honouring CSS backslash escapes."""
    out, i, n = [], 0, len(sel)
    while i < n:
        c = sel[i]
        if c == "\\":
            out.append(sel[i:i + 2])
            i += 2
            continue
        if c == ":":
            i += 1
            while i < n and (sel[i].isalnum() or sel[i] in "-_"):
                i += 1
            if i < n and sel[i] == "(":
                depth = 0
                while i < n:
                    if sel[i] == "(":
                        depth += 1
                    elif sel[i] == ")":
                        depth -= 1
                        if depth == 0:
                            i += 1
                            break
                    i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out).strip()


def bare_class(compound: str) -> str | None:
    """Class name if `compound` is exactly ONE class selector (pseudos ok)."""
    s = strip_pseudo(compound)
    if not s.startswith(".") or len(s) < 2:
        return None
    rest = s[1:]
    if not rest or any(ch.isspace() for ch in rest):
        return None
    if any(ch in ">+~[]," for ch in rest):
        return None
    return rest.replace("\\", "")


def strip_comments(text: str) -> str:
    """Blank out comments (keeping offsets) so they never enter a selector."""
    return re.sub(r"/\*.*?\*/", lambda m: " " * len(m.group(0)), text, flags=re.S)


def rule_blocks(text: str) -> list[tuple[str, str]]:
    """Yield (selector, body) for every rule, at any nesting depth."""
    out: list[tuple[str, str]] = []
    text = strip_comments(text)
    i, n = 0, len(text)
    while i < n:
        if text[i].isspace():
            i += 1
            continue
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        brace = text.find("{", i)
        if brace == -1:
            break
        # the selector runs back to the previous block boundary
        start = max(text.rfind("}", 0, i), text.rfind(";", 0, i), text.rfind("{", 0, i)) + 1
        depth, k = 0, brace
        while k < n:
            if text[k] == "{":
                depth += 1
            elif text[k] == "}":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        selector = text[start:brace].strip()
        body = text[brace + 1:k]
        if selector.startswith("@"):
            out.extend(rule_blocks(body))   # descend into media/supports
        else:
            out.append((selector, body))
        i = k + 1
    return out


def rules(text: str) -> dict[str, str]:
    """Map class name -> body, for BARE single-class declarations only."""
    out: dict[str, str] = {}
    for selector, body in rule_blocks(text):
        names = [bare_class(part) for part in split_commas(selector)]
        if not names or not all(n for n in names):
            continue  # scoped / compound selector - not a bare declaration
        out.setdefault(names[0], re.sub(r"\s+", " ", body).strip())
    return out


def contextual(text: str) -> dict[str, list[str]]:
    """Map class name -> scoped selectors that reference it."""
    out: dict[str, list[str]] = {}
    for selector, _body in rule_blocks(text):
        for part in split_commas(selector):
            if bare_class(part) is not None:
                continue
            for token in re.findall(r"\.((?:\\.|[A-Za-z0-9_/-])+)", part):
                name = token.replace("\\", "")
                if name and selector not in out.setdefault(name, []):
                    out[name].append(selector)
    return out


s = rules(STYLES.read_text(encoding="utf-8", errors="ignore"))
u = rules(UTILS.read_text(encoding="utf-8", errors="ignore"))

both = sorted(set(s) & set(u))
conflicting = [(c, s[c], u[c]) for c in both if s[c] != u[c]]

print(f"declared in styles.css   : {len(s)}")
print(f"declared in utilities.css : {len(u)}")
print(f"in BOTH                   : {len(both)}")
print(f"of those, DIFFERENT bodies: {len(conflicting)}")
print("-" * 78)
for name, a, b in conflicting:
    print(f"\n{name}")
    print(f"  styles.css   : {a[:110]}")
    print(f"  utilities.css: {b[:110]}")

scoped = contextual(STYLES.read_text(encoding="utf-8", errors="ignore"))
print()
print("-" * 78)
print("Contextual overrides of a utility class (scoped selector - NOT duplicates):")
n = 0
for name in sorted(set(u)):
    for selector in scoped.get(name, []):
        n += 1
        clean = selector.encode("ascii", "replace").decode("ascii")
        print(f"  {name}: {clean[:100]}")
print(f"  ({n} scoped rule(s) - these keep their higher specificity)")
