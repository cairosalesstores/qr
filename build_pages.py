"""Build one product page per item into docs/, ready to publish on GitHub Pages.

Usage:  python build_pages.py

Each page (docs/p/<code>.html) is what the big QR code on the printed card opens:
photo, price (cash + installments), full specs, highlights, and a comparison with
similar products in the store — on size, performance, warranty and value, not just price.
"""
import csv
import hashlib
import html
import json
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent
DOCS = ROOT / "docs"
SITE_BASE = "https://cairosalesstores.github.io/qr"
ASSISTANT_NAME = "كايرو"


def css_version():
    """Short hash of the stylesheet, so a changed design always beats the cache."""
    return hashlib.sha1(CSS.encode()).hexdigest()[:8]


def public_config():
    """Supabase URL + publishable key, so the page can reach the assistant.
    Both are designed to be public; the secret keys never leave this Mac."""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return {}
    env = dict(line.split("=", 1) for line in env_file.read_text().splitlines()
               if "=" in line and not line.startswith("#"))
    url, key = env.get("SUPABASE_URL", "").strip(), env.get("SUPABASE_ANON_KEY", "").strip()
    return {"url": url, "key": key} if url and key else {}

STORE = {"name": "أسواق القاهرة للمبيعات", "hotline": "16141"}

BRAND_AR = {
    "LG": "إل جي", "SAMSUNG": "سامسونج", "TORNADO": "تورنيدو", "BEKO": "بيكو",
    "Bompani": "بومباني", "Glem Gas": "جليم جاز", "Simfer": "سيمفر", "Syinix": "سينكس",
    "TCL": "تي سي إل", "Haier": "هاير", "Hisense": "هايسنس", "Fresh": "فريش",
    "Unionaire": "يونيون اير", "Ariston": "أريستون", "Indesit": "إنديست", "Bosch": "بوش",
    "Elica": "إليكا", "Gorenje": "جورينيه", "Fagor": "فاجور", "Franke": "فرانكي",
}


# --------------------------------------------------------------------------- data


def family_of(p):
    """Group products so only comparable things are compared."""
    text = f"{p['site'].get('category','')} {p['site'].get('title','')}"
    if "شاشات" in text or "تليفزيون" in text or "تلفزيون" in text:
        return "tv"
    if "شفاط" in text:
        return "hood"
    if "غسال" in text:
        return "washer"
    return "other"


def num(value):
    m = re.search(r"\d[\d,.]*", str(value or ""))
    return float(m.group().replace(",", "")) if m else None


def size_of(p, family):
    """Screen inches / hood width in cm / washer kilos."""
    s = p["site"]["specs"]
    text = f"{p['site'].get('title','')} {p.get('store_name','')}"
    if family == "tv":
        return num(s.get("الحجم")) or num(re.search(r"(\d{2,3})\s*بوصة", text).group(1) if re.search(r"(\d{2,3})\s*بوصة", text) else None)
    if family == "hood":
        return num(s.get("الأبعاد")) or num(re.search(r"(\d{2,3})\s*سم", text).group(1) if re.search(r"(\d{2,3})\s*سم", text) else None)
    if family == "washer":
        return num(s.get("الحجم"))
    return None


def performance_of(p, family):
    """The number customers actually compare within a family."""
    s = p["site"]["specs"]
    text = " ".join([p["site"].get("title", ""), p["site"].get("short_description", ""), p.get("store_name", "")])
    if family == "tv":
        panel = next((k for k in ("OLED", "QNED", "Neo QLED", "QLED", "Mini LED", "LED")
                      if k.lower() in text.lower()), "LED")
        return {"label": "نوع الشاشة", "text": panel, "value": None, "better": "high"}
    if family == "hood":
        m = re.search(r"(\d{3,4})\s*(?:م ?3|م³|متر مكعب)", text)
        v = float(m.group(1)) if m else None
        return {"label": "قوة الشفط", "text": f"{int(v):,} م³/س" if v else "—", "value": v, "better": "high"}
    if family == "washer":
        m = re.search(r"(\d{3,4})\s*لفة", text)
        v = float(m.group(1)) if m else None
        return {"label": "سرعة العصر", "text": f"{int(v):,} لفة" if v else "—", "value": v, "better": "high"}
    return {"label": "", "text": "—", "value": None, "better": "high"}


def warranty_years(p):
    return num(p["site"]["specs"].get("الضمان")) or (2 if "عامين" in str(p["site"]["specs"].get("الضمان", "")) else None)


def price_of(p):
    return num(p["site"].get("price_cash"))


def size_unit(family):
    return {"tv": "بوصة", "hood": "سم", "washer": "كيلو"}.get(family, "")


def value_row(p, family):
    """Value for money, e.g. price per inch / per m³ of suction."""
    price, size = price_of(p), size_of(p, family)
    perf = performance_of(p, family)
    if family == "tv" and price and size:
        return {"label": "السعر لكل بوصة", "text": f"{round(price / size):,} ج", "value": price / size, "better": "low"}
    if family == "hood" and price and perf["value"]:
        return {"label": "السعر لكل م³ شفط", "text": f"{round(price / perf['value']):,} ج", "value": price / perf["value"], "better": "low"}
    if family == "washer" and price and size:
        return {"label": "السعر لكل كيلو", "text": f"{round(price / size):,} ج", "value": price / size, "better": "low"}
    return None


def rivals(code, products, family, limit=3):
    """Closest products of the same family, by size then price."""
    me = products[code]
    my_size, my_price = size_of(me, family), price_of(me)
    others = [(c, p) for c, p in products.items()
              if c != code and p.get("site") and family_of(p) == family and price_of(p)]

    def distance(item):
        _, p = item
        s, pr = size_of(p, family), price_of(p)
        size_gap = abs((s or 0) - (my_size or 0)) if my_size and s else 99
        price_gap = abs((pr or 0) - (my_price or 0)) / max(my_price or 1, 1)
        return (size_gap, price_gap)

    return [c for c, _ in sorted(others, key=distance)[:limit]]


# --------------------------------------------------------------------------- text


def clean_title(p):
    title = p["site"].get("title") or p.get("store_name", "")
    title = title.replace(p["model"], "").strip(" -·")
    title = re.sub(r"\s+TV\s*$", "", title)  # "شاشة … سمارت TV" — the TV only orphans a line
    return re.sub(r"\s+", " ", title).strip()


def series_hint(p):
    """The short series code makes two otherwise identical rows tellable apart."""
    text = f"{p['site'].get('title', '')} {p.get('model', '')}"
    for token in re.findall(r"\b[A-Z]{1,5}\d{1,3}[A-Z]?\b", text.upper()):
        if token in ("4K", "8K", "3D") or token.isdigit():
            continue
        return token
    return ""


def compare_label(p, family):
    """Identity for a comparison row: brand, size and the one spec that separates them.
    The catalogue title is a marketing line and truncates into nonsense."""
    bits = [(p["site"].get("brand") or "").strip()]
    size = size_of(p, family)
    if size:
        bits.append(f"{int(size)} {size_unit(family)}")
    perf = performance_of(p, family)["text"]
    series = series_hint(p)
    if perf and perf != "—":
        # "QNED QNED85" reads as a stutter; the series already carries the panel
        if series and perf.upper().replace(" ", "") in series.upper():
            bits.append(series)
        elif series:
            bits.append(f"{perf} {series}")
        else:
            bits.append(perf)
    elif series:
        bits.append(series)
    label = " · ".join(b for b in bits if b)
    return label or short_title(p, 30)


def short_title(p, limit=64):
    """A comparison row needs a name, not the whole catalogue line."""
    t = clean_title(p)
    for cut in (" و ساوند", " و مكبر", " + ", " / "):
        if cut in t:
            t = t.split(cut)[0]
    t = re.split(r"\s+-\s+", t)[0].strip()
    return t if len(t) <= limit else t[:limit].rsplit(" ", 1)[0] + "…"


def quick_facts(p, family):
    """A few headline facts, built from the spec table (no guessing)."""
    s = p["site"]["specs"]
    perf = performance_of(p, family)
    size = size_of(p, family)
    facts = []
    if size:
        facts.append(("المقاس", f"{int(size)} {size_unit(family)}"))
    if perf["text"] and perf["text"] != "—":
        facts.append((perf["label"], perf["text"]))
    for key in ("دقة", "نظام التشغيل", "اللون", "شكل الشفاط", "نوع الفتحة", "عدد الفلاتر",
                "ريسيفر داخلي", "سمارت", "مدخنة", "بخار", "مجفف", "حلة ستانلس"):
        v = (s.get(key) or "").strip()
        if v and v.lower() not in ("no", "لا"):
            facts.append((key, "نعم" if v.lower() == "yes" else v))
    if s.get("الضمان"):
        facts.append(("الضمان", s["الضمان"]))
    if s.get("بلد الصنع"):
        facts.append(("بلد الصنع", s["بلد الصنع"]))
    return facts[:6]


def spec_rows(p):
    """All specs from the site, model row dropped (it is shown above)."""
    skip = {"الموديل"}
    return [(k, v) for k, v in p["site"]["specs"].items() if k not in skip and v]


def fmt(n):
    return f"{int(n):,}" if n is not None else "—"


# --------------------------------------------------------------------------- html


