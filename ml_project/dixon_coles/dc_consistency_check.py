"""D3 go/no-go check: are the production model's 1X2 and O/U outputs
mutually consistent, or do they disagree on the underlying scoreline?

Method, per match in `output/predictions_*.csv`:
  1. Read the model's 1X2 probs (Home/Draw/Away %) and O/U probs.
  2. Fit (lambda_home, lambda_away) of a plain bivariate-Poisson so its
     1X2 marginals best reproduce the model's 1X2 (2-D least-squares).
  3. From those fitted lambdas, compute the Poisson-implied Over 2.5
     probability and compare to the model's actual Over %.
  4. The gap = how far the model's O/U head is from what a single
     coherent scoreline distribution (matched to its OWN 1X2 head)
     would produce. Large, frequent gaps ⇒ the two heads contradict ⇒
     D3's joint model is a concrete win, not just elegance.

Read-only. Writes a report to `output/dixon_coles/` only. Touches no
production code or artifacts beyond reading the predictions CSVs.
"""

from __future__ import annotations

import csv
import datetime
import glob
import json
import os

import numpy as np
from scipy.optimize import minimize

from . import DC_OUTPUT_DIR
from .dc_scoreline import markets_from_lambdas


def _to_float(v):
    try:
        if isinstance(v, str):
            v = v.strip().rstrip('%')
            if not v:
                return None
        return float(v)
    except (TypeError, ValueError):
        return None


def fit_lambdas_to_1x2(p_home: float, p_draw: float, p_away: float) -> tuple:
    """Find (lambda_home, lambda_away) whose plain-Poisson 1X2 marginals
    best match the given (p_home, p_draw, p_away). Returns
    (lam_home, lam_away, fit_residual) — RMS error on the three 1X2
    probs. NOTE: 1X2 weakly identifies the TOTAL goals (the win/draw/
    loss split constrains the lambda *difference* far more than the
    *sum*), so the Over implied from a 1X2-only fit is one of many
    plausible values. The joint fit below is the rigorous metric."""
    target = np.array([p_home, p_draw, p_away], dtype=float)

    def loss(x):
        m = markets_from_lambdas(x[0], x[1], rho=0.0)
        pred = np.array([m['home'], m['draw'], m['away']])
        return float(np.sum((pred - target) ** 2))

    x0 = [1.4 if p_home >= p_away else 1.1,
          1.1 if p_home >= p_away else 1.4]
    res = minimize(loss, x0, method='L-BFGS-B',
                   bounds=[(0.05, 6.0), (0.05, 6.0)])
    return float(res.x[0]), float(res.x[1]), math_sqrt(loss(res.x) / 3.0)


def fit_lambdas_joint(p_home: float, p_draw: float, p_away: float,
                      p_over: float, p_under: float) -> tuple:
    """Find the SINGLE (lambda_home, lambda_away) that best reproduces
    BOTH the 1X2 head AND the O/U head simultaneously. Returns
    (lam_home, lam_away, joint_residual) where joint_residual is the RMS
    error across all 5 marginals.

    This is the rigorous consistency metric: if even the best single
    scoreline distribution can't fit both heads (high residual), no
    coherent goal model produced them → the two heads genuinely
    contradict, and a joint model (D3) reconciles them by construction.
    A low residual means the heads ARE jointly realisable (consistent)."""
    target = np.array([p_home, p_draw, p_away, p_over, p_under], dtype=float)

    def loss(x):
        m = markets_from_lambdas(x[0], x[1], rho=0.0)
        pred = np.array([m['home'], m['draw'], m['away'], m['over'], m['under']])
        return float(np.sum((pred - target) ** 2))

    x0 = [1.4 if p_home >= p_away else 1.1,
          1.1 if p_home >= p_away else 1.4]
    res = minimize(loss, x0, method='L-BFGS-B',
                   bounds=[(0.05, 6.0), (0.05, 6.0)])
    return float(res.x[0]), float(res.x[1]), math_sqrt(loss(res.x) / 5.0)


def math_sqrt(x):
    return float(np.sqrt(max(x, 0.0)))


