# inbox-cli

Gmail unread inbox TUI via [gog](https://github.com/steipete/gog), with reply drafts in your Obsidian vault.

**Not** the Obsidian `Inbox.md` processor (that is [`~/Projects/inbox`](https://github.com/pxg/Inbox), command `vault-inbox`).

**PyPI:** [`inbox-cli`](https://pypi.org/project/inbox-cli/) (the name `inbox` on PyPI is the old Nylas SDK). **Command:** `inbox`.

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (`brew install uv`)
- `gog` on `PATH` with Gmail OAuth per mailbox in `accounts.md`
- macOS (optional editor after pick; default is Obsidian)

## Setup

```bash
cd ~/Projects/inbox-cli
uv sync
uv tool install -e .
```

Editable install: code changes apply on the next run without reinstalling.

```bash
inbox list
inbox pick 2
```

`email-inbox` remains as a deprecated alias for the same entry point.

### Vault path

`--vault-root` → `INBOX_VAULT_ROOT` → `~/.config/inbox-cli/config.toml` → `~/Documents/Obsidian Vault`

Legacy: `EMAIL_INBOX_VAULT_ROOT` and `~/.config/email-inbox/` still work.

```bash
mkdir -p ~/.config/inbox-cli
cp config.toml.example ~/.config/inbox-cli/config.toml
```

### Editor after pick

Opens the reply file when you pick a row (TUI **enter** or `inbox pick N`).

Precedence: `--no-open` / `--open` → `INBOX_EDITOR` → `config.toml` → Obsidian.

| `editor` in config | Behaviour |
|--------------------|-----------|
| `"obsidian"` (default) | Obsidian URI / `open -a Obsidian` |
| `"none"` | Do not open |
| `"cursor"`, `"code"`, `"vscode"` | Built-in shortcuts |
| `["your-cli", "{path}"]` | Any command; `{path}` is the reply file |

`open_obsidian = false` in config still works (same as `editor = "none"`).

### Auto-refresh (TUI)

While browsing, the inbox re-fetches from Gmail every **60 seconds** (not during reply/send/mark-read). Disable with `auto_refresh_seconds = 0` in config or `INBOX_AUTO_REFRESH=0`.

### Without a global install

```bash
uv run --directory ~/Projects/inbox-cli inbox list
```

## TUI (default on TTY)

**Browse:** ↑↓, **enter** reply, **o** open (Gmail), **r** refresh, **x** mark read, **q** quit. Inbox auto-refreshes every **60s** in browse mode. **After open:** **d** draft, **s** send, **esc** back.

**`--no-tui`:** typed row numbers and post-pick prompts (fallback).

## Commands

```bash
inbox              # list + TUI
inbox list
inbox pick 2
inbox send path/to/reply.md
inbox send path/to/reply.md --send
```

## Output formats

| Flag | Use |
|------|-----|
| default | Rich table (non-TUI) or Textual TUI |
| `--format markdown` | Pipe table |
| `--json` | Session JSON only |

## Development

```bash
uv run pytest
```

After dependency changes: `uv sync` then `uv tool install --force -e .`

## Troubleshooting auth

If an account token expires, `inbox list` now prints an auth warning with a ready-to-run reauth command, for example:

```bash
pete@petegraham.co.uk authentication expired/invalid, run: gog auth add pete@petegraham.co.uk --services gmail
```