CSS = """
@font-face { font-family: "Cairo"; src: url("../assets/Cairo.ttf"); font-weight: 200 1000; }
@font-face { font-family: "Inter"; src: url("../assets/Inter.ttf"); font-weight: 100 900; }
@font-face { font-family: "Naskh"; src: url("../assets/NotoNaskhArabic.ttf"); font-weight: 400 700;
  unicode-range: U+0600-06FF, U+0750-077F, U+08A0-08FF, U+FB50-FDFF, U+FE70-FEFF, U+200C-200F; }
:root {
  --paper: #F0EEE6;       /* warm background */
  --card: #FAF9F5;        /* raised surface */
  --ink: #141413;
  --ink-soft: #3D3D3A;
  --muted: #6E6B64;
  --line: #E3E0D6;
  --clay: #D97757;        /* accent */
  --clay-deep: #B5573B;
  --leaf: #5C7F58;        /* good / cheaper */
  --sand: #CC9B7A;
}
* { box-sizing: border-box; margin: 0; padding: 0; -webkit-tap-highlight-color: transparent; }
body { background: var(--paper); font-family: "Cairo", system-ui, sans-serif; color: var(--ink); padding-bottom: 24px; }
.sheet { max-width: 460px; margin: 0 auto; background: var(--paper); min-height: 100vh; }
.n { font-family: "Inter", sans-serif; direction: ltr; unicode-bidi: isolate; font-feature-settings: "tnum"; }
.serif { font-family: "Naskh", "Inter", serif; }
img { max-width: 100%; display: block; }

/* ---------- identity ---------- */
.band { padding: 15px 20px 13px; display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.head { padding: 18px 20px 0; }
.band .store { display: flex; align-items: center; gap: 8px; text-decoration: none; font-size: 12px; font-weight: 700; color: var(--muted); }
.band .store img { width: 28px; height: 28px; border-radius: 50%; }
.stock { font-size: 10.5px; font-weight: 700; border-radius: 30px; padding: 5px 11px; background: rgba(92,127,88,.14); color: var(--leaf); white-space: nowrap; }
.stock.out { background: rgba(181,87,59,.13); color: var(--clay-deep); }
.stock.checking { background: rgba(204,155,122,.2); color: #8A6440; }
.soldout.checking { background: rgba(204,155,122,.14); border-color: rgba(204,155,122,.4); }
.soldout.checking b { color: #8A6440; }
h1 { font-family: "Naskh", "Inter", sans-serif; font-size: 22px; font-weight: 700; line-height: 1.6; letter-spacing: -.2px; }
.meta { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 11px; }
.meta span { font-size: 11.5px; font-weight: 700; color: var(--muted); }
.meta span + span::before { content: "·"; margin-inline-end: 6px; color: var(--line); }

/* ---------- photo + price ---------- */
.shot { margin: 0 14px; border-radius: 24px; padding: 14px 16px; background: #FFFFFF;
        box-shadow: 0 1px 2px rgba(20,20,19,.04), 0 10px 24px -14px rgba(20,20,19,.25); }
.shot img { margin: 0 auto; max-height: 215px; width: auto; }
.money { margin: 14px 20px 0; display: flex; align-items: flex-end; gap: 12px; }
.money .lbl { font-size: 10.5px; font-weight: 700; color: var(--muted); letter-spacing: .4px; }
.money .cash { font-family: "Inter"; font-weight: 800; font-size: 38px; letter-spacing: -1.4px; line-height: 1.05; }
.money .cur { font-size: 13px; font-weight: 700; color: var(--muted); }
.money .inst { margin-inline-start: auto; text-align: left; font-size: 10.5px; font-weight: 600; color: var(--muted); line-height: 1.7; }
.money .inst b { font-family: "Inter"; font-weight: 800; font-size: 15px; color: var(--ink); }
.price { margin: 20px 20px 0; }
.price .lbl { font-size: 11px; font-weight: 700; color: var(--muted); letter-spacing: .3px; }
.price .big { font-family: "Inter", sans-serif; font-weight: 800; font-size: 44px; letter-spacing: -2px;
              line-height: 1.12; margin-top: 1px; display: flex; align-items: baseline; gap: 7px;
              direction: ltr; unicode-bidi: isolate; justify-content: flex-end; }
.price .big small { font-family: "Cairo", sans-serif; font-size: 14px; font-weight: 700; color: var(--muted); letter-spacing: 0; }
.price .save { display: inline-block; margin-top: 10px; font-size: 11.5px; font-weight: 800; color: var(--leaf);
               background: rgba(92,127,88,.12); border-radius: 30px; padding: 6px 12px; }
.price .inst { margin-top: 9px; font-size: 12.5px; font-weight: 600; color: var(--ink-soft); }
.price .inst b { font-weight: 800; color: var(--ink); }
.pay { margin: 16px 20px 0; display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.pay .opt { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 13px 14px; }
.pay .opt.cash { background: rgba(217,119,87,.1); border-color: rgba(217,119,87,.3); }
.pay .k { font-size: 10.5px; font-weight: 700; color: var(--muted); }
.pay .v { font-family: "Inter"; font-weight: 800; font-size: 25px; letter-spacing: -1px; margin-top: 5px; line-height: 1.1; }
.pay .v small { font-family: "Cairo"; font-size: 11px; font-weight: 700; color: var(--muted); letter-spacing: 0; }
.pay .tip { font-size: 10px; font-weight: 700; color: var(--muted); margin-top: 6px; }
.pay .cash .tip { color: var(--leaf); }
.pay .opt:only-child { grid-column: 1 / -1; }
.paynote { margin: 10px 20px 0; background: rgba(92,127,88,.1); border: 1px solid rgba(92,127,88,.25); border-radius: 12px; padding: 11px 13px; font-size: 11.5px; font-weight: 600; color: var(--ink-soft); line-height: 1.7; }
.paynote b { font-weight: 800; color: var(--ink); }
.fresh { margin: 10px 20px 0; display: flex; align-items: center; gap: 7px; font-size: 12px; font-weight: 700; color: var(--muted); }
.fresh .dot { width: 6px; height: 6px; border-radius: 50%; background: var(--leaf); flex-shrink: 0; }
.soldout { margin: 16px 20px 0; padding: 15px 16px; border-radius: 14px; background: rgba(217,119,87,.1); border: 1px solid rgba(217,119,87,.28); }
.soldout b { display: block; font-family: "Naskh", "Inter", sans-serif; font-size: 16px; color: var(--clay-deep); margin-bottom: 5px; }
.soldout span { font-size: 12px; font-weight: 600; color: var(--ink-soft); line-height: 1.7; }


/* ---------- why this product ---------- */
.points { margin: 24px 20px 0; }
.points .pt { display: flex; gap: 10px; align-items: flex-start; padding: 9px 0; }
.points .pt + .pt { border-top: 1px solid var(--line); }
.points .pt i { width: 5px; height: 5px; border-radius: 50%; background: var(--clay);
                margin-top: 9px; flex-shrink: 0; }
.points .pt p { margin: 0; font-size: 13.5px; line-height: 1.75; color: var(--ink-soft); }
.points .pt b { color: var(--ink); font-weight: 800; }

/* ---------- this price, beside the nearest ones ---------- */
.compare { margin: 26px 20px 0; }
.cmp-list { display: grid; gap: 7px; }
.cmp-row { display: flex; align-items: center; gap: 8px; padding: 11px 12px; border-radius: 13px;
           background: var(--card); border: 1px solid var(--line); text-decoration: none; color: inherit;
           min-width: 0; }
.cmp-row.me { background: var(--ink); border-color: var(--ink); color: #F3F1EC; }
.cmp-name { flex: 1; min-width: 0; font-size: 11.5px; font-weight: 700; line-height: 1.5;
            white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cmp-price { font-family: "Inter", sans-serif; font-weight: 800; font-size: 13.5px; white-space: nowrap; }
.cmp-diff { font-size: 10px; font-weight: 800; white-space: nowrap; border-radius: 20px; padding: 5px 8px; }
.cmp-diff.up { color: var(--clay-deep); background: rgba(181,87,59,.12); }
.cmp-diff.down { color: var(--leaf); background: rgba(92,127,88,.14); }
.cmp-diff.here { color: rgba(243,241,236,.6); background: rgba(255,255,255,.1); }
.cmp-note { font-size: 11.5px; font-weight: 700; color: var(--muted); margin-top: 10px; }

/* ---------- the one line that answers "is it right for me" ---------- */
.fit { margin: 16px 20px 0; display: flex; gap: 9px; align-items: flex-start;
       border-inline-start: 3px solid rgba(92,127,88,.45); padding: 2px 13px 2px 0;
       font-size: 13px; line-height: 1.8; color: var(--ink-soft); }
.fit .tick { color: var(--leaf); font-weight: 800; flex-shrink: 0; }
.fit p { margin: 0; flex: 1; }
.fit b { color: var(--ink); font-weight: 800; }

/* ---------- كايرو, the assistant ---------- */
.kairo { margin: 0; background: #1B1B19; color: #F3F1EC; border-radius: 20px; padding: 16px 15px 15px; }
.kairo-top { display: flex; align-items: center; gap: 11px; }
.kairo-who { min-width: 0; }
.kairo-top .lang { margin-inline-start: auto; }
.kairo-face { width: 36px; height: 36px; border-radius: 50%; flex-shrink: 0; color: #fff;
              background: linear-gradient(150deg, var(--clay), #E0906F); display: grid; place-items: center; }
.kairo-face svg { width: 19px; height: 19px; }
.kairo-top b { font-family: "Naskh", "Inter", sans-serif; font-size: 15px; font-weight: 700; display: block; }
.kairo-top span { font-size: 11px; opacity: .62; }
.kairo-asks { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 14px; }
.kairo-asks .ask { font: inherit; font-size: 12px; font-weight: 700; color: #FFF6F1; cursor: pointer;
                   background: rgba(217,119,87,.18); border: 1px solid rgba(217,119,87,.45);
                   border-radius: 30px; padding: 8px 14px; }
.kairo-bar { display: flex; align-items: center; gap: 6px; margin-top: 12px; background: #fff;
             border-radius: 30px; padding: 6px 6px 6px 10px; }
.kairo-bar input { flex: 1; border: 0; background: none; font: inherit; font-family: "Cairo", sans-serif;
                   font-size: 13.5px; color: var(--ink); outline: none; min-width: 0; padding: 6px 0; }
.kairo-bar button[type="submit"] { border: 0; width: 36px; height: 36px; border-radius: 50%; background: var(--clay);
                    display: grid; place-items: center; cursor: pointer; flex-shrink: 0; }
.kairo-bar button svg { width: 16px; height: 16px; }
.kairo-bar .mic { border: 0; background: none; color: var(--muted); width: 32px; height: 32px;
                  display: grid; place-items: center; cursor: pointer; flex-shrink: 0; border-radius: 50%; }
.kairo-bar .mic svg { width: 19px; height: 19px; }
.kairo-bar .mic.on { color: #fff; background: var(--clay); animation: hear 1.1s ease-in-out infinite; }
@keyframes hear { 0%, 100% { box-shadow: 0 0 0 0 rgba(217,119,87,.5); } 50% { box-shadow: 0 0 0 7px rgba(217,119,87,0); } }
.kairo .lang { border: 1px solid rgba(255,255,255,.22); background: rgba(255,255,255,.06);
               color: #F3F1EC; cursor: pointer; font: inherit; font-size: 11px; font-weight: 800;
               border-radius: 20px; padding: 6px 11px; flex-shrink: 0; }
.kairo-thread { margin-top: 13px; display: flex; flex-direction: column; gap: 9px;
                max-height: 340px; overflow-y: auto; }
.kairo-hint { margin-top: 11px; background: rgba(217,119,87,.16); border: 1px solid rgba(217,119,87,.4);
              border-radius: 12px; padding: 10px 12px; font-size: 11.5px; line-height: 1.7; color: #F3F1EC; }
.kairo-hint b { font-weight: 800; }
.turn { display: flex; }
.turn.me { justify-content: flex-start; }
.turn.them { justify-content: flex-end; }
.turn .b { max-width: 88%; border-radius: 14px; padding: 10px 13px; font-size: 13px; line-height: 1.75;
           white-space: pre-wrap; }
.turn.me .b { background: rgba(255,255,255,.1); border-start-start-radius: 4px; }
.turn.them .b { background: #fff; color: var(--ink); border-start-end-radius: 4px; }
.turn .b.dots { color: var(--muted); letter-spacing: 3px; animation: breathe 1.1s ease-in-out infinite; }
@keyframes breathe { 0%, 100% { opacity: .35; } 50% { opacity: 1; } }
.turn .handover { background: var(--clay); color: #fff; font-size: 12.5px; font-weight: 800;
                  border-radius: 12px; padding: 10px 16px; }

/* ---------- everything else, one tap away ---------- */
.more { display: grid; gap: 9px; }
.more details { background: var(--card); border: 1px solid var(--line); border-radius: 14px; overflow: hidden; }
.more summary { list-style: none; cursor: pointer; padding: 13px 16px; gap: 12px;
                display: flex; align-items: center; justify-content: space-between; }
.more summary div b { display: block; font-family: "Naskh", "Inter", sans-serif; font-size: 14.5px; font-weight: 700; }
.more summary div span { display: block; font-size: 11px; font-weight: 600; color: var(--muted); margin-top: 3px; }
.more summary::-webkit-details-marker { display: none; }
.more .chev { width: 9px; height: 9px; flex-shrink: 0; border-right: 2px solid var(--muted);
              border-bottom: 2px solid var(--muted); transform: rotate(45deg) translate(-2px,-2px);
              transition: transform .15s ease; }
.more details[open] .chev { transform: rotate(-135deg) translate(-2px,-2px); }
.more .inner { padding: 0 16px 16px; }
.more .cmp { border: 0; }

/* ---------- sections ---------- */
section { margin: 22px 20px 0; }
h2 { font-family: "Naskh", "Inter", sans-serif; font-size: 17px; font-weight: 700; margin-bottom: 4px; }
.lede { font-size: 12.5px; font-weight: 600; color: var(--muted); line-height: 1.7; margin-bottom: 13px; }

/* stat tiles */
.stats { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.stat { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 13px; }
.stat .k { font-size: 12px; font-weight: 700; color: var(--muted); }
.stat .v.small { font-family: "Cairo"; font-size: 15px; letter-spacing: 0; }
.stat .v { font-family: "Inter"; font-size: 21px; font-weight: 800; margin-top: 6px; line-height: 1.1; letter-spacing: -.5px; }
.stat .v small { font-family: "Cairo"; font-size: 11px; font-weight: 700; color: var(--muted); letter-spacing: 0; }
.stat .note { font-size: 11.5px; font-weight: 700; margin-top: 6px; line-height: 1.5; }
.up { color: var(--clay-deep); } .down { color: var(--leaf); } .flat { color: var(--muted); }

/* price meter */
.meter { margin-top: 12px; background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 14px; }
.meter .cap { font-size: 12px; font-weight: 700; color: var(--muted); margin-bottom: 11px; }
.meter .track { position: relative; height: 7px; border-radius: 6px; margin-top: 26px; background: linear-gradient(to left, rgba(92,127,88,.45), rgba(217,119,87,.5)); }
.meter .pin { position: absolute; top: -27px; transform: translateX(50%); background: var(--clay); color: #fff; font-size: 10px; font-weight: 800; border-radius: 7px; padding: 3px 7px; white-space: nowrap; }
.meter .pin::after { content: ""; position: absolute; bottom: -9px; inset-inline-start: calc(50% - 5px); width: 10px; height: 10px; background: var(--clay); transform: rotate(45deg); border-radius: 2px; box-shadow: 0 1px 2px rgba(20,20,19,.18); }
.meter .ends { display: flex; justify-content: space-between; margin-top: 9px; font-size: 11.5px; font-weight: 700; color: var(--muted); }

/* guidance + explainer */
.advice { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 15px; }
.advice p { font-size: 12.5px; line-height: 1.85; color: var(--ink-soft); }
.advice p + p { margin-top: 9px; }
.advice b { color: var(--ink); }
.scale { display: flex; gap: 6px; margin-top: 12px; }
.scale div { flex: 1; text-align: center; font-size: 11px; font-weight: 700; color: var(--muted); background: rgba(20,20,19,.045); border-radius: 8px; padding: 7px 3px; line-height: 1.5; }
.scale div.on { background: var(--clay); color: #fff; }
.scale div.on span { color: rgba(255,255,255,.85); }
.scale span { display: block; font-family: "Inter"; font-weight: 800; font-size: 11px; color: var(--ink); }

/* faq */
.faq { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 4px 15px; }
.faq .q { padding: 12px 0; border-bottom: 1px solid var(--line); }
.faq .q:last-child { border: 0; }
.faq .q b { display: block; font-size: 12px; font-weight: 700; margin-bottom: 4px; }
.faq .q span { font-size: 12.5px; font-weight: 600; color: var(--muted); line-height: 1.65; }

/* comparison */
.cmp { width: 100%; border-collapse: collapse; font-size: 11.5px; background: var(--card); border: 1px solid var(--line); border-radius: 14px; overflow: hidden; }
.cmp th, .cmp td { padding: 10px 5px; text-align: center; border-bottom: 1px solid var(--line); }
.cmp thead th { font-weight: 700; font-size: 10.5px; line-height: 1.4; vertical-align: bottom; }
.cmp thead th a { color: inherit; text-decoration: none; display: block; padding: 6px 2px; margin: -6px -2px; }
.cmp thead th .go { display: block; font-size: 11px; font-weight: 700; color: var(--clay-deep); margin-top: 4px; }
.cmp thead th small { display: block; font-weight: 600; color: var(--muted); font-size: 10.5px; margin-top: 3px; }
.cmp tbody th { text-align: right; font-weight: 600; color: var(--muted); font-size: 10.5px; white-space: nowrap; padding-inline-start: 12px; }
.cmp td { font-weight: 700; }
.cmp .me { background: rgba(217,119,87,.1); }
.cmp .best { color: var(--leaf); font-weight: 800; }
.cmp .best::after { content: " ✓"; font-size: 9px; }
.mytag { display: inline-block; font-size: 9px; font-weight: 800; color: #fff; background: var(--clay); border-radius: 20px; padding: 2px 8px; margin-bottom: 5px; }
.legend { margin-top: 10px; font-size: 12px; color: var(--muted); font-weight: 600; line-height: 1.6; }

/* facts + specs */
.facts { display: grid; grid-template-columns: 1fr 1fr; gap: 1px; background: var(--line);
         border: 1px solid var(--line); border-radius: 14px; overflow: hidden; }
.fct { background: var(--card); padding: 12px 14px; display: flex; flex-direction: column; gap: 3px; }
.fct span { font-size: 10.5px; font-weight: 700; color: var(--muted); }
.fct b { font-size: 13px; font-weight: 800; color: var(--ink); }
.about { font-size: 12.5px; line-height: 1.9; color: var(--ink-soft); margin-top: 12px; }
.specs { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 4px 15px; }
.sp { display: flex; align-items: center; gap: 10px; padding: 10px 0; border-bottom: 1px solid var(--line); }
.sp:last-child { border: 0; }
.sp .k { flex: 1; font-size: 11.5px; font-weight: 600; color: var(--muted); }
.sp .v { font-size: 12.5px; font-weight: 700; text-align: left; }

.note-foot { margin: 24px 20px 0; padding-top: 16px; border-top: 1px solid var(--line); text-align: center; font-size: 12px; color: var(--muted); font-weight: 600; line-height: 1.9; }
.note-foot b { font-family: "Inter"; color: var(--ink-soft); }


/* alternatives when a product has no price */
.alts { display: grid; grid-template-columns: repeat(3, 1fr); gap: 9px; }
.alt-card { background: var(--card); border: 1px solid var(--line); border-radius: 13px; padding: 9px; text-decoration: none; color: inherit; display: block; }
.alt-card img { border-radius: 9px; margin-bottom: 8px; width: 100%; }
.alt-card b { display: block; font-size: 11px; font-weight: 700; line-height: 1.45; height: 47px; overflow: hidden; }
.alt-card span { display: block; font-family: "Inter"; font-weight: 800; font-size: 12.5px; color: var(--clay-deep); margin-top: 6px; }
.alt-card span small { font-family: "Cairo"; font-size: 10px; font-weight: 700; color: var(--muted); }

/* index finder */
.finder { padding: 0 20px; display: grid; gap: 10px; }
.search { display: flex; align-items: center; gap: 9px; background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 11px 13px; color: var(--muted); }
.search svg { width: 17px; height: 17px; flex-shrink: 0; }
.search input { flex: 1; border: 0; background: none; font: inherit; font-size: 13.5px; color: var(--ink); outline: none; min-width: 0; }
.tabs { display: flex; gap: 7px; overflow-x: auto; padding-bottom: 2px; }
.tab { font: inherit; font-size: 12.5px; font-weight: 700; cursor: pointer; white-space: nowrap; background: var(--card); color: var(--ink-soft); border: 1px solid var(--line); border-radius: 20px; padding: 7px 14px; }
.tab.on { background: var(--ink); color: var(--paper); border-color: var(--ink); }
.tab .n { opacity: .6; font-size: 11px; }
.tab:focus-visible, .search input:focus-visible { outline: 2px solid var(--clay); outline-offset: 2px; }
.empty { text-align: center; color: var(--muted); font-size: 13px; font-weight: 600; padding: 30px 0; }
.item .p.out { color: var(--muted); font-family: "Cairo"; font-size: 11.5px; font-weight: 700; }
.item .p small { font-family: "Cairo"; font-size: 10.5px; font-weight: 700; color: var(--muted); }

/* index */
.page { max-width: 460px; margin: 0 auto; min-height: 100vh; }
.top { padding: 16px 20px; display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.brandline { display: flex; align-items: center; gap: 9px; text-decoration: none; color: inherit; }
.brandline img { width: 32px; height: 32px; border-radius: 50%; }
.brandline b { font-family: "Naskh", "Inter", sans-serif; font-size: 15px; font-weight: 700; }
.brandline span { display: block; font-size: 10px; color: var(--muted); font-weight: 600; }
.where { font-size: 10.5px; font-weight: 700; color: var(--muted); background: rgba(20,20,19,.05); border-radius: 8px; padding: 6px 10px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 10px; padding: 8px 20px 24px; }
.item { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 11px; text-decoration: none; color: inherit; }
.item img { border-radius: 10px; margin-bottom: 9px; }
.item b { display: block; font-size: 11.5px; font-weight: 700; line-height: 1.45; height: 33px; overflow: hidden; }
.item .p { font-family: "Inter"; font-weight: 800; font-size: 13px; color: var(--clay-deep); margin-top: 7px; }
.wrap { padding: 0 20px 24px; }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 16px; padding: 16px; margin-bottom: 12px; }
.kicker { font-size: 11px; font-weight: 700; color: var(--muted); margin-bottom: 8px; }
.sub { margin-top: 8px; font-size: 11.5px; font-weight: 600; color: var(--muted); }
.sub b { font-family: "Inter"; font-weight: 700; color: var(--ink); }
.note { margin-top: 16px; text-align: center; font-size: 10px; color: var(--muted); font-weight: 600; }
"""


