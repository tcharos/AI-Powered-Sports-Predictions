"""Shared helpers for the 2026-10 pipeline-audit experiments.

Read-only: loads what the deployed pipeline already wrote (predictions,
verification, scraped slates) and scores it. Nothing here writes outside
output/experiments/.

    load_live_settled()  -> one row per settled club prediction, with the raw
                            (pre-heuristic) and final probabilities, outcome,
                            total goals, and the devigged Flashscore price when
                            the slate's pre-match odds survived.
"""
import glob
import json
import os
import re

import numpy as np
import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(PROJECT_ROOT, 'output')
EXP_DIR = os.path.join(OUT_DIR, 'experiments')

_DATE_RE = re.compile(r'_(\d{4}-\d{2}-\d{2})\.(csv|json)$')
EPS = 1e-3


def _dated_files(prefix, ext):
    """{date: path} across output/ and output/history/, output/ winning."""
    found = {}
    for d in (os.path.join(OUT_DIR, 'history'), OUT_DIR):
        for p in glob.glob(os.path.join(d, f'{prefix}_*.{ext}')):
            m = _DATE_RE.search(p)
            if m:
                found[m.group(1)] = p
    return found


CURRENT_SEASON = ('2627', '26-27')
FRESH_DIR = os.path.join(EXP_DIR, '_fresh_history')


def fresh_history_dir():
    """A MatchHistory look-alike that ALSO carries the current season.

    scripts/update_football_data.py is pinned to season 2526, so since the
    2026/27 season began the 22 main leagues have had no current-season rows
    in data_sets/MatchHistory. This builds output/experiments/_fresh_history/:
    a symlink to every existing corpus CSV plus the current season's files,
    downloaded once into output/experiments/_fd2627/. data_sets/ is untouched.
    """
    import io
    import zipfile
    import requests
    sys_path = os.path.join(PROJECT_ROOT, 'scripts')
    import sys
    if sys_path not in sys.path:
        sys.path.insert(0, sys_path)
    from update_football_data import MAIN_MAPPING

    code, suffix = CURRENT_SEASON
    raw = os.path.join(EXP_DIR, '_fd2627')
    if not os.path.isdir(raw) or not glob.glob(os.path.join(raw, '*.csv')):
        os.makedirs(raw, exist_ok=True)
        r = requests.get(f'https://www.football-data.co.uk/mmz4281/{code}/data.zip', timeout=60)
        r.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            for name in z.namelist():
                c = os.path.splitext(os.path.basename(name))[0]
                if name.endswith('.csv') and c in MAIN_MAPPING:
                    with open(os.path.join(raw, f'{c}.csv'), 'wb') as f:
                        f.write(z.read(name))
    os.makedirs(FRESH_DIR, exist_ok=True)
    src = os.path.join(PROJECT_ROOT, 'data_sets', 'MatchHistory')
    for p in glob.glob(os.path.join(src, '*.csv')):
        dst = os.path.join(FRESH_DIR, os.path.basename(p))
        if not os.path.lexists(dst):
            os.symlink(p, dst)
    for p in glob.glob(os.path.join(raw, '*.csv')):
        c = os.path.splitext(os.path.basename(p))[0]
        dst = os.path.join(FRESH_DIR, f'{MAIN_MAPPING[c]}_{suffix}.csv')
        if not os.path.lexists(dst):
            os.symlink(p, dst)
    return FRESH_DIR


def load_slate_odds():
    """{match_id: dict(odds_1, odds_X, odds_2, odds_O, odds_U)} from every
    scraped pre-match slate that still carries odds."""
    out = {}
    for date, path in sorted(_dated_files('matches', 'json').items()):
        try:
            with open(path) as f:
                rows = json.load(f)
        except Exception:
            continue
        for r in rows:
            def num(k):
                v = r.get(k)
                try:
                    v = float(str(v).replace(',', '.'))
                    return v if v > 1.0 else np.nan
                except (TypeError, ValueError):
                    return np.nan
            o = dict(odds_1=num('interaction_1x2_1'), odds_X=num('interaction_1x2_X'),
                     odds_2=num('interaction_1x2_2'), odds_O=num('over_2_5'),
                     odds_U=num('under_2_5'))
            if r.get('match_id') and not np.isnan(o['odds_1']):
                out[r['match_id']] = o
    return out


def load_slates():
    """{date: list-of-scraped-fixtures} for every pre-match slate with odds."""
    out = {}
    for date, path in sorted(_dated_files('matches', 'json').items()):
        try:
            with open(path) as f:
                rows = json.load(f)
        except Exception:
            continue
        if any(r.get('interaction_1x2_1') not in (None, '-') for r in rows):
            out[date] = rows
    return out


