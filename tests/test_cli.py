import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from email_inbox.cli import run
from email_inbox.list_inbox import ListResult


def test_default_command_is_list(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "accounts.md"
    vault = tmp_path / "vault"
    gmail_dir = vault / "Projects" / "Inbox-CLI"
    gmail_dir.mkdir(parents=True)
    (gmail_dir / "accounts.md").write_text(fixture.read_text())

    with patch("email_inbox.cli.fetch_combined_inbox", return_value=ListResult([], [], [], [])):
        code = run(["--vault-root", str(vault), "--no-interactive"])
    assert code == 2


def test_list_empty_inbox_starts_tui_on_tty(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "accounts.md"
    vault = tmp_path / "vault"
    gmail_dir = vault / "Projects" / "Inbox-CLI"
    gmail_dir.mkdir(parents=True)
    (gmail_dir / "accounts.md").write_text(fixture.read_text())

    with (
        patch("email_inbox.cli.ensure_gmail_auth") as ensure_auth,
        patch("email_inbox.cli.fetch_combined_inbox", return_value=ListResult([], [], [], [])),
        patch.object(sys.stdin, "isatty", return_value=True),
        patch.object(sys.stdout, "isatty", return_value=True),
        patch("email_inbox.cli.run_pick_loop", return_value=0) as run_loop,
    ):
        code = run(["list", "--vault-root", str(vault)])
    assert code == 0
    ensure_auth.assert_called_once()
    run_loop.assert_called_once()
    assert run_loop.call_args[0][1] == []


def test_list_empty_inbox(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "accounts.md"
    vault = tmp_path / "vault"
    gmail_dir = vault / "Projects" / "Inbox-CLI"
    gmail_dir.mkdir(parents=True)
    (gmail_dir / "accounts.md").write_text(fixture.read_text())

    with patch("email_inbox.cli.fetch_combined_inbox", return_value=ListResult([], [], [], [])):
        code = run(["list", "--vault-root", str(vault)])
    assert code == 2


def test_list_missing_accounts(tmp_path: Path) -> None:
    code = run(["list", "--vault-root", str(tmp_path)])
    assert code == 1


def test_version() -> None:
    with pytest.raises(SystemExit) as exc:
        run(["--version"])
    assert exc.value.code == 0
