"""Audit of what the predictor is actually fed, fixture by fixture.

For every club fixture predicted since a date, classifies each team's
entity resolution the way predict_matches.MatchPredictor sees it:

  ok            canonical name exists in the corpus, last seen in this league
  other_league  in the corpus but last seen in a different league
                (legitimate after promotion/relegation; suspicious otherwise)
  not_in_corpus resolver returned a name the corpus does not contain
                (e.g. "Nottingham" -> "Nottingham Forest"; corpus says
                "Nott'm Forest") -> ELO None -> 1500, form all zeros
  unresolved    resolver returned None — usually a cached None in
                team_mappings.json, which is never retried -> 1500 + zeros

and, per league: whether the deployed 1X2 model has training rows for it,
how stale the corpus is at the slate date, and whether standings resolved
(the "No Standings Data" log line).

Read-only. Resolver writes are disabled.

Usage: python3 scripts/experiment_serve_inputs_audit.py [--since 2026-08-01]
Writes output/experiments/serve_inputs_audit_<ts>.{json,txt}
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from audit_common import PROJECT_ROOT, _dated_files, write_report  # noqa: E402

sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'ml_project'))
os.chdir(PROJECT_ROOT)
import entity_resolver  # noqa: E402
entity_resolver.EntityResolver.save_mappings = lambda self: None
from entity_resolver import EntityResolver  # noqa: E402
from data_loader import DataLoader  # noqa: E402
from ml_project.calibration.league_aliases import LEAGUE_ALIASES  # noqa: E402

TRAINED = {'B1', 'D1', 'D2', 'E0', 'E1', 'E2', 'E3', 'F1', 'F2', 'G1', 'I1', 'I2',
           'N1', 'P1', 'SC0', 'SC1', 'SC2', 'SC3', 'SP1', 'SP2', 'T1'}


def base_league(name):
    if ':' in name:
        c, rest = name.split(':', 1)
        return f"{c.strip()}: {rest.split(' - ', 1)[0].strip()}"
    return name


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', default='2026-08-01')
    args = ap.parse_args()

    preds = {d: p for d, p in _dated_files('predictions', 'csv').items() if d >= args.since}
    df = pd.concat([pd.read_csv(p).assign(slate=d) for d, p in sorted(preds.items())], ignore_index=True)
    df = df[~df['Adj Logs'].fillna('').str.contains('NT model')].copy()
    df['league_base'] = df['League'].map(base_league)
    df['cat'] = df['league_base'].map(lambda l: LEAGUE_ALIASES.get(l, l))

    hist = DataLoader('data_sets/MatchHistory').load_historical_data()
    long = pd.concat([hist[['date', 'home_team', 'league']].rename(columns={'home_team': 'team'}),
                      hist[['date', 'away_team', 'league']].rename(columns={'away_team': 'team'})])
    long = long.dropna(subset=['date']).sort_values('date')
    last_league = long.groupby('team')['league'].last().astype(str).to_dict()
    # Results only: future fixtures can sit in a file with no score yet.
    played = hist.dropna(subset=['date', 'FTHG'])
    league_dates = {k: np.sort(g['date'].to_numpy()) for k, g in played.groupby(played['league'].astype(str))}

    def newest_before(cat, slate):
        arr = league_dates.get(cat)
        if arr is None:
            return np.nan
        k = np.searchsorted(arr, np.datetime64(pd.Timestamp(slate)))
        return (pd.Timestamp(slate) - pd.Timestamp(arr[k - 1])).days if k else np.nan
    res = EntityResolver()

    def classify(team, cat):
        c = res.get_canonical_name(team)
        if c is None:
            return 'unresolved', None
        if c not in last_league:
            return 'not_in_corpus', c
        if str(cat).startswith('EUROPE') or last_league[c] == str(cat):
            return 'ok', c
        return 'other_league', c

    for side in ('Home', 'Away'):
        r = [classify(t, c) for t, c in zip(df[f'{side} Team'], df['cat'])]
        df[f'{side}_status'] = [x[0] for x in r]
        df[f'{side}_canon'] = [x[1] for x in r]
    bad = {'unresolved', 'not_in_corpus'}
    df['any_bad'] = df['Home_status'].isin(bad) | df['Away_status'].isin(bad)
    df['no_standings'] = df['Adj Logs'].fillna('').str.contains('No Standings Data')
    df['trained'] = df['cat'].isin(TRAINED)
    df['stale_days'] = [newest_before(c, s) for s, c in zip(df['slate'], df['cat'].astype(str))]

    L = []
    P = L.append
    n = len(df)
    P(f'SERVE-INPUT AUDIT — {n} club predictions, {df.slate.nunique()} slates since {args.since}')
    P('')
    P('Fixture-level summary')
    P(f'  league never trained on by the deployed 1X2 model : {np.mean(~df.trained):.1%}')
    P(f'  a team unresolved or not in corpus (1500 ELO, zero form): {df.any_bad.mean():.1%}')
    P(f'  no standings (PPG/att/def neutral, H1-H6 skipped) : {df.no_standings.mean():.1%}')
    tr = df[df.trained]
    P(f'  trained leagues: corpus newest row older than 60 days at slate: {np.mean(tr.stale_days > 60):.1%} '
      f'(median {np.nanmedian(tr.stale_days):.0f} days)')
    clean = df.trained & ~df.any_bad & (df.stale_days <= 14)
    P(f'  => fixtures whose inputs match what the model was trained on: {clean.mean():.1%}')
    P('')
    st = pd.concat([df['Home_status'], df['Away_status']]).value_counts()
    P('Team-resolution status (both sides): ' + ', '.join(f'{k} {v}' for k, v in st.items()))
    P('')
    P(f"{'league':<38}{'n':>5}{'trained':>8}{'stale d':>8}{'bad team':>9}{'no stand':>9}")
    g = df.groupby('league_base').agg(n=('slate', 'size'), trained=('trained', 'first'),
                                       stale=('stale_days', 'median'), bad=('any_bad', 'mean'),
                                       nostand=('no_standings', 'mean')).sort_values('n', ascending=False)
    for lg, r in g.iterrows():
        P(f"{lg[:37]:<38}{r.n:>5}{'yes' if r.trained else 'NO':>8}{r.stale:>8.0f}{r.bad:>9.0%}{r.nostand:>9.0%}")
    P('')
    P('Unresolved / not-in-corpus teams (domestic leagues), most frequent first:')
    rows = []
    for side in ('Home', 'Away'):
        s = df[df[f'{side}_status'].isin(bad) & ~df['league_base'].str.startswith('EUROPE')]
        rows += list(zip(s['league_base'], s[f'{side} Team'], s[f'{side}_status'], s[f'{side}_canon']))
    t = pd.DataFrame(rows, columns=['league', 'team', 'status', 'canon']).value_counts(dropna=False).reset_index(name='n')
    for _, r in t.head(40).iterrows():
        P(f"  {r.n:>3}x  {r.league[:30]:<31} {r.team:<24} {r.status:<14} -> {r.canon}")

    text = '\n'.join(L) + '\n'
    print(text)
    payload = {'n': n, 'untrained_share': float(np.mean(~df.trained)),
               'bad_team_share': float(df.any_bad.mean()),
               'no_standings_share': float(df.no_standings.mean()),
               'clean_share': float(clean.mean()),
               'per_league': g.reset_index().to_dict(orient='records'),
               'bad_teams': t.to_dict(orient='records')}
    base = write_report('serve_inputs_audit', payload, text)
    print(f'wrote {base}.{{json,txt}}')


if __name__ == '__main__':
    main()
