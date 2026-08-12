"""Textual inbox session — table stays on screen; browse and action modes."""

from __future__ import annotations

import asyncio
import sys
import time
from collections.abc import Callable
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import DataTable as DataTableWidget
from textual.widgets import Label, ListItem, ListView, Static
from rich.text import Text

from email_inbox.browser import open_url
from email_inbox.formatting import (
    ACCOUNT_TABLE_MAX,
    DATE_TABLE_WIDTH,
    FROM_TABLE_MAX,
    INDEX_TABLE_WIDTH,
    SUBJECT_TABLE_MAX,
    InboxRow,
)
from email_inbox.mark_read import mark_read_inbox_row
from email_inbox.paths import session_path
from email_inbox.pick import AmbiguousProjectError
from email_inbox.editor import EditorConfig, open_reply_file
from email_inbox.pick_flow import pick_inbox_row_flow
from email_inbox.session import build_session, load_session, write_session
from email_inbox.routing import list_project_options, project_from_input
from email_inbox.send import AlreadySentError, is_reply_sent, push_draft, push_send
from email_inbox.config import DEFAULT_AUTO_REFRESH_SECONDS
from email_inbox.auth_prompt import ensure_gmail_auth_for_addresses
from email_inbox.list_inbox import ListResult
from email_inbox.theme import (
    BG,
    BORDER,
    CURSOR_BG,
    CURSOR_FG,
    HEADER,
    HINT_KEY,
    HINT_LABEL,
    MODAL_BORDER,
    SUBJECT,
    TEXT,
    TEXT_BRIGHT,
    hint_commands_text,
    title_bar,
)


class InboxDataTable(DataTableWidget):
    """Row cursor; Enter selects row (RowSelected), not app-level binding."""

    BINDINGS = [
        *DataTableWidget.BINDINGS,
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("x", "mark_read_focused", "Mark read", priority=True),
        Binding("r", "refresh_focused", "Refresh", priority=True),
        Binding("o", "open_gmail_focused", "Open", priority=True),
    ]

    def action_refresh_focused(self) -> None:
        app = self.app
        if isinstance(app, InboxTuiApp):
            app.action_refresh_inbox()

    def action_open_gmail_focused(self) -> None:
        app = self.app
        if isinstance(app, InboxTuiApp):
            app.action_open_gmail()

    def __init__(self, **kwargs) -> None:
        super().__init__(cursor_type="row", **kwargs)

    def action_mark_read_focused(self) -> None:
        app = self.app
        if isinstance(app, InboxTuiApp):
            app.action_mark_read_row()


class ProjectPickerScreen(ModalScreen[str | None]):
    """Choose project when routing is ambiguous."""

    CSS = f"""
    ProjectPickerScreen {{
        align: center middle;
        background: {BG};
    }}
    #picker_dialog {{
        width: 64;
        height: auto;
        max-height: 22;
        border: solid {MODAL_BORDER};
        background: {BG};
        color: {TEXT_BRIGHT};
        padding: 1 2;
    }}
    #picker_dialog > Label {{
        color: {HEADER};
        text-style: bold;
    }}
    #project_list {{
        height: auto;
        max-height: 14;
        background: {BG};
        color: {TEXT_BRIGHT};
    }}
    ListView > ListItem.highlight {{
        background: {CURSOR_BG};
        color: {CURSOR_FG};
    }}
    """

    def __init__(self, vault_root: Path, candidates: list[str]) -> None:
        super().__init__()
        self.vault_root = vault_root
        self.candidates = candidates

    def compose(self) -> ComposeResult:
        with Container(id="picker_dialog"):
            yield Label("Ambiguous project — enter to select, esc cancel")
            yield ListView(id="project_list")

    def on_mount(self) -> None:
        list_view = self.query_one("#project_list", ListView)
        shown: set[str] = set()
        for oid, label in list_project_options(self.vault_root):
            if oid == "0" or oid in self.candidates:
                list_view.append(ListItem(Label(f"{oid}  {label}", id=f"opt_{oid}")))
                shown.add(oid)
        for name in self.candidates:
            if name not in shown:
                list_view.append(ListItem(Label(f"--project {name}", id=f"opt_{name}")))
        if list_view.children:
            list_view.index = 0

    def _selected_token(self) -> str:
        list_view = self.query_one("#project_list", ListView)
        item = list_view.highlighted_child
        if item is None:
            return ""
        label = item.query_one(Label)
        text = str(label.renderable) if hasattr(label, "renderable") else str(label)
        return text.strip().split()[0].removeprefix("--project")

    def key_enter(self) -> None:
        token = self._selected_token()
        resolved = project_from_input(self.vault_root, token, candidates=self.candidates)
        self.dismiss(None if resolved == "invalid" else resolved)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        label = event.item.query_one(Label)
        text = str(label.renderable)
        token = text.strip().split()[0].removeprefix("--project")
        resolved = project_from_input(self.vault_root, token, candidates=self.candidates)
        self.dismiss(None if resolved == "invalid" else resolved)

    def key_escape(self) -> None:
        self.dismiss(None)