# --------------------------------------------------------------------------- buying guidance


def fit_advice(p, family):
    """Who the product suits, from its size. Written as guidance, not a promise."""
    size = size_of(p, family)
    if not size:
        return None
    size = int(size)
    if family == "tv":
        low, high = round(size * 0.04, 1), round(size * 0.063, 1)
        scale = [("أقل من 43", "أوضة نوم"), ("50 - 55", "أوضة معيشة صغيرة"),
                 ("65 - 75", "صالة كبيرة"), ("أكبر من 75", "صالة واسعة جدًا")]
        pick = 0 if size < 43 else (1 if size <= 55 else (2 if size <= 75 else 3))
        text = (f"شاشة <b>{size} بوصة</b> بتبان أحسن لما تقعد على بُعد "
                f"<b>{low} لـ {high} متر</b> منها تقريبًا.")
        return {"text": text, "scale": scale, "pick": pick,
                "hint": "قِس المسافة بين الكنبة والحيطة اللي هتعلق عليها، والرقم ده يقولك المقاس المناسب."}
    if family == "hood":
        scale = [("60 سم", "بوتاجاز 4 شعلة"), ("90 سم", "بوتاجاز 5 شعلة أو أكبر")]
        pick = 0 if size <= 60 else 1
        perf = performance_of(p, family)
        text = (f"شفاط <b>{size} سم</b> المفروض يكون بعرض البوتاجاز أو أوسع منه شوية. "
                + (f"قوة الشفط <b>{int(perf['value']):,} م³/ساعة</b> تكفي مطبخ لحد "
                   f"<b>{round(perf['value'] / 12)} متر مربع</b> تقريبًا." if perf["value"] else ""))
        return {"text": text, "scale": scale, "pick": pick,
                "hint": "القاعدة التقريبية: حجم المطبخ × 12 = قوة الشفط اللي محتاجها."}
    if family == "washer":
        scale = [("6 - 7 كيلو", "شخص أو اتنين"), ("8 - 9 كيلو", "أسرة 3 لـ 5"),
                 ("10 كيلو فأكثر", "أسرة كبيرة")]
        pick = 0 if size <= 7 else (1 if size <= 9 else 2)
        return {"text": f"غسالة <b>{size} كيلو</b> مناسبة للغسيل اليومي "
                        f"لأسرة من <b>{max(1, size - 5)} لـ {size - 3} أفراد</b> تقريبًا.",
                "scale": scale, "pick": pick,
                "hint": "الكيلو هنا وزن الغسيل الجاف، مش سعة الحلة."}
    return None


