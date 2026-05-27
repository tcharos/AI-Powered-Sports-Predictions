"""National-teams / D7 step 4a — the predictor core.

Given two national teams (+ neutral-venue flag), produce 1X2 + O/U 2.5
probabilities from the trained models (scripts/national_teams/train_nt.py),
using CURRENT ELO (eloratings World.tsv) and each team's recent form
(international_matches.csv). This is the self-contained core; the Flashscore
fixtures + live-odds + EV wiring (step 4b) sits on top.

Usage:
    python3 scripts/national_teams/predict_nt.py "Spain" "Brazil" --neutral
    python3 scripts/national_teams/predict_nt.py "USA" "Mexico"        # host = home
"""
import argparse
import sys
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import xgboost as xgb
from rapidfuzz import fuzz, process

ROOT = Path(__file__).resolve().parent.parent.parent
CSV = ROOT / "data_sets" / "national_teams" / "international_matches.csv"
MODEL_DIR = ROOT / "models" / "national_teams"
RAW = ROOT / "output" / "national_teams" / "raw"
HDR = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                     "AppleWebKit/537.36 Chrome/120.0 Safari/537.36"}
HOME_ADV_ELO, FORM_N = 100, 5
FEATURES = ["elo_diff", "abs_elo_diff", "elo_exp", "is_friendly",
            "home_form_pts", "home_form_gd", "home_form_gf",
            "away_form_pts", "away_form_gd", "away_form_gf"]


def _norm(s):
    return unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower().strip()


_MANUAL_ALIASES = {"usa": "US", "us": "US", "uae": "AE", "korea": "KR",
                   "south korea": "KR", "north korea": "KP", "ivory coast": "CI",
                   "czechia": "CZ", "bosnia": "BA", "drc": "CD"}


def load_teams():
    """Return (code2name canonical, norm2code over ALL aliases)."""
    txt = (RAW / "_teams.tsv").read_text(encoding="utf-8")
    code2name, norm2code = {}, {}
    for line in txt.strip().split("\n"):
        p = line.split("\t")
        if len(p) >= 2:
            code2name[p[0]] = p[1]
            for nm in p[1:]:                       # canonical + all aliases
                norm2code.setdefault(_norm(nm), p[0])
    norm2code.update(_MANUAL_ALIASES)
    return code2name, norm2code


def current_elo():
    """code -> current ELO from eloratings World.tsv (col2=code, col3=elo)."""
    r = requests.get("https://www.eloratings.net/World.tsv", headers=HDR, timeout=20)
    out = {}
    for line in r.content.decode("utf-8", "replace").replace("−", "-").strip().split("\n"):
        c = line.split("\t")
        if len(c) >= 4:
            try:
                out[c[2]] = int(c[3])
            except ValueError:
                pass
    return out


def resolve(name, norm2code, code2name):
    """Input team name -> (code, canonical_name) via alias/fuzzy match."""
    n = _norm(name)
    if n in norm2code:
        code = norm2code[n]
        return code, code2name.get(code, name)
    m = process.extractOne(n, list(norm2code.keys()), scorer=fuzz.WRatio)
    if not m or m[1] < 82:
        return None, None
    code = norm2code[m[0]]
    return code, code2name.get(code, name)


def team_form(df, team):
    """Most-recent-5 form (pts/gd/gf) for a team, from their own perspective."""
    g = df[(df.home_team == team) | (df.away_team == team)].sort_values("date").tail(FORM_N)
    if g.empty:
        return 0.0, 0.0, 0.0
    pts, gd, gf = [], [], []
    for _, r in g.iterrows():
        is_home = r.home_team == team
        f, a = (r.home_score, r.away_score) if is_home else (r.away_score, r.home_score)
        pts.append(3 if f > a else (1 if f == a else 0)); gd.append(f - a); gf.append(f)
    return float(np.mean(pts)), float(np.mean(gd)), float(np.mean(gf))


def _feat_row(home_elo, away_elo, hform, aform, neutral):
    d = home_elo - away_elo
    home_adv = 0 if neutral else HOME_ADV_ELO
    elo_exp = 1.0 / (1.0 + 10 ** (-(d + home_adv) / 400.0))
    return pd.DataFrame([{
        "elo_diff": d, "abs_elo_diff": abs(d), "elo_exp": elo_exp, "is_friendly": 0,
        "home_form_pts": hform[0], "home_form_gd": hform[1], "home_form_gf": hform[2],
        "away_form_pts": aform[0], "away_form_gd": aform[1], "away_form_gf": aform[2],
    }])[FEATURES]


def _run(m1, mo, x):
    p = m1.predict_proba(x)[0]                      # [P_home, P_draw, P_away]
    lam = float(max(mo.predict(x)[0], 1e-3))
    return p, lam


def predict(home, away, neutral=False, elo=None, df=None, teams=None):
    code2name, norm2code = teams or load_teams()
    elo = elo or current_elo()
    df = df if df is not None else pd.read_csv(CSV, parse_dates=["date"])

    hc, hn = resolve(home, norm2code, code2name)
    ac, an = resolve(away, norm2code, code2name)
    if hc is None or ac is None:
        raise ValueError(f"could not resolve: {home if hc is None else away}")
    he, ae = elo.get(hc), elo.get(ac)
    if he is None or ae is None:
        raise ValueError(f"no current ELO for {hn if he is None else an}")

    hform, aform = team_form(df, hn), team_form(df, an)
    m1 = xgb.XGBClassifier(); m1.load_model(MODEL_DIR / "winner.json")
    mo = xgb.XGBRegressor(); mo.load_model(MODEL_DIR / "total.json")

    if neutral:
        # No real home team → predict both orientations and average (the model
        # is trained on genuine home/away, so it carries a slight home-slot tilt
        # that averaging cancels).
        p1, lam1 = _run(m1, mo, _feat_row(he, ae, hform, aform, True))   # home = arg1
        p2, lam2 = _run(m1, mo, _feat_row(ae, he, aform, hform, True))   # home = arg2
        p_home, p_draw, p_away = (p1[0] + p2[2]) / 2, (p1[1] + p2[1]) / 2, (p1[2] + p2[0]) / 2
        lam = (lam1 + lam2) / 2
    else:
        p, lam = _run(m1, mo, _feat_row(he, ae, hform, aform, False))
        p_home, p_draw, p_away = p[0], p[1], p[2]

    p_over = float(1 - np.exp(-lam) * (1 + lam + lam ** 2 / 2))
    return {
        "home": hn, "away": an, "neutral": bool(neutral),
        "home_elo": he, "away_elo": ae,
        "P_home": round(float(p_home), 3), "P_draw": round(float(p_draw), 3),
        "P_away": round(float(p_away), 3),
        "exp_goals": round(lam, 2), "P_over25": round(p_over, 3),
        "P_under25": round(float(1 - p_over), 3),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("home"); ap.add_argument("away")
    ap.add_argument("--neutral", action="store_true")
    a = ap.parse_args()
    r = predict(a.home, a.away, a.neutral)
    print(f"\n{r['home']} (elo {r['home_elo']}) vs {r['away']} (elo {r['away_elo']})"
          f"{'  [neutral]' if r['neutral'] else '  [home adv]'}")
    print(f"  1X2:  Home {r['P_home']:.0%}  Draw {r['P_draw']:.0%}  Away {r['P_away']:.0%}")
    print(f"  O/U:  exp goals {r['exp_goals']}  Over2.5 {r['P_over25']:.0%}  Under2.5 {r['P_under25']:.0%}")


if __name__ == "__main__":
    main()
