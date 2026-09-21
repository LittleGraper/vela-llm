# vela-llm

[![PyPI](https://img.shields.io/pypi/v/vela-llm.svg)](https://pypi.org/project/vela-llm/)
[![中文](https://img.shields.io/badge/lang-%E4%B8%AD%E6%96%87-red.svg)](https://github.com/LittleGraper/vela-llm/blob/main/README.zh-CN.md)

`vela-llm` is a local LiteLLM proxy for GitHub Copilot Models. It provides OpenAI- and Anthropic-compatible APIs and listens only on `127.0.0.1` by default.

## Installation

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/):

```bash
uv tool install vela-llm
```

## Quick start

```bash
# Authenticate with GitHub Copilot
vl login

# Start the local proxy
vl start

# Show the API key and base URLs (the key is masked unless explicitly requested)
vl api --show-key

# Update to the latest stable PyPI release
vl update
```

Each `vl start` displays one of four randomly selected static VELA wordmarks.
The banner uses cyan accents in color terminals, plain text when redirected or
`NO_COLOR` is set, and a compact wordmark in narrow terminals.

Each `vl start` refreshes the GitHub Copilot model catalog and stores its public
capabilities in `models-cache.json` inside the vela-llm configuration directory.
The proxy registers Copilot's maximum context window, prompt limit, and output
limit with LiteLLM and resolves the effective context budget using per-model preferences.

VELA uses Copilot API version `2026-08-01` for model discovery and inference,
with a shared VS Code `1.137.0` / Copilot Chat `0.65.0` compatibility profile.
Public GitHub account queries separately use REST API version `2022-11-28`.
`/v1/models` also preserves `capabilities`, `billing`, and `supported_endpoints`,
and exposes `default_context_size` and `context_size_options` when provided by
the catalog. Options use the default billing input threshold and maximum input
capability; the raw long-context billing threshold is preserved separately.
These metadata limits do not prove successful inference at the maximum size.
Restart VELA after upgrading to refresh the model cache and request headers.

## CLI presentation

Run `vl` in a terminal to open the persistent Textual workspace. A fixed header shows
one randomly selected banner, proxy status, address, and default model. The command input
stays at the bottom; only the middle page/result is replaced, without accumulating history.
Small terminals use a compact banner; exiting restores the terminal.

Type `/` for suggestions, use Up/Down to select, Tab to complete, and Enter to run.
Commands include `/models`, `/start`, `/stop`, `/api`, `/test`, `/login`, `/whoami`,
`/logout`, `/update`, and `/about`, with existing options such as `/test --model gpt-4.1`.
The VELA group contains `/update` and `/about`; About shows the project summary, version, and links.
Press `/` from a list or settings page to focus the command input and start a command.
The `/api` page shows both base URLs and one shared, masked API key. Click Show key or press K
outside the command input to reveal or hide it; leaving the page masks it again.
Esc returns one level; command results return to the homepage.
Only on the homepage, press Esc twice within 3 seconds to exit. The first press shows an inline hint below
the input without changing the page or focus. Any other key, click, paste, or timeout
cancels confirmation. Ctrl+C returns to the homepage and shows the same hint; press Esc to confirm. Closing the workspace leaves the background proxy running;
use `/stop` to stop it. Exiting the workspace cancels the current command. Only one external command runs
at a time, and device-login codes appear immediately while authorization is pending.
`/start` runs the proxy in the background; use `vl start --foreground` outside the workspace
for foreground operation.

`vl help` and every `<command> --help` share a grouped Rich help layout.
Without a TTY, bare `vl` still prints help.
API endpoints, account details, device-login instructions, startup and process details,
update instructions, and connectivity results use the same palette and aligned fields.
Long-running checks show a transient progress indicator in terminals; redirected output
has no animation or ANSI controls. API keys stay masked until explicitly revealed on the `/api`
page or with `vl api --show-key`.
Expected command failures show a concise error and a non-zero exit code.
Direct commands such as `vl start` and `vl api` retain regular CLI output for scripts.

## Per-model context settings

Run `uv run vl models` from a checkout, or `vl models` after installation.
Use Up/Down to select, Enter to edit, D to set the default model, and R to refresh.
Press P to preview the saved `models.toml` with syntax highlighting and line numbers;
the preview is read-only and Esc returns to the model list. Refresh is available with R
on the model page; there is no separate `/refresh` command.
Esc goes back one page; only the homepage accepts double Esc to close the workspace.
Moving the cursor never saves a change.

The interactive menu uses Textual: a row-highlighted, scrollable table with aligned
columns and keyboard-driven context pages. Tab moves focus to **Unavailable models**
when present. `vl models` opens the models page in the same full-screen workspace,
restoring the terminal on exit. `--fullscreen` remains accepted for compatibility.
Startup summaries, model test progress/results, and non-interactive model listings
use Rich. Redirected output remains plain text without terminal control sequences.

- **Auto** follows the upstream default; Enter saves and returns to the model list.
- **Maximum** follows the upstream maximum; Enter saves and returns to the list.
- **Custom** opens available sizes. Select one with Up/Down and save with Enter.
  This stores a fixed token count, not an arbitrary number or multiple selections.

Choices represent Copilot prompt budgets, not the combined input/output window.
There are no separate input/output controls. Missing metadata is `Unknown`;
Maximum cannot be saved without a known maximum, and missing options are not invented.
A pricing notice appears when upstream long-context billing metadata is available.

### Refresh and activation

`vl start` refreshes the full catalog before launch. In a terminal, `vl models` first
shows the cache and refreshes in the background. R retries manually; overlapping
refreshes within a menu are coalesced. Esc never waits for the network. Subpages keep
stable options, and saves validate against the latest successful cache.
Non-interactive `vl models` prints a local snapshot without refreshing or logging in.

Subsequent model queries and inference read local configuration again, without fetching
the upstream catalog or restarting VELA. `/v1/models` retains upstream limits and adds
`context_mode`, `configured_context_size`, `context_size` (effective value), and
`context_status` (`ok`, `unknown`, or `needs_review`). Effective context is registered as
LiteLLM's prompt budget (`max_input_tokens`); total-window and output metadata are unchanged.
This does not add token counting, length rejection, history truncation, output parameter
overrides, or upstream entitlement. Clients may need to refresh their model cache;
clients that only recognize standard fields may ignore the context extensions.

### Persistence and model removal

The existing configuration directory is used (`%APPDATA%\vela-llm` on Windows,
overridable with `VELA_LLM_CONFIG_DIR`). With a custom `VELA_LLM_MODELS_CONFIG`, the
catalog is stored beside that file.

- `models-cache.json` stores the last successful catalog and `fetched_at`. Failed,
  invalid, or empty responses preserve the cache and preferences. Refresh never starts
  interactive authentication; run `vl login` first when needed.
- `models.toml` stores preferences by exact model ID. Writes preserve comments,
  unrelated settings, and other models. A process lock protects read/modify/write and
  atomic replacement prevents partial files.

```toml
[models]
default = "model-a"

[models.context."model-a"]
mode = "custom"
size = 272000

[models.context."model-b"]
mode = "maximum"
```

Auto is represented by no model override; selecting it restores defaults.
Switching Custom to Maximum removes the old size. Following a successful refresh,
Auto/Maximum follow the catalog. A Custom value no longer offered is retained and marked
`Needs review`, with an explicit Auto fallback (or `Unknown` if no default exists).

Removed models move to `Unavailable models`; preferences are never deleted automatically.
Returning models are revalidated. Enter on `Delete saved context` in an unavailable model's
details removes its override. This does not change the default model. If the default
disappears, the menu asks for a new selection and inference without an explicit model
returns 404; VELA never silently switches models. Explicit model IDs retain their
existing upstream passthrough behavior.
