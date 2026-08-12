from pathlib import Path
from unittest.mock import patch

from email_inbox.editor import EditorConfig
from email_inbox.formatting import InboxRow
from email_inbox.interactive import run_pick_loop, should_interact
from email_inbox.list_inbox import ListResult


def _row() -> InboxRow:
    return InboxRow(
        mailbox="alice@example.com",
        label="example.com",
        thread_id="tid1",
        from_header="Bob Client <bob@client.example.com>",
        subject="Hello",
        date="2026-06-04 11:12",
    )


def test_should_interact_tty() -> None:
    with (
        patch.object(__import__("sys").stdin, "isatty", return_value=True),
        patch.object(__import__("sys").stdout, "isatty", return_value=True),
    ):
        assert should_interact(
            interactive=False, no_interactive=False, is_json=False, row_count=3
        )


def test_should_interact_tty_at_inbox_zero_with_tui() -> None:
    with (
        patch.object(__import__("sys").stdin, "isatty", return_value=True),
        patch.object(__import__("sys").stdout, "isatty", return_value=True),
    ):
        assert should_interact(
            interactive=False,
            no_interactive=False,
            is_json=False,
            row_count=0,
            use_tui=True,
        )


def test_should_not_interact_empty_without_tui() -> None:
    with (
        patch.object(__import__("sys").stdin, "isatty", return_value=True),
        patch.object(__import__("sys").stdout, "isatty", return_value=True),
    ):
        assert not should_interact(
            interactive=False,
            no_interactive=False,
            is_json=False,
            row_count=0,
            use_tui=False,
        )


def test_should_not_interact_no_interactive() -> None:
    assert not should_interact(interactive=False, no_interactive=True, is_json=False, row_count=3)


def test_pick_loop_quit_immediately(tmp_path: Path) -> None:
    with patch("builtins.input", return_value="q"):
        code = run_pick_loop(tmp_path, [_row()], editor=EditorConfig.none())
    assert code == 0


def test_pick_loop_pick_then_quit(tmp_path: Path) -> None:
    reply = tmp_path / "reply.md"
    reply.write_text("x")
    with (
        patch("builtins.input", side_effect=["1", "q"]),
        patch("email_inbox.interactive.pick_row_prompt", return_value=reply),
    ):
        code = run_pick_loop(tmp_path, [_row()], editor=EditorConfig.none())
    assert code == 0


def test_after_pick_draft(tmp_path: Path) -> None:
    reply = tmp_path / "reply.md"
    reply.write_text("x")
    with (
        patch("builtins.input", side_effect=["1", "d", "q"]),
        patch("email_inbox.interactive.pick_row_prompt", return_value=reply),
        patch("email_inbox.interactive.push_draft", return_value="✓ Gmail draft"),
    ):
        code = run_pick_loop(tmp_path, [_row()], editor=EditorConfig.none())
    assert code == 0


def test_after_pick_refresh_reprints_table(tmp_path: Path) -> None:
    reply = tmp_path / "reply.md"
    reply.write_text("x")
    refreshed = [_row(), _row()]

    def refresh() -> ListResult:
        return ListResult(refreshed, [], [], [])

    with (
        patch("builtins.input", side_effect=["1", "r", "q"]),
        patch("email_inbox.interactive.pick_row_prompt", return_value=reply),
        patch("email_inbox.interactive.render_inbox") as render,
    ):
        code = run_pick_loop(
            tmp_path,
            [_row()],
            editor=EditorConfig.none(),
            refresh_inbox=refresh,
        )
    assert code == 0
    render.assert_called_once_with(refreshed, output_format="auto")
