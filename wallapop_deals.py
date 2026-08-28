#!/usr/bin/env python3
"""
Wallapop deal finder — MVP.

Searches Wallapop for a list of products (default: houseplants) near a location,
flags "good prices" using two rules, and prints/saves the deals. Dedupes against
a local seen.json so the same listing isn't reported twice.

Uses only the Python standard library (no pip install) so it runs unchanged in
GitHub Actions, like the Amazon deal notifier.

A "good price" listing is one that is NOT reserved and passes at least one of:
  1. price <= the search's configured max_price, OR
  2. price <= median(current results) * (1 - discount_below_median)
     (only when there are >= min_samples_for_median results to compare against).
"""

import json
import math
import os
import smtplib
import statistics
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from email.message import EmailMessage

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
SEEN_PATH = os.path.join(HERE, "seen.json")
RESULTS_PATH = os.path.join(HERE, "deals.json")

API_URL = "https://api.wallapop.com/api/v3/search"
MANAGE_URL = "https://amir-gutterman.github.io/wallapop-deal-notifier/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "X-DeviceOS": "0",
    "Origin": "https://es.wallapop.com",
    "Referer": "https://es.wallapop.com/",
}


def load_json(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def fold(text):
    """Lowercase and strip accents so 'Plástico' matches 'plastico'."""
    text = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def is_excluded(title, exclude_folded):
    t = fold(title)
    return any(kw and kw in t for kw in exclude_folded)


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * 2 * math.asin(math.sqrt(a))


def search(keywords, lat, lon):
    """Return the list of item dicts for a single search term (first page)."""
    params = {
        "source": "search_box",
        "keywords": keywords,
        "latitude": lat,
        "longitude": lon,
        "order_by": "newest",
    }
    url = API_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    try:
        return data["data"]["section"]["payload"]["items"]
    except (KeyError, TypeError):
        return []


def normalize(item, home_lat, home_lon):
    price = (item.get("price") or {}).get("amount")
    loc = item.get("location") or {}
    lat, lon = loc.get("latitude"), loc.get("longitude")
    distance = None
    if lat is not None and lon is not None:
        distance = round(haversine_km(home_lat, home_lon, lat, lon), 1)
    return {
        "id": item.get("id"),
        "title": item.get("title"),
        "price": price,
        "currency": (item.get("price") or {}).get("currency", "EUR"),
        "city": loc.get("city"),
        "distance_km": distance,
        "reserved": bool((item.get("reserved") or {}).get("flag")),
        "shippable": bool((item.get("shipping") or {}).get("item_is_shippable")),
        "url": "https://es.wallapop.com/item/" + (item.get("web_slug") or ""),
        "created_at": item.get("created_at"),
    }


def build_email_html(deals, city):
    rows = []
    for d in deals:
        dist = f"{d['distance_km']} km" if d.get("distance_km") is not None else "?"
        rows.append(
            f'<tr>'
            f'<td style="padding:6px 10px;font-weight:700;white-space:nowrap">'
            f'€{d["price"]:.0f}</td>'
            f'<td style="padding:6px 10px">'
            f'<a href="{d["url"]}" style="color:#1a73e8;text-decoration:none">'
            f'{d["title"]}</a><br>'
            f'<span style="color:#666;font-size:12px">{d["city"]} · {dist} · '
            f'{", ".join(d["reasons"])}</span></td></tr>'
        )
    return (
        f'<div style="font-family:system-ui,Arial,sans-serif;max-width:640px">'
        f'<h2 style="margin:0 0 4px">🌱 {len(deals)} new Wallapop deal(s) in {city}</h2>'
        f'<p style="color:#666;margin:0 0 12px;font-size:13px">'
        f'Under your max price or 40% below market median.</p>'
        f'<table style="border-collapse:collapse;width:100%">{"".join(rows)}</table>'
        f'<p style="margin:18px 0 0">'
        f'<a href="{MANAGE_URL}" style="display:inline-block;background:#0b8f4d;'
        f'color:#fff;text-decoration:none;padding:10px 18px;border-radius:8px;'
        f'font-family:system-ui,Arial,sans-serif;font-weight:600">'
        f'⚙️ Manage watch-list</a></p>'
        f'</div>'
    )


def send_email(deals, city):
    """Send a digest of new deals via Gmail SMTP. Silently skips if unconfigured."""
    user = os.environ.get("GMAIL_USER")
    password = os.environ.get("GMAIL_APP_PASSWORD")
    if not user or not password:
        print("  (email skipped — GMAIL_USER / GMAIL_APP_PASSWORD not set)")
        return False
    # MAIL_TO may be set but empty (unset GitHub secret => ""); fall back to sender.
    recipient = os.environ.get("MAIL_TO") or user

    msg = EmailMessage()
    msg["Subject"] = f"🌱 {len(deals)} new Wallapop deal(s) in {city}"
    msg["From"] = user
    msg["To"] = recipient
    plain = "\n".join(
        f"€{d['price']:.0f}  {d['title']}  [{d['city']}, "
        f"{d['distance_km']}km]  {d['url']}" for d in deals
    )
    msg.set_content((plain or "No new deals.") + f"\n\nManage watch-list: {MANAGE_URL}")
    msg.add_alternative(build_email_html(deals, city), subtype="html")

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(user, password)
        s.send_message(msg)
    print(f"  email sent to {recipient} ({len(deals)} deal(s))")
    return True


def main():
    # Windows terminals default to cp1252; force UTF-8 so € and emoji print.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    cfg = load_json(CONFIG_PATH, {})
    seen = set(load_json(SEEN_PATH, []))
    loc = cfg["location"]
    home_lat, home_lon = loc["latitude"], loc["longitude"]
    max_dist = cfg.get("max_distance_km")
    gp = cfg.get("good_price", {})
    discount = gp.get("discount_below_median", 0.40)
    min_samples = gp.get("min_samples_for_median", 6)
    exclude_folded = [fold(kw) for kw in cfg.get("exclude_keywords", []) if kw.strip()]

    all_deals = []
    new_ids = set()

    for s in cfg["searches"]:
        kw = s["keywords"]
        try:
            items = search(kw, home_lat, home_lon)
        except Exception as e:
            print(f"  ! {kw}: request failed ({e})", file=sys.stderr)
            continue

        market = [normalize(it, home_lat, home_lon) for it in items]
        market = [r for r in market if r["price"] is not None]
        if cfg.get("exclude_reserved", True):
            market = [r for r in market if not r["reserved"]]
        # Drop unwanted listings (e.g. artificial / plastic plants) by title keyword.
        if exclude_folded:
            market = [r for r in market if not is_excluded(r["title"], exclude_folded)]

        # Median reflects the whole market for this term (before distance filter),
        # so relative-bargain detection has enough samples to be meaningful.
        prices = [r["price"] for r in market]
        median = statistics.median(prices) if len(prices) >= min_samples else None
        threshold = median * (1 - discount) if median is not None else None

        # Distance / shipping filters decide which listings we actually report.
        rows = market
        if cfg.get("require_shippable", False):
            rows = [r for r in rows if r["shippable"]]
        if max_dist is not None:
            rows = [r for r in rows if r["distance_km"] is None or r["distance_km"] <= max_dist]

        for r in rows:
            reasons = []
            if s.get("max_price") is not None and r["price"] <= s["max_price"]:
                reasons.append(f"<= max €{s['max_price']}")
            if threshold is not None and r["price"] <= threshold:
                reasons.append(f"{int(discount*100)}% below median €{median:.0f}")
            if not reasons:
                continue
            deal = dict(r, search=kw, reasons=reasons, is_new=r["id"] not in seen)
            all_deals.append(deal)
            new_ids.add(r["id"])

        print(f"  {kw}: {len(items)} listings, median "
              f"{('€%.0f' % median) if median else 'n/a'}, "
              f"{sum(1 for d in all_deals if d['search']==kw)} deal(s)")
        time.sleep(1.0)  # be polite

    all_deals.sort(key=lambda d: (not d["is_new"], d["price"]))
    save_json(RESULTS_PATH, all_deals)
    save_json(SEEN_PATH, sorted(seen | new_ids))

    fresh = [d for d in all_deals if d["is_new"]]
    print("\n" + "=" * 70)
    print(f"{len(all_deals)} deal(s) total, {len(fresh)} NEW since last run")
    print("=" * 70)
    for d in all_deals:
        tag = "🆕" if d["is_new"] else "  "
        dist = f"{d['distance_km']}km" if d["distance_km"] is not None else "?"
        print(f"{tag} €{d['price']:>6.0f}  {d['title'][:42]:<42} "
              f"[{d['city']}, {dist}]  ({', '.join(d['reasons'])})")
        print(f"     {d['url']}")

    city = loc.get("city", "")
    # A dispatch with SEND_TEST_EMAIL=true emails whatever deals exist, so you can
    # verify delivery without waiting for a genuinely new listing.
    if os.environ.get("SEND_TEST_EMAIL", "").lower() == "true":
        send_email(all_deals[:10] or fresh, city)
    elif fresh:
        send_email(fresh, city)
    else:
        print("  no new deals — no email sent")


if __name__ == "__main__":
    main()
