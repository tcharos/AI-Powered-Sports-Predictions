"""D4 probe #2 — READ-ONLY: does a Flashscore player page expose a usable
IMPORTANCE proxy (season appearances / goals / minutes) for an absentee?

The will-not-play list gives a /player/<slug>/<id> link per missing player
but no importance. The preview prose only covers headline/playing names,
NOT the absentees — so importance must come from a structured per-player
stat. This checks whether Flashscore's own player page supplies it (keeping
us single-source, keyed on the player_id we already extract), before we
reach for api-sports.

Output → output/d4_probe/player_<id>.{json,html}

Usage:
    python3 scripts/d4_injuries/probe_player_page.py [/player/slug/id ...]
"""

import asyncio
import json
import os
import re
import sys
import datetime

from playwright.async_api import async_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT_DIR = os.path.join(ROOT, "output", "d4_probe")
BASE = "https://www.flashscore.com"

DEFAULTS = ["/player/wahi-sepe-elye/8jdHWf50", "/player/el-jamali-nadir/QH4ZFeuh"]


async def _accept(page):
    for sel in ("#onetrust-accept-btn-handler", 'button:has-text("Accept")'):
        try:
            b = page.locator(sel)
            if await b.count() > 0:
                await b.first.click(timeout=3000); await page.wait_for_timeout(400); return
        except Exception:
            pass


# Dump: header (name/position/age), any class hints, and any table/row that
# looks like season stats (appearances / goals / minutes), plus a text fallback.
_JS = r"""
() => {
  const res = {header_text: '', class_hints: [], stat_tables: [], full_text: ''};
  const head = document.querySelector('[class*="playerHeader"], [class*="ParticipantHeader"], header');
  res.header_text = head ? (head.innerText||'').slice(0,800) : '';
  const cls = new Set();
  for (const e of document.querySelectorAll('div,section,table,span')) {
    const c = (e.className && e.className.toString)? e.className.toString():'';
    if (/career|statistic|playerStats|matches|appearance|season|position|table/i.test(c)) cls.add(c.trim());
  }
  res.class_hints = Array.from(cls).slice(0,40);
  // any element whose text mentions appearances/goals/minutes near numbers
  const RE = /appearance|matches played|goals|minutes|assist|position/i;
  const cand = Array.from(document.querySelectorAll('div,section,table,ul'))
    .filter(e => { const t=(e.innerText||''); return RE.test(t) && /\d/.test(t) && t.length<1500; });
  const seen = new Set();
  for (const e of cand.slice(0,30)) {
    const t = (e.innerText||'').trim();
    if (seen.has(t)) continue; seen.add(t);
    res.stat_tables.push({class:(e.className||'').toString().slice(0,80), text:t.slice(0,1200)});
    if (res.stat_tables.length>=15) break;
  }
  res.full_text = (document.body.innerText||'').slice(0,4000);
  return res;
}
"""


async def grab(page, path):
    pid = path.rstrip('/').split('/')[-1]
    # try the career/stats tab variants
    for suffix in ('/', ''):
        try:
            await page.goto(BASE + path + suffix, wait_until="domcontentloaded", timeout=60000)
            break
        except Exception:
            continue
    await _accept(page)
    await page.wait_for_timeout(3000)
    data = await page.evaluate(_JS)
    html = await page.content()
    with open(os.path.join(OUT_DIR, f"player_{pid}.html"), "w") as f:
        f.write(html)
    with open(os.path.join(OUT_DIR, f"player_{pid}.json"), "w") as f:
        json.dump({"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                   "path": path, **data}, f, indent=2, ensure_ascii=False)
    return pid, data


async def main():
    paths = sys.argv[1:] or DEFAULTS
    os.makedirs(OUT_DIR, exist_ok=True)
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(locale="en-US",
            user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"))
        page = await ctx.new_page()
        for path in paths:
            pid, data = await grab(page, path)
            print("="*70)
            print(f"PLAYER {path}  (id={pid})")
            print("--- header ---")
            print("  " + (data['header_text'] or '(empty)').replace("\n", " | ")[:400])
            print("--- class hints ---")
            for c in data['class_hints'][:20]: print("  ", c)
            print(f"--- stat-table candidates: {len(data['stat_tables'])} ---")
            for s in data['stat_tables'][:6]:
                print(f"  [{s['class']}]")
                print("    " + s['text'][:500].replace("\n", " | "))
        await b.close()
    print(f"\nDumps → {OUT_DIR}/player_*.json|html")


if __name__ == "__main__":
    asyncio.run(main())
