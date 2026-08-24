#!/usr/bin/env python3
"""
Local web interface for the Wallapop deal finder.

Run:  python server.py     then open  http://localhost:8765

Lets you manage the watch-list (add/remove searches) without editing JSON,
trigger a run, and view the latest deals. Stdlib only — no pip install.
The daily cloud run (GitHub Actions) uses the same config.json this edits.
"""

import html
import json
import os
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import wallapop_deals as finder

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
DEALS_PATH = os.path.join(HERE, "deals.json")
PORT = 8765


def load(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Wallapop Deal Watch</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: system-ui, sans-serif; max-width: 820px; margin: 0 auto;
         padding: 24px; line-height: 1.5; }}
  h1 {{ font-size: 1.4rem; }}
  .muted {{ opacity: .65; font-size: .9rem; }}
  form.inline {{ display: inline; }}
  .card {{ border: 1px solid #8884; border-radius: 10px; padding: 14px 16px;
          margin: 10px 0; }}
  .row {{ display: flex; align-items: center; gap: 10px; justify-content: space-between; }}
  input {{ padding: 7px 9px; border-radius: 7px; border: 1px solid #8886;
          background: transparent; color: inherit; font-size: .95rem; }}
  button {{ padding: 7px 12px; border-radius: 7px; border: 0; cursor: pointer;
           font-size: .9rem; background: #2d7; color: #032; font-weight: 600; }}
  button.del {{ background: #e556; color: inherit; font-weight: 400; padding: 4px 9px; }}
  button.run {{ background: #37e; color: #fff; font-size: 1rem; padding: 9px 18px; }}
  .deal {{ border-left: 3px solid #2d7; padding: 8px 12px; margin: 8px 0;
          background: #8881; border-radius: 0 8px 8px 0; }}
  .deal.old {{ border-left-color: #8886; }}
  .price {{ font-weight: 700; font-size: 1.05rem; }}
  .tag {{ font-size: .78rem; opacity: .7; }}
  a {{ color: #4af; }}
  .new-badge {{ background: #2d7; color: #032; border-radius: 5px;
               padding: 1px 6px; font-size: .72rem; font-weight: 700; }}
  .chips {{ display: flex; flex-wrap: wrap; gap: 8px; }}
  .chip {{ display: inline-flex; align-items: center; gap: 6px; background: #8882;
          border-radius: 999px; padding: 4px 6px 4px 12px; font-size: .85rem; }}
  .chip button {{ background: #e556; color: inherit; font-weight: 700;
                 border-radius: 999px; padding: 0 8px; line-height: 1.4; }}
</style></head><body>
<h1>🌱 Wallapop Deal Watch</h1>
<p class="muted">Location: {city} · radius {radius} km · flag if under max price
   or {discount}% below market median</p>

<h2>Watch-list</h2>
{search_rows}

<div class="card">
  <form method="post" action="/add" class="row">
    <input name="keywords" placeholder="search term (e.g. sansevieria)" required style="flex:2">
    <input name="max_price" type="number" step="0.5" placeholder="max €" required style="flex:1">
    <button type="submit">+ Add</button>
  </form>
</div>

<h2>Exclude words</h2>
<p class="muted">Listings whose title contains any of these are dropped
   (accent- and case-insensitive), e.g. “artificial”, “plástico”.</p>
<div class="card">
  <div class="chips">{exclude_chips}</div>
  <form method="post" action="/add_exclude" class="row" style="margin-top:10px">
    <input name="word" placeholder="word to exclude (e.g. plastico)" required style="flex:2">
    <button type="submit">+ Add</button>
  </form>
</div>

<form method="post" action="/run"><button class="run" type="submit">▶ Run now</button></form>
<p class="muted">Last run found {n_deals} deal(s){run_note}. Deals refresh below after a run.</p>

<h2>Latest deals</h2>
{deal_rows}

</body></html>"""


def render(run_note=""):
    cfg = load(CONFIG_PATH, {})
    deals = load(DEALS_PATH, [])

    rows = []
    for i, s in enumerate(cfg.get("searches", [])):
        kw = html.escape(str(s.get("keywords", "")))
        mp = s.get("max_price", "")
        rows.append(
            f'<div class="card"><div class="row"><div><b>{kw}</b> '
            f'<span class="muted">max €{mp}</span></div>'
            f'<form class="inline" method="post" action="/delete">'
            f'<input type="hidden" name="index" value="{i}">'
            f'<button class="del" type="submit">remove</button></form></div></div>'
        )
    search_rows = "\n".join(rows) or '<p class="muted">No searches yet — add one below.</p>'

    drows = []
    for d in deals:
        cls = "deal" if d.get("is_new") else "deal old"
        badge = '<span class="new-badge">NEW</span> ' if d.get("is_new") else ""
        dist = f"{d['distance_km']}km" if d.get("distance_km") is not None else "?"
        title = html.escape(str(d.get("title", "")))
        reasons = html.escape(", ".join(d.get("reasons", [])))
        drows.append(
            f'<div class="{cls}">{badge}<span class="price">€{d["price"]:.0f}</span> '
            f'· {title} <span class="tag">[{html.escape(str(d.get("city")))}, {dist}]</span><br>'
            f'<span class="tag">{reasons}</span> · '
            f'<a href="{html.escape(d["url"])}" target="_blank">view listing ↗</a></div>'
        )
    deal_rows = "\n".join(drows) or '<p class="muted">No deals yet — hit “Run now”.</p>'

    chips = []
    for i, w in enumerate(cfg.get("exclude_keywords", [])):
        chips.append(
            f'<span class="chip">{html.escape(str(w))}'
            f'<form class="inline" method="post" action="/del_exclude">'
            f'<input type="hidden" name="index" value="{i}">'
            f'<button type="submit" title="remove">×</button></form></span>'
        )
    exclude_chips = "".join(chips) or '<span class="muted">None yet.</span>'

    gp = cfg.get("good_price", {})
    return PAGE.format(
        city=html.escape(str(cfg.get("location", {}).get("city", "?"))),
        radius=cfg.get("max_distance_km", "?"),
        discount=int(gp.get("discount_below_median", 0.4) * 100),
        search_rows=search_rows,
        exclude_chips=exclude_chips,
        deal_rows=deal_rows,
        n_deals=len(deals),
        run_note=run_note,
    )


class Handler(BaseHTTPRequestHandler):
    def _send(self, body, status=200):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, note=""):
        self.send_response(303)
        self.send_header("Location", "/" + (("?" + note) if note else ""))
        self.end_headers()

    def _form(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8")
        return {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}

    def do_GET(self):
        if self.path.startswith("/"):
            self._send(render())

    def do_POST(self):
        cfg = load(CONFIG_PATH, {})
        cfg.setdefault("searches", [])
        cfg.setdefault("exclude_keywords", [])
        if self.path == "/add_exclude":
            f = self._form()
            word = f.get("word", "").strip()
            if word and word.lower() not in [w.lower() for w in cfg["exclude_keywords"]]:
                cfg["exclude_keywords"].append(word)
                save_config(cfg)
            return self._redirect()
        if self.path == "/del_exclude":
            f = self._form()
            try:
                cfg["exclude_keywords"].pop(int(f["index"]))
                save_config(cfg)
            except (KeyError, ValueError, IndexError):
                pass
            return self._redirect()
        if self.path == "/add":
            f = self._form()
            try:
                cfg["searches"].append({
                    "keywords": f["keywords"].strip(),
                    "max_price": float(f["max_price"]),
                })
                save_config(cfg)
            except (KeyError, ValueError):
                pass
            return self._redirect()
        if self.path == "/delete":
            f = self._form()
            try:
                cfg["searches"].pop(int(f["index"]))
                save_config(cfg)
            except (KeyError, ValueError, IndexError):
                pass
            return self._redirect()
        if self.path == "/run":
            try:
                finder.main()
            except Exception as e:
                print("run error:", e)
            return self._redirect()
        self._send("not found", 404)

    def log_message(self, *a):
        pass  # quiet


if __name__ == "__main__":
    try:
        import sys
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(f"Wallapop Deal Watch -> http://localhost:{PORT}  (Ctrl+C to stop)")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