EXPLAINERS = {
    "tv": [
        ("يعني إيه 4K؟", "دقة 3840×2160 بيكسل، يعني 4 أضعاف الفل إتش دي. الفرق بيبان أكتر في المقاسات من 55 بوصة وفوق."),
        ("الفرق بين OLED و QNED و LED؟",
         "OLED كل بيكسل بينوّر لوحده، فالأسود بيبقى أسود حقيقي وده الأغلى. QNED و QLED شاشات LED بألوان أنقى. LED العادية أرخص وألوانها أقل."),
        ("سمارت وريسيفر داخلي؟", "سمارت يعني نتفليكس ويوتيوب من غير جهاز زيادة. ريسيفر داخلي يعني القنوات الأرضية تشتغل من غير ريسيفر منفصل."),
    ],
    "hood": [
        ("قوة الشفط بتتقاس إزاي؟", "بالمتر المكعب في الساعة (م³/ساعة). كل ما زادت، كل ما شفط الزيت والروايح أسرع، وكل ما الصوت بيعلى شوية."),
        ("هرمي ولا مائل ولا بلت إن؟", "الهرمي هو الشكل التقليدي وبيشفط كويس. المائل شكله مودرن وبيوفر مساحة للراس. البلت إن بينتصب جوه الدولاب."),
        ("الفلاتر بتتغير كل قد إيه؟", "فلتر الألومنيوم بيتغسل كل شهر تقريبًا. فلتر الفحم (لو الشفاط شغال من غير مدخنة) بيتغير كل 4 لـ 6 شهور."),
    ],
    "washer": [
        ("سرعة العصر (اللفة) تفرق؟", "كل ما زادت اللفة، الغسيل بيطلع أنشف وبيجف أسرع. 1000 لفة كفاية، و1400 أحسن للبطاطين والتقيل."),
        ("البخار بيعمل إيه؟", "بيساعد في إزالة البقع والحساسية من غير نقع، وبيقلل الكرمشة."),
        ("الفرق بين فتحة أمامية وعلوية؟", "الأمامية بتوفر مياه وكهربا وبتتحط تحت الرخامة. العلوية أسهل في التحميل ومش محتاجة تنحني."),
    ],
}


def faqs(p, family):
    """Answers built from this product's own spec sheet."""
    s = p["site"]["specs"]
    yes = lambda v: (v or "").strip().lower() in ("نعم", "yes")
    out = []
    if s.get("الضمان"):
        out.append(("الضمان كام؟", f"{s['الضمان']} ضمان الوكيل" + (f" · صنع في {s['بلد الصنع']}" if s.get("بلد الصنع") else "")))
    if family == "tv":
        if s.get("دقة"):
            out.append(("الدقة إيه؟", f"{s['دقة']} بيكسل" + (" · ألترا 4K" if yes(s.get('إلترا')) else "")))
        out.append(("سمارت؟", "أيوه، بنظام " + s.get("نظام التشغيل", "سمارت") if yes(s.get("سمارت")) else "لأ، شاشة عادية"))
        out.append(("ريسيفر داخلي؟", "أيوه، مش محتاج ريسيفر منفصل" if yes(s.get("ريسيفر داخلي")) else "لأ، محتاج ريسيفر"))
        if s.get("HDMI مداخل") or s.get("USB مداخل"):
            out.append(("المداخل؟", f"{s.get('HDMI مداخل','-')} مدخل HDMI · {s.get('USB مداخل','-')} مدخل USB"))
    if family == "hood":
        if s.get("عدد الفلاتر"):
            out.append(("عدد الفلاتر؟", f"{s['عدد الفلاتر']} فلتر"))
        if s.get("مدخنة"):
            out.append(("بمدخنة؟", "أيوه، بمدخنة" if yes(s.get("مدخنة")) else "لأ، من غير مدخنة"))
        if s.get("العرض") or s.get("الإرتفاع"):
            out.append(("الأبعاد؟", f"عرض {s.get('العرض','-')} · عمق {s.get('العمق','-')} · ارتفاع {s.get('الإرتفاع','-')}"))
    if family == "washer":
        if s.get("بخار"):
            out.append(("فيها بخار؟", "أيوه" if yes(s.get("بخار")) else "لأ"))
        if s.get("مجفف"):
            out.append(("بتنشف؟", "أيوه، فيها مجفف" if yes(s.get("مجفف")) else "لأ، غسالة بس من غير مجفف"))
        if s.get("العرض"):
            out.append(("الأبعاد؟", f"عرض {s.get('العرض','-')} · عمق {s.get('العمق','-')} · ارتفاع {s.get('الإرتفاع','-')}"))
    return out[:5]



