"""Scrape product details from cairosales.com for the items in data/items.csv.

Usage:
  python scrape_site.py                 # every item without scraped data yet
  python scrape_site.py 101062465 ...   # only these item codes
  python scrape_site.py --refresh       # re-download everything (e.g. for new prices)
  python scrape_site.py --reparse       # rebuild products.json from saved pages, no downloads
  python scrape_site.py --prices        # nightly: re-read known product pages only (prices/specs)

For each item: search the site by model, open the matching Arabic product page, and save
title, brand, category, cash + installment prices, stock status, images, description and the spec table.

Outputs:
  data/raw/<code>.html     downloaded product page (so parsing can be redone offline)
  data/products.json       full details per item code (for the product pages / AI agent)
  data/products.csv        one row per item, specs flattened, opens in Excel
  data/items.csv           'url' column filled with the short Arabic product link

The site sits behind Cloudflare, which blocks plain HTTP clients, so pages are loaded
with headless Google Chrome, one at a time, with a pause between requests.
"""
import csv
import difflib
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from bs4 import BeautifulSoup

ROOT = Path(__file__).parent
DATA = ROOT / "data"
RAW = DATA / "raw"
ITEMS = DATA / "items.csv"
PRODUCTS_JSON = DATA / "products.json"
PRODUCTS_CSV = DATA / "products.csv"
CATALOG = DATA / "catalog.json"

SITE = "https://cairosales.com"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/140.0 Safari/537.36")
PAUSE_SECONDS = 3


def fetch(url):
    for attempt in range(1, 4):
        try:
            out = subprocess.run(
                [CHROME, "--headless=new", "--disable-gpu", "--virtual-time-budget=10000",
                 f"--user-agent={USER_AGENT}", "--dump-dom", url],
                capture_output=True, text=True, timeout=60,
            ).stdout
            break
        except subprocess.TimeoutExpired:
            if attempt == 3:
                raise RuntimeError(f"timed out loading {url}")
        finally:
            time.sleep(PAUSE_SECONDS)
    if "Attention Required! | Cloudflare" in out:
        raise RuntimeError("blocked by Cloudflare")
    return out


