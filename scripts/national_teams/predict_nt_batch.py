"""National-teams / D7 step 4b — batch predictor + router output.

Reads the scraped matches JSON, keeps only national-team competitions (World
Cup, Euro, Nations League — via nt_competitions.is_international), predicts each
with the NT model (predict_nt), and APPENDS rows to the same
output/predictions_<date>.csv the club predictor writes — identical schema, so
/auto_wager + lanes + dashboard consume them with zero changes.

The club predictor skips these matches (predict_matches.py skip-guard), so the
two never double-count. Run AFTER the club prediction step.

Betting columns (1X2 pick, EV, Kelly, O/U) replicate predict_matches.py exactly
so EV-gating behaves identically across club and NT bets.

Usage:
    PYTHONPATH="$(pwd):$(pwd)/ml_project" python3 scripts/national_teams/predict_nt_batch.py
    python3 scripts/national_teams/predict_nt_batch.py --matches output/matches_2026-06-12.json
"""
import argparse
import glob
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))   # predict_nt
sys.path.insert(0, str(ROOT))                               # ml_project.* package
sys.path.insert(0, str(ROOT / "ml_project"))

from predict_nt import load_teams, current_elo, predict  # noqa: E402
from ml_project.national_teams.nt_competitions import (    # noqa: E402
    is_international, is_neutral_competition)

OUT = ROOT / "output"
# Same column order the club predictor writes (predict_matches.py).
COLUMNS = ['Date', 'League', 'Home Team', 'Away Team', 'Home ELO', 'Away ELO',
           'Prediction 1X2', 'Prediction 1X2 Odd', 'Conf 1X2', 'EV 1X2', 'Kelly 1X2',
           'Prediction O/U', 'Prediction O/U Odd', 'Conf O/U', 'EV O/U', 'Kelly O/U',
           'Home Win %', 'Draw %', 'Away Win %', 'Over %', 'Under %',
           'Home Win % (raw)', 'Draw % (raw)', 'Away Win % (raw)',
           'Over % (raw)', 'Under % (raw)',
           'Cal 1X2 Source', 'Cal O/U Source', 'Adj Logs', 'match_id']


def _odd(s):
    try:
        return float(str(s).replace(',', '.')) if s and s != '-' else 0.0
    except ValueError:
        return 0.0


def _kelly(odd, prob):
    """EV and quarter-Kelly — identical to predict_matches.calculate_kelly."""
    if odd <= 1.0 or prob <= 0:
        return 0.0, 0.0
    b = odd - 1.0
    f = max((b * prob - (1 - prob)) / b, 0.0)
    return (prob * odd) - 1.0, f * 0.25


def _dates(matches):
    out = []
    for m in matches:
        ms = m.get('start_time', 'Unknown')
        if ms != 'Unknown':
            try:
                out.append(pd.to_datetime(ms.split(' ')[0], dayfirst=True).strftime("%Y-%m-%d"))
            except Exception:
                pass
    return out