def peer_set(code, products, family, with_scope=False):
    """Products of the same family and, when there are enough, the same size."""
    my_size = size_of(products[code], family)
    same = [c for c, q in products.items()
            if family_of(q) == family and price_of(q)]
    sized = [c for c in same if my_size and size_of(products[c], family) == my_size]
    peers, scope = (sized, "size") if len(sized) >= 3 else (same, "family")
    return (peers, scope) if with_scope else peers


def analysis(code, products, family):
    """Numbers that help a customer judge the product, all derived from our own data."""
    p = products[code]
    price = price_of(p)
    peers = peer_set(code, products, family)
    prices = sorted(price_of(products[c]) for c in peers)
    out = {"peers": len(peers), "min": prices[0] if prices else None,
           "max": prices[-1] if prices else None}

    if price and prices:
        cheaper = sum(1 for x in prices if x < price)
        out["rank"] = cheaper + 1
        span = (prices[-1] - prices[0]) or 1
        out["position"] = max(0, min(100, round((price - prices[0]) / span * 100)))

    value = value_row(p, family)
    if value:
        peer_values = [v for v in ((value_row(products[c], family) or {}).get("value") for c in peers) if v]
        if peer_values:
            avg = sum(peer_values) / len(peer_values)
            out["value"] = value
            out["value_avg"] = avg
            out["value_diff"] = round((value["value"] - avg) / avg * 100)

    inst = num(p["site"].get("price_installment"))
    if inst and price and inst > price:
        out["cash_saving"] = inst - price
        out["cash_saving_pct"] = round((inst - price) / inst * 100)

    years = warranty_years(p)
    peer_years = [w for w in (warranty_years(products[c]) for c in peers) if w]
    if years:
        out["warranty"] = years
        out["warranty_best"] = bool(peer_years) and years >= max(peer_years) and len(set(peer_years)) > 1
    return out


PANEL_WORDS = [
    ("OLED", "شاشة OLED — الأسود أسود حقيقي والألوان أدق"),
    ("QNED", "تقنية QNED — ألوان أنقى وسطوع أعلى من اللي قبلها"),
    ("QLED", "تقنية QLED — ألوان أنقى وسطوع أعلى من اللي قبلها"),
    ("NANOCELL", "تقنية NanoCell — ألوان أدق من الشاشة العادية"),
    ("MINI", "إضاءة Mini LED — تباين أعلى وسطوع أقوى"),
]


def key_points(p, family):
    """The few things worth knowing, each one read straight off the spec sheet."""
    s = p["site"]["specs"]
    yes = lambda v: (v or "").strip().lower() in ("نعم", "yes")
    size = size_of(p, family)
    out = []

    if family == "tv":
        if size:
            low, high = round(size * 0.04, 1), round(size * 0.063, 1)
            out.append(f"<b>{int(size)} بوصة</b> — أحسن مشاهدة على بُعد {low} لـ {high} متر")
        panel = performance_of(p, family)["text"].upper()   # read off the title, not a spec row
        for key, line in PANEL_WORDS:
            if key in panel:
                out.append(line)
                break
        res = s.get("دقة") or ""
        if "4320" in res:
            out.append("<b>دقة 8K</b> — أعلى دقة متاحة دلوقتي")
        elif "2160" in res:
            out.append("<b>دقة 4K</b> — صورة حادة حتى لو قاعد قريب")
        if yes(s.get("سمارت")):
            system = (s.get("نظام التشغيل") or "").strip()
            out.append(f"سمارت{' بنظام ' + system if system else ''} — نتفليكس ويوتيوب من غير جهاز زيادة")
        if yes(s.get("ريسيفر داخلي")):
            out.append("ريسيفر داخلي — القنوات شغالة من غير علبة")

    elif family == "hood":
        perf = performance_of(p, family)
        if size:
            out.append(f"<b>{int(size)} سم</b> — يتحط فوق بوتاجاز بنفس العرض أو أصغر")
        if perf["value"]:
            out.append(f"شفط <b>{int(perf['value']):,} م³/ساعة</b> — يكفي مطبخ لحد "
                       f"{round(perf['value'] / 12)} متر مربع")
        if s.get("شكل الشفاط"):
            out.append(f"شكل {s['شكل الشفاط']}")
        if s.get("عدد الفلاتر"):
            out.append(f"{s['عدد الفلاتر']} فلتر")

    elif family == "washer":
        if size:
            out.append(f"<b>{int(size)} كيلو</b> — تكفي أسرة من {max(1, int(size) - 5)} لـ {int(size) - 3} أفراد")
        if s.get("نوع الفتحة"):
            out.append(f"فتحة {s['نوع الفتحة']}")
        if yes(s.get("بخار")):
            out.append("بخار — يساعد على البقع والحساسية من غير نقع")
        if yes(s.get("مجفف")):
            out.append("مجفف — الغسيل بيطلع ناشف")

    if s.get("الضمان"):
        out.append(f"ضمان <b>{s['الضمان']}</b> من الوكيل")
    return out[:4]


def price_compare(code, products, family):
    """This product beside the closest ones we sell, as a plain price difference."""
    price = price_of(products[code])
    if not price:
        return None
    rows = []
    for c in rivals(code, products, family, limit=3):
        peer_price = price_of(products[c])
        if not peer_price:
            continue
        rows.append({"code": c, "title": compare_label(products[c], family),
                     "size": size_of(products[c], family), "price": peer_price,
                     "diff": round(peer_price - price)})
    if not rows:
        return None
    codes, scope = peer_set(code, products, family, with_scope=True)
    peers = sorted(price_of(products[c]) for c in codes)
    return {"rows": rows, "peers": len(peers), "scope": scope,
            "low": peers[0] if peers else None, "high": peers[-1] if peers else None}


def photo(code, fallback="", alt="", cls="", prefix="../"):
    """WebP at two widths with the JPEG as fallback; one <img> tag if we have no local copy."""
    jpg = DOCS / "img" / f"{code}.jpg"
    alt = html.escape(alt)
    if not jpg.exists():
        return f'<img src="{fallback}" alt="{alt}" class="{cls}" loading="lazy">' if fallback else ""
    small = DOCS / "img" / f"{code}-400.webp"
    large = DOCS / "img" / f"{code}-800.webp"
    if not small.exists():
        return f'<img src="{prefix}img/{code}.jpg" alt="{alt}" class="{cls}" loading="lazy">'
    srcset = f'{prefix}img/{code}-400.webp 400w'
    if large.exists():
        srcset += f', {prefix}img/{code}-800.webp 800w'
    return (f'<picture><source type="image/webp" srcset="{srcset}" sizes="(max-width: 460px) 100vw, 460px">'
            f'<img src="{prefix}img/{code}.jpg" alt="{alt}" class="{cls}" loading="lazy" decoding="async"></picture>')


