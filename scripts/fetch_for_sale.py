#!/usr/bin/env python3
"""
List apartments currently for sale on Myggdalsvägen (Brf Sjötungan) from
Booli and Hemnet, and print them as Markdown.

Booli is queried through its public GraphQL endpoint; Hemnet's search page
embeds the results in __NEXT_DATA__. Hemnet blocks plain HTTP clients, so
both are fetched with curl_cffi impersonating Chrome.

Usage:
    python scripts/fetch_for_sale.py [--out for_sale.md]
"""
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timezone

from curl_cffi import requests

BOOLI_AREA_ID = "93076"      # Myggdalsvägen, Tyresö (areaSuggestionSearch)
HEMNET_LOCATION_ID = "485045"  # Myggdalsvägen, Tyresö (/locations/show)

BOOLI_QUERY = """
query ($areaId: ID!, $page: Int) {
  searchForSale(input: {areaId: $areaId, filters: [], page: $page}) {
    totalCount
    pages
    result {
      ... on Listing {
        booliId url streetAddress published
        listPrice { raw }
        rooms { raw }
        livingArea { formatted }
      }
    }
  }
}
"""


def _booli_post(payload: dict) -> dict:
    # Booli sometimes rejects one browser fingerprint but accepts another,
    # so try a few before giving up.
    errors = []
    for browser in ("chrome", "safari", "firefox", "edge"):
        r = requests.post(
            "https://www.booli.se/graphql",
            json=payload,
            headers={
                "api-client": "booli.se",
                "Origin": "https://www.booli.se",
                "Referer": "https://www.booli.se/sok/till-salu",
            },
            impersonate=browser,
            timeout=30,
        )
        if r.ok:
            return r.json()
        snippet = re.sub(r"\s+", " ", r.text)[:200]
        errors.append(f"{browser}: HTTP {r.status_code} server={r.headers.get('server')} body={snippet!r}")
    raise RuntimeError("Booli blocked all attempts: " + " | ".join(errors))


def fetch_booli() -> list[dict]:
    rows, page, pages = [], 1, 1
    while page <= pages:
        body = _booli_post({"query": BOOLI_QUERY, "variables": {"areaId": BOOLI_AREA_ID, "page": page}})
        if body.get("errors"):
            raise RuntimeError(f"Booli GraphQL error: {body['errors']}")
        res = body["data"]["searchForSale"]
        pages = res.get("pages") or 1
        for x in res["result"]:
            rows.append({
                "address": x["streetAddress"],
                "price": (x.get("listPrice") or {}).get("raw"),
                "rooms": (x.get("rooms") or {}).get("raw"),
                "area": (x.get("livingArea") or {}).get("formatted"),
                "published": (x.get("published") or "")[:10],
                "url": "https://www.booli.se" + x["url"],
            })
        page += 1
    return rows


def fetch_hemnet() -> list[dict]:
    # Hemnet intermittently returns 403 to cloud IPs (e.g. GitHub runners),
    # so retry a few times with a pause.
    for attempt in range(5):
        r = requests.get(
            f"https://www.hemnet.se/bostader?location_ids[]={HEMNET_LOCATION_ID}",
            impersonate=("chrome", "safari", "firefox", "edge", "chrome")[attempt],
            timeout=30,
        )
        if r.status_code != 403:
            break
        time.sleep(30)
    r.raise_for_status()
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.S)
    if not m:
        raise RuntimeError("Hemnet: __NEXT_DATA__ not found (blocked or page changed)")
    state = json.loads(m.group(1))["props"]["pageProps"]["__APOLLO_STATE__"]

    # Only the actual search result cards; the same object also holds
    # "nearby" recommendation cards that are outside Myggdalsvägen.
    search = state["ROOT_QUERY"]["search"]
    results = next(v for k, v in search.items() if k.startswith("listings(") and v.get("cards") is not None)

    rows = []
    for ref in results["cards"]:
        c = state[ref["__ref"]]
        published = c.get("publishedAt")
        rows.append({
            "address": c["streetAddress"],
            "price": c.get("askingPrice"),
            "rooms": c.get("rooms"),
            "area": c.get("livingAndSupplementalAreas"),
            "published": datetime.fromtimestamp(float(published), timezone.utc).strftime("%Y-%m-%d") if published else "",
            "url": f"https://www.hemnet.se/bostad/{c['slug']}",
            "upcoming": c.get("upcoming", False),
        })
    if results.get("total", len(rows)) > len(rows):
        print(f"warning: Hemnet reports {results['total']} listings, parsed {len(rows)}")
    return rows


def to_markdown(source: str, rows: list[dict]) -> str:
    lines = [f"## {source}: {len(rows)} listings", "",
             "| Address | Price | Rooms | Area | Published |", "|---|---|---|---|---|"]
    for x in sorted(rows, key=lambda x: x["published"], reverse=True):
        price = f"{x['price']:,} kr".replace(",", " ") if isinstance(x["price"], int) else (x["price"] or "–")
        if x.get("upcoming"):
            price += " (snart till salu)"
        lines.append(f"| [{x['address']}]({x['url']}) | {price} | {x['rooms'] or '–'} | {x['area'] or '–'} | {x['published']} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", help="also write the Markdown to this file")
    ap.add_argument("--sources", default="booli,hemnet",
                    help="comma-separated: booli,hemnet (Booli is blocked from GitHub runners)")
    args = ap.parse_args()

    fetchers = {"booli": ("Booli", fetch_booli), "hemnet": ("Hemnet", fetch_hemnet)}
    sections = []
    for name, fetch in (fetchers[s.strip()] for s in args.sources.split(",")):
        try:
            sections.append(to_markdown(name, fetch()))
        except Exception as e:  # keep the other source's output if one fails
            sections.append(f"## {name}: FAILED\n\n`{e}`\n")

    md = f"# Till salu på Myggdalsvägen – {datetime.now():%Y-%m-%d}\n\n" + "\n".join(sections)
    print(md)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(md)
    if all("FAILED" in s.splitlines()[0] for s in sections):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
