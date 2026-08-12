"""Interactive Gmail re-auth when gog refresh tokens are missing or expired."""

from __future__ import annotations

import sys

from email_inbox.accounts import Mailbox
from email_inbox.gog import gmail_auth_records, run_gog_auth_add


def mailboxes_needing_auth(addresses: list[str]) -> list[str]:
    """Configured mailboxes that are missing Gmail auth or have an invalid refresh token."""
    records = gmail_auth_records()
    needing: list[str] = []
    for address in addresses:
        record = records.get(address)
        if record is None or not record.has_gmail or not record.valid:
            needing.append(address)
    return needing


def prompt_and_reauth(address: str) -> bool:
    """
    Ask the user to sign in for one mailbox; run gog auth add on confirmation.

    Returns True when refresh token is valid after the attempt.
    """
    while True:
        try:
            answer = input(
                f"\nGmail sign-in required for {address}\n"
                "  Enter — open Google sign-in\n"
                "  q — skip this account\n"
            ).strip()
        except (EOFError, KeyboardInterrupt):
            print(file=sys.stderr)
            return False

        if answer.lower() in ("q", "quit", "skip", "n", "no"):
            return False

        print(f"Opening Google sign-in for {address}…", file=sys.stderr)
        if not run_gog_auth_add(address):
            print(f"Sign-in failed for {address}. Try again or press q to skip.", file=sys.stderr)
            continue

        record = gmail_auth_records().get(address)
        if record is not None and record.has_gmail and record.valid:
            print(f"Signed in as {address}", file=sys.stderr)
            return True

        print(f"Sign-in did not complete for {address}. Try again or press q to skip.", file=sys.stderr)


def ensure_gmail_auth_for_addresses(addresses: list[str]) -> None:
    """Prompt and re-auth each address that still needs Gmail access."""
    skipped: set[str] = set()
    while True:
        needing = [address for address in mailboxes_needing_auth(addresses) if address not in skipped]
        if not needing:
            return
        address = needing[0]
        if not prompt_and_reauth(address):
            skipped.add(address)


def ensure_gmail_auth(mailboxes: list[Mailbox]) -> None:
    """Prompt and re-auth mailboxes from accounts.md before fetching inbox."""
    ensure_gmail_auth_for_addresses([mailbox.address for mailbox in mailboxes])
