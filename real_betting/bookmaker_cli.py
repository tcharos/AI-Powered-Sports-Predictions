"""Command-line entrypoint for the real-betting bookmaker module.

Usage (either invocation works — `__main__.py` re-exports `main`):
    python -m real_betting --help
    python -m real_betting.bookmaker_cli --help
    python -m real_betting.bookmaker_cli set-credentials <bookmaker>
    python -m real_betting.bookmaker_cli login <bookmaker> [--headless]
    python -m real_betting.bookmaker_cli find-fixtures <bookmaker> [--date YYYY-MM-DD]

Step 1 (module skeleton): every subcommand wires up cleanly but raises
NotImplementedError with a pointer to the relevant NEXT_STEPS.md step.
The point of step 1 is to verify the plumbing, not to do anything yet.
"""

import argparse
import getpass
import sys

from . import config
from .credentials import (
    _backend_warning, delete_credentials, get_credentials, has_credentials,
    mask_username, set_credentials,
)


def _bookmaker_arg(parser):
    parser.add_argument(
        'bookmaker',
        choices=config.BOOKMAKERS,
        help='Which bookmaker to act on.',
    )


def cmd_set_credentials(args):
    """Prompt for username + password, store in the macOS Keychain."""
    warn = _backend_warning()
    if warn:
        print(warn, file=sys.stderr)

    print(f"Storing credentials for '{args.bookmaker}' in the system keyring.")
    print("Password input is hidden; press Enter when done.")
    try:
        username = input('Username: ').strip()
        if not username:
            print("Aborted: username is empty.", file=sys.stderr)
            return 1
        password = getpass.getpass('Password: ')
        if not password:
            print("Aborted: password is empty.", file=sys.stderr)
            return 1
    except (KeyboardInterrupt, EOFError):
        print("\nAborted.", file=sys.stderr)
        return 130

    set_credentials(args.bookmaker, username, password)
    print(f"OK — stored credentials for {mask_username(username)} under "
          f"service '{config.KEYCHAIN_SERVICE_PREFIX}:{args.bookmaker}'.")
    return 0


def cmd_get_credentials(args):
    """Print masked credential info for a bookmaker. Never echoes password."""
    creds = get_credentials(args.bookmaker)
    if not creds:
        print(f"No credentials stored for '{args.bookmaker}'. "
              f"Run: python -m real_betting set-credentials {args.bookmaker}")
        return 1
    print(f"bookmaker: {args.bookmaker}")
    print(f"username:  {mask_username(creds['username'])}")
    print(f"password:  *** (set; not displayed by design)")
    return 0


def cmd_delete_credentials(args):
    """Remove a bookmaker's stored credentials from the keyring."""
    if not has_credentials(args.bookmaker):
        print(f"No credentials stored for '{args.bookmaker}'. Nothing to do.")
        return 0
    confirm = input(f"Delete stored credentials for '{args.bookmaker}'? [y/N] ").strip().lower()
    if confirm != 'y':
        print("Cancelled.")
        return 1
    delete_credentials(args.bookmaker)
    print(f"OK — credentials for '{args.bookmaker}' removed from keyring.")
    return 0


def cmd_login(args):
    """Attempt login (headed by default). Saves screenshot + balance."""
    from .bookmakers import get_bookmaker_class
    from .session import session_lock

    cls = get_bookmaker_class(args.bookmaker)
    try:
        with session_lock():
            bm = cls(headless=args.headless,
                     reuse_session=not args.fresh_session)
            try:
                ok = bm.login()
            finally:
                bm.close()
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0 if ok else 1


def cmd_find_fixtures(args):
    """Scrape today's (or --date's) fixtures from a bookmaker."""
    print(f"[stub] find-fixtures {args.bookmaker} date={args.date}: not implemented yet.")
    print("       See NEXT_STEPS.md → 'Real betting integration' step 6b.")
    return 1


def cmd_dry_run_freiburg_villa(args):
    """ONE-SHOT dry-run: prep a specific Pamestoixima bet to the
    ready-to-place state, then STOP. Cannot place a real bet."""
    from .dryrun_freiburg_villa import cmd_dry_run_freiburg_villa as _impl
    return _impl(args)


