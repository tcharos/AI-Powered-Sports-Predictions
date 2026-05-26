"""D4 probe — READ-ONLY exploration of Flashscore match-detail pages for
injury/availability signal. Touches NO production code, model, or UI.

Two pages per match (user-supplied selectors thesis):
  - LINEUPS page  → a "(players) will not play" section: structured names,
    possibly a reason icon (injury / suspension / doubtful).
  - SUMMARY page  → free-text "most important news" preview: richer, where
    player IMPORTANCE lives, but needs text analysis.

Goal: dump what's actually in the DOM (structured candidates + raw text +
raw HTML snippets) so the InjuryAdjuster + any text parsing is designed
from reality, not assumption. Headless Chromium (Flashscore allows it,
unlike Pamestoixima).

Output → output/d4_probe/<mid>.{json,lineups.html,summary.html}

Usage:
    python3 scripts/d4_injuries/probe_flashscore.py \
        [SUMMARY_URL] [LINEUPS_URL]
    # defaults to the Nice vs St-Etienne example the operator provided.
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

DEFAULT_SUMMARY = ("https://www.flashscore.com/match/football/"
                   "nice-YagoQJpq/st-etienne-YL2QybFe/?mid=O4wBNeOr")
DEFAULT_LINEUPS = ("https://www.flashscore.com/match/football/"
                   "nice-YagoQJpq/st-etienne-YL2QybFe/summary/lineups/?mid=O4wBNeOr")


async def _accept_cookies(page):
    for sel in ("#onetrust-accept-btn-handler",
                'button:has-text("I Accept")',
                'button:has-text("Accept")'):
        try:
            btn = page.locator(sel)
            if await btn.count() > 0:
                await btn.first.click(timeout=3000)
                await page.wait_for_timeout(500)
                return True
        except Exception:
            pass
    return False


# --- JS scanners run in the page context -----------------------------------

# Find the "will not play" / missing-players block: locate the smallest
# element whose text mentions not-playing, walk to a reasonable container,
# and dump its HTML + any name-like descendants + class names (so we can
# spot a reason/injury icon class).
_JS_LINEUPS = r"""
() => {
  const res = {hits: [], class_hints: [], full_text: ''};
  const RE = /will not play|won['’]t play|not play|missing player|injur|suspend|doubtful/i;
  const els = Array.from(document.querySelectorAll('div,section,ul,header,h1,h2,h3,h4,span'));
  // class-name hints anywhere on the page
  const cls = new Set();
  for (const e of els) {
    const c = (e.className && e.className.toString) ? e.className.toString() : '';
    if (/missing|lineup|line-?up|incident|injur|suspend|absent|sidelined/i.test(c)) cls.add(c.trim());
  }
  res.class_hints = Array.from(cls).slice(0, 40);
  // locate label elements, climb to a container, snapshot it
  const labels = els.filter(e => {
    const t = (e.textContent || '').trim();
    return RE.test(t) && t.length < 80;
  });
  const seen = new Set();
  for (const lab of labels.slice(0, 8)) {
    let node = lab;
    for (let i = 0; i < 4 && node.parentElement; i++) node = node.parentElement;
    if (seen.has(node)) continue;
    seen.add(node);
    const names = Array.from(node.querySelectorAll('a,span,div'))
      .map(x => (x.textContent || '').trim())
      .filter(t => t && t.length > 1 && t.length < 40);
    res.hits.push({
      label: (lab.textContent || '').trim(),
      container_class: (node.className || '').toString(),
      container_html: node.outerHTML.slice(0, 4000),
      descendant_texts: Array.from(new Set(names)).slice(0, 40),
    });
  }
  // fallback: whole lineups region text for eyeballing
  const region = document.querySelector('[class*="lineup"], [class*="lineUp"], .lf__lineUp')
                 || document.body;
  res.full_text = (region.innerText || '').slice(0, 6000);
  return res;
}
"""

# Summary page: grab news/preview text blocks (free text we'll later parse).
_JS_SUMMARY = r"""
() => {
  const res = {blocks: [], class_hints: []};
  const els = Array.from(document.querySelectorAll('div,section,article,p,a,h2,h3'));
  const cls = new Set();
  for (const e of els) {
    const c = (e.className && e.className.toString) ? e.className.toString() : '';
    if (/news|preview|article|headline|tldr|summary|matchInfo|highlight/i.test(c)) cls.add(c.trim());
  }
  res.class_hints = Array.from(cls).slice(0, 40);
  // candidate text blocks: elements whose class hints at news/preview and
  // that carry a meaningful chunk of text
  const seen = new Set();
  for (const e of els) {
    const c = (e.className && e.className.toString) ? e.className.toString() : '';
    if (!/news|preview|article|headline|tldr|matchInfo/i.test(c)) continue;
    const t = (e.innerText || '').trim();
    if (t.length < 30 || seen.has(t)) continue;
    seen.add(t);
    res.blocks.push({class: c.trim(), text: t.slice(0, 2500)});
    if (res.blocks.length >= 25) break;
  }
  return res;
}
"""


async def _grab(page, url, js, wait_selectors):
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    await _accept_cookies(page)
    # give the SPA time to hydrate the tab content
    for sel in wait_selectors:
        try:
            await page.wait_for_selector(sel, timeout=8000)
            break
        except Exception:
            continue
    await page.wait_for_timeout(2500)
    html = await page.content()
    data = await page.evaluate(js)
    return data, html


async def main():
    summary_url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SUMMARY
    lineups_url = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_LINEUPS
    m = re.search(r"mid=([A-Za-z0-9]+)", lineups_url) or re.search(r"mid=([A-Za-z0-9]+)", summary_url)
    mid = m.group(1) if m else "unknown"
    os.makedirs(OUT_DIR, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            locale="en-US",
            user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0 Safari/537.36"))
        page = await ctx.new_page()

        print(f"[lineups] {lineups_url}")
        line_data, line_html = await _grab(
            page, lineups_url, _JS_LINEUPS,
            ['[class*="lineUp"]', '[class*="missing"]', '.lf__lineUp'])

        print(f"[summary] {summary_url}")
        summ_data, summ_html = await _grab(
            page, summary_url, _JS_SUMMARY,
            ['[class*="news"]', '[class*="preview"]', '.detailScore__wrapper'])

        await browser.close()

    with open(os.path.join(OUT_DIR, f"{mid}.lineups.html"), "w") as f:
        f.write(line_html)
    with open(os.path.join(OUT_DIR, f"{mid}.summary.html"), "w") as f:
        f.write(summ_html)
    report = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "mid": mid, "summary_url": summary_url, "lineups_url": lineups_url,
        "lineups": line_data, "summary": summ_data,
    }
    path = os.path.join(OUT_DIR, f"{mid}.json")
    with open(path, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    # console digest
    print("\n=== LINEUPS class hints ===")
    for c in line_data.get("class_hints", []):
        print("  ", c)
    print(f"\n=== LINEUPS 'will not play' hits: {len(line_data.get('hits', []))} ===")
    for h in line_data.get("hits", []):
        print(f"  label={h['label']!r} container_class={h['container_class']!r}")
        print(f"    names: {h['descendant_texts']}")
    print("\n=== SUMMARY class hints ===")
    for c in summ_data.get("class_hints", []):
        print("  ", c)
    print(f"\n=== SUMMARY text blocks: {len(summ_data.get('blocks', []))} ===")
    for b in summ_data.get("blocks", [])[:6]:
        print(f"  [{b['class']}]")
        print("   ", b["text"][:400].replace("\n", " | "))
    print(f"\nReport → {path}")
    print(f"Raw HTML → {OUT_DIR}/{mid}.lineups.html , {mid}.summary.html")


if __name__ == "__main__":
    asyncio.run(main())
