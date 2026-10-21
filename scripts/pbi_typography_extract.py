import zipfile, json, sys, glob, os, re
from collections import Counter, defaultdict

DIR = 'powerbi sample data'
OUT_JSON = 'pbix_analysis/results/_typography_insights.json'
OUT_MD = 'pbix_analysis/TYPOGRAPHY_INSIGHTS_REPORT.md'

def decode_layout(raw):
    raw = raw.strip()
    if raw[:1] == '\ufeff':
        raw = raw[1:]
    try:
        return json.loads(raw)
    except Exception:
        pass
    return json.loads(raw.decode('utf-16-le').lstrip('\ufeff'))

NUM_LIT = re.compile(r'^([\-]?[\d\.]+)(D|L|I)$')

def decode_literal_num(s):
    s = s.strip()
    if s in ('true', 'false'):
        return s == 'true'
    if s.startswith("'") and s.endswith("'"):
        s = s[1:-1]
    m = NUM_LIT.match(s)
    if m:
        f = float(m.group(1))
        return int(f) if f == int(f) else f
    try:
        f = float(s)
        return int(f) if f == int(f) else f
    except Exception:
        return None

def lit(v):
    """Normalize a powerbi formatted property value to plain python."""
    if isinstance(v, dict):
        if 'expr' in v:
            return lit(v['expr'])
        if 'Literal' in v:
            return lit(v['Literal'])
        if 'Value' in v and set(k for k in v.keys()) == {'Value'}:
            dec = decode_literal_num(v['Value']) if isinstance(v['Value'], str) else v['Value']
            return v['Value'] if dec is None else dec
        if 'solid' in v:
            return lit(v['solid'])
        if 'color' in v:
            c = v['color']
            if isinstance(c, dict) and 'expr' in c:
                return lit(c)
            return c
        if 'ThemeDataColor' in v:
            return {'ThemeDataColor': v['ThemeDataColor']}
        # deep single-key wrappers
        if len(v) == 1:
            k0 = next(iter(v))
            if isinstance(v[k0], dict) or isinstance(v[k0], str):
                return lit(v[k0])
        return v
    if isinstance(v, list):
        return [lit(x) for x in v]
    return v

def norm_color(v):
    c = lit(v)
    if isinstance(c, dict):
        if 'solid' in c:
            return c['solid']
        cc = c.get('color')
        return cc
    return c

def parse_pt(text):
    """'28pt' -> 28 ; '13.3333px' -> 10 ; already numeric -> as-is ; else original"""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    s = str(text)
    m = re.match(r'^\-?([\d\.]+)\s*px$', s)
    if m:
        return round(float(m.group(1)) / 1.333333, 1)
    m = re.match(r'^\-?([\d\.]+)\s*pt$', s)
    if m:
        return float(m.group(1))
    return s

FS_RE = re.compile(r'^[\d\.]+pt$')

def walk_objects(obj, path, acc):
    """Walk a formatting object tree, collecting (groupPath.propertyKey, value) pairs."""
    def collect(node, gfx, pfx):
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, dict) and 'properties' in v:
                    for pk, pv in v['properties'].items():
                        acc[(gfx + k, pk)] = pv
                        collect(pv, gfx + k + '.', pfx + k + '.')
                elif isinstance(v, dict) and ('expr' in v or 'solid' in v or ('Literal' in v and 'Value' in v)):
                    acc[(gfx, k)] = v if k != 'properties' else acc.get((gfx, k), v)
                elif isinstance(v, dict):
                    collect(v, gfx + k + '.', pfx + k + '.')
                elif isinstance(v, list):
                    for it in v:
                        if isinstance(it, dict):
                            if 'properties' in it:
                                for pk, pv in it['properties'].items():
                                    acc[(gfx + k, pk)] = pv
                                    collect(pv, gfx + k + '.', pfx + k + '.')
                            else:
                                collect(it, gfx + k + '.', pfx + k + '.')
    collect(obj, '', '')

def get_prop(obj, *path):
    cur = obj
    for p in path:
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            return None
    return cur

PBX_LIST = glob.glob(os.path.join(DIR, '*.pbix'))
files = []
for pb in sorted(PBX_LIST):
    base = os.path.basename(pb)
    files.append({'file': base, 'path': pb})