def run_check(min_fit_quality: float = 0.05,
              gap_thresholds=(0.05, 0.10, 0.15)) -> dict:
    """Scan all predictions CSVs, compute the per-match O/U inconsistency
    gap, aggregate. Returns the summary dict (also written to disk)."""
    files = sorted(
        glob.glob(os.path.join("output", "predictions_*.csv")) +
        glob.glob(os.path.join("output", "history", "predictions_*.csv"))
    )
    rows = []
    for path in files:
        with open(path) as f:
            for r in csv.DictReader(f):
                ph = _to_float(r.get('Home Win %'))
                pd_ = _to_float(r.get('Draw %'))
                pa = _to_float(r.get('Away Win %'))
                over = _to_float(r.get('Over %'))
                under = _to_float(r.get('Under %'))
                if None in (ph, pd_, pa, over, under):
                    continue
                # Skip degenerate rows (probs must roughly sum to 1).
                if not (0.8 < (ph + pd_ + pa) < 1.2):
                    continue
                # Secondary, intuitive metric: fit to 1X2 only, see what
                # Over it implies vs the model's Over.
                lam_h, lam_a, resid = fit_lambdas_to_1x2(ph, pd_, pa)
                poisson_over = markets_from_lambdas(lam_h, lam_a, rho=0.0)['over']
                # Primary, rigorous metric: best single distribution for
                # BOTH heads at once → residual.
                jlh, jla, jresid = fit_lambdas_joint(ph, pd_, pa, over, under)
                rows.append({
                    'file': os.path.basename(path),
                    'match': f"{r.get('Home Team','?')} vs {r.get('Away Team','?')}",
                    'league': r.get('League', ''),
                    'model_over': over,
                    'poisson_over': round(poisson_over, 4),
                    'gap': round(over - poisson_over, 4),
                    'abs_gap': round(abs(over - poisson_over), 4),
                    'fit_residual': round(resid, 4),
                    'joint_residual': round(jresid, 4),
                    'joint_lam_home': round(jlh, 3),
                    'joint_lam_away': round(jla, 3),
                    'lam_home': round(lam_h, 3),
                    'lam_away': round(lam_a, 3),
                })

    summary = {
        'ts': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'predictions_files': len(files),
        'matches_total': len(rows),
        'min_fit_quality': min_fit_quality,
    }

    # PRIMARY metric: joint-fit residual (can a single scoreline
    # distribution reproduce BOTH heads?). Computed on all rows.
    jres = np.array([r['joint_residual'] for r in rows]) if rows else np.array([])
    if len(jres):
        summary['joint_residual'] = {
            'mean': round(float(jres.mean()), 4),
            'median': round(float(np.median(jres)), 4),
            'p90': round(float(np.percentile(jres, 90)), 4),
            'max': round(float(jres.max()), 4),
            # A joint residual > ~0.03 RMS means no single distribution
            # fits both heads within ~3pp — a real inconsistency.
            'frac_over_0.03': round(float((jres > 0.03).mean()), 3),
            'frac_over_0.05': round(float((jres > 0.05).mean()), 3),
        }

    # SECONDARY metric: 1X2-fit → implied-Over gap (intuitive, but
    # caveated by 1X2's weak identification of the total). Only where
    # the 1X2 fit itself was clean.
    trusted = [r for r in rows if r['fit_residual'] <= min_fit_quality]
    abs_gaps = np.array([r['abs_gap'] for r in trusted]) if trusted else np.array([])
    summary['matches_trusted_1x2_fit'] = len(trusted)
    if len(abs_gaps):
        summary['oneXtwo_to_ou_gap'] = {
            'mean_abs_gap': round(float(abs_gaps.mean()), 4),
            'median_abs_gap': round(float(np.median(abs_gaps)), 4),
            'p90_abs_gap': round(float(np.percentile(abs_gaps, 90)), 4),
            'max_abs_gap': round(float(abs_gaps.max()), 4),
            'frac_over_threshold': {
                str(t): round(float((abs_gaps > t).mean()), 3)
                for t in gap_thresholds
            },
        }
    # Worst offenders by joint residual for eyeballing.
    summary['worst_examples'] = sorted(
        rows, key=lambda r: -r['joint_residual'])[:15]

    os.makedirs(DC_OUTPUT_DIR, exist_ok=True)
    out_path = os.path.join(DC_OUTPUT_DIR, 'consistency_check.json')
    with open(out_path, 'w') as f:
        json.dump(summary, f, indent=2)
    summary['_report_path'] = out_path
    return summary