def load_live_settled(include_nt=False):
    preds = _dated_files('predictions', 'csv')
    vers = _dated_files('verification', 'csv')
    rows = []
    for date in sorted(set(preds) & set(vers)):
        p = pd.read_csv(preds[date])
        v = pd.read_csv(vers[date])
        if p.empty or v.empty:
            continue
        p['slate'] = date
        v = v.rename(columns={'Home': 'Home Team', 'Away': 'Away Team'})
        v = v[['Home Team', 'Away Team', 'Score']].drop_duplicates(['Home Team', 'Away Team'])
        rows.append(p.merge(v, on=['Home Team', 'Away Team'], how='inner'))
    df = pd.concat(rows, ignore_index=True)

    sc = df['Score'].astype(str).str.extract(r'^\s*(\d+)\s*[-:]\s*(\d+)')
    df['hg'] = pd.to_numeric(sc[0], errors='coerce')
    df['ag'] = pd.to_numeric(sc[1], errors='coerce')
    df = df.dropna(subset=['hg', 'ag']).copy()
    df['y'] = np.select([df.hg > df.ag, df.hg == df.ag], [0, 1], 2)
    df['over'] = ((df.hg + df.ag) > 2.5).astype(int)

    logs = df['Adj Logs'].fillna('')
    df['is_nt'] = logs.str.contains('NT model')
    if not include_nt:
        df = df[~df['is_nt']].copy()

    for c in ['Home Win %', 'Draw %', 'Away Win %', 'Over %', 'Under %',
              'Home Win % (raw)', 'Draw % (raw)', 'Away Win % (raw)',
              'Over % (raw)', 'Under % (raw)']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df = df.dropna(subset=['Home Win % (raw)', 'Home Win %']).copy()

    odds = load_slate_odds()
    o = pd.DataFrame([odds.get(m, {}) for m in df['match_id']], index=df.index)
    for c in ['odds_1', 'odds_X', 'odds_2', 'odds_O', 'odds_U']:
        df[c] = o[c] if c in o else np.nan
    ip = np.column_stack([1 / df.odds_1, 1 / df.odds_X, 1 / df.odds_2])
    df['overround'] = ip.sum(axis=1)
    df[['mkt_H', 'mkt_D', 'mkt_A']] = ip / ip.sum(axis=1, keepdims=True)
    io, iu = 1 / df.odds_O, 1 / df.odds_U
    df['mkt_over'] = io / (io + iu)
    return df.reset_index(drop=True)


# ------------------------------------------------------------------ metrics --

def norm3(p):
    p = np.clip(np.asarray(p, dtype=float), EPS, 1)
    return p / p.sum(axis=1, keepdims=True)


def ll3(p, y):
    p = norm3(p)
    return -np.log(p[np.arange(len(y)), y])


def brier3(p, y):
    p = norm3(p)
    return ((p - np.eye(3)[y]) ** 2).sum(axis=1)


def rps3(p, y):
    p = norm3(p)
    cp = np.cumsum(p, axis=1)[:, :2]
    co = np.cumsum(np.eye(3)[y], axis=1)[:, :2]
    return ((cp - co) ** 2).sum(axis=1) / 2


def ll2(p, y):
    p = np.clip(np.asarray(p, dtype=float), EPS, 1 - EPS)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def brier2(p, y):
    return (np.asarray(p, dtype=float) - y) ** 2


def boot_ci(x, n=4000, seed=0, stat=np.mean):
    """Percentile bootstrap CI of a per-row statistic."""
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    if len(x) < 2:
        return (np.nan, np.nan)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(x), size=(n, len(x)))
    s = stat(x[idx], axis=1) if stat is np.mean else np.array([stat(x[i]) for i in idx])
    return (float(np.percentile(s, 2.5)), float(np.percentile(s, 97.5)))


def summarise_paired(base, alt, label_base, label_alt):
    """Mean of each per-row loss and the paired diff (alt - base) with CI."""
    d = np.asarray(alt) - np.asarray(base)
    lo, hi = boot_ci(d)
    return {'n': int(len(d)), label_base: float(np.mean(base)), label_alt: float(np.mean(alt)),
            'diff': float(np.mean(d)), 'ci': [lo, hi]}


def write_report(name, payload, text):
    os.makedirs(EXP_DIR, exist_ok=True)
    ts = pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')
    base = os.path.join(EXP_DIR, f'{name}_{ts}')
    with open(base + '.json', 'w') as f:
        json.dump(payload, f, indent=2, default=float)
    with open(base + '.txt', 'w') as f:
        f.write(text)
    return base
