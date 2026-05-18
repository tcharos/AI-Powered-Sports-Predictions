"""Command-line entrypoint for the real-betting module.

Usage:
    python -m real_betting.cli --help
    python -m real_betting.cli set-credentials <bookmaker>
    python -m real_betting.cli login <bookmaker> [--headless]
    python -m real_betting.cli find-fixtures <bookmaker> [--date YYYY-MM-DD]

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
        username = input('Username (email): ').strip()
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
              f"Run: python -m real_betting.cli set-credentials {args.bookmaker}")
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
    print(f"[stub] login {args.bookmaker} (headless={args.headless}): not implemented yet.")
    print("       See NEXT_STEPS.md → 'Real betting integration' step 3.")
    return 1


def cmd_find_fixtures(args):
    """Scrape today's (or --date's) fixtures from a bookmaker."""
    print(f"[stub] find-fixtures {args.bookmaker} date={args.date}: not implemented yet.")
    print("       See NEXT_STEPS.md → 'Real betting integration' step 6b.")
    return 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog='python -m real_betting.cli',
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
    sp.set_defaults(func=cmd_login)

    sp = sub.add_parser('find-fixtures', help="Scrape today's fixtures.")
    _bookmaker_arg(sp)
    sp.add_argument('--date', default=None,
                    help='Target date YYYY-MM-DD (default: today).')
    sp.set_defaults(func=cmd_find_fixtures)

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
