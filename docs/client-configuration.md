# Harness clients

Run `vl`, then `/clients` (under **Models**) to configure DSH, Codex or Kimi
for VELA. Start the proxy with `/start` before using a client.

PI supports native Copilot login and is no longer managed by VELA. Existing PI
configuration, authentication and legacy synchronization records are left intact;
VELA ignores those records and will not update PI during model or context changes.

## One workflow for every client

Every detail page has the same controls:

- **Available models**: a checklist, initially all selected. Use ↑↓ to move,
  Space to toggle, A to select all, Enter to keep the draft, or Esc to discard it.
- **Configure**: fetch the latest Copilot model capabilities, then write all
  checked models in one operation. Network/login failure keeps client files intact.
- **Delete configuration**: back up and remove VELA-owned configuration, then
  remove the synchronization record so a later refresh cannot recreate it.
- **P Preview**: show a masked diff without saving.
- **Test request (uses model quota)**: explicitly send one short text request
  through the local proxy using a configured model.

Esc returns one page. `/` focuses the command input; letters typed there do not
trigger page shortcuts. There is no Refresh shortcut on Clients. Selecting an
active model happens inside the client, not in a VELA single-choice model menu.

The all-selected setting follows the full catalog, including newly discovered
models. A custom selection includes only explicitly checked names. Missing names
remain saved in case they return. If no selected model is currently compatible,
Configure and automatic synchronization pause until the selection is updated;
Delete configuration remains available when files can be read.

Configure explicitly applies the previewable changes, including a valid externally
edited VELA provider. Automatic synchronization skips externally edited managed
fields. Switching models inside a client is not a managed-field conflict.
Preview uses the saved catalog and does not access the network. Configure
regenerates the plan with fresh capabilities, preserving the checklist; an
all-selected checklist includes new models. Edits made to client files while
refreshing abort the save rather than being overwritten. Delete never refreshes.

## Client-specific storage and activation

| Client | Default files | Directory override | After Configure |
| --- | --- | --- | --- |
| DSH | `~/.dsh/settings.yaml`, `~/.dsh/.credentials.yaml` | `DSH_HOME` | Select a model in the DSH Web picker |
| Codex | `~/.codex/config.toml`, `~/.codex/vela-models.json` | `CODEX_HOME` | Restart Codex, then use `/model` |
| Kimi Code 2 | `~/.kimi-code/config.toml` | `KIMI_CODE_HOME` | Restart Kimi, then use `/model` inside Kimi |
| Legacy kimi-cli | `~/.kimi/config.toml` | `KIMI_SHARE_DIR` | Restart Kimi, then use `/model` inside Kimi |

Other providers and unrelated settings are preserved. TOML/YAML comments survive;
JSON formatting may change. Existing `auth.json` files are never modified. Project
settings, explicit launch flags and named profiles can override user settings.
No client is installed or launched automatically.

### DSH

VELA manages `vela` and, where needed, `vela-chat`. Configure and synchronization
leave `agent-default-model` untouched. Deleting a VELA provider clears a default
pointing to it without selecting another model; unrelated defaults stay intact.
Model entries include image input, known output limits and `reasoningEfforts`
mapped from advertised wire values. Unsupported thinking levels are not offered.
The model display name (`name` in DSH) comes from the upstream model name. When
none is published, the ID is formatted with capitalized words and hyphens replaced
by spaces, preserving the GPT/version prefix: `gpt-6-astra` becomes `GPT-6 Astra`.
Configure and synchronization
update these labels without changing model IDs or the active model selection.

Versioned credentials receive `refs.VELA_API_KEY`; existing flat files retain their
legacy layout. Other references and OAuth records are preserved. A credential
shared by another route is retained on deletion. A conflicting inherited
`VELA_API_KEY` blocks Configure. POSIX credential files use owner-only permissions.

### Kimi

VELA writes `vela/<model-id>` entries and the corresponding providers. It does not
choose a new active/default model. When upgrading an old single-model `vela`
alias, its default is remapped to the same underlying model if still selected.
If a removed model was the default, that reference is cleared (an empty default
for legacy kimi-cli, or removal of the key for Kimi Code 2). Other defaults are
preserved.
Model `capabilities` includes `image_in` and `thinking` where advertised, and
`always_thinking` when reasoning is required (no `none` effort is available).
Kimi Code 2 also receives `tool_use`, model display names, known input/output
limits, `support_efforts`, `default_effort` and `off_effort` where applicable.
Chat Completions providers use `openai` in Kimi Code 2 and `openai_legacy` in the
legacy CLI; Responses providers use `openai_responses` in both.

