"""Scoreline math for the Dixon-Coles model (D3).

Pure functions — no I/O, no global state. Given expected goals
(lambda_home, lambda_away) and an optional Dixon-Coles low-score
correction rho, build the joint scoreline probability matrix and derive
the market probabilities (1X2, Over/Under any line, BTTS) from it —
all mutually consistent because they come from ONE distribution.

This is the foundation the eventual DC fitter/predictor builds on, and
it's what the consistency check (dc_consistency_check.py) uses to ask:
"could the production model's separate 1X2 and O/U outputs have come
from a single coherent scoreline distribution?"

References: Dixon & Coles (1997), "Modelling Association Football Scores
and Inefficiencies in the Football Betting Market".
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np


def _poisson_pmf_vec(lam: float, max_goals: int) -> np.ndarray:
    """[P(0; lam), ..., P(max_goals; lam)] for X ~ Poisson(lam)."""
    ks = np.arange(0, max_goals + 1)
    # exp(-lam) * lam^k / k!  — computed in log space for stability.
    log_pmf = -lam + ks * math.log(lam) - np.array([math.lgamma(k + 1) for k in ks])
    return np.exp(log_pmf)


def _dc_tau(i: int, j: int, lam_h: float, lam_a: float, rho: float) -> float:
    """Dixon-Coles low-score dependence correction for the four
    cells (0,0), (1,0), (0,1), (1,1). rho=0 → no correction (plain
    independent bivariate Poisson)."""
    if i == 0 and j == 0:
        return 1.0 - lam_h * lam_a * rho
    if i == 0 and j == 1:
        return 1.0 + lam_h * rho
    if i == 1 and j == 0:
        return 1.0 + lam_a * rho
    if i == 1 and j == 1:
        return 1.0 - rho
    return 1.0


def scoreline_matrix(lam_home: float, lam_away: float,
                     rho: float = 0.0, max_goals: int = 10) -> np.ndarray:
    """Joint P(home_goals=i, away_goals=j) matrix, shape (max_goals+1)^2.

    Independent Poisson marginals with the Dixon-Coles tau correction on
    the four low-score cells. Renormalised so the matrix sums to 1 (the
    tau correction perturbs the total slightly)."""
    lam_home = max(float(lam_home), 1e-6)
    lam_away = max(float(lam_away), 1e-6)
    ph = _poisson_pmf_vec(lam_home, max_goals)
    pa = _poisson_pmf_vec(lam_away, max_goals)
    mat = np.outer(ph, pa)
    if rho != 0.0:
        for (i, j) in ((0, 0), (0, 1), (1, 0), (1, 1)):
            mat[i, j] *= _dc_tau(i, j, lam_home, lam_away, rho)
    total = mat.sum()
    if total > 0:
        mat = mat / total
    return mat


def markets_from_matrix(mat: np.ndarray, ou_line: float = 2.5) -> dict:
    """Derive consistent market probabilities from a scoreline matrix.

    Returns {home, draw, away, over, under, btts} — all from the same
    joint distribution, so they can never contradict each other."""
    n = mat.shape[0]
    idx = np.arange(n)
    home = float(mat[np.greater.outer(idx, idx)].sum())   # i > j
    away = float(mat[np.less.outer(idx, idx)].sum())       # i < j
    draw = float(np.trace(mat))                            # i == j
    # Over/Under on total goals i + j.
    tot = np.add.outer(idx, idx)
    over = float(mat[tot > ou_line].sum())
    under = float(mat[tot < ou_line].sum())  # ou_line is .5 so no ties
    # BTTS (both teams to score): i>=1 and j>=1.
    btts = float(mat[1:, 1:].sum())
    return {'home': home, 'draw': draw, 'away': away,
            'over': over, 'under': under, 'btts': btts}


def markets_from_lambdas(lam_home: float, lam_away: float,
                         rho: float = 0.0, ou_line: float = 2.5,
                         max_goals: int = 10) -> dict:
    """Convenience: scoreline_matrix → markets_from_matrix in one call."""
    return markets_from_matrix(
        scoreline_matrix(lam_home, lam_away, rho=rho, max_goals=max_goals),
        ou_line=ou_line,
    )
