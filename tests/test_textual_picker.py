import asyncio
import time
from pathlib import Path
from unittest.mock import patch

from email_inbox.formatting import InboxRow
from textual.widgets import Static

from email_inbox.editor import EditorConfig
from email_inbox.list_inbox import ListResult
from email_inbox.textual_picker import InboxTuiApp, InboxZeroScreen


def _list_result(rows: list[InboxRow]) -> ListResult:
    return ListResult(rows=rows, auth_warnings=[], search_errors=[], auth_needed=[])


def _row(subject: str = "Hello") -> InboxRow:
    return InboxRow(
        mailbox="alice@example.com",
        label="example.com",
        thread_id="tid1",
        from_header="Bob Client <bob@client.example.com>",
        subject=subject,
        date="2026-06-04 11:12",
    )


def test_enter_does_not_show_opening_reply_busy(tmp_path: Path) -> None:
    reply = tmp_path / "reply.md"
    reply.write_text("x")

    def slow_pick(*_args, **_kwargs) -> Path:
        time.sleep(0.2)
        return reply

    async def run() -> None:
        app = InboxTuiApp(tmp_path, [_row()], editor=EditorConfig.none())
        with patch("email_inbox.textual_picker.pick_inbox_row_flow", side_effect=slow_pick):
            async with app.run_test() as pilot:
                await pilot.press("enter")
                await pilot.pause(delay=0.05)
                hint = app.query_one("#hint_bar", Static)
                assert "OPENING" not in str(hint.content).upper()
                await pilot.pause(delay=0.3)

    asyncio.run(run())


def test_enter_keeps_row_during_slow_pick(tmp_path: Path) -> None:
    reply = tmp_path / "reply.md"
    reply.write_text("x")

    def slow_pick(*_args, **_kwargs) -> Path:
        time.sleep(0.2)
        return reply

    async def run() -> None:
        app = InboxTuiApp(tmp_path, [_row(), _row("Two")], editor=EditorConfig.none())
        with patch("email_inbox.textual_picker.pick_inbox_row_flow", side_effect=slow_pick):
            async with app.run_test() as pilot:
                await pilot.press("enter")
                await pilot.pause(delay=0.08)
        assert len(app.rows) == 2

    asyncio.run(run())


def test_enter_opens_row_and_keeps_in_table(tmp_path: Path) -> None:
    reply = tmp_path / "reply.md"
    reply.write_text("x")

    async def run() -> None:
        app = InboxTuiApp(tmp_path, [_row(), _row("Two")], editor=EditorConfig.none())
        with (
            patch("email_inbox.textual_picker.pick_inbox_row_flow", return_value=reply),
            patch("email_inbox.textual_picker.mark_read_inbox_row") as mark_read,
        ):
            async with app.run_test() as pilot:
                await pilot.press("enter")
                await pilot.pause(delay=0.3)
        assert app.mode == "action"
        assert app.reply_path == reply
        assert app.open_row_index == 0
        assert len(app.rows) == 2
        mark_read.assert_not_called()

    asyncio.run(run())


def test_escape_from_action_keeps_row_in_table(tmp_path: Path) -> None:
    reply = tmp_path / "reply.md"
    reply.write_text("x")

    async def run() -> None:
        app = InboxTuiApp(tmp_path, [_row(), _row("Two")], editor=EditorConfig.none())
        with patch("email_inbox.textual_picker.pick_inbox_row_flow", return_value=reply):
            async with app.run_test() as pilot:
                await pilot.press("enter")
                await pilot.pause()
                await pilot.press("escape")
                await pilot.pause()
        assert app.mode == "browse"
        assert len(app.rows) == 2
        assert app.reply_path is None

    asyncio.run(run())


def test_hint_bar_shows_browse_actions_without_subject() -> None:
    async def run() -> None:
        app = InboxTuiApp(
            Path("/tmp"),
            [_row()],
            editor=EditorConfig.none(),
            refresh_inbox=lambda: _list_result([_row()]),
        )
        async with app.run_test():
            hint = app.query_one("#hint_bar", Static)
            text = str(hint.content)
            assert "[enter]" in text
            assert "reply" in text
            assert "[o]" in text
            assert "[r]" in text
            assert " | " in text
            assert "Hello" not in text

    asyncio.run(run())


def test_open_gmail_uses_thread_url_and_removes_row() -> None:
    async def run() -> None:
        app = InboxTuiApp(Path("/tmp"), [_row(), _row("Two")], editor=EditorConfig.none())
        with (
            patch("email_inbox.textual_picker.open_url") as open_url,
            patch("email_inbox.textual_picker.mark_read_inbox_row"),
        ):
            async with app.run_test() as pilot:
                await pilot.press("o")
                await pilot.pause(delay=0.05)
        open_url.assert_called_once()
        assert "tid1" in open_url.call_args[0][0]
        assert len(app.rows) == 1
        assert app.rows[0].subject == "Two"

    asyncio.run(run())