def cmd_dry_run_cashout_discovery(args):
    """ONE-SHOT: discover Pamestoixima My Bets / cashout DOM for the
    hardcoded Machida vs Urawa bet. Dumps HTML + screenshots; never
    confirms a cashout."""
    from .dryrun_cashout_discovery import cmd_dry_run_cashout_discovery as _impl
    return _impl(args)


def cmd_dry_run_batch_placement(args):
    """ONE-SHOT: walk the hardcoded BETS list, placing each as its own
    slip. Real-money path gated by EXECUTE_PLACE_BETS in the module."""
    from .dryrun_batch_placement import cmd_dry_run_batch_placement as _impl
    return _impl(args)


def cmd_discover_fixtures(args):
    """Discover Pamestoixima football fixtures (read-only). Writes
    output/real_betting/fixtures_<today>.json. See discover_fixtures.py
    for the lookup helper used by batch placement."""
    from .discover_fixtures import cmd_discover_fixtures as _impl
    return _impl(args)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog='python -m real_betting',
        description='Dormant real-betting integration. Read-only operations '
                    'against a bookmaker. See NEXT_STEPS.md.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest='command', required=True, metavar='<command>')

    sp = sub.add_parser('set-credentials', help='Store username/password in macOS Keychain.')
    _bookmaker_arg(sp)
    sp.set_defaults(func=cmd_set_credentials)

    sp = sub.add_parser('get-credentials', help='Show masked credential info.')
    _bookmaker_arg(sp)
    sp.set_defaults(func=cmd_get_credentials)

    sp = sub.add_parser('delete-credentials', help='Remove stored credentials.')
    _bookmaker_arg(sp)
    sp.set_defaults(func=cmd_delete_credentials)

    sp = sub.add_parser('login', help='Authenticate against a bookmaker.')
    _bookmaker_arg(sp)
    sp.add_argument('--headless', action='store_true',
                    help='Run Chromium headless. Default: headed (see NEXT_STEPS step 8).')
    sp.add_argument('--fresh-session', action='store_true',
                    help='Ignore saved storage state; always start from a blank browser.')
    sp.set_defaults(func=cmd_login)

    sp = sub.add_parser('find-fixtures', help="Scrape today's fixtures.")
    _bookmaker_arg(sp)
    sp.add_argument('--date', default=None,
                    help='Target date YYYY-MM-DD (default: today).')
    sp.set_defaults(func=cmd_find_fixtures)

    # One-shot dry-run for Phase 9 plumbing. Hardcoded match / stake.
    # Cannot place a real bet — code path stops at slip-ready state.
    sp = sub.add_parser(
        'dry-run-freiburg-villa',
        help='ONE-SHOT: prep Freiburg vs Aston Villa O/U Over 2.5 €10 on Pamestoixima. '
             'Stops before "Place bet". Headed mode forced.',
    )
    sp.set_defaults(func=cmd_dry_run_freiburg_villa)

    # One-shot My Bets / cashout DOM discovery. Hardcoded Machida vs Urawa.
    # Clicks Cash Out only to surface the confirm modal, never confirms.
    sp = sub.add_parser(
        'dry-run-cashout-discovery',
        help='ONE-SHOT: discover My Bets + cashout selectors on Pamestoixima '
             'using the Machida vs Urawa bet. Reads only — never confirms cashout.',
    )
    sp.set_defaults(func=cmd_dry_run_cashout_discovery)

    # One-shot batch placement (scenario #5 from test_case_scenarios.md).
    # Each bet committed as its own slip — no multi/parlay. Real-money
    # path gated by EXECUTE_PLACE_BETS in the module.
    sp = sub.add_parser(
        'dry-run-batch-placement',
        help='ONE-SHOT: place hardcoded BETS list on Pamestoixima, one slip '
             'per bet. Default safe (EXECUTE_PLACE_BETS=False); flip in module '
             'to commit real bets. Headed mode forced.',
    )
    sp.set_defaults(func=cmd_dry_run_batch_placement)

    # Real-betting step 6b — Pamestoixima football fixture discovery.
    # Read-only. Output feeds the lookup helper used by batch placement.
    sp = sub.add_parser(
        'discover-fixtures',
        help='Scrape every Pamestoixima football fixture into '
             'output/real_betting/fixtures_<today>.json. Read-only — no clicks '
             'on odds buttons. Headed mode forced.',
    )
    sp.add_argument('--date', default=None,
                    help='Reserved for future use; today only currently.')
    sp.set_defaults(func=cmd_discover_fixtures)

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