class InboxZeroScreen(ModalScreen[None]):
    """Celebration when the last unread row leaves the table."""

    CSS = f"""
    InboxZeroScreen {{
        align: center middle;
        background: {BG};
    }}
    #zero_dialog {{
        width: 52;
        height: auto;
        border: solid {MODAL_BORDER};
        background: {BG};
        padding: 1 3;
    }}
    #zero_title {{
        width: 100%;
        text-align: center;
        color: {HEADER};
        text-style: bold;
        padding: 0 0 1 0;
    }}
    #zero_subtitle {{
        width: 100%;
        text-align: center;
        color: {SUBJECT};
        padding: 0 0 1 0;
    }}
    #zero_hint {{
        width: 100%;
        text-align: center;
        color: {HINT_LABEL};
    }}
    """

    def compose(self) -> ComposeResult:
        with Container(id="zero_dialog"):
            yield Label("✦  INBOX ZERO  ✦", id="zero_title")
            yield Label("You're all caught up.", id="zero_subtitle")
            yield Label("enter · esc · q — continue", id="zero_hint")

    def key_enter(self) -> None:
        self.dismiss(None)

    def key_escape(self) -> None:
        self.dismiss(None)

    def key_q(self) -> None:
        self.dismiss(None)


class InboxTuiApp(App[int]):
    """
    Browse: enter reply, o open, r refresh, x mark read, q quit.
    Action (after open): d draft, s send, o open, esc browse, q quit.
    """

    SHOW_FOOTER = False

    CSS = f"""
    Screen {{
        background: {BG};
    }}
    InboxDataTable {{
        height: 1fr;
        background: {BG};
        color: {TEXT_BRIGHT};
        border: solid {BORDER};
    }}
    InboxDataTable > .datatable--cursor {{
        background: {CURSOR_BG};
        color: {CURSOR_FG};
        text-style: bold;
    }}
    InboxDataTable > .datatable--header {{
        background: {BG};
        color: {HEADER};
        text-style: bold;
    }}
    InboxDataTable > .datatable--odd-row {{
        background: {BG};
    }}
    InboxDataTable > .datatable--even-row {{
        background: #140f24;
    }}
    #hint_bar {{
        height: 1;
        padding: 0 1;
        color: {TEXT};
        background: {BG};
    }}
    """

    BINDINGS = [
        Binding("q", "quit_app", "Quit", priority=True),
        Binding("r", "refresh_inbox", "Refresh"),
        Binding("x", "mark_read_row", "Mark read", priority=True),
        Binding("o", "open_gmail", "Open", priority=True),
        Binding("enter", "open_row", "Open", show=False),
        Binding("d", "push_draft", "Draft"),
        Binding("s", "push_send", "Send"),
        Binding("escape", "back_to_browse", "Back"),
    ]

    def __init__(
        self,
        vault_root: Path,
        rows: list[InboxRow],
        *,
        editor: EditorConfig,
        refresh_inbox: Callable[[], ListResult] | None = None,
        auto_refresh_seconds: int = DEFAULT_AUTO_REFRESH_SECONDS,
    ) -> None:
        super().__init__()
        self.vault_root = vault_root
        self.rows = list(rows)
        self.editor = editor
        self.refresh_inbox = refresh_inbox
        self.auto_refresh_seconds = (
            auto_refresh_seconds if refresh_inbox is not None else 0
        )
        self.reply_path: Path | None = None
        self.open_row_index: int | None = None
        self.action_row: InboxRow | None = None
        self._busy_message: str | None = None
        self._inbox_zero_celebration_active = False
        self._auto_refresh_pause_until = 0.0
        self._recently_dismissed: dict[tuple[str, str], float] = {}
        self.mode = "browse"

    def compose(self) -> ComposeResult:
        yield InboxDataTable(id="inbox_table", show_cursor=True)
        yield Static("", id="hint_bar")

    def on_mount(self) -> None:
        self._hint_bar = self.query_one("#hint_bar", Static)
        self.title = title_bar(len(self.rows))
        self._fill_table()
        self._update_ui()
        self.query_one("#inbox_table", InboxDataTable).focus()
        if self.auto_refresh_seconds > 0:
            self.set_interval(
                self.auto_refresh_seconds,
                self._auto_refresh_tick,
                name="inbox_auto_refresh",
            )

    def _fill_table(self, *, cursor_row: int | None = None) -> None:
        table = self.query_one("#inbox_table", InboxDataTable)
        table.clear(columns=True)
        table.add_column("#", width=INDEX_TABLE_WIDTH)
        table.add_column("From", width=FROM_TABLE_MAX)
        table.add_column("Subject", width=SUBJECT_TABLE_MAX)
        table.add_column("Account", width=ACCOUNT_TABLE_MAX)
        table.add_column("Date", width=DATE_TABLE_WIDTH)
        for index, row in enumerate(self.rows, start=1):
            table.add_row(
                str(index),
                row.from_for_table,
                row.subject_for_table,
                row.account_for_table,
                row.date,
            )
        if self.rows:
            if cursor_row is not None:
                table.move_cursor(row=min(cursor_row, len(self.rows) - 1))
            else:
                table.move_cursor(row=min(table.cursor_row or 0, len(self.rows) - 1))

    def _sync_session_from_rows(self) -> None:
        path = session_path(self.vault_root)
        try:
            session = load_session(path)
            query = str(session.get("query") or "in:inbox is:unread")
        except FileNotFoundError:
            query = "in:inbox is:unread"
        write_session(path, build_session(self.rows, query=query))

    def _apply_table_ui(self, *, focus_table: bool = True) -> None:
        """Redraw table from self.rows (fast; safe to call before gog finishes)."""
        self.title = title_bar(len(self.rows))
        self.mode = "browse"
        self.reply_path = None
        self.open_row_index = None
        self.action_row = None
        self._fill_table()
        self._update_ui()
        self.refresh()
        if focus_table and self.rows:
            self.query_one("#inbox_table", InboxDataTable).focus()

    def _pause_auto_refresh(self, seconds: float = 10) -> None:
        self._auto_refresh_pause_until = time.monotonic() + seconds

    def _note_dismissed(self, row: InboxRow) -> None:
        key = (row.mailbox, row.thread_id)
        suppress = self.auto_refresh_seconds or DEFAULT_AUTO_REFRESH_SECONDS
        self._recently_dismissed[key] = time.monotonic() + suppress

    def _filter_recently_dismissed(self, rows: list[InboxRow]) -> list[InboxRow]:
        now = time.monotonic()
        self._recently_dismissed = {
            key: expiry
            for key, expiry in self._recently_dismissed.items()
            if expiry > now
        }
        return [
            row
            for row in rows
            if (row.mailbox, row.thread_id) not in self._recently_dismissed
        ]

    def _apply_auto_refresh_rows(self, rows: list[InboxRow]) -> None:
        """Merge fetch into browse table without leaving action mode."""
        if self.mode != "browse" or self._busy_message:
            return
        table = self.query_one("#inbox_table", InboxDataTable)
        cursor = table.cursor_row
        self.rows = rows
        self.title = title_bar(len(self.rows))
        try:
            self._sync_session_from_rows()
        except OSError:
            pass
        self._fill_table()
        if cursor is not None and self.rows:
            table.move_cursor(row=min(cursor, len(self.rows) - 1))
        self._update_ui()

    def _auto_refresh_tick(self) -> None:
        if not self.refresh_inbox or self.auto_refresh_seconds <= 0:
            return
        if (
            self._busy_message
            or self.mode != "browse"
            or self._inbox_zero_celebration_active
        ):
            return
        if time.monotonic() < self._auto_refresh_pause_until:
            return
        self.run_worker(self._do_auto_refresh(), exclusive=True, group="inbox_refresh")

    def _cursor_row_index(self) -> int | None:
        if not self.rows:
            return None
        table = self.query_one("#inbox_table", InboxDataTable)
        row_index: int | None = None
        coord = table.cursor_coordinate
        if coord is not None:
            row_index = coord.row
        elif table.cursor_row is not None:
            row_index = table.cursor_row
        if row_index is None or row_index < 0 or row_index >= len(self.rows):
            return None
        return row_index

    def _current_row(self) -> InboxRow | None:
        row_number = self._cursor_row_number()
        if row_number is None:
            return None
        return self.rows[row_number - 1]

    def _cursor_row_number(self) -> int | None:
        row_index = self._cursor_row_index()
        if row_index is None:
            return None
        return row_index + 1

    def _update_ui(self) -> None:
        self._update_hint_bar()

    def _hint_content(self) -> Text:
        if self._busy_message:
            msg = self._busy_message.rstrip("…").upper()
            return Text(f"  * {msg} *", style=HEADER)
        if not self.rows:
            cmds: list[tuple[str, str]] = []
            if self.refresh_inbox:
                cmds.append(("r", "refresh"))
            cmds.append(("q", "quit"))
            prefix = Text("  INBOX ZERO — ", style=HEADER)
            prefix.append_text(hint_commands_text(*cmds))
            return prefix
        if self.mode == "action" and self.reply_path is not None:
            label = self.reply_path.name
            if is_reply_sent(self.reply_path):
                actions = hint_commands_text(
                    ("o", "open"), ("esc", "back"), ("q", "quit")
                )
            else:
                actions = hint_commands_text(
                    ("d", "draft"),
                    ("s", "send"),
                    ("o", "open"),
                    ("esc", "back"),
                    ("q", "quit"),
                )
            prefix = Text(f"  {label} — ", style=HEADER)
            prefix.append_text(actions)
            return prefix
        cmds = [
            ("enter", "reply"),
            ("o", "open"),
            ("x", "read"),
        ]
        if self.refresh_inbox:
            cmds.append(("r", "refresh"))
        cmds.append(("q", "quit"))
        line = Text("  ")
        line.append_text(hint_commands_text(*cmds))
        return line

    def _update_hint_bar(self) -> None:
        hint = getattr(self, "_hint_bar", None)
        if hint is None:
            try:
                hint = self.query_one("#hint_bar", Static)
            except Exception:
                return
        hint.update(self._hint_content())

    def _set_busy(self, message: str) -> None:
        self._busy_message = message
        self._update_ui()

    def _clear_busy(self) -> None:
        self._busy_message = None
        self._update_ui()

    def _focused_inbox_row(self) -> InboxRow | None:
        if self.mode == "action" and self.action_row is not None:
            return self.action_row
        return self._current_row()

    def _drop_row_from_table(
        self, row_index: int, *, celebrate_on_empty: bool = True
    ) -> InboxRow:
        row = self.rows[row_index]
        del self.rows[row_index]
        try:
            self._sync_session_from_rows()
        except OSError as exc:
            self.notify(f"Session save failed: {exc}", severity="warning")
        self.title = title_bar(len(self.rows))
        cursor_after = min(row_index, len(self.rows) - 1) if self.rows else None
        self._redraw_table(cursor_row=cursor_after)
        self._note_dismissed(row)
        self._pause_auto_refresh()
        if not self.rows and celebrate_on_empty:
            self._schedule_inbox_zero_celebration()
        return row

    def _schedule_inbox_zero_celebration(self) -> None:
        if self._inbox_zero_celebration_active:
            return
        self.run_worker(self._run_inbox_zero_celebration(), exclusive=False, group="inbox_zero")

    async def _run_inbox_zero_celebration(self) -> None:
        self._inbox_zero_celebration_active = True
        try:
            await self.push_screen_wait(InboxZeroScreen())
        finally:
            self._inbox_zero_celebration_active = False

    def _restore_row_at(self, row_index: int, row: InboxRow) -> None:
        self.rows.insert(min(row_index, len(self.rows)), row)
        try:
            self._sync_session_from_rows()
        except OSError:
            pass
        self.title = title_bar(len(self.rows))
        self._redraw_table(cursor_row=min(row_index, len(self.rows) - 1) if self.rows else None)

    def _redraw_table(self, *, cursor_row: int | None = None) -> None:
        self._fill_table(cursor_row=cursor_row)

    def action_open_gmail(self) -> None:
        if self._busy_message:
            return
        if self.mode == "action":
            row = self.action_row
            if row is None:
                self.notify("No row selected")
                return
            open_url(row.gmail_url)
            return
        row_index = self._cursor_row_index()
        if row_index is None:
            self.notify("No row selected")
            return
        row = self._drop_row_from_table(row_index)
        self._update_ui()
        open_url(row.gmail_url)

        async def finish() -> None:
            await self._complete_mark_read(row, row_index)

        self.run_worker(finish, exclusive=False, group="mark_read")

    def on_data_table_row_selected(self, event: DataTableWidget.RowSelected) -> None:
        if event.control.id != "inbox_table":
            return
        self.action_open_row()

    async def _pick_at_row(self, row: InboxRow, *, project: str | None = None) -> Path | None:
        while True:
            try:
                return await asyncio.to_thread(
                    pick_inbox_row_flow,
                    self.vault_root,
                    row,
                    editor=EditorConfig.none(),
                    project=project,
                )
            except AmbiguousProjectError as exc:
                project = await self.push_screen_wait(
                    ProjectPickerScreen(self.vault_root, exc.candidates)
                )
                if project is None:
                    self.notify("Open cancelled")
                    return None

    def action_open_row(self) -> None:
        self.run_worker(self._do_open_row(), exclusive=False, group="open_reply")

    async def _do_open_row(self) -> None:
        row_index = self._cursor_row_index()
        row = self._current_row()
        if row is None or row_index is None:
            self.notify("No row selected")
            return
        path = await self._pick_at_row(row)
        if path is None:
            return
        self.action_row = row
        self.open_row_index = row_index
        self.reply_path = path
        self.mode = "action"
        self._update_ui()

        if self.editor.opens:

            async def launch_editor() -> None:
                await asyncio.to_thread(open_reply_file, path, self.editor)

            self.run_worker(launch_editor, exclusive=False, group="editor")

    def action_mark_read_row(self) -> None:
        if self.mode != "browse":
            self.notify("Mark read is for browse mode (esc to go back)")
            return
        row_index = self._cursor_row_index()
        if row_index is None:
            self.notify("No row selected")
            return
        row = self._drop_row_from_table(row_index)
        self._update_ui()

        async def finish() -> None:
            await self._complete_mark_read(row, row_index)

        self.run_worker(finish, exclusive=False, group="mark_read")

    async def _complete_mark_read(self, row: InboxRow, row_index: int) -> None:
        """Gmail mark-read in background; row already removed from the table."""
        try:
            await asyncio.to_thread(mark_read_inbox_row, row)
        except (RuntimeError, ValueError, KeyError, FileNotFoundError) as exc:
            self._restore_row_at(row_index, row)
            self._update_ui()
            self.notify(str(exc), severity="error", timeout=6)

    def _maybe_reauth(self, result: ListResult) -> ListResult:
        if not result.auth_needed:
            return result
        with self.suspend():
            ensure_gmail_auth_for_addresses(result.auth_needed)
        if self.refresh_inbox is None:
            return result
        return self.refresh_inbox()

    def action_refresh_inbox(self) -> None:
        if self._busy_message or self.refresh_inbox is None:
            return
        self.run_worker(self._do_refresh_inbox(), exclusive=True, group="inbox_refresh")

    async def _do_refresh_inbox(self) -> None:
        if self.refresh_inbox is None:
            return
        self._set_busy("Refreshing inbox…")
        try:
            result = await asyncio.to_thread(self.refresh_inbox)
            result = self._maybe_reauth(result)
        except Exception as exc:
            self.notify(str(exc), severity="error", timeout=6)
            return
        finally:
            self._clear_busy()
        self.rows = result.rows
        self._apply_table_ui()

    async def _do_auto_refresh(self) -> None:
        if self.refresh_inbox is None:
            return
        if self.mode != "browse" or self._busy_message:
            return
        try:
            result = await asyncio.to_thread(self.refresh_inbox)
        except Exception:
            return
        rows = self._filter_recently_dismissed(result.rows)
        self._apply_auto_refresh_rows(rows)

    def action_back_to_browse(self) -> None:
        self.mode = "browse"
        self.reply_path = None
        self.action_row = None
        self.open_row_index = None
        self._update_ui()
        if not self.rows:
            self._schedule_inbox_zero_celebration()

    def action_quit_app(self) -> None:
        self.exit(0)

    def action_push_draft(self) -> None:
        if self._busy_message:
            return
        if self.mode != "action":
            self.notify("Open a row first (enter)")
            return
        self.run_worker(self._do_push_draft(), exclusive=True)

    async def _do_push_draft(self) -> None:
        if self.reply_path is None:
            return
        if is_reply_sent(self.reply_path):
            self.notify("Already sent")
            return
        self._set_busy("Creating Gmail draft…")
        try:
            await asyncio.to_thread(push_draft, self.reply_path)
        except (AlreadySentError, ValueError, RuntimeError) as exc:
            self.notify(str(exc), severity="error", timeout=6)
            return
        finally:
            self._clear_busy()
        self._remove_open_row_after_send()

    def action_push_send(self) -> None:
        if self._busy_message:
            return
        if self.mode != "action":
            self.notify("Open a row first (enter)")
            return
        self.run_worker(self._do_push_send(), exclusive=True)

    async def _do_push_send(self) -> None:
        if self.reply_path is None:
            return
        if is_reply_sent(self.reply_path):
            self.notify("Already sent")
            return
        self._set_busy("Sending via Gmail…")
        try:
            await asyncio.to_thread(push_send, self.reply_path)
        except (AlreadySentError, ValueError, RuntimeError) as exc:
            self.notify(str(exc), severity="error", timeout=6)
            return
        finally:
            self._clear_busy()
        self._remove_open_row_after_send()

    def _remove_open_row_after_send(self) -> None:
        """Drop thread from inbox after send/draft (Gmail marked read in push_*)."""
        if self.open_row_index is None:
            self.action_back_to_browse()
            return
        row_index = self.open_row_index
        self.open_row_index = None
        self.action_row = None
        self.reply_path = None
        self.mode = "browse"
        self._drop_row_from_table(row_index)
        self._update_ui()


def run_textual_inbox_session(
    vault_root: Path,
    rows: list[InboxRow],
    *,
    editor: EditorConfig,
    refresh_inbox: Callable[[], ListResult] | None = None,
    auto_refresh_seconds: int = DEFAULT_AUTO_REFRESH_SECONDS,
) -> int:
    """Run full Textual inbox session until user quits."""
    if not sys.stdin.isatty():
        return 0
    app = InboxTuiApp(
        vault_root,
        rows,
        editor=editor,
        refresh_inbox=refresh_inbox,
        auto_refresh_seconds=auto_refresh_seconds,
    )
    return app.run() or 0


InboxPickerApp = InboxTuiApp
