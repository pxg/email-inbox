from unittest.mock import patch

from email_inbox.accounts import Mailbox
from email_inbox.auth_prompt import ensure_gmail_auth, mailboxes_needing_auth, prompt_and_reauth
from email_inbox.gog import GmailAuthRecord


def test_mailboxes_needing_auth_missing_and_invalid() -> None:
    records = {
        "a@gmail.com": GmailAuthRecord("a@gmail.com", True, True),
        "b@co.uk": GmailAuthRecord("b@co.uk", True, False, error="invalid_grant"),
    }
    with patch("email_inbox.auth_prompt.gmail_auth_records", return_value=records):
        needing = mailboxes_needing_auth(["a@gmail.com", "b@co.uk", "c@example.com"])
    assert needing == ["b@co.uk", "c@example.com"]


@patch("email_inbox.auth_prompt.prompt_and_reauth", return_value=True)
@patch(
    "email_inbox.auth_prompt.mailboxes_needing_auth",
    side_effect=[["a@gmail.com", "b@co.uk"], ["b@co.uk"], []],
)
def test_ensure_gmail_auth_prompts_each(need_mock, prompt_mock) -> None:
    mailboxes = [
        Mailbox("a@gmail.com", "a@gmail"),
        Mailbox("b@co.uk", "co.uk"),
    ]
    ensure_gmail_auth(mailboxes)
    assert prompt_mock.call_count == 2


@patch("email_inbox.auth_prompt.run_gog_auth_add", return_value=True)
@patch("email_inbox.auth_prompt.gmail_auth_records")
def test_prompt_and_reauth_success(auth_mock, run_mock) -> None:
    auth_mock.return_value = {
        "pete@example.com": GmailAuthRecord("pete@example.com", True, True),
    }
    with patch("builtins.input", return_value=""):
        ok = prompt_and_reauth("pete@example.com")
    assert ok is True
    run_mock.assert_called_once_with("pete@example.com")


@patch("email_inbox.auth_prompt.run_gog_auth_add")
def test_prompt_and_reauth_skip(run_mock) -> None:
    with patch("builtins.input", return_value="q"):
        ok = prompt_and_reauth("pete@example.com")
    assert ok is False
    run_mock.assert_not_called()
