"""National-teams / D7 step 1 — build the international-match training corpus.

Fetches every national team's match-by-match file from eloratings.net (plain
HTTP, cached), parses the 16-col schema, derives leakage-free PRE-match ELO, and
writes one unified dataset:  data_sets/national_teams/international_matches.csv

Per-country TSV schema (cols, confirmed 2026-05-27):
  0 year  1 month  2 day  3 home_cc  4 away_cc  5 home_score  6 away_score
  7 comp_code  8 venue/host (blank = home team's ground)  9 home_elo_delta
  10 home_elo_POST  11 away_elo_POST  12..15 rank deltas/positions

PRE-match ELO (what a model may use without leakage):
  home_elo_pre = col10 - col9 ;  away_elo_pre = col11 + col9
(col9 is the home team's elo change; Elo exchange is symmetric, so away's change
is -col9. Verified: pre/post chain correctly across consecutive matches.)

Each match appears in BOTH teams' files identically → dedupe on (date,home,away).

Usage:
    python3 scripts/national_teams/build_dataset.py            # full build
    python3 scripts/national_teams/build_dataset.py --since 2006
"""
import argparse
import os
import sys
import time
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent.parent
RAW_DIR = ROOT / "output" / "national_teams" / "raw"
OUT_DIR = ROOT / "data_sets" / "national_teams"
OUT_CSV = OUT_DIR / "international_matches.csv"
BASE = "https://www.eloratings.net"
HDR = {"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")}
_SESSION = requests.Session(); _SESSION.headers.update(HDR)


def fetch(path: str, cache: Path, force=False) -> str | None:
    """GET {BASE}/{path}, cached to `cache`. None on 404/empty."""
    if cache.exists() and not force:
        t = cache.read_text(encoding="utf-8")
        return t or None
    try:
        r = _SESSION.get(f"{BASE}/{path}", timeout=25)
    except Exception as e:
        print(f"  ! fetch error {path}: {e!r}")
        return None
    if r.status_code != 200 or "text/html" in r.headers.get("content-type", ""):
        cache.write_text("", encoding="utf-8")  # negative-cache 404s
        return None
    txt = r.content.decode("utf-8", "replace").replace("−", "-")  # unicode minus
    cache.write_text(txt, encoding="utf-8")
    time.sleep(0.3)  # be polite
    return txt


def load_map(filename: str, cache: Path) -> dict:
    """First col -> first label, from a *.tsv helper file."""
    txt = fetch(filename, cache)
    out = {}
    for line in (txt or "").strip().split("\n"):
        parts = line.split("\t")
        if len(parts) >= 2:
            out[parts[0]] = parts[1]
    return out


def parse_country(text: str, code2name: dict, comp2name: dict) -> list[dict]:
    out = []
    for line in text.strip().split("\n"):
        c = line.split("\t")
        if len(c) < 12:
            continue
        try:
            y, mo, d = int(c[0]), int(c[1]), int(c[2])
            hs, as_ = int(c[5]), int(c[6])
            delta = int(c[9]) if c[9] not in ("", "-") else 0
            home_post, away_post = int(c[10]), int(c[11])
        except (ValueError, IndexError):
            continue
        out.append({
            "date": f"{y:04d}-{mo:02d}-{d:02d}",
            "home_code": c[3], "away_code": c[4],
            "home_team": code2name.get(c[3], c[3]),
            "away_team": code2name.get(c[4], c[4]),
            "home_score": hs, "away_score": as_,
            "comp": c[7], "comp_name": comp2name.get(c[7], c[7]),
            "neutral": 1 if c[8].strip() else 0,
            "home_elo_pre": home_post - delta,   # leakage-free
            "away_elo_pre": away_post + delta,
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", type=int, default=None, help="keep matches on/after this year")
    ap.add_argument("--force", action="store_true", help="ignore cache, re-fetch")
    args = ap.parse_args()

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    code2name = load_map("en.teams.tsv", RAW_DIR / "_teams.tsv")
    comp2name = load_map("en.tournaments.tsv", RAW_DIR / "_tournaments.tsv")
    print(f"teams: {len(code2name)} | tournaments: {len(comp2name)}")

    all_rows, fetched, missing = [], 0, 0
    for code, name in code2name.items():
        safe = name.replace("/", "_")
        txt = fetch(f"{name}.tsv", RAW_DIR / f"{safe}.tsv", force=args.force)
        if not txt:
            missing += 1
            continue
        fetched += 1
        all_rows.extend(parse_country(txt, code2name, comp2name))
    print(f"fetched {fetched} country files ({missing} missing/404); raw rows {len(all_rows)}")

    df = pd.DataFrame(all_rows)
    df = df.drop_duplicates(subset=["date", "home_code", "away_code"]).reset_index(drop=True)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    if args.since:
        df = df[df["date"].dt.year >= args.since].reset_index(drop=True)

    df.to_csv(OUT_CSV, index=False)
    print(f"\n=== {len(df)} unique international matches "
          f"({df['date'].min().date()} → {df['date'].max().date()}) -> {OUT_CSV}")
    print(f"distinct teams: {pd.concat([df['home_team'], df['away_team']]).nunique()}")
    print("\ntop competition codes:")
    for code, n in df["comp"].value_counts().head(15).items():
        print(f"  {code:<5} {comp2name.get(code, '?'):<28} {n}")
    BETTABLE = {"WQ", "EQ", "ENA", "ENB", "ENC", "END", "ENL", "WC", "EC", "CA", "AC", "AFC", "NL"}
    nb = int(df["comp"].isin(BETTABLE).sum())
    print(f"\ncompetitive (bettable scope, incl. finals): {nb} matches")


if __name__ == "__main__":
    main()