def test_open_gmail_restores_row_when_mark_read_fails() -> None:
    async def run() -> None:
        app = InboxTuiApp(Path("/tmp"), [_row(), _row("Two")], editor=EditorConfig.none())
        with (
            patch("email_inbox.textual_picker.open_url"),
            patch(
                "email_inbox.textual_picker.mark_read_inbox_row",
                side_effect=RuntimeError("mark-read failed"),
            ),
        ):
            async with app.run_test() as pilot:
                await pilot.press("o")
                await pilot.pause(delay=0.5)
        assert len(app.rows) == 2

    asyncio.run(run())


def test_mark_read_removes_row_immediately_before_gog() -> None:
    def slow_mark(_row: InboxRow) -> str:
        time.sleep(0.2)
        return "✓ Marked read"

    async def run() -> None:
        app = InboxTuiApp(Path("/tmp"), [_row(), _row("Two")], editor=EditorConfig.none())
        with patch(
            "email_inbox.textual_picker.mark_read_inbox_row",
            side_effect=slow_mark,
        ):
            async with app.run_test() as pilot:
                await pilot.press("x")
                await pilot.pause(delay=0.05)
        assert len(app.rows) == 1
        assert app.rows[0].subject == "Two"

    asyncio.run(run())


def test_mark_read_keeps_cursor_on_next_row() -> None:
    async def run() -> None:
        app = InboxTuiApp(
            Path("/tmp"),
            [_row("One"), _row("Two"), _row("Three")],
            editor=EditorConfig.none(),
        )
        with patch("email_inbox.textual_picker.mark_read_inbox_row"):
            async with app.run_test() as pilot:
                await pilot.press("down")
                await pilot.press("x")
                await pilot.pause(delay=0.05)
                table = app.query_one("#inbox_table")
        assert len(app.rows) == 2
        assert app.rows[0].subject == "One"
        assert app.rows[1].subject == "Three"
        assert table.cursor_row == 1

    asyncio.run(run())


def test_mark_read_last_row_moves_to_row_above() -> None:
    async def run() -> None:
        app = InboxTuiApp(
            Path("/tmp"),
            [_row("One"), _row("Two"), _row("Three")],
            editor=EditorConfig.none(),
        )
        with patch("email_inbox.textual_picker.mark_read_inbox_row"):
            async with app.run_test() as pilot:
                await pilot.press("down")
                await pilot.press("down")
                await pilot.press("x")
                await pilot.pause(delay=0.05)
                table = app.query_one("#inbox_table")
        assert len(app.rows) == 2
        assert app.rows[1].subject == "Two"
        assert table.cursor_row == 1

    asyncio.run(run())


def test_mark_read_restores_row_when_gog_fails() -> None:
    async def run() -> None:
        app = InboxTuiApp(Path("/tmp"), [_row(), _row("Two")], editor=EditorConfig.none())
        with patch(
            "email_inbox.textual_picker.mark_read_inbox_row",
            side_effect=RuntimeError("mark-read failed"),
        ):
            async with app.run_test() as pilot:
                await pilot.press("x")
                await pilot.pause(delay=0.5)
        assert len(app.rows) == 2

    asyncio.run(run())


def test_inbox_zero_celebration_on_last_mark_read() -> None:
    async def run() -> None:
        app = InboxTuiApp(Path("/tmp"), [_row()], editor=EditorConfig.none())
        with (
            patch(
                "email_inbox.textual_picker.mark_read_inbox_row",
                return_value="✓ Marked read",
            ),
            patch.object(
                InboxTuiApp,
                "push_screen_wait",
                autospec=True,
            ) as push_screen,
        ):
            async with app.run_test() as pilot:
                await pilot.press("x")
                await pilot.pause(delay=0.5)
        assert len(app.rows) == 0
        push_screen.assert_called_once()
        screen = push_screen.call_args[0][1]
        assert isinstance(screen, InboxZeroScreen)

    asyncio.run(run())


def test_auto_refresh_skips_action_mode() -> None:
    async def run() -> None:
        app = InboxTuiApp(
            Path("/tmp"),
            [_row()],
            editor=EditorConfig.none(),
            refresh_inbox=lambda: _list_result([_row(), _row("New")]),
            auto_refresh_seconds=60,
        )
        app.mode = "action"
        app.action_row = _row()
        with patch.object(app, "run_worker") as run_worker:
            app._auto_refresh_tick()
        run_worker.assert_not_called()

    asyncio.run(run())


def test_auto_refresh_filters_recently_dismissed() -> None:
    async def run() -> None:
        dismissed = _row()
        incoming = _row("New")
        incoming = InboxRow(
            mailbox=incoming.mailbox,
            label=incoming.label,
            thread_id="tid2",
            from_header=incoming.from_header,
            subject=incoming.subject,
            date=incoming.date,
        )
        app = InboxTuiApp(
            Path("/tmp"),
            [dismissed],
            editor=EditorConfig.none(),
            auto_refresh_seconds=60,
        )
        app._note_dismissed(dismissed)
        filtered = app._filter_recently_dismissed([dismissed, incoming])
        assert len(filtered) == 1
        assert filtered[0].subject == "New"

    asyncio.run(run())


