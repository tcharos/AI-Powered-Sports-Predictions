"""Dixon-Coles / bivariate-Poisson scoreline model — D3 (FOOTBALL_NEXT_STEPS.md).

ISOLATED, EXPERIMENTAL package. Deliberately self-contained and
DC-namespaced so it can't disturb the production XGBoost flow:

  - Reads, never writes, the existing production artifacts
    (`output/predictions_*.csv`, `data_sets/MatchHistory/`).
  - Its own output dirs: `output/dixon_coles/`, `models/dixon_coles/`.
  - No edits to data_loader / feature_engineering / train_model /
    predict_matches / web_ui — nothing here is imported by them.

Nothing in this package is wired into predictions, the betting flow,
or the UI yet. It's a parallel track to validate (and eventually
build) the Dixon-Coles model without touching anything that works.
"""

# DC-namespaced output locations (created on demand by callers).
DC_OUTPUT_DIR = "output/dixon_coles"
DC_MODELS_DIR = "models/dixon_coles"
