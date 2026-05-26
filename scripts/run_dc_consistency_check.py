"""CLI: D3 1X2↔O/U consistency check (see ml_project/dixon_coles/).

Read-only — scans output/predictions_*.csv, writes a report to
output/dixon_coles/consistency_check.json. Does not touch any
production flow.

Usage:
    PYTHONPATH="$(pwd):$(pwd)/ml_project" python3 scripts/run_dc_consistency_check.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'ml_project'))

from dixon_coles.dc_consistency_check import run_check


def main():
    s = run_check()
    print("=== D3 1X2↔O/U consistency check ===")
    print(f"predictions files scanned : {s['predictions_files']}")
    print(f"matches total             : {s['matches_total']}")
    print()
    if 'joint_residual' in s:
        j = s['joint_residual']
        print("PRIMARY — joint-fit residual (RMS across all 5 markets; can a")
        print("single scoreline distribution reproduce BOTH the 1X2 and O/U heads?):")
        print(f"  mean={j['mean']}  median={j['median']}  p90={j['p90']}  max={j['max']}")
        print(f"  fraction inconsistent: >0.03 RMS → {j['frac_over_0.03']}, "
              f">0.05 RMS → {j['frac_over_0.05']}")
        print("  (residual > ~0.03 = no single distribution fits both heads within ~3pp)")
    if 'oneXtwo_to_ou_gap' in s:
        g = s['oneXtwo_to_ou_gap']
        print()
        print(f"SECONDARY — 1X2→implied-Over gap (caveat: 1X2 weakly identifies "
              f"total goals), n={s['matches_trusted_1x2_fit']}:")
        print(f"  mean={g['mean_abs_gap']}  median={g['median_abs_gap']}  "
              f"p90={g['p90_abs_gap']}  max={g['max_abs_gap']}")
        print(f"  fraction over threshold: {g['frac_over_threshold']}")
    print()
    print("Worst 15 by joint residual:")
    for r in s.get('worst_examples', []):
        print(f"  {r['match']:<38} jointRMS={r['joint_residual']:.3f} "
              f"model_over={r['model_over']:.2f} (best-joint λ "
              f"{r['joint_lam_home']}/{r['joint_lam_away']})")
    print()
    print(f"Report → {s['_report_path']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