def _row(m, r, neutral):
    """Build one predictions-CSV row from an NT prediction `r`."""
    b_h, b_d, b_a = (_odd(m.get('interaction_1x2_1')), _odd(m.get('interaction_1x2_X')),
                     _odd(m.get('interaction_1x2_2')))
    ov, un = _odd(m.get('over_2_5')), _odd(m.get('under_2_5'))
    p_h, p_d, p_a = r['P_home'], r['P_draw'], r['P_away']
    p_o, p_u = r['P_over25'], r['P_under25']

    # 1X2 pick — same rule as predict_matches (draw only if clearly highest).
    if p_d > max(p_h, p_a) + 0.05:
        pick, conf, odd = 'X', p_d, b_d
    elif p_h >= p_a:
        pick, conf, odd = '1', p_h, b_h
    else:
        pick, conf, odd = '2', p_a, b_a
    ev1, k1 = _kelly(odd, conf)

    ou_pick = "Over 2.5" if p_o >= p_u else "Under 2.5"
    ou_conf, ou_odd = (p_o, ov) if ou_pick == "Over 2.5" else (p_u, un)
    evo, ko = _kelly(ou_odd, ou_conf)

    ms = m.get('start_time', 'Unknown')
    try:
        # Parse the FULL kickoff datetime (keep the time — surfaced in the bet slip).
        date_str = pd.to_datetime(ms, dayfirst=True).strftime("%Y-%m-%d %H:%M")
    except Exception:
        date_str = ms
    return {
        'Date': date_str, 'League': m.get('league', ''),
        'Home Team': r['home'], 'Away Team': r['away'],
        'Home ELO': int(r['home_elo']), 'Away ELO': int(r['away_elo']),
        'Prediction 1X2': pick, 'Prediction 1X2 Odd': f"{odd:.2f}",
        'Conf 1X2': f"{conf:.2f}", 'EV 1X2': f"{ev1:.2f}", 'Kelly 1X2': f"{k1:.2%}",
        'Prediction O/U': ou_pick, 'Prediction O/U Odd': f"{ou_odd:.2f}",
        'Conf O/U': f"{ou_conf:.2f}", 'EV O/U': f"{evo:.2f}", 'Kelly O/U': f"{ko:.2%}",
        'Home Win %': f"{p_h:.2f}", 'Draw %': f"{p_d:.2f}", 'Away Win %': f"{p_a:.2f}",
        'Over %': f"{p_o:.2f}", 'Under %': f"{p_u:.2f}",
        # NT model has no separate raw/calibrated stage: raw == final.
        'Home Win % (raw)': f"{p_h:.2f}", 'Draw % (raw)': f"{p_d:.2f}",
        'Away Win % (raw)': f"{p_a:.2f}", 'Over % (raw)': f"{p_o:.2f}",
        'Under % (raw)': f"{p_u:.2f}",
        'Cal 1X2 Source': 'nt', 'Cal O/U Source': 'nt',
        'Adj Logs': f"NT model{' (neutral)' if neutral else ''}",
        'match_id': m.get('match_id', ''),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--matches", help="scraped matches JSON (default: latest output/matches_*.json)")
    ap.add_argument("--out", help="predictions CSV to append to (default: predictions_<date>.csv)")
    args = ap.parse_args()

    mpath = args.matches or (sorted(glob.glob(str(OUT / "matches_*.json")))[-1]
                             if glob.glob(str(OUT / "matches_*.json")) else None)
    if not mpath:
        sys.exit("no matches JSON found")
    matches = json.load(open(mpath))
    intl = [m for m in matches if is_international(m.get('league', ''))]
    print(f"{mpath}: {len(matches)} matches, {len(intl)} international")
    if not intl:
        print("no national-team matches to predict.")
        return

    teams = load_teams()
    elo = current_elo()
    df = pd.read_csv(ROOT / "data_sets" / "national_teams" / "international_matches.csv",
                     parse_dates=["date"])

    rows, skipped = [], []
    for m in intl:
        league = m.get('league', '')
        neutral = is_neutral_competition(league)
        try:
            r = predict(m.get('home_team'), m.get('away_team'), neutral,
                        elo=elo, df=df, teams=teams)
        except Exception as e:
            skipped.append(f"{m.get('home_team')} v {m.get('away_team')} ({e})")
            continue
        rows.append(_row(m, r, neutral))

    if skipped:
        print(f"skipped {len(skipped)} (unresolved): " + "; ".join(skipped[:6]))
    if not rows:
        print("no NT rows produced.")
        return

    nt_df = pd.DataFrame(rows)[COLUMNS]
    tgt = (pd.Series(_dates(matches)).mode()[0] if _dates(matches)
           else "unknown_date")
    out_csv = Path(args.out) if args.out else OUT / f"predictions_{tgt}.csv"

    if out_csv.exists():
        club = pd.read_csv(out_csv)
        club = club[~club.get('match_id', pd.Series(dtype=str)).isin(nt_df['match_id'])]
        combined = pd.concat([club, nt_df], ignore_index=True)
    else:
        combined = nt_df
    combined.to_csv(out_csv, index=False)
    print(f"\nappended {len(nt_df)} NT predictions -> {out_csv} "
          f"(total {len(combined)} rows)")
    print(nt_df[['League', 'Home Team', 'Away Team', 'Home Win %', 'Draw %',
                 'Away Win %', 'Over %', 'EV 1X2']].to_string(index=False))


if __name__ == "__main__":
    main()