def test_auto_refresh_no_timer_when_disabled() -> None:
    async def run() -> None:
        app = InboxTuiApp(
            Path("/tmp"),
            [_row()],
            editor=EditorConfig.none(),
            refresh_inbox=lambda: _list_result([_row()]),
            auto_refresh_seconds=0,
        )
        with patch.object(app, "set_interval") as set_interval:
            async with app.run_test():
                pass
        set_interval.assert_not_called()

    asyncio.run(run())


def test_refresh_empty_inbox_does_not_celebrate() -> None:
    async def run() -> None:
        app = InboxTuiApp(
            Path("/tmp"),
            [_row()],
            editor=EditorConfig.none(),
            refresh_inbox=lambda: _list_result([]),
        )
        with patch.object(InboxTuiApp, "push_screen_wait", autospec=True) as push_screen:
            async with app.run_test() as pilot:
                await pilot.press("r")
                await pilot.pause(delay=0.3)
        assert len(app.rows) == 0
        push_screen.assert_not_called()

    asyncio.run(run())


def test_mark_read_removes_row(tmp_path: Path) -> None:
    async def run() -> None:
        app = InboxTuiApp(tmp_path, [_row(), _row("Two")], editor=EditorConfig.none())
        with (
            patch(
                "email_inbox.textual_picker.mark_read_inbox_row",
                return_value="✓ Marked read",
            ),
            patch("email_inbox.textual_picker.write_session") as write_sess,
        ):
            async with app.run_test() as pilot:
                await pilot.press("x")
                await pilot.pause(delay=0.3)
        assert len(app.rows) == 1
        assert app.rows[0].subject == "Two"
        write_sess.assert_called_once()

    asyncio.run(run())


def test_inbox_data_table_uses_row_cursor() -> None:
    async def run() -> None:
        app = InboxTuiApp(Path("/tmp"), [_row()], editor=EditorConfig.none())
        async with app.run_test():
            table = app.query_one("#inbox_table")
            assert table.cursor_type == "row"

    asyncio.run(run())


def test_send_removes_row_from_table(tmp_path: Path) -> None:
    reply = tmp_path / "reply.md"
    reply.write_text(
        """---
type: email-reply
status: editing
mailbox: alice@example.com
send_from: alice@example.com
to: friend@example.com
subject: "Re: Hello"
reply_to_message_id: "mid1"
---
Thanks
"""
    )

    async def run() -> None:
        app = InboxTuiApp(tmp_path, [_row(), _row("Two")], editor=EditorConfig.none())
        with (
            patch("email_inbox.textual_picker.pick_inbox_row_flow", return_value=reply),
            patch("email_inbox.textual_picker.push_send", return_value="✓ Sent"),
        ):
            async with app.run_test() as pilot:
                await pilot.press("enter")
                await pilot.pause()
                await pilot.press("s")
                await pilot.pause(delay=0.3)
        assert len(app.rows) == 1
        assert app.rows[0].subject == "Two"
        assert app.mode == "browse"

    asyncio.run(run())


def test_send_shows_busy_indicator(tmp_path: Path) -> None:
    reply = tmp_path / "reply.md"
    reply.write_text(
        """---
type: email-reply
status: editing
mailbox: alice@example.com
send_from: alice@example.com
to: friend@example.com
subject: "Re: Hello"
reply_to_message_id: "mid1"
---
Thanks
"""
    )

    def slow_send(_path: Path) -> str:
        time.sleep(0.15)
        return "✓ Sent"

    async def run() -> None:
        app = InboxTuiApp(tmp_path, [_row()], editor=EditorConfig.none())
        with (
            patch("email_inbox.textual_picker.pick_inbox_row_flow", return_value=reply),
            patch("email_inbox.textual_picker.push_send", side_effect=slow_send),
        ):
            async with app.run_test() as pilot:
                await pilot.press("enter")
                await pilot.pause()
                await pilot.press("s")
                await pilot.pause(delay=0.05)
                hint = app.query_one("#hint_bar", Static)
                assert "SENDING" in str(hint.content).upper()
                await pilot.pause(delay=0.3)

    asyncio.run(run())


def test_interactive_delegates_to_textual_session(tmp_path: Path) -> None:
    with (
        patch.object(__import__("sys").stdin, "isatty", return_value=True),
        patch(
            "email_inbox.interactive.run_textual_inbox_session",
            return_value=0,
        ) as run_session,
    ):
        from email_inbox.interactive import run_pick_loop

        code = run_pick_loop(tmp_path, [_row()], editor=EditorConfig.none(), use_tui=True)
    assert code == 0
    run_session.assert_called_once()