`KIMI_CODE_HOME` explicitly selects the modern format and takes precedence over
`KIMI_SHARE_DIR`, which explicitly selects the legacy format. Without either,
VELA prefers an existing `~/.kimi-code` directory or the legacy migration marker.
An unmigrated installation with only `~/.kimi/config.toml` or `config.json`
continues using the legacy location; a new installation defaults to Kimi Code 2.
The target config path is displayed on the client page. If it changes after an
upgrade, automatic sync pauses for review; Configure retains the saved model
checklist and updates the new location without modifying the previous file.

Kimi requires a known context capacity for every configured model. Models without
capacity metadata are omitted with a preview note. Legacy `config.json` must first
be migrated by the legacy CLI. Its launch-time `--config` or `--config-file`
overrides are outside this flow. After changing configuration, restart Kimi and
enter `/model` inside Kimi, not in VELA. Restarting does not depend on a particular
version's reload command.

### Codex

Configure enables VELA in the default user configuration and sets a native
`model_catalog_json` path. No extra profile selection is required in VELA. If the
startup model is not in the selected catalog, the adapter chooses a compatible
initial model (VELA's default when selected, otherwise the first catalog entry).
Once a valid model is selected inside Codex, synchronization preserves it.

Reasoning choices come from each model's advertised
`capabilities.supports.reasoning_effort`, including `none` only when supported.
The catalog default is `medium` when available, otherwise the first advertised
effort. Models without reasoning metadata get no invented choices or default.
Existing `model_reasoning_effort` preferences are preserved. Configure and model
synchronization update older generated catalogs that omitted reasoning choices;
manually edited catalogs require explicit Configure because automatic sync
preserves external edits. Restart Codex to load the updated reasoning picker.

Image input is advertised in the Codex catalog when Copilot reports
`capabilities.supports.vision = true` (or explicit image modality metadata is
present). Missing or false vision capability stays text-only. Older generated
text-only catalogs are updated by Configure or synchronization; restart Codex
afterward so its attachment controls read the corrected capabilities.

The adapter records the original startup model, provider, catalog path and context
override. Delete restores those settings where they still belong to the VELA
integration, preserves later external provider changes, and removes the generated
catalog file when it contains only VELA-owned data. A missing pre-VELA restoration
record from an older adapter results in clearing VELA references instead.
Unrelated named `*.config.toml` profiles are not edited. Old `[profiles.*]` VELA
references are cleaned on deletion; new profile files are not generated.

Codex loads the native catalog on startup, so restart after a catalog update.
Only Responses-compatible models are included. Per-model context replaces the
global context override while VELA is active. Its native catalog requires agent
instructions; the official Codex fallback prompt is bundled with its license and
attribution in `src/vendor/codex/`. Preview summarizes that unchanged prompt.

## Protocols and context

DSH and Kimi use one Responses provider for Responses-capable models, with a
separate `vela-chat` route when some models only support Chat Completions. A
Chat-only catalog uses `vela`. Codex
requires Responses. Embeddings and explicitly incompatible endpoints are excluded.

VELA's effective per-model context setting takes precedence. When upstream has no
default tier and VELA has no effective setting, the known input-token capacity is
used. No arbitrary capacity is invented for Kimi.
Saving or deleting a context preference immediately synchronizes already
configured clients. Conflicts are reported without rolling back the VELA
preference or overwriting external client edits. Restart Codex/DSH or
Kimi after the update; running clients and existing conversations may retain
their earlier settings.

## Runtime compatibility

For models with advertised reasoning efforts, the proxy fills an omitted effort
with `medium` when supported, otherwise the first advertised effort. An explicit
unsupported effort from an old session or model switch is adjusted the same way,
with a warning in the proxy log and an `X-VELA-Reasoning-Effort` response header.
Explicitly non-reasoning models have the effort omitted. Valid choices are never
changed. No capability is inferred from the model name when metadata is missing.

Conversation messages, images, tool calls/results and response IDs are preserved.
An image-containing conversation sent to a model explicitly lacking vision gets
an actionable error, not silently stripped images. Unsupported advertised
protocols are rejected before inference. Mid-stream upstream errors are emitted
as error events, not successful completion markers.

## Status and synchronization

| Status | Meaning |
| --- | --- |
| Not configured | VELA has not been configured for this client. |
| Configured | Managed settings match the current VELA settings. |
| Out of date | The endpoint, key or selected catalog needs updating. |
| Needs review | Managed fields changed externally, no selected model is usable, or files cannot be read. |

These describe files, not client installation or successful inference. The list
checks current files on entry and return. A detail page keeps its draft until it
is closed; Preview does not silently update it.
The separate **Connection** row starts as **Not checked**. After Configure, an
authenticated model-list request checks proxy reachability and catalog agreement
without spending inference quota. Offline/authentication failures do not undo
successfully written configuration and are shown separately. **Test request**
checks a completed text answer through the selected client's protocol; it does
not certify images, tools, every model, long-context limits or old conversations.
Successful checks are green, progress and unchecked states are neutral, and failed
checks use the warning color. Request feedback is independent of whether configuration
was saved during the current visit.

A successful model refresh (opening `/models`, pressing R there, `vl models` in an
interactive terminal, or the refresh during `/start` / `vl start`) synchronizes
already-configured clients using their saved checklist selections. A failed/empty
refresh preserves all client files. There is no periodic background polling.

Unconfigured or deleted clients are never added automatically. External edits to
managed fields pause synchronization for that client; other clients still update.
Old single-model records upgrade when their original fingerprints still match.
Configure remains available for explicit reconciliation. Refresh output reports
client synchronization failures separately from catalog discovery.

## Client upgrades and verification

Client configuration formats are versioned by their upstream projects. VELA
preserves unknown user settings and refuses unrecognized advertised reasoning
values rather than generating an invalid catalog. Refresh/reconfigure and restart
the client after upgrades. Project settings, environment overrides and historical
session snapshots still belong to the client; VELA does not rewrite session files.

Regression coverage includes upstream capability fixtures, sync/refresh failure
and concurrency cases, and proxy image/tool/multi-turn request preservation.
When Codex is on PATH, native tests exercise its model list and an isolated
image/tool round trip followed by resuming the conversation on another model.
These use a local test provider, not paid inference. The optional installed DSH
parser check uses the `VELA_TEST_DSH_ADAPTER` module path.
Set `VELA_TEST_KIMI_CODE` to a Kimi Code 2 executable to check generated files with
its native `doctor` and `provider list` commands in an isolated data directory.
Passing these checks is not a guarantee for future client versions or upstream
availability; use the connection check after configuration and an explicit text
probe when needed.

## Persistence and recovery

Preferences and managed-field fingerprints are stored in `clients/<client>.json`
beside `models-cache.json`. Codex also records the startup settings needed for
restoration. These records contain no API key. Managed fields exclude normal
in-client model selection; backups still include the complete original files.

Before writing, VELA compares files and its record with the opened snapshot, then
rechecks VELA settings. If either changed, reopen the page. Concurrent VELA saves
share a state lock; clients do not share it, so avoid editing their files during
Configure or Delete.

Changed originals are backed up byte-for-byte under `clients/backups/<client>-<id>/`.
`paths.json` maps backup names to original paths. Backups may contain credentials;
they use owner-only permissions on POSIX and the user's directory permissions on
Windows. Each replacement is atomic. Caught errors roll back writes/deletions
without overwriting later external edits. A crash between replacements can require
manual recovery from backup. Close the client, restore the relevant originals and
reopen `/clients` to review before configuring again.

## References

- [DSH providers](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/user/guide/providers.md)
- [Codex configuration](https://learn.chatgpt.com/docs/config-file/config-advanced)
- [Kimi Code configuration](https://moonshotai.github.io/kimi-code/en/configuration/config-files)
- [Kimi Code commands](https://moonshotai.github.io/kimi-code/en/reference/slash-commands)
- [Legacy kimi-cli configuration](https://github.com/MoonshotAI/kimi-cli/blob/main/docs/en/configuration/config-files.md)
