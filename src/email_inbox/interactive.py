"""Interactive pick loop after inbox list."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

from email_inbox.auth_prompt import ensure_gmail_auth_for_addresses
from email_inbox.config import load_config, resolve_auto_refresh_seconds
from email_inbox.editor import EditorConfig
from email_inbox.formatting import InboxRow, render_inbox
from email_inbox.list_inbox import ListResult
from email_inbox.pick_flow import pick_row_prompt
from email_inbox.send import AlreadySentError, is_reply_sent, push_draft, push_send
from email_inbox.textual_picker import run_textual_inbox_session


def should_interact(
    *,
    interactive: bool,
    no_interactive: bool,
    is_json: bool,
    row_count: int,
    use_tui: bool = False,
) -> bool:
    if is_json:
        return False
    if row_count == 0 and not use_tui:
        return False
    if no_interactive:
        return False
    if interactive:
        return True
    return sys.stdin.isatty() and sys.stdout.isatty()


def run_pick_loop(
    vault_root: Path,
    rows: list[InboxRow],
    *,
    editor: EditorConfig | None = None,
    refresh_inbox: Callable[[], ListResult] | None = None,
    output_format: str = "auto",
    use_tui: bool | None = None,
    auto_refresh_seconds: int | None = None,
) -> int:
    """Pick a row via Textual (default on TTY) or typed prompts with --no-tui."""
    if editor is None:
        editor = load_config().editor
    if use_tui is None:
        use_tui = sys.stdin.isatty()
    if auto_refresh_seconds is None:
        auto_refresh_seconds = resolve_auto_refresh_seconds()

    if use_tui and sys.stdin.isatty():
        return run_textual_inbox_session(
            vault_root,
            rows,
            editor=editor,
            refresh_inbox=refresh_inbox,
            auto_refresh_seconds=auto_refresh_seconds,
        )

    current_rows = list(rows)
    can_refresh = refresh_inbox is not None

    while True:
        row_number = _prompt_pick_row(len(current_rows))
        if row_number is None:
            return 0

        path = pick_row_prompt(vault_root, row_number, editor=editor)
        if path is None:
            continue

        while True:
            menu = _after_pick_menu(
                vault_root,
                path,
                len(current_rows),
                can_refresh=can_refresh,
            )
            if menu == "quit":
                return 0
            if menu == "refresh" and refresh_inbox is not None:
                current_rows = _refresh_rows_from_inbox(refresh_inbox)
                render_inbox(current_rows, output_format=output_format)
            break


def _refresh_rows_from_inbox(refresh_inbox: Callable[[], ListResult]) -> list[InboxRow]:
    result = refresh_inbox()
    if result.auth_needed:
        ensure_gmail_auth_for_addresses(result.auth_needed)
        result = refresh_inbox()
    return result.rows


def _prompt_pick_row(row_count: int) -> int | None:
    """Return 1-based row number, or None to quit."""
    while True:
        raw = _prompt(f"Pick row [1-{row_count}] (q to quit): ")
        if _is_quit(raw):
            return None
        try:
            row_number = int(raw)
        except ValueError:
            print("Enter a row number or q.", file=sys.stderr)
            continue
        if row_number < 1 or row_number > row_count:
            print(f"Row must be between 1 and {row_count}.", file=sys.stderr)
            continue
        return row_number


def _after_pick_menu(
    vault_root: Path,
    reply_path: Path,
    row_count: int,
    *,
    can_refresh: bool,
) -> str:
    """Returns quit, continue (back to pick prompt), or refresh (re-fetch and re-print table)."""
    while True:
        sent = is_reply_sent(reply_path)
        raw = _prompt(_next_prompt(row_count, sent=sent, can_refresh=can_refresh))
        lower = raw.lower()

        if _is_quit(raw):
            return "quit"

        if _is_refresh(raw):
            if can_refresh:
                return "refresh"
            continue

        if sent and lower in ("d", "draft", "s", "send"):
            print("Already sent.", file=sys.stderr)
            continue

        if lower in ("d", "draft"):
            try:
                print(push_draft(reply_path))
            except (AlreadySentError, ValueError, RuntimeError) as exc:
                print(str(exc), file=sys.stderr)
            continue

        if lower in ("s", "send"):
            try:
                print(push_send(reply_path))
            except (AlreadySentError, ValueError, RuntimeError) as exc:
                print(str(exc), file=sys.stderr)
            continue

        if lower in ("b", "browse", "back"):
            return "continue"

        try:
            row_number = int(raw)
        except ValueError:
            print(_next_hint(sent=sent, can_refresh=can_refresh), file=sys.stderr)
            continue

        if row_number < 1 or row_number > row_count:
            if row_count == 0:
                print("Inbox empty — r refresh, b browse, or q.", file=sys.stderr)
            else:
                print(f"Row must be between 1 and {row_count}.", file=sys.stderr)
            continue

        new_path = pick_row_prompt(
            vault_root,
            row_number,
            editor=load_config().editor,
        )
        if new_path is None:
            continue
        reply_path = new_path
        nested = _after_pick_menu(
            vault_root,
            reply_path,
            row_count,
            can_refresh=can_refresh,
        )
        if nested in ("quit", "refresh"):
            return nested


def _next_prompt(row_count: int, *, sent: bool, can_refresh: bool) -> str:
    refresh = "[r]efresh, " if can_refresh else ""
    if row_count == 0:
        label = "Next (sent)" if sent else "Next"
        return f"{label}: {refresh}[b]rowse, [q]uit: "
    if sent:
        return f"Next (sent): [b]rowse, {refresh}[q]uit: "
    return f"Next: [d]raft, [s]end, [b]rowse, {refresh}[q]uit: "


def _next_hint(*, sent: bool, can_refresh: bool) -> str:
    parts = []
    if not sent:
        parts.extend(["d", "s"])
    parts.extend(["b", "r" if can_refresh else None, "q"])
    return f"Enter {', '.join(p for p in parts if p)}."


def _is_refresh(raw: str) -> bool:
    return raw.lower() in ("r", "refresh", "list", "l")


def _prompt(message: str) -> str:
    try:
        return input(message).strip()
    except (EOFError, KeyboardInterrupt):
        print(file=sys.stderr)
        raise SystemExit(0) from None


def _is_quit(raw: str) -> bool:
    return raw.lower() in ("q", "quit", "exit", "")