all_pages = 0
type_counter = Counter()
textbox_runs = []          # (size, weight, family, color, text) for textbox runs
title_fonts = []           # visual title explicit fontSize
label_fonts = []           # axis/data-label explicit fontSize
font_families = Counter()
theme_record = []
format_strings = []
label_styles = []
insight_panels = []        # long textboxes
all_card_fonts = []
theme_classes = defaultdict(Counter)
theme_texts = defaultdict(Counter)
theme_record = []
errors = []

for f in files:
    pb = f['path']
    try:
        z = zipfile.ZipFile(pb)
        names = z.namelist()
        if 'Report/Layout' not in names:
            errors.append((pb, 'no layout'))
            continue
        L = decode_layout(z.read('Report/Layout'))
        # theme
        theme = None
        for n in names:
            if n.startswith('Report/StaticResources/SharedResources/BaseThemes/'):
                try:
                    theme = json.loads(z.read(n))
                except Exception:
                    pass
                break
    except Exception as e:
        errors.append((pb, 'zip/layout: %s' % e))
        continue

    if theme:
        tc = theme.get('textClasses')
        rec = {'file': f['file'], 'classes': {}}
        if isinstance(tc, dict):
            for k, v in tc.items():
                rec['classes'][k] = {
                    'fontSize': str(parse_pt(v.get('fontSize'))),
                    'fontFamily': v.get('fontFamily'),
                    'foreground': None,
                }
                if font_families is not None:
                    ff = v.get('fontFamily')
                    if ff and isinstance(ff, str) and ff.strip("' "):
                        font_families[ff.strip("' ")] += 1
        theme_record.append(rec)
        dc = theme.get('dataColors')
        if dc and hasattr(dc, 'items'):
            for kk, vv in dc.items():
                pass

    sections = L.get('sections', [])
    for si, sec in enumerate(sections):
        all_pages += 1
        page = sec.get('displayName', 'Page %d' % si)
        pw, ph = sec.get('width'), sec.get('height')
        for vc in sec.get('visualContainers', []):
            try:
                c = json.loads(vc['config'])
            except Exception:
                continue
            sv = c.get('singleVisual', {})
            vtype = sv.get('visualType')
            if not vtype:
                continue
            type_counter[vtype] += 1
            objects = sv.get('objects') or {}
            vco = sv.get('vcObjects') or {}

            # ---- textboxes / richText: paragraphs + runs
            if vtype in ('textbox', 'richText', 'rectangle', 'shape', 'image'):
                paras = get_prop(objects, 'general')
                runs = []
                if isinstance(paras, list):
                    for grp in paras:
                        props = grp.get('properties', {})
                        for pk, pv in props.items():
                            if 'paragraph' in pk.lower() or pk == 'paragraphs':
                                pv = lit(pv)
                                if isinstance(pv, list):
                                    for para in pv:
                                        if not isinstance(para, dict):
                                            continue
                                        trs = para.get('textRuns')
                                        if isinstance(trs, list):
                                            for tr in trs:
                                                if not isinstance(tr, dict):
                                                    continue
                                                val = tr.get('value')
                                                sty = tr.get('textStyle') or {}
                                                runs.append({
                                                    'text': val,
                                                    'fontSize': parse_pt(sty.get('fontSize')),
                                                    'fontWeight': sty.get('fontWeight'),
                                                    'fontFamily': sty.get('fontFamily'),
                                                    'color': norm_color(sty.get('color')),
                                                })
                # vcObjects.title -> full-textbox title style
                tstyle = None
                tshow = None
                tfont = None
                tsize = None
                tbold = None
                tcol = None
                tvco = get_prop(vco, 'title')
                if isinstance(tvco, list):
                    tvco = tvco[0] if tvco else None
                if isinstance(tvco, dict):
                    prop = tvco.get('properties', tvco)
                    tshow = lit(prop.get('show'))
                    ttext = lit(prop.get('text'))
                    tfont = lit(prop.get('fontFamily'))
                    tsize = parse_pt(lit(prop.get('fontSize')))
                    tbold = lit(prop.get('bold'))
                    tcol = norm_color(prop.get('fontColor'))
                    if isinstance(tsize, (int, float)):
                        title_fonts.append((page, vtype, tsize, tbold, tcol, str(ttext)[:60]))
                    if tfont:
                        font_families[tfont] += 1
                if vtype == 'textbox':
                    combined = ' | '.join(str(r['text']) if isinstance(r['text'], str) else '' for r in runs)
                    if len(combined) > 120:
                        insight_panels.append({
                            'file': f['file'], 'page': page,
                            'x': vc.get('x'), 'y': vc.get('y'),
                            'w': vc.get('width'), 'h': vc.get('height'),
                            'text': combined[:2000],
                            'runs': runs,
                        })
                    for r in runs:
                        if r['fontSize']:
                            textbox_runs.append((r['fontSize'], r['fontWeight'], r['fontFamily'], r['color'], r['text']))

            # ---- generic font info anywhere in objects/vcObjects (axes, labels, legend, tiles)
            acc = {}
            if isinstance(objects, dict):
                walk_objects(objects, '', acc)
            if isinstance(vco, dict):
                walk_objects(vco, 'VC.', acc)

            if vtype in ('card', 'multiRowCard', 'cardVisual'):
                for (pfx, key), val in acc.items():
                    lv = lit(val)
                    if key in ('fontSize', 'valueFontSize', 'calloutFontSize', 'labelFontSize', 'subTitleFontSize') and isinstance(lv, (int, float)):
                        all_card_fonts.append((pfx + key, lv))

            for (pfx, key), val in acc.items():
                lv = lit(val)
                if key in ('fontFamily', 'fontFamilyName'):
                    ff = lv
                    if isinstance(ff, str):
                        font_families[ff] += 1
                if key in ('fontSize', 'labelFontSize', 'titleFontSize', 'calloutFontSize', 'headerFontSize',
                           'axisFontSize', 'legendFontSize', 'dataLabelFontSize') and isinstance(lv, (int, float, str)):
                    fs = parse_pt(lv)
                    if isinstance(fs, float):
                        label_fonts.append((pfx + key, fs))
                if key == 'labelStyle' and isinstance(lv, str):
                    label_styles.append(lv)
                if key in ('formatString', 'labelDisplayUnits', 'valueDisplayUnits'):
                    if isinstance(lv, str) and lv and key == 'formatString':
                        format_strings.append(lv)