def norm_model(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def text(el):
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip() if el else ""


def search(model):
    """Return product URLs from the site search results for this model."""
    html = fetch(f"{SITE}/ar/search?controller=search&orderby=position&orderway=desc"
                 f"&search_query={quote(model)}")
    soup = BeautifulSoup(html, "html.parser")
    urls = []
    for a in soup.select("#center_column .product_list a.product-name"):
        url = a.get("href", "").split("?")[0]
        if url and url not in urls:
            urls.append(url)
    return urls


def meta(soup, prop):
    el = soup.find("meta", attrs={"property": prop})
    return el.get("content", "").strip() if el else ""


def parse_product(html):
    soup = BeautifulSoup(html, "html.parser")
    ref = soup.select_one("#product_reference span[content]")
    reference = ref.get("content", "").strip() if ref else ""

    specs = {}
    for row in soup.select("table.table-data-sheet tr"):
        cells = row.find_all("td")
        if len(cells) >= 2:
            key, value = text(cells[0]), text(cells[1])
            if key:
                specs[key] = value

    # The site sells each product as two options (cash deal / installments), each with its own price
    price_cash = price_installment = ""
    for li in soup.select("#combinationswithimages li"):
        label = text(li.select_one("span"))
        amount = re.sub(r"[^\d]", "", text(li.select_one("strong")))
        if "الكاش" in label or "Cash" in label:
            price_cash = amount
        elif "التقسيط" in label:
            price_installment = amount
    if not price_cash:
        price_cash = meta(soup, "product:price:amount").split(".")[0]

    crumbs = [text(a) for a in soup.select(".breadcrumb a")]
    crumbs = [c for c in crumbs if c and c not in (">",)]

    images = []
    for a in soup.select("#thumbs_list a"):
        href = a.get("href", "")
        if href and href not in images:
            images.append(href)
    if not images and meta(soup, "og:image"):
        images.append(meta(soup, "og:image"))

    id_product = meta(soup, "product:retailer_item_id")
    return {
        "id_product": id_product,
        "reference": reference,
        "title": meta(soup, "og:title") or text(soup.select_one("h1")),
        "brand": meta(soup, "product:brand"),
        "category": crumbs[-1] if crumbs else "",
        "breadcrumb": crumbs,
        "price_cash": price_cash,
        "price_installment": price_installment,
        "currency": meta(soup, "product:price:currency"),
        "availability": meta(soup, "product:availability"),
        "short_description": text(soup.select_one("#short_description_content")),
        "specs": specs,
        "images": images,
        "url": meta(soup, "og:url"),
        "short_url": f"{SITE}/ar/?controller=product&id_product={id_product}" if id_product else "",
    }


def load_items():
    with ITEMS.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def save_items(rows):
    with ITEMS.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def load_products():
    return json.loads(PRODUCTS_JSON.read_text(encoding="utf-8")) if PRODUCTS_JSON.exists() else {}


def save_products(products):
    PRODUCTS_JSON.write_text(json.dumps(products, ensure_ascii=False, indent=2), encoding="utf-8")

    spec_keys = []
    for p in products.values():
        for k in (p.get("site") or {}).get("specs", {}):
            if k not in spec_keys:
                spec_keys.append(k)
    base = ["code", "model", "store_name", "status", "title", "brand", "category",
            "price_cash", "price_installment", "availability", "url", "image", "short_description"]
    with PRODUCTS_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(base + spec_keys)
        for code in sorted(products):
            p = products[code]
            s = p.get("site") or {}
            w.writerow([code, p["model"], p["store_name"], p["status"], s.get("title", ""),
                        s.get("brand", ""), s.get("category", ""), s.get("price_cash", ""),
                        s.get("price_installment", ""), s.get("availability", ""), s.get("short_url", ""),
                        (s.get("images") or [""])[0], s.get("short_description", "")]
                       + [s.get("specs", {}).get(k, "") for k in spec_keys])


# Sales codes carry market prefixes/suffixes the website drops:
# Samsung QA65LS03D -> 65LS03D, UA50U8000HUXEG -> 50U8000H
PREFIXES = ("qa", "ua", "qe", "ue")
SUFFIXES = ("haexeg", "aexeg", "huxeg", "uxeg", "xeg", "aruq", "amrg")


def model_variants(model):
    """The same model written the way different systems code it."""
    base = norm_model(model)
    out = {base}
    for v in list(out):
        for suf in SUFFIXES:
            if v.endswith(suf) and len(v) - len(suf) >= 5:
                out.add(v[: -len(suf)])
    for v in list(out):
        for pre in PREFIXES:
            if v.startswith(pre) and len(v) - len(pre) >= 5:
                out.add(v[len(pre):])
    return {v for v in out if v}


def models_match(mine, theirs):
    """Same model allowing for punctuation, market prefixes/suffixes and small spelling gaps."""
    if not norm_model(mine) or not norm_model(theirs):
        return False
    for a in model_variants(mine):
        for b in model_variants(theirs):
            if a == b:
                return True
            if len(min(a, b, key=len)) >= 5 and (a in b or b in a):
                return True
            if len(a) >= 6 and len(b) >= 6 and difflib.SequenceMatcher(None, a, b).ratio() >= 0.88:
                return True
    return False


def model_tokens(model):
    """Distinctive pieces of a model number: digit runs and longer letter runs."""
    low = model.lower()
    return [t for t in re.findall(r"\d{3,}|[a-z]{4,}", low)]


def catalog_urls(model, limit=5):
    """Best candidates from the crawled index, by model appearing in the URL slug."""
    if not CATALOG.exists():
        return []
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    key = norm_model(model)
    if len(key) < 4:
        return []
    tokens = model_tokens(model)
    variants = model_variants(model)
    scored = []
    for url in catalog:
        slug = norm_model(url.rsplit("/", 1)[-1].replace(".html", ""))
        if any(v in slug for v in variants if len(v) >= 5):
            score = 1.0
        elif (tokens and any(t in slug for t in tokens)) or not tokens:
            # model written differently (missing prefix, extra letters): compare the tail
            tail = slug[-(len(key) + 8):]
            score = difflib.SequenceMatcher(None, key, tail).ratio()
            if score < 0.5:
                continue
        else:
            continue
        scored.append((-score, len(slug), url))
    scored.sort()
    return [u for _, _, u in scored[:limit]]


def scrape_item(item, reparse=False):
    code, model = item["code"], item["model"]
    raw = RAW / f"{code}.html"
    record = {"code": code, "model": model, "store_name": item["name"],
              "scraped_at": datetime.now().isoformat(timespec="seconds")}

    if not reparse:
        # the category index is reliable; the site search is the fallback
        urls = catalog_urls(model)
        alt = (item.get("model_alt") or "").strip()
        if alt:
            urls += [u for u in catalog_urls(alt) if u not in urls]
        if not urls:
            urls += [u for u in search(model) if u not in urls]
        if not urls:
            return {**record, "status": "not_found", "site": None}
        match = None
        for url in urls[:5]:
            html = fetch(url)
            site = parse_product(html)
            spellings = [model] + ([alt] if alt else [])
            if any(models_match(m, site["reference"])
                   or models_match(m, site["specs"].get("الموديل", "")) for m in spellings):
                match = html
                break
        if match is None:
            return {**record, "status": "no_exact_match", "site": None, "candidates": urls[:5]}
        RAW.mkdir(parents=True, exist_ok=True)
        raw.write_text(match, encoding="utf-8")

    if not raw.exists():
        return {**record, "status": "not_found", "site": None}
    return {**record, "status": "ok", "site": parse_product(raw.read_text(encoding="utf-8"))}


def refresh_prices(item, products):
    """Re-read a product page we already matched — no search, no catalogue, one request."""
    code = item["code"]
    old = products[code]
    url = (old.get("site") or {}).get("url") or (old.get("site") or {}).get("short_url")
    if not url:
        return old
    html = fetch(url)
    site = parse_product(html)
    old_site = old.get("site") or {}
    now = datetime.now().isoformat(timespec="seconds")

    # Is this a usable page at all? An empty or broken response (network down overnight,
    # Cloudflare, a timeout) must never be read as evidence about the product itself.
    healthy = bool(site.get("title")) and bool(site.get("specs") or site.get("images"))
    if not healthy or not site.get("reference"):
        return {**old, "status": "stale", "checked_at": now}

    # Only once the page is known good can a mismatch mean the product really changed.
    # Our model spelling can differ from the site's (OCR of the scanned stock report), so the
    # strongest check is that the page still shows the same reference it did when we matched it.
    old_ref = old_site.get("reference", "")
    spellings = [item.get("model", ""), item.get("model_alt", ""), old_ref]
    same_product = (
        (old_ref and norm_model(old_ref) == norm_model(site["reference"]))
        or any(models_match(m, site["reference"]) or models_match(m, site["specs"].get("الموديل", ""))
               for m in spellings if m)
    )
    if not same_product:
        # the page really does show a different product: keep the old record for a human to check
        return {**old, "status": "moved", "checked_at": now}

    RAW.mkdir(parents=True, exist_ok=True)
    (RAW / f"{code}.html").write_text(html, encoding="utf-8")

    record = {**old, "status": "ok", "site": site, "scraped_at": now}
    if site.get("price_cash"):
        # remember the last price we saw, so a later disappearance can be explained
        record["last_price_cash"] = site["price_cash"]
        record["last_price_at"] = now
        record["price_missing_runs"] = 0
    else:
        # the page is fine but carries no price: could be sold out, could be a site hiccup.
        # count it — one run is not enough to tell a customer the product is gone.
        record["price_missing_runs"] = int(old.get("price_missing_runs", 0)) + 1
        record["last_price_cash"] = old.get("last_price_cash") or old_site.get("price_cash", "")
        record["last_price_at"] = old.get("last_price_at") or old.get("scraped_at", "")
    return record


def main(args):
    refresh = "--refresh" in args
    reparse = "--reparse" in args
    prices_only = "--prices" in args
    codes = [a for a in args if not a.startswith("--")]

    items = load_items()
    products = load_products()
    if prices_only:
        # nightly job: only items already matched, so the site sees ~1 request per product
        todo = [i for i in items if products.get(i["code"], {}).get("status") == "ok"
                and (not codes or i["code"] in codes)]
    else:
        todo = [i for i in items if (not codes or i["code"] in codes)
                and (refresh or reparse or codes or products.get(i["code"], {}).get("status") != "ok")]
    if reparse:
        todo = [i for i in todo if (RAW / f"{i['code']}.html").exists()]

    for n, item in enumerate(todo, 1):
        try:
            rec = refresh_prices(item, products) if prices_only else scrape_item(item, reparse=reparse)
        except Exception as e:  # keep going; failed items are retried on the next run
            prev = products.get(item["code"])
            if prev and prev.get("site"):
                # a timeout or a blocked request must never cost us a product we already have
                rec = {**prev, "last_error": str(e),
                       "checked_at": datetime.now().isoformat(timespec="seconds")}
            else:
                rec = {"code": item["code"], "model": item["model"], "store_name": item["name"],
                       "status": f"error: {e}", "site": None}
        products[item["code"]] = rec
        site = rec.get("site") or {}
        print(f"[{n}/{len(todo)}] {item['code']} {item['model']}: {rec['status']}"
              + (f" | cash {site.get('price_cash')} / installments {site.get('price_installment')} EGP | {len(site.get('specs', {}))} specs" if site else ""),
              flush=True)
        if site.get("short_url"):
            item["url"] = site["short_url"]
        save_products(products)  # save as we go so an interrupted run loses nothing

    save_items(items)
    ok = sum(1 for p in products.values() if p["status"] == "ok")
    print(f"done: {ok}/{len(products)} items matched on the site")


if __name__ == "__main__":
    main(sys.argv[1:])
