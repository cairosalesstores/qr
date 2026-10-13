"""Send the scraped product data to Supabase, so pages and the assistant read live prices.

Usage:
  python push_supabase.py            # push everything we have
  python push_supabase.py --dry-run  # show what would change, send nothing

Reads credentials from .env (never committed). The secret key is used here and only
here: the browser only ever gets the publishable key, which cannot write.

Run after build_pages.py in the nightly job, so the validation gate has already
approved the data before any of it reaches the database.
"""
import json
import sys
from pathlib import Path

import httpx

import build_pages

ROOT = Path(__file__).parent
BATCH = 100


def credentials():
    env_file = ROOT / ".env"
    if not env_file.exists():
        sys.exit("no .env file — copy .env.example to .env and fill in the Supabase keys")
    env = dict(line.split("=", 1) for line in env_file.read_text().splitlines()
               if "=" in line and not line.startswith("#"))
    missing = [k for k in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY") if not env.get(k, "").strip()]
    if missing:
        sys.exit(f"missing in .env: {', '.join(missing)}")
    key = env["SUPABASE_SERVICE_KEY"].strip()
    return env["SUPABASE_URL"].strip(), {
        "apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json",
    }


def row(code, record):
    """One product as the database wants it."""
    site = record["site"]
    family = build_pages.family_of(record)
    specs = site.get("specs", {})
    return {
        "code": code,
        "model": record.get("model", ""),
        "name": record.get("store_name", ""),
        "title": build_pages.clean_title(record),
        "brand": site.get("brand", ""),
        "category": site.get("category", ""),
        "family": family,
        "price_cash": build_pages.price_of(record),
        "price_instalment": build_pages.num(site.get("price_installment")),
        "warranty": specs.get("الضمان", ""),
        "country": specs.get("بلد الصنع", ""),
        "specs": specs,
        "description": site.get("short_description", ""),
        "image_url": (site.get("images") or [""])[0],
        "site_url": site.get("short_url", ""),
        "last_price_cash": build_pages.num(record.get("last_price_cash")),
        "price_missing_runs": int(record.get("price_missing_runs", 0)),
        "updated_at": record.get("scraped_at") or "now()",
    }


def send(client, url, headers, table, rows, on_conflict=None):
    """Upsert in batches; PostgREST takes a list and merges on the primary key."""
    sent = 0
    for i in range(0, len(rows), BATCH):
        chunk = rows[i:i + BATCH]
        params = {"on_conflict": on_conflict} if on_conflict else {}
        r = client.post(f"{url}/rest/v1/{table}", headers={**headers, "Prefer": "resolution=merge-duplicates"},
                        params=params, json=chunk, timeout=120)
        if r.status_code >= 300:
            sys.exit(f"{table}: HTTP {r.status_code} {r.text[:300]}")
        sent += len(chunk)
        print(f"  {table}: {sent}/{len(rows)}", flush=True)
    return sent


def main(dry_run=False):
    url, headers = credentials()
    products = json.loads((ROOT / "data" / "products.json").read_text(encoding="utf-8"))
    usable = {c: p for c, p in products.items() if p.get("status") == "ok" and p.get("site")}
    rows = [row(c, p) for c, p in sorted(usable.items())]
    history = [{"code": r["code"], "price_cash": r["price_cash"],
                "price_instalment": r["price_instalment"]} for r in rows if r["price_cash"] is not None]

    print(f"{len(rows)} products ready · {len(history)} with a price")
    if dry_run:
        print("dry run — nothing sent")
        return

    with httpx.Client() as client:
        send(client, url, headers, "products", rows, on_conflict="code")
        send(client, url, headers, "price_history", history)

    r = httpx.get(f"{url}/rest/v1/products?select=code", headers={**headers, "Prefer": "count=exact",
                  "Range": "0-0"}, timeout=60)
    print("database now holds:", r.headers.get("content-range", "?").split("/")[-1], "products")


if __name__ == "__main__":
    main("--dry-run" in sys.argv)
