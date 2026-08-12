"""CLI entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from email_inbox import __version__
from email_inbox.accounts import AccountsConfig, load_accounts
from email_inbox.auth_prompt import ensure_gmail_auth
from email_inbox.config import load_config
from email_inbox.formatting import InboxRow, render_inbox
from email_inbox.interactive import run_pick_loop, should_interact
from email_inbox.list_inbox import ListResult, fetch_combined_inbox, list_result_to_json
from email_inbox.editor import EditorConfig, open_reply_file, resolve_editor
from email_inbox.paths import accounts_path, resolve_vault_root, session_path
from email_inbox.pick import AmbiguousProjectError, format_success, pick_and_write
from email_inbox.routing import format_project_menu, project_from_input
from email_inbox.send import AlreadySentError, push_draft, push_send
from email_inbox.session import build_session, write_session


def main(argv: list[str] | None = None) -> None:
    sys.exit(run(argv))


def run(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return int(args.handler(args))


def _add_list_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--newer-than",
        metavar="DURATION",
        help="Gmail query suffix, e.g. 7d",
    )
    parser.add_argument(
        "--max",
        type=int,
        help="Max threads per mailbox (default: from accounts.md)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit session JSON to stdout (no table)",
    )
    parser.add_argument(
        "--format",
        choices=("auto", "rich", "markdown"),
        default="auto",
        help="Table style: rich in terminal; markdown when piped (default: auto)",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Prompt to pick a row after listing (default on when TTY)",
    )
    parser.add_argument(
        "--no-interactive",
        action="store_true",
        help="Do not prompt after list",
    )
    parser.add_argument(
        "--no-tui",
        action="store_true",
        help="Typed row-number prompts instead of the Textual inbox (fallback for broken terminals)",
    )
    _add_open_flags(parser)


def _add_open_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--open",
        action="store_true",
        help="Open reply in Obsidian after pick (overrides editor config)",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Do not open an editor after pick",
    )


def _resolve_editor(args: argparse.Namespace) -> EditorConfig:
    return resolve_editor(
        cli_no_open=getattr(args, "no_open", False),
        cli_open=getattr(args, "open", False),
    )


def _build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--vault-root",
        help="Obsidian vault root (overrides config and INBOX_VAULT_ROOT)",
    )

    list_options = argparse.ArgumentParser(add_help=False)
    _add_list_flags(list_options)

    parser = argparse.ArgumentParser(
        prog="inbox",
        parents=[common, list_options],
        description="List unread Gmail inbox threads (gog) for vault-configured mailboxes.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    sub = parser.add_subparsers(dest="command")
    sub.required = False

    list_parser = sub.add_parser(
        "list",
        parents=[common, list_options],
        help="Fetch unread threads and print inbox table",
    )
    list_parser.set_defaults(handler=_cmd_list)

    pick_parser = sub.add_parser(
        "pick",
        parents=[common],
        help="Draft reply file for session row N (run list first)",
    )
    pick_parser.add_argument(
        "number",
        type=int,
        help="Row number from the last inbox table",
    )
    pick_parser.add_argument(
        "--project",
        help="Project folder name, or 0 for vault emails/",
    )
    _add_open_flags(pick_parser)
    pick_parser.set_defaults(handler=_cmd_pick)

    send_parser = sub.add_parser(
        "send",
        parents=[common],
        help="Push a reply file to Gmail (draft by default)",
    )
    send_parser.add_argument(
        "path",
        type=Path,
        help="Path to email-reply markdown file",
    )
    send_parser.add_argument(
        "--send",
        action="store_true",
        help="Send immediately (default: create Gmail draft only)",
    )
    send_parser.add_argument(
        "--force",
        action="store_true",
        help="Allow send even when frontmatter status is already sent",
    )
    send_parser.set_defaults(handler=_cmd_send)

    parser.set_defaults(handler=_cmd_list)
    return parser


def _inbox_query(newer_than: str | None) -> str:
    query = "in:inbox is:unread"
    if newer_than:
        query = f"{query} newer_than:{newer_than}"
    return query


def _use_tui(args: argparse.Namespace) -> bool:
    """Textual inbox on TTY by default; --no-tui uses typed row numbers."""
    if getattr(args, "no_tui", False):
        return False
    return sys.stdin.isatty() and sys.stdout.isatty()


def _interactive_tty(args: argparse.Namespace) -> bool:
    if args.json or args.no_interactive:
        return False
    if args.interactive:
        return True
    return sys.stdin.isatty() and sys.stdout.isatty()


def _print_list_warnings(result: ListResult) -> None:
    for line in result.auth_warnings:
        print(line, file=sys.stderr)
    for line in result.search_errors:
        print(f"search failed: {line}", file=sys.stderr)


def _fetch_and_save_session(
    vault: Path,
    config: AccountsConfig,
    *,
    newer_than: str | None,
    max_per_mailbox: int | None,
    quiet_warnings: bool = False,
) -> ListResult:
    """Fetch from gog and persist session without printing a table."""
    result = fetch_combined_inbox(
        config,
        newer_than=newer_than,
        max_per_mailbox=max_per_mailbox,
    )
    if not quiet_warnings:
        _print_list_warnings(result)
    if result.rows:
        write_session(
            session_path(vault),
            build_session(result.rows, query=_inbox_query(newer_than)),
        )
    return result


def _fetch_and_display_inbox(
    vault: Path,
    config: AccountsConfig,
    *,
    newer_than: str | None,
    max_per_mailbox: int | None,
    output_format: str,
) -> ListResult:
    """Fetch unread threads from gog (not session cache), print table, save session."""
    result = fetch_combined_inbox(
        config,
        newer_than=newer_than,
        max_per_mailbox=max_per_mailbox,
    )
    _print_list_warnings(result)
    render_inbox(result.rows, output_format=output_format)
    if result.rows:
        write_session(
            session_path(vault),
            build_session(result.rows, query=_inbox_query(newer_than)),
        )
    return result


def _cmd_list(args: argparse.Namespace) -> int:
    vault = resolve_vault_root(args.vault_root)
    accounts_file = accounts_path(vault)
    if not accounts_file.is_file():
        print(f"accounts file not found: {accounts_file}", file=sys.stderr)
        return 1

    config = load_accounts(accounts_file)
    query = _inbox_query(args.newer_than)

    if _interactive_tty(args) and not args.json:
        ensure_gmail_auth(config.mailboxes)

    if args.json:
        result = fetch_combined_inbox(
            config,
            newer_than=args.newer_than,
            max_per_mailbox=args.max,
        )
        _print_list_warnings(result)
        print(list_result_to_json(result, query=query))
        if result.rows:
            write_session(session_path(vault), build_session(result.rows, query=query))
    elif _interactive_tty(args) and _use_tui(args):
        result = _fetch_and_save_session(
            vault,
            config,
            newer_than=args.newer_than,
            max_per_mailbox=args.max,
            quiet_warnings=True,
        )
    elif _interactive_tty(args):
        result = _fetch_and_display_inbox(
            vault,
            config,
            newer_than=args.newer_than,
            max_per_mailbox=args.max,
            output_format=args.format,
        )
    else:
        result = _fetch_and_display_inbox(
            vault,
            config,
            newer_than=args.newer_than,
            max_per_mailbox=args.max,
            output_format=args.format,
        )

    if result.search_errors and not result.rows:
        return 1

    use_tui = _use_tui(args)
    if should_interact(
        interactive=args.interactive,
        no_interactive=args.no_interactive,
        is_json=args.json,
        row_count=len(result.rows),
        use_tui=use_tui,
    ):
        editor = _resolve_editor(args)

        def refresh_inbox() -> ListResult:
            return _fetch_and_save_session(
                vault,
                config,
                newer_than=args.newer_than,
                max_per_mailbox=args.max,
                quiet_warnings=True,
            )

        return run_pick_loop(
            vault,
            result.rows,
            editor=editor,
            refresh_inbox=refresh_inbox,
            output_format=args.format,
            use_tui=use_tui,
        )

    if not result.rows:
        return 2

    return 0


def _cmd_pick(args: argparse.Namespace) -> int:
    vault = resolve_vault_root(args.vault_root)
    editor = _resolve_editor(args)
    project: str | None = args.project

    while True:
        try:
            path = pick_and_write(vault, args.number, project=project)
        except FileNotFoundError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        except KeyError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        except AmbiguousProjectError as exc:
            print(format_project_menu(vault, exc.candidates), file=sys.stderr)
            if not sys.stdin.isatty():
                return 3
            choice = input("Project (number or name): ").strip()
            resolved = project_from_input(vault, choice, candidates=exc.candidates)
            if resolved == "invalid":
                print("Invalid project.", file=sys.stderr)
                return 3
            project = resolved
            continue
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        else:
            break

    print(format_success(vault, path))
    if open_reply_file(path, editor):
        print(editor.success_message(), file=sys.stderr)
    return 0


def _cmd_send(args: argparse.Namespace) -> int:
    path = args.path.expanduser().resolve()
    if not path.is_file():
        print(f"file not found: {path}", file=sys.stderr)
        return 1
    try:
        if args.send:
            print(push_send(path, force=args.force))
        else:
            print(push_draft(path))
    except (AlreadySentError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    main()