for pb, err in errors:
    print('ERR', pb, err)

print('files ok:', len(files) - len(errors), 'errors:', len(errors))
print('pages:', all_pages)
print('visual types:')
for t, n in type_counter.most_common(40):
    print('  %-28s %d' % (t, n))
print('font families (all sources):', font_families.most_common(20))

from collections import Counter as C
size_c = C()
for fs, *_ in textbox_runs:
    size_c['tb'] += 1
    pass
fs_hist = C(r[0] for r in textbox_runs)
print('textbox run fontSize histogram:', fs_hist.most_common(20))
wt_hist = C((r[1] or 'normal') for r in textbox_runs)
print('textbox run fontWeight:', wt_hist.most_common(10))
tf_hist = C((round(t[2], 1) if isinstance(t[2], (int, float)) else t[2]) for t in title_fonts)
print('visual title fontSize:', tf_hist.most_common(15))
lf_hist = C(k + '=' + str(v) for k, v in label_fonts if isinstance(v, (int, float)))
print('axis/label font sizes:', lf_hist.most_common(25))
fs_count = C(fs for fs in format_strings)
print('format strings:', fs_count.most_common(20))
print('card value/callout font sizes:', C(k + '=' + str(v) for k, v in all_card_fonts).most_common(15))
print('labelStyle (donut/pie):', C(label_styles).most_common(10))
print('insight-ish long textbox panels:', len(insight_panels))
for p in insight_panels[:8]:
    print('  ->', p['file'][:40], p['page'], 'rect=(%s,%s %sx%s)' % (p['x'], p['y'], p['w'], p['h']), 'text len', len(p['text']))

json.dump({
 'type_counter': dict(type_counter),
 'textbox_runs': textbox_runs[:500],
 'title_fonts': title_fonts,
 'label_fonts': label_fonts,
 'font_families': dict(font_families),
 'format_strings': format_strings,
 'label_styles': label_styles,
 'all_card_fonts': all_card_fonts,
 'insight_panels': insight_panels,
 'theme_record': theme_record,
 'errors': errors,
 'pages': all_pages,
 'files_ok': len(files) - len(errors),
}, open(OUT_JSON, 'w'), indent=1, default=str)
print('wrote', OUT_JSON)