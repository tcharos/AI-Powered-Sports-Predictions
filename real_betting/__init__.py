"""Real betting integration (DORMANT).

Plumbing for read-only bookmaker scrapes: login → fixture discovery →
predictions↔fixtures matching → odds comparison. **Bet placement,
settlement, and withdrawal are explicitly out of scope.**

Status, checklist, and anti-bot mitigations live in FOOTBALL_NEXT_STEPS.md
under "Real betting integration — Pamestoixima (DORMANT)".

CLI: python -m real_betting --help    (entrypoint: bookmaker_cli.py)
"""
