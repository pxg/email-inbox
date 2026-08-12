"""Parallel unread inbox fetch and merge."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any

from email_inbox.accounts import AccountsConfig, Mailbox
from email_inbox.formatting import InboxRow
from email_inbox.gog import (
    GogError,
    auth_warning,
    enrich_multi_message_rows,
    gmail_auth_records,
    gmail_search_unread,
    inbox_row_message_fields_from_search,
    is_auth_error,
)


@dataclass
class ListResult:
    rows: list[InboxRow]
    auth_warnings: list[str]
    search_errors: list[str]
    auth_needed: list[str]


def fetch_combined_inbox(
    config: AccountsConfig,
    *,
    newer_than: str | None = None,
    max_per_mailbox: int | None = None,
) -> ListResult:
    max_n = max_per_mailbox or config.max_unread_per_mailbox
    auth_records = gmail_auth_records()
    auth_warnings: list[str] = []
    auth_needed: list[str] = []
    search_errors: list[str] = []
    threads_by_key: dict[tuple[str, str], InboxRow] = {}

    mailboxes_to_query: list[Mailbox] = []
    for mb in config.mailboxes:
        record = auth_records.get(mb.address)
        if record is None or not record.has_gmail:
            auth_warnings.append(
                f"{mb.address} not authorized — gog auth add {mb.address} --services gmail"
            )
            auth_needed.append(mb.address)
            continue
        if not record.valid:
            auth_warnings.append(auth_warning(mb.address, record.error or "invalid token"))
            auth_needed.append(mb.address)
            continue
        mailboxes_to_query.append(mb)

    if not mailboxes_to_query:
        return ListResult(
            rows=[],
            auth_warnings=auth_warnings,
            search_errors=search_errors,
            auth_needed=auth_needed,
        )

    with ThreadPoolExecutor(max_workers=len(mailboxes_to_query)) as pool:
        futures = {
            pool.submit(
                _fetch_one,
                mb,
                max_n=max_n,
                newer_than=newer_than,
            ): mb
            for mb in mailboxes_to_query
        }
        for future in as_completed(futures):
            mb = futures[future]
            try:
                rows = future.result()
            except GogError as exc:
                if is_auth_error(str(exc)):
                    auth_warnings.append(auth_warning(mb.address, str(exc)))
                    auth_needed.append(mb.address)
                else:
                    search_errors.append(f"{mb.address}: {exc}")
                continue
            for row in rows:
                key = (row.mailbox, row.thread_id)
                threads_by_key[key] = row

    merged = sorted(threads_by_key.values(), key=lambda r: r.date, reverse=True)
    return ListResult(
        rows=merged,
        auth_warnings=auth_warnings,
        search_errors=search_errors,
        auth_needed=auth_needed,
    )


def _fetch_one(mb: Mailbox, *, max_n: int, newer_than: str | None) -> list[InboxRow]:
    raw_threads = gmail_search_unread(mb.address, max_results=max_n, newer_than=newer_than)
    rows: list[InboxRow] = []
    for t in raw_threads:
        thread_id = str(t.get("id") or "")
        if not thread_id:
            continue
        message_count, latest_message_id, snippet = inbox_row_message_fields_from_search(t)
        rows.append(
            InboxRow(
                mailbox=mb.address,
                label=mb.label,
                thread_id=thread_id,
                from_header=str(t.get("from") or ""),
                subject=str(t.get("subject") or ""),
                date=str(t.get("date") or ""),
                message_count=message_count,
                latest_message_id=latest_message_id,
                snippet=snippet,
            )
        )
    return enrich_multi_message_rows(mb.address, rows)


def list_result_to_json(result: ListResult, *, query: str) -> str:
    from email_inbox.session import build_session

    payload = build_session(result.rows, query=query)
    payload["auth_warnings"] = result.auth_warnings
    payload["search_errors"] = result.search_errors
    payload["auth_needed"] = result.auth_needed
    return json.dumps(payload, indent=2)
