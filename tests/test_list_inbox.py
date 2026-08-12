from unittest.mock import patch

from email_inbox.accounts import AccountsConfig, Mailbox
from email_inbox.gog import GogError, GmailAuthRecord
from email_inbox.list_inbox import fetch_combined_inbox


def _config() -> AccountsConfig:
    return AccountsConfig(
        mailboxes=[
            Mailbox("a@gmail.com", "a@gmail"),
            Mailbox("b@co.uk", "co.uk"),
        ],
        default_mailbox="a@gmail.com",
        max_unread_per_mailbox=10,
    )


def _auth_records(
    *,
    valid: set[str],
    invalid: set[str] = frozenset(),
    missing: set[str] = frozenset(),
) -> dict[str, GmailAuthRecord]:
    records: dict[str, GmailAuthRecord] = {}
    for address in valid:
        records[address] = GmailAuthRecord(address, True, True)
    for address in invalid:
        records[address] = GmailAuthRecord(address, True, False, error="invalid_grant")
    return records


@patch("email_inbox.gog.gmail_thread_get")
@patch("email_inbox.list_inbox.gmail_search_unread")
@patch("email_inbox.list_inbox.gmail_auth_records")
def test_parallel_merge_and_sort(auth_mock, search_mock, thread_get_mock) -> None:
    auth_mock.return_value = _auth_records(valid={"a@gmail.com", "b@co.uk"})
    thread_get_mock.return_value = {
        "thread": {
            "messages": [
                {
                    "id": "m1",
                    "payload": {"headers": [{"name": "From", "value": "Bob <b@y.com>"}]},
                },
                {
                    "id": "m2",
                    "labelIds": ["UNREAD", "INBOX"],
                    "snippet": "Follow up",
                    "payload": {
                        "headers": [{"name": "From", "value": "Carol <carol@y.com>"}],
                    },
                },
            ]
        }
    }

    def search_side_effect(mailbox: str, **kwargs):
        if mailbox == "a@gmail.com":
            return [
                {
                    "id": "t1",
                    "from": "Alice <a@x.com>",
                    "subject": "Older",
                    "date": "2026-06-03 10:00",
                    "messageCount": 1,
                }
            ]
        return [
            {
                "id": "t2",
                "from": "Bob <b@y.com>",
                "subject": "Newer",
                "date": "2026-06-04 09:00",
                "messageCount": 2,
            }
        ]

    search_mock.side_effect = search_side_effect
    result = fetch_combined_inbox(_config())
    assert len(result.rows) == 2
    assert result.rows[0].subject == "Newer"
    assert result.rows[0].message_count == 2
    assert result.rows[0].from_header == "Carol <carol@y.com>"
    assert result.rows[0].latest_message_id == "m2"
    thread_get_mock.assert_called_once_with("b@co.uk", "t2")
    assert result.rows[1].subject == "Older"
    assert result.rows[1].latest_message_id == "t1"


@patch("email_inbox.list_inbox.gmail_auth_records")
def test_skip_unauthorized(auth_mock) -> None:
    auth_mock.return_value = _auth_records(valid={"a@gmail.com"})
    with patch("email_inbox.list_inbox.gmail_search_unread", return_value=[]):
        result = fetch_combined_inbox(_config())
    assert len(result.auth_warnings) == 1
    assert "b@co.uk" in result.auth_warnings[0]
    assert result.auth_needed == ["b@co.uk"]


@patch("email_inbox.list_inbox.gmail_auth_records")
def test_invalid_refresh_token_skipped_with_warning(auth_mock) -> None:
    auth_mock.return_value = _auth_records(valid={"a@gmail.com"}, invalid={"b@co.uk"})
    with patch("email_inbox.list_inbox.gmail_search_unread", return_value=[]) as search_mock:
        result = fetch_combined_inbox(_config())

    search_mock.assert_called_once()
    assert search_mock.call_args[0][0] == "a@gmail.com"
    assert result.search_errors == []
    assert len(result.auth_warnings) == 1
    assert "b@co.uk authentication expired/invalid" in result.auth_warnings[0]
    assert result.auth_needed == ["b@co.uk"]


@patch("email_inbox.list_inbox.gmail_auth_records")
def test_expired_token_promoted_to_auth_warning(auth_mock) -> None:
    auth_mock.return_value = _auth_records(valid={"a@gmail.com", "b@co.uk"})

    def search_side_effect(mailbox: str, **kwargs):
        if mailbox == "b@co.uk":
            raise GogError(mailbox, "invalid_grant: Token has been expired or revoked")
        return []

    with patch("email_inbox.list_inbox.gmail_search_unread", side_effect=search_side_effect):
        result = fetch_combined_inbox(_config())

    assert result.search_errors == []
    assert len(result.auth_warnings) == 1
    assert "b@co.uk authentication expired/invalid" in result.auth_warnings[0]
    assert "gog auth add b@co.uk --services gmail" in result.auth_warnings[0]
    assert result.auth_needed == ["b@co.uk"]