def alternatives(code, products, family=None, near_price=None, limit=3):
    """In-stock products to offer when this one has no price — closest in price first."""
    pool = [(c, q) for c, q in products.items()
            if c != code and q.get("status") == "ok" and price_of(q)
            and (family is None or family_of(q) == family)]
    if not pool:
        return []
    if near_price:
        pool.sort(key=lambda cq: abs(price_of(cq[1]) - near_price))
    else:
        pool.sort(key=lambda cq: price_of(cq[1]))
        # no reference price: show a cheap, a middle and a dearer option
        if len(pool) > limit:
            step = max(1, len(pool) // limit)
            pool = [pool[min(i * step, len(pool) - 1)] for i in range(limit)]
    return [c for c, _ in pool[:limit]]


def alternatives_html(codes, products, prefix=""):
    cards = []
    for c in codes:
        q = products[c]
        cards.append(
            f'<a class="alt-card" href="{prefix}{c}.html">{photo(c, alt=q["model"])}'
            f'<b>{html.escape(clean_title(q))}</b>'
            f'<span class="n">{fmt(price_of(q))} <small>جنيه</small></span></a>'
        )
    return (f'<section><h2>بدائل متوفرة دلوقتي</h2>'
            f'<div class="lede">أقرب المنتجات المتاحة عندنا</div>'
            f'<div class="alts">{"".join(cards)}</div></section>') if cards else ""


def freshness(when=None):
    """How old the price is, in the words a customer would use."""
    when = when or date.today()
    days = (date.today() - when).days
    if days <= 0:
        return "السعر محدّث النهاردة"
    if days == 1:
        return "السعر محدّث إمبارح"
    if days < 7:
        return f"السعر محدّث من {days} أيام"
    if days < 30:
        weeks = days // 7
        return "السعر محدّث من أسبوع" if weeks == 1 else f"السعر محدّث من {weeks} أسابيع"
    return "السعر محتاج تحديث — اسأل البائع"


def assistant_section(code, product_title):
    """كايرو — the question box that sits under the price."""
    cfg = public_config()
    if not cfg:
        return ""
    e = html.escape
    asks = ["تنفع لأوضتي؟", "فيه أرخص منها؟", "إيه أهم مميزاتها؟", "الضمان كام؟"]
    chips = "".join(f'<button class="ask" type="button">{e(a)}</button>' for a in asks)
    return f"""
  <section id="assistant">
    <div class="kairo">
      <div class="kairo-top">
        <span class="kairo-face">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
            <path d="M12 3v3M7.5 7.5 5.4 5.4M16.5 7.5l2.1-2.1"/><rect x="4" y="8" width="16" height="12" rx="4"/>
            <circle cx="9.5" cy="14" r="1.2" fill="currentColor"/><circle cx="14.5" cy="14" r="1.2" fill="currentColor"/>
          </svg>
        </span>
        <div class="kairo-who">
          <b>اسأل {ASSISTANT_NAME} عن المنتج ده</b>
          <span>مساعد ذكي · بيرد من بيانات المنتج</span>
        </div>
        <button type="button" id="lang" class="lang" aria-label="لغة الصوت" hidden>ع</button>
      </div>
      <div class="kairo-thread" id="thread" hidden></div>
      <div class="kairo-hint" id="hint" hidden></div>
      <div class="kairo-asks" id="asks">{chips}</div>
      <form class="kairo-bar" id="askform" autocomplete="off">
        <button type="button" id="mic" class="mic" aria-label="اسأل بصوتك" hidden>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
            <rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>
          </svg>
        </button>
        <input id="q" type="text" placeholder="اكتب سؤالك هنا…" maxlength="300" enterkeyhint="send">
        <button type="submit" aria-label="ابعت">
          <svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round"><path d="M5 12h13M12 5l7 7-7 7"/></svg>
        </button>
      </form>
    </div>
  </section>

  <script>
  (function () {{
    var CFG = {json.dumps(cfg)};
    var CODE = {json.dumps(code)};
    var session = (crypto.randomUUID && crypto.randomUUID()) || String(Date.now());
    var history = [];
    var thread = document.getElementById('thread');
    var form = document.getElementById('askform');
    var input = document.getElementById('q');
    var asks = document.getElementById('asks');

    function bubble(role, text) {{
      var row = document.createElement('div');
      row.className = 'turn ' + role;
      row.innerHTML = '<div class="b"></div>';
      row.querySelector('.b').textContent = text;
      thread.appendChild(row);
      thread.hidden = false;
      row.scrollIntoView({{ block: 'nearest', behavior: 'smooth' }});
      return row;
    }}

    async function ask(question, spoken) {{
      if (!question) return;
      asks.hidden = true;
      bubble('me', question);
      var waiting = bubble('them', '•••');
      waiting.querySelector('.b').classList.add('dots');
      input.value = '';
      var bubbleText = waiting.querySelector('.b');
      try {{
        // Not streamed: the answer arrives in about a second anyway, and the
        // server cleans it up (greetings, honorifics) before sending it.
        var r = await fetch(CFG.url + '/functions/v1/ask', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json', 'apikey': CFG.key, 'Authorization': 'Bearer ' + CFG.key }},
          body: JSON.stringify({{ code: CODE, question: question, session_id: session, history: history,
                                  voice: !!spoken, language: VOICE.lang.slice(0, 2) }})
        }});
        var data = await r.json();
        bubbleText.classList.remove('dots');
        var answer = data.answer;
        if (!answer) {{
          bubbleText.textContent = VOICE.lang === 'en-US'
            ? 'Sorry, I cannot answer right now. Please ask a salesperson.'
            : 'معلش، مش قادر أرد دلوقتي. اسأل البائع من فضلك.';
          return;
        }}
        bubbleText.textContent = answer;
        history.push({{ role: 'user', text: question }}, {{ role: 'assistant', text: answer }});
        if (data.handover) {{
          var call = document.createElement('div');
          call.className = 'turn them';
          call.innerHTML = '<div class="handover">كلّم البائع</div>';
          thread.appendChild(call);
        }}
      }} catch (err) {{
        bubbleText.classList.remove('dots');
        bubbleText.textContent = VOICE.lang === 'en-US'
          ? 'The network is weak — try again or ask a salesperson.'
          : 'الشبكة ضعيفة دلوقتي — جرّب تاني أو اسأل البائع.';
      }}
    }}

    // Voice: the page records the question and Gemini both hears and answers it.
    // The browser's own recogniser was poor at Egyptian Arabic and, on iPhone,
    // only worked when Dictation happened to be switched on.
    var VOICE = {{ lang: 'ar-EG' }};
    var micBtn = document.getElementById('mic');
    var langBtn = document.getElementById('lang');
    var hint = document.getElementById('hint');
    var TYPE_HINT = 'اكتب سؤالك هنا…';
    var Media = navigator.mediaDevices && navigator.mediaDevices.getUserMedia;
    var Ctx = window.AudioContext || window.webkitAudioContext;

    try {{ VOICE.lang = localStorage.getItem('kairo-voice-lang') || 'ar-EG'; }} catch (e) {{}}

    function say(html) {{ hint.innerHTML = html || ''; hint.hidden = !html; }}
    function paintLang() {{
      langBtn.textContent = VOICE.lang === 'ar-EG' ? 'ع' : 'EN';
      input.placeholder = VOICE.lang === 'ar-EG' ? TYPE_HINT : 'Ask about this product…';
    }}

    if (Media && Ctx) {{
      micBtn.hidden = false;
      langBtn.hidden = false;
      paintLang();

      langBtn.addEventListener('click', function () {{
        VOICE.lang = VOICE.lang === 'ar-EG' ? 'en-US' : 'ar-EG';
        try {{ localStorage.setItem('kairo-voice-lang', VOICE.lang); }} catch (e) {{}}
        paintLang();
      }});

      var stream = null, ctx = null, node = null, source = null;
      var frames = [], recording = false, began = 0, quietFor = 0, heard = false;

      function teardown() {{
        recording = false;
        micBtn.classList.remove('on');
        if (node) {{ try {{ node.disconnect(); }} catch (e) {{}} node.onaudioprocess = null; node = null; }}
        if (source) {{ try {{ source.disconnect(); }} catch (e) {{}} source = null; }}
        if (stream) {{ stream.getTracks().forEach(function (t) {{ t.stop(); }}); stream = null; }}
        if (ctx) {{ try {{ ctx.close(); }} catch (e) {{}} ctx = null; }}
        paintLang();
      }}

      // 16 kHz mono WAV: the one audio format that travels everywhere
      function toWav(buffers, rate) {{
        var total = 0, i, j;
        for (i = 0; i < buffers.length; i++) total += buffers[i].length;
        var all = new Float32Array(total), at = 0;
        for (i = 0; i < buffers.length; i++) {{ all.set(buffers[i], at); at += buffers[i].length; }}
        var ratio = rate / 16000, outLen = Math.floor(all.length / ratio);
        var out = new Float32Array(outLen);
        for (i = 0; i < outLen; i++) {{
          var from = Math.floor(i * ratio), to = Math.min(Math.floor((i + 1) * ratio), all.length);
          var sum = 0, n = 0;
          for (j = from; j < to; j++) {{ sum += all[j]; n++; }}
          out[i] = n ? sum / n : 0;
        }}
        var buf = new ArrayBuffer(44 + out.length * 2), view = new DataView(buf);
        function text(off, s) {{ for (var k = 0; k < s.length; k++) view.setUint8(off + k, s.charCodeAt(k)); }}
        text(0, 'RIFF'); view.setUint32(4, 36 + out.length * 2, true); text(8, 'WAVE');
        text(12, 'fmt '); view.setUint32(16, 16, true); view.setUint16(20, 1, true);
        view.setUint16(22, 1, true); view.setUint32(24, 16000, true);
        view.setUint32(28, 32000, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
        text(36, 'data'); view.setUint32(40, out.length * 2, true);
        var off = 44;
        for (i = 0; i < out.length; i++) {{
          var s = Math.max(-1, Math.min(1, out[i]));
          view.setInt16(off, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
          off += 2;
        }}
        return new Blob([buf], {{ type: 'audio/wav' }});
      }}

      function finish() {{
        if (!recording) return;
        var rate = ctx ? ctx.sampleRate : 44100;
        var grabbed = frames.slice();
        var spoke = heard;
        teardown();
        if (!spoke || !grabbed.length) {{
          say(VOICE.lang === 'ar-EG' ? 'مسمعتش حاجة. دوس على الميكروفون واتكلم.' : 'I did not hear anything. Tap the mic and speak.');
          return;
        }}
        var reader = new FileReader();
        reader.onloadend = function () {{
          var b64 = String(reader.result).split(',')[1];
          askByVoice(b64);
        }};
        reader.readAsDataURL(toWav(grabbed, rate));
      }}

      micBtn.addEventListener('click', function () {{
        if (recording) {{ finish(); return; }}
        say('');
        navigator.mediaDevices.getUserMedia({{ audio: {{ channelCount: 1, echoCancellation: true, noiseSuppression: true }} }})
          .then(function (s) {{
            stream = s;
            ctx = new Ctx();
            if (ctx.resume) ctx.resume();
            source = ctx.createMediaStreamSource(s);
            node = ctx.createScriptProcessor(4096, 1, 1);
            frames = []; recording = true; heard = false; quietFor = 0; began = Date.now();
            micBtn.classList.add('on');
            input.placeholder = VOICE.lang === 'ar-EG' ? 'اتكلم دلوقتي…' : 'Listening…';
            node.onaudioprocess = function (ev) {{
              if (!recording) return;
              var data = ev.inputBuffer.getChannelData(0);
              frames.push(new Float32Array(data));
              var sum = 0;
              for (var i = 0; i < data.length; i++) sum += data[i] * data[i];
              var level = Math.sqrt(sum / data.length);
              if (level > 0.015) {{ heard = true; quietFor = 0; }}
              else if (heard) {{ quietFor += data.length / ctx.sampleRate; }}
              // stop by itself once they finish talking, or after 20 seconds
              if ((heard && quietFor > 1.2) || (Date.now() - began) > 20000) finish();
            }};
            source.connect(node);
            node.connect(ctx.destination);
          }})
          .catch(function () {{
            say(VOICE.lang === 'ar-EG'
              ? 'الميكروفون مقفول للموقع ده. افتح <b>الإعدادات ← Safari ← الميكروفون</b> واسمح، وجرّب تاني.'
              : 'The microphone is blocked for this site. Allow it in <b>Settings → Safari → Microphone</b>.');
          }});
      }});
    }}

    // A recorded question: Gemini hears it and answers it in one go, and tells us
    // what it heard so the customer can see we understood them.
    async function askByVoice(wavBase64) {{
      var mine = bubble('me', VOICE.lang === 'ar-EG' ? '🎤 …' : '🎤 …');
      var waiting = bubble('them', '•••');
      var waitText = waiting.querySelector('.b');
      waitText.classList.add('dots');
      asks.hidden = true;
      try {{
        var r = await fetch(CFG.url + '/functions/v1/ask', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json', 'apikey': CFG.key, 'Authorization': 'Bearer ' + CFG.key }},
          body: JSON.stringify({{ code: CODE, audio: wavBase64, mime: 'audio/wav', voice: true,
                                  session_id: session, history: history, language: VOICE.lang.slice(0, 2) }})
        }});
        var data = await r.json();
        waitText.classList.remove('dots');
        if (!data.answer) {{
          mine.querySelector('.b').textContent = VOICE.lang === 'ar-EG' ? '🎤 صوت' : '🎤 voice';
          waitText.textContent = VOICE.lang === 'ar-EG'
            ? 'معلش، مش قادر أرد دلوقتي. اسأل البائع من فضلك.'
            : 'Sorry, I cannot answer right now. Please ask a salesperson.';
          return;
        }}
        var said = (data.said || '').trim();
        mine.querySelector('.b').textContent = said || (VOICE.lang === 'ar-EG' ? '🎤 صوت' : '🎤 voice');
        waitText.textContent = data.answer;
        history.push({{ role: 'user', text: said || '[صوت]' }}, {{ role: 'assistant', text: data.answer }});
        if (data.handover) {{
          var call = document.createElement('div');
          call.className = 'turn them';
          call.innerHTML = '<div class="handover">كلّم البائع</div>';
          thread.appendChild(call);
        }}
      }} catch (err) {{
        waitText.classList.remove('dots');
        waitText.textContent = VOICE.lang === 'ar-EG'
          ? 'الشبكة ضعيفة دلوقتي — جرّب تاني أو اسأل البائع.'
          : 'The network is weak — try again or ask a salesperson.';
      }}
    }}

    form.addEventListener('submit', function (ev) {{ ev.preventDefault(); ask(input.value.trim()); }});
    Array.prototype.forEach.call(asks.querySelectorAll('.ask'), function (b) {{
      b.addEventListener('click', function () {{ ask(b.textContent); }});
    }});
  }})();
  </script>"""


def rank_label(rank, peers):
    """'الأرخص' / 'تاني أرخص' / 'الأغلى' instead of a bare 5-of-5."""
    ordinals = {2: "تاني", 3: "تالت", 4: "رابع", 5: "خامس"}
    if rank == 1:
        return "الأرخص"
    if rank == peers:
        return "الأغلى"
    if rank in ordinals:
        return f"{ordinals[rank]} أرخص"
    from_top = peers - rank + 1
    if from_top in ordinals:
        return f"{ordinals[from_top]} أغلى"
    return f"رقم {rank} من {peers} من الأرخص للأغلى"


def page_html(code, products):
    p = products[code]
    s = p["site"]
    e = html.escape
    family = family_of(p)
    perf = performance_of(p, family)
    price = price_of(p)
    inst = num(s.get("price_installment")) or price
    # Stock rule: a price means we have it. No price for one run only means we are still
    # checking — a single missing price is as likely to be a website hiccup as a sold-out item.
    in_stock = bool(price)
    missing_runs = int(p.get("price_missing_runs", 0))
    confirming = not in_stock and missing_runs == 1
    last_price = num(p.get("last_price_cash"))
    a = analysis(code, products, family) if in_stock else {}
    size = size_of(p, family)
    specs = s.get("specs", {})
    img = photo(code, fallback=(s.get("images") or [""])[0], alt=p["model"])
    alt_codes = [] if in_stock else alternatives(code, products, family, last_price)

    # ---------- identity chips
    meta = []
    if size:
        meta.append(f'<span>{int(size)} {size_unit(family)}</span>')
    if perf["text"] != "—":
        meta.append(f'<span>{e(perf["text"])}</span>')
    if specs.get("الضمان"):
        meta.append(f'<span>ضمان {e(specs["الضمان"])}</span>')

    # ---------- price block
    if in_stock:
        has_inst = bool(inst and inst != price)
        saving = int(inst - price) if has_inst else 0
        saving_pct = round(saving / inst * 100) if has_inst and inst else 0
        money = f"""
    <div class="price">
      <div class="lbl">السعر كاش</div>
      <div class="big"><span class="n">{fmt(price)}</span> <small>جنيه</small></div>
    </div>
    <div class="fresh"><span class="dot"></span> {freshness()} · السعر في الفرع هو الأساس</div>"""
    elif confirming:
        money = f"""
    <div class="money">
      <div class="soldout checking">
        <b>بنأكد سعر المنتج ده دلوقتي</b>
        <span>{f'آخر سعر سجّلناه <b class="n">{fmt(last_price)}</b> جنيه · ' if last_price else ''}اسأل البائع على السعر النهاردة</span>
      </div>
    </div>"""
    else:
        money = """
    <div class="money">
      <div class="soldout">
        <b>المنتج ده مش متوفر دلوقتي</b>
        <span>اسأل البائع عن وجوده في الفرع أو إمتى هيوصل</span>
      </div>
    </div>"""

    # ---------- quick analysis tiles
    tiles = []
    if a.get("rank"):
        tiles.append(f"""<div class="stat"><div class="k">سعره مقارنة بالمقاس ده</div>
          <div class="v small">{e(rank_label(a['rank'], a['peers']))}</div>
          <div class="note flat">من بين <span class="n">{a['peers']}</span> منتج بنفس المقاس عندنا</div></div>""")
    if a.get("warranty"):
        tiles.append(f"""<div class="stat"><div class="k">الضمان</div>
          <div class="v"><span class="n">{int(a['warranty'])}</span> <small>سنة</small></div>
          <div class="note {'down' if a.get('warranty_best') else 'flat'}">{'الأطول في فئته' if a.get('warranty_best') else 'ضمان الوكيل'}</div></div>""")

    meter = ""
    if a.get("position") is not None and a.get("min") != a.get("max"):
        meter = f"""
      <div class="meter">
        <div class="cap">سعره بين {a['peers']} منتج بنفس المقاس عندنا</div>
        <div class="track">
          <span class="pin" style="inset-inline-start:{max(6, min(94, a['position']))}%"><b class="n">{fmt(price)}</b></span>
        </div>
        <div class="ends"><span>الأرخص <b class="n">{fmt(a['min'])}</b></span><span>الأغلى <b class="n">{fmt(a['max'])}</b></span></div>
      </div>"""

    analysis_inner = f'<div class="stats">{"".join(tiles)}</div>{meter}' if tiles else ""

    # ---------- comparison table
    others = rivals(code, products, family) if in_stock else []
    table_inner = ""
    if others:
        cols = [code] + others
        rows = [("سعر الكاش", [price_of(products[c]) for c in cols], "low"),
                (f"المقاس ({size_unit(family)})", [size_of(products[c], family) for c in cols], None)]
        perfs = [performance_of(products[c], family) for c in cols]
        if any(x["text"] != "—" for x in perfs):
            rows.append((perf["label"], [x["value"] for x in perfs],
                         "high" if any(x["value"] for x in perfs) else None))
        rows.append(("الضمان (سنة)", [warranty_years(products[c]) for c in cols], "high"))

        head = []
        for i, c in enumerate(cols):
            q = products[c]
            nm = e(BRAND_AR.get(q["site"].get("brand", ""), q["site"].get("brand", "")))
            inner = f'{nm}<small class="n">{e(q["model"])}</small>'
            if i:
                inner = f'<a href="{c}.html">{inner}<span class="go">افتح ›</span></a>'
            head.append(f'<th class="{"me" if i == 0 else ""}">'
                        + ('<span class="mytag">ده</span><br>' if i == 0 else "") + inner + '</th>')

        body = []
        for label, values, better in rows:
            numeric = [v for v in values if isinstance(v, (int, float))]
            best = None
            if better and len(set(numeric)) > 1:
                best = min(numeric) if better == "low" else max(numeric)
            cells = []
            for i, v in enumerate(values):
                if label == perf["label"]:
                    cell = e(perfs[i]["text"])
                elif isinstance(v, (int, float)):
                    cell = f'<span class="n">{fmt(v)}</span>'
                else:
                    cell = "—"
                klass = " ".join(x for x in ["me" if i == 0 else "", "best" if best is not None and v == best else ""] if x)
                cells.append(f'<td class="{klass}">{cell}</td>')
            body.append(f"<tr><th>{e(label)}</th>{''.join(cells)}</tr>")

        table_inner = f"""<table class="cmp">
        <thead><tr><th></th>{''.join(head)}</tr></thead>
        <tbody>{''.join(body)}</tbody>
      </table>
      <div class="legend">✓ = الأفضل في السطر ده · دوس على اسم أي منتج تشوف صفحته</div>"""

    # ---------- who it suits
    advice = fit_advice(p, family)
    advice_inner = ""
    # the one line worth showing without a tap
    headline_fit = (f'<div class="fit"><span class="tick">✓</span><p>{advice["text"]}</p></div>'
                    if advice and in_stock else "")
    if advice:
        scale = "".join(
            f'<div class="{"on" if i == advice["pick"] else ""}"><span>{e(a)}</span>{e(b)}</div>'
            for i, (a, b) in enumerate(advice["scale"]))
        advice_inner = f"""<div class="advice">
        <p>{advice['text']}</p>
        <div class="scale">{scale}</div>
        <p style="margin-top:12px;font-size:12px;color:var(--muted)">{e(advice['hint'])}</p>
      </div>"""

    # ---------- what the numbers mean
    explain = EXPLAINERS.get(family, [])
    explain_inner = ""
    if explain:
        items = "".join(f'<div class="q"><b>{e(q)}</b><span>{e(ans)}</span></div>' for q, ans in explain)
        explain_inner = items

    # ---------- answers from this product's own sheet
    qa = faqs(p, family)
    faq_inner = ""
    if qa:
        items = "".join(f'<div class="q"><b>{e(q)}</b><span>{e(ans)}</span></div>' for q, ans in qa)
        faq_inner = items

    points = key_points(p, family)
    points_html = ""
    if points and in_stock:
        items = "".join(f'<div class="pt"><i></i><p>{t}</p></div>' for t in points)
        points_html = f'<section class="points"><h2>ليه المنتج ده</h2>{items}</section>'

    cmp_data = price_compare(code, products, family) if in_stock else None
    compare_html = ""
    if cmp_data:
        me_row = (f'<div class="cmp-row me"><div class="cmp-name">{e(compare_label(p, family))}</div>'
                  f'<div class="cmp-price n">{fmt(price)}</div>'
                  f'<div class="cmp-diff here">ده</div></div>')
        others = "".join(
            f'<a class="cmp-row" href="{r["code"]}.html"><div class="cmp-name">{e(r["title"])}</div>'
            f'<div class="cmp-price n">{fmt(r["price"])}</div>'
            f'<div class="cmp-diff {"up" if r["diff"] > 0 else "down"}">'
            f'{"أغلى" if r["diff"] > 0 else "أرخص"} بـ <span class="n">{fmt(abs(r["diff"]))}</span></div></a>'
            for r in cmp_data["rows"])
        note = (f'عندنا <span class="n">{cmp_data["peers"]}</span> منتج بنفس المقاس، '
                f'أسعارها من <span class="n">{fmt(cmp_data["low"])}</span> لـ '
                f'<span class="n">{fmt(cmp_data["high"])}</span> جنيه'
                if cmp_data["scope"] == "size" and cmp_data["peers"] > 1
                and cmp_data["low"] != cmp_data["high"] else "")
        compare_html = (f'<section class="compare"><h2>السعر جنب الشبيه</h2>'
                        f'<div class="cmp-list">{me_row}{others}</div>'
                        f'{f"<div class=\'cmp-note\'>{note}</div>" if note else ""}</section>')
    about = e(s.get("short_description", "")).strip()
    spec_list = "".join(f'<div class="sp"><div class="k">{e(k)}</div><div class="v">{e(v)}</div></div>'
                        for k, v in spec_rows(p))

    return f"""<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(clean_title(p))} - {STORE['name']}</title>
<link rel="stylesheet" href="../assets/style.css?v={css_version()}">
</head>
<body>
<div class="sheet">

  <div class="band">
    <a class="store" href="../index.html"><img src="../assets/logo.jpeg" alt=""> {STORE['name']}</a>
    <span class="stock{'' if in_stock else (' checking' if confirming else ' out')}">{'● متوفر' if in_stock else ('● بنأكد السعر' if confirming else '● غير متوفر حاليًا')}</span>
  </div>

  {f'<div class="shot">{img}</div>' if img else ''}

  <div class="head">
    <h1>{e(clean_title(p))}</h1>
    <div class="meta">{''.join(meta)}</div>
  </div>

  {money}
  {points_html}
  {compare_html}
  {assistant_section(code, clean_title(p))}
  {alternatives_html(alt_codes, products) if alt_codes else ''}

  <section class="more">
    <details><summary><div><b>المواصفات الكاملة</b><span>كل التفاصيل من كتالوج الشركة</span></div><span class="chev"></span></summary>
      <div class="inner">{f'<p class="about">{about}</p>' if about else ''}<div class="specs">{spec_list}</div></div></details>
  </section>

  <div class="note-foot">
    الأسعار شاملة الضريبة · التوصيل والتركيب مجانًا داخل القاهرة<br>
    آخر تحديث للأسعار <b class="n">{date.today().isoformat()}</b>
  </div>
</div>
</body>
</html>
"""


CATEGORY_LABELS = {"tv": "شاشات", "hood": "شفاطات", "washer": "غسالات", "other": "منتجات تانية"}


def index_html(products, items=None):
    e = html.escape
    items = items or {}
    cards = []

    def card(code, title, price_html, family, extra=""):
        haystack = e(f"{title} {items.get(code, {}).get('model', '')}").lower()
        return (f'<a class="item" data-family="{family}" data-search="{haystack}" href="p/{code}.html">'
                f'{photo(code, alt=title, prefix="")}<b>{e(title)}</b>{price_html}{extra}</a>')

    for code, prod in sorted(products.items(), key=lambda kv: -(price_of(kv[1]) or 0)):
        cards.append(card(code, clean_title(prod),
                          f'<div class="p n">{fmt(price_of(prod))} <small>جنيه</small></div>',
                          family_of(prod)))
    for code, item in sorted(items.items(), key=lambda kv: kv[1]["name"]):
        if code in products:
            continue
        family = {"شاشات": "tv", "شفاط": "hood"}.get(item.get("category", ""), "other")
        cards.append(card(code, item["name"], '<div class="p out">غير متوفر حاليًا</div>', family))

    counts = {}
    for code, prod in products.items():
        counts[family_of(prod)] = counts.get(family_of(prod), 0) + 1
    tabs = ['<button type="button" class="tab on" data-family="all">الكل</button>']
    for family, label in CATEGORY_LABELS.items():
        if counts.get(family):
            tabs.append(f'<button type="button" class="tab" data-family="{family}">{label}'
                        f' <span class="n">{counts[family]}</span></button>')

    return f"""<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>منتجات {STORE['name']}</title>
<link rel="stylesheet" href="assets/style.css?v={css_version()}">
</head>
<body>
<div class="page">
  <div class="top">
    <div class="brandline">
      <img src="assets/logo.jpeg" alt="">
      <div><b>{STORE['name']}</b></div>
    </div>
    <div class="where"><span class="n">{len(items) or len(products)}</span> منتج</div>
  </div>

  <div class="finder">
    <label class="search">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg>
      <input id="q" type="search" placeholder="دوّر باسم المنتج أو الموديل" autocomplete="off">
    </label>
    <div class="tabs">{''.join(tabs)}</div>
  </div>

  <div class="wrap">
    <div class="grid" id="grid">{''.join(cards)}</div>
    <p class="empty" id="empty" hidden>مفيش منتج بالاسم ده — جرّب كلمة تانية</p>
  </div>
</div>

<script>
(function () {{
  var q = document.getElementById('q');
  var grid = document.getElementById('grid');
  var empty = document.getElementById('empty');
  var items = Array.prototype.slice.call(grid.querySelectorAll('.item'));
  var tabs = Array.prototype.slice.call(document.querySelectorAll('.tab'));
  var family = 'all';

  function apply() {{
    var term = (q.value || '').trim().toLowerCase();
    var shown = 0;
    items.forEach(function (el) {{
      var okFamily = family === 'all' || el.dataset.family === family;
      var okTerm = !term || el.dataset.search.indexOf(term) !== -1;
      el.hidden = !(okFamily && okTerm);
      if (!el.hidden) shown++;
    }});
    empty.hidden = shown !== 0;
  }}

  q.addEventListener('input', apply);
  tabs.forEach(function (tab) {{
    tab.addEventListener('click', function () {{
      tabs.forEach(function (t) {{ t.classList.toggle('on', t === tab); }});
      family = tab.dataset.family;
      apply();
    }});
  }});
}})();
</script>
</body>
</html>
"""


def minimal_page(code, item, products=None):
    """Item not on the website: no price, so it counts as out of stock."""
    e = html.escape
    products = products or {}
    name = e(item.get("name") or item.get("store_name") or "")
    family = {"شاشات": "tv", "شفاط": "hood"}.get(item.get("category", ""))
    if family is None and "غسال" in item.get("name", ""):
        family = "washer"
    alts = alternatives_html(alternatives(code, products, family), products)
    return f"""<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{name} - {STORE['name']}</title>
<link rel="stylesheet" href="../assets/style.css?v={css_version()}">
</head>
<body>
<div class="page">
  <div class="top">
    <a class="brandline" href="../index.html">
      <img src="../assets/logo.jpeg" alt="">
      <div><b>{STORE['name']}</b></div>
    </a>
    <div class="where">{e(item.get('category',''))}</div>
  </div>
  <div class="wrap">
    <div class="card">
      <div class="kicker"><span>{e(item.get('category',''))}</span></div>
      <h1>{name}</h1>
      <div class="sub">الموديل <b>{e(item.get('model',''))}</b></div>
      <div class="soldout">
        <b>المنتج ده مش متوفر دلوقتي</b>
        <span>اسأل البائع عن وجوده في الفرع أو إمتى هيوصل</span>
      </div>
    </div>
    {alts}
    <div class="note">آخر تحديث <b class="n">{date.today().isoformat()}</b></div>
  </div>
</div>
</body>
</html>
"""


def main():
    products = json.loads((ROOT / "data" / "products.json").read_text(encoding="utf-8"))
    products = {c: p for c, p in products.items() if p.get("status") == "ok" and p.get("site")}
    with (ROOT / "data" / "items.csv").open(encoding="utf-8-sig", newline="") as f:
        items = {row["code"]: row for row in csv.DictReader(f)}

    (DOCS / "p").mkdir(parents=True, exist_ok=True)
    (DOCS / "assets").mkdir(parents=True, exist_ok=True)
    (DOCS / "assets" / "style.css").write_text(CSS, encoding="utf-8")
    for name, src in (("logo.jpeg", "logo.jpeg"), ("Cairo.ttf", "fonts/Cairo.ttf"), ("Inter.ttf", "fonts/Inter.ttf"),
                      ("NotoNaskhArabic.ttf", "fonts/NotoNaskhArabic.ttf")):
        (DOCS / "assets" / name).write_bytes((ROOT / "assets" / src).read_bytes())
    (DOCS / ".nojekyll").write_text("", encoding="utf-8")

    for code in products:
        (DOCS / "p" / f"{code}.html").write_text(page_html(code, products), encoding="utf-8")
    missing = [c for c in items if c not in products]
    for code in missing:
        (DOCS / "p" / f"{code}.html").write_text(minimal_page(code, items[code], products), encoding="utf-8")
    (DOCS / "index.html").write_text(index_html(products, items), encoding="utf-8")
    print(f"built {len(products) + len(missing)} pages in docs/ "
          f"({len(products)} with website data, {len(missing)} marked out of stock)"
          f"  →  {SITE_BASE}/p/<code>.html")


if __name__ == "__main__":
    main()
