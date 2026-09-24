"""User-level harness adapters and review-before-write configuration plans.

Configuration contracts are linked in docs/client-configuration.md. Reading and
previewing never create files; only apply writes client configuration.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from collections.abc import Mapping
from contextlib import ExitStack
from dataclasses import dataclass, field
from difflib import unified_diff
from io import StringIO
from pathlib import Path
from uuid import uuid4

import httpx
import tomlkit
from ruamel.yaml import YAML

from atomic_files import atomic_write, config_lock
from github_copilot_models import refresh_model_cache
from model_capabilities import (
    REASONING_DESCRIPTIONS,
    default_effort,
    input_modalities,
    model_display_name,
    reasoning_efforts,
    supports,
)

CLIENTS = {"dsh": "DSH", "codex": "Codex", "kimi": "Kimi"}
MASK = "••••••••••••"
DELETE = object()
USE_SAVED = object()


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def read_bytes(path: Path) -> bytes | None:
    if path.is_symlink():
        raise ValueError(f"Configuration is a symbolic link: {path}. Edit its target manually.")
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def yaml_codec():
    codec = YAML()
    codec.preserve_quotes = True
    codec.allow_duplicate_keys = False
    return codec


def parse(path: Path, raw: bytes | None):
    if raw is None:
        return tomlkit.document() if path.suffix == ".toml" else {}
    try:
        text = raw.decode("utf-8-sig")
        if path.suffix == ".toml":
            result = tomlkit.parse(text)
        elif path.suffix == ".json":

            def unique(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("Duplicate key")
                    result[key] = value
                return result

            result = json.loads(text, object_pairs_hook=unique)
        else:
            result = yaml_codec().load(text)
            if result is None:
                result = {}
        if not isinstance(result, Mapping):
            raise ValueError("Expected mapping")
        # DSH treats null reference sections as empty mappings. Normalize for
        # stable field-level plans before and after inserting the first key.
        if (
            path.name == ".credentials.yaml"
            and "version" in result
            and "refs" in result
            and result["refs"] is None
        ):
            result["refs"] = {}
        # Also rejects cyclic YAML aliases, which are not valid client settings.
        json.dumps(result, default=str)
        return result
    except Exception:
        # Parsers often include the offending line, which can contain a secret.
        raise ValueError(
            f"Invalid configuration: {path}. Fix the file and reopen this page."
        ) from None


def serialize(path: Path, document) -> str:
    if path.suffix == ".toml":
        return tomlkit.dumps(document)
    if path.suffix == ".json":
        return json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    stream = StringIO()
    yaml_codec().dump(document, stream)
    return stream.getvalue()


def get_value(document, keys):
    for key in keys:
        if not isinstance(document, Mapping):
            raise ValueError("A configuration section has an incompatible type.")
        if key not in document:
            return None
        document = document[key]
    return document


def set_value(document, keys, value):
    for key in keys[:-1]:
        if key not in document:
            document[key] = {}
        else:
            # Detach YAML aliases so editing this section cannot modify another provider.
            document[key] = copy.deepcopy(document[key])
        document = document[key]
        if not isinstance(document, Mapping):
            raise ValueError("A configuration section has an incompatible type.")
    if value is DELETE:
        document.pop(keys[-1], None)
    else:
        document[keys[-1]] = copy.deepcopy(value)


def redact(value, *, secret_file=False):
    """Preview parsed data, never raw comments or parser diagnostics containing secrets."""
    if isinstance(value, Mapping):
        return {
            str(key): MASK
            if secret_file
            or re.search(
                r"key|token|secret|password|credential|authorization|headers|^env$",
                str(key),
                re.I,
            )
            else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def dsh_credential_operations(document, key):
    """Patch refs in current DSH stores, preserving legacy stores and login records."""
    versioned = not document or "version" in document
    if versioned:
        if document and (type(document["version"]) is not int or document["version"] != 1):
            raise ValueError("Unsupported DSH credential file version. Expected version 1.")
        if set(document) - {"version", "refs", "records"}:
            raise ValueError(
                "Unknown section in DSH credential file. Expected version, refs and records."
            )
        refs = document.get("refs", {})
        records = document.get("records", {})
        refs = {} if refs is None else refs
        records = {} if records is None else records
        if not isinstance(refs, Mapping) or not isinstance(records, Mapping):
            raise ValueError("DSH credential refs and records must be mappings.")
    else:
        refs = document
    if any(
        not isinstance(name, str)
        or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)
        or not isinstance(value, str)
        or not value
        for name, value in refs.items()
    ):
        raise ValueError(
            "DSH credential references must have valid names and non-empty string values."
        )
    if versioned:
        return {("version",): 1, ("refs", "VELA_API_KEY"): key}
    return {("VELA_API_KEY",): key}


@dataclass
class FileChange:
    path: Path
    before: bytes | None
    document: object
    operations: dict
    after: bytes | None = b""

    def projection(self, document):
        return {json.dumps(keys): get_value(document, keys) for keys in self.operations}

    def preview(self) -> tuple[str, str]:
        safe = redact(self.document, secret_file=self.path.name == ".credentials.yaml")
        if self.path.name == "vela-models.json" and isinstance(safe.get("models"), list):
            bundled = (Path(__file__).parent / "vendor/codex/prompt.md").read_text(encoding="utf-8")
            for model in safe["models"]:
                if isinstance(model, dict) and model.get("base_instructions") == bundled:
                    model["base_instructions"] = "[Bundled Codex fallback prompt; unchanged]"
        # Re-serialize parsed values to omit comments and preserve no hidden secrets.
        safe = json.loads(json.dumps(safe, default=str))
        syntax = {".toml": "toml", ".yaml": "yaml"}.get(self.path.suffix, "json")
        return serialize(self.path, safe), syntax

    def preview_diff(self, key: str) -> tuple[str, str]:
        after, syntax = self.preview()
        old = FileChange(self.path, None, parse(self.path, self.before), {})
        before, _ = old.preview()
        if key:
            before, after = before.replace(key, MASK), after.replace(key, MASK)
        diff = "".join(
            unified_diff(
                before.splitlines(True),
                after.splitlines(True),
                fromfile="Current",
                tofile="After apply",
            )
        )
        # A key-only change intentionally has no visible secret difference.
        return (diff, "diff") if diff else (after, syntax)


@dataclass
class ClientPlan:
    client: str
    endpoint: str
    state_path: Path
    state_before: bytes | None = None
    status: str = "Not configured"
    error: str = ""
    changes: list[FileChange] = field(default_factory=list)
    record: dict = field(default_factory=dict)
    summary: list[str] = field(default_factory=list)
    catalog_count: int = 0
    selected_models: list[str] | None = None
    action: str = "apply"
    has_configuration: bool = False

    @property
    def changed(self):
        return any(change.before != change.after for change in self.changes)


class ClientManager:
    def __init__(self, settings, *, home: Path | None = None, environ=None, settings_loader=None):
        self.settings = settings
        self.settings_loader = settings_loader
        self.home = home or Path.home()
        self.environ = os.environ if environ is None else environ
        self.state_dir = settings.model_cache_path.parent / "clients"

    def root(self, variable, fallback):
        return Path(self.environ.get(variable) or self.home / fallback).expanduser().absolute()

    def paths(self, client):
        if client == "dsh":
            root = self.root("DSH_HOME", ".dsh")
            return [root / "settings.yaml", root / ".credentials.yaml"]
        if client == "codex":
            root = self.root("CODEX_HOME", ".codex")
            return [root / "config.toml", root / "vela-models.json"]
        if client == "kimi":
            return [self.kimi_layout()[0] / "config.toml"]
        raise ValueError("Unsupported client.")

    def kimi_layout(self):
        """Prefer Kimi Code 2; retain explicitly selected or unmigrated legacy installs."""
        if self.environ.get("KIMI_CODE_HOME"):
            return self.root("KIMI_CODE_HOME", ".kimi-code"), False
        if self.environ.get("KIMI_SHARE_DIR"):
            return self.root("KIMI_SHARE_DIR", ".kimi"), True
        modern = self.home / ".kimi-code"
        legacy = self.home / ".kimi"
        if modern.exists() or (legacy / ".migrated-to-kimi-code").exists():
            return modern, False
        if any((legacy / name).exists() for name in ("config.toml", "config.json")):
            return legacy, True
        return modern, False

    @staticmethod
    def protocol(client, entry):
        """Use HTTP endpoints supported by both the model and the client."""
        if entry.get("mode") in ("embedding", "embeddings"):
            return None
        endpoints = entry.get("supported_endpoints")
        if not endpoints:
            # Older catalogs may only carry mode; preserve the previous fallback.
            return "responses" if client == "codex" or entry.get("mode") == "responses" else "chat"
        endpoints = {endpoint.removeprefix("/v1") for endpoint in endpoints}
        if client != "codex" and "/chat/completions" in endpoints:
            return "chat"
        if "/responses" in endpoints:
            return "responses"
        return None

    def models(self, client):
        return [
            entry
            for entry in self.settings.model_registry()
            if self.protocol(client, entry) is not None
        ]

    def catalog(self, client):
        return sorted(self.models(client), key=lambda entry: entry["name"])

    @staticmethod
    def context_budget(entry):
        context = entry.get("context_size")
        if type(context) is int and context > 0:
            return context
        # Some upstream models publish an input limit but no default tier.
        # Use that known capacity instead of inventing a client-specific size.
        maximum = entry.get("max_input_tokens")
        return maximum if type(maximum) is int and maximum > 0 else None

    @classmethod
    def model_info(cls, entry):
        info = {
            "id": entry["name"],
            "name": model_display_name(entry),
            "input": cls.input_modalities(entry),
        }
        context = cls.context_budget(entry)
        if type(context) is int and context > 0:
            info["contextWindow"] = context
        output = entry.get("max_output_tokens")
        if type(output) is int and output > 0:
            info["maxTokens"] = output
        efforts = reasoning_efforts(entry)
        if efforts is not None:
            thinking = any(effort != "none" for effort in efforts)
            info["reasoningEfforts"] = (
                {"off" if effort == "none" else effort: effort for effort in efforts}
                if thinking
                else False
            )
        return info

    def provider_groups(self, entries):
        # Prefer one Responses route for the whole catalog when possible.
        # Chat-only models need a second route in single-protocol clients.
        responses = [
            e
            for e in entries
            if self.protocol("codex", e) == "responses"
            and (e.get("supported_endpoints") or e.get("mode") == "responses")
        ]
        chat = [e for e in entries if e not in responses]
        if not responses:
            return {"vela": ("chat", chat)}
        groups = {"vela": ("responses", responses)}
        if chat:
            groups["vela-chat"] = ("chat", chat)
        return groups

    @classmethod
    def codex_catalog(cls, entries):
        # Codex's native catalog is not the OpenAI /models response format.
        instructions = (Path(__file__).parent / "vendor/codex/prompt.md").read_text(
            encoding="utf-8"
        )
        models = []
        for index, entry in enumerate(entries):
            info = {
                "slug": entry["name"],
                "display_name": entry["name"],
                "description": "VELA",
                "base_instructions": instructions,
                "visibility": "list",
                "supported_in_api": True,
                "priority": index,
                **cls.codex_reasoning(entry),
                "shell_type": "default",
                "support_verbosity": False,
                "supports_reasoning_summary": False,
                "supports_reasoning_summary_parameter": False,
                "truncation_policy": {"mode": "tokens", "limit": 10000},
                "experimental_supported_tools": [],
                "input_modalities": cls.input_modalities(entry),
            }
            context = cls.context_budget(entry)
            if type(context) is int and context > 0:
                info["context_window"] = context
            parallel = supports(entry).get("parallel_tool_calls")
            if type(parallel) is bool:
                info["supports_parallel_tool_calls"] = parallel
            models.append(info)
        return models

    @staticmethod
    def codex_reasoning(entry):
        """Translate advertised efforts without inventing model capabilities."""
        efforts = reasoning_efforts(entry) or []
        result = {
            "supported_reasoning_levels": [
                {"effort": effort, "description": REASONING_DESCRIPTIONS[effort]}
                for effort in efforts
            ]
        }
        if efforts:
            result["default_reasoning_level"] = default_effort(efforts)
        return result

    input_modalities = staticmethod(input_modalities)

    @staticmethod
    def legacy_managed_paths(client, paths, documents):
        """Fields fingerprinted by the previous single-model adapter."""
        if client == "codex":
            keys = [
                ("model",),
                ("model_provider",),
                ("model_context_window",),
                ("model_providers", "vela"),
            ]
            profile = documents[0].get("profile")
            if isinstance(profile, str) and profile:
                keys += [
                    ("profiles", profile, name)
                    for name in ("model", "model_provider", "model_context_window")
                ]
            return {str(paths[0]): [list(k) for k in keys]}
        if client == "kimi":
            keys = [("default_model",), ("providers", "vela"), ("models", "vela")]
            return {str(paths[0]): [list(k) for k in keys]}
        return {
            str(paths[0]): [
                ["llm-pi-ai", "providers", "vela"],
                ["agent-default-model", "provider"],
                ["agent-default-model", "model"],
                ["agent-default-model", "reasoningEffort"],
            ],
            str(paths[1]): [["version"], ["refs", "VELA_API_KEY"]]
            if "version" in documents[1]
            else [["VELA_API_KEY"]],
        }

    def plan(
        self,
        client: str,
        *,
        selected_models=USE_SAVED,
        remove: bool = False,
    ) -> ClientPlan:
        if client not in CLIENTS:
            raise ValueError("Unsupported client.")
        if client == "dsh":
            from dsh_config import plan_dsh

            return plan_dsh(self, selected_models=selected_models, remove=remove)
        from harness_config import plan_harness

        return plan_harness(self, client, selected_models=selected_models, remove=remove)

    def sync_configured(self):
        """Sync only clients with a successful VELA Apply and no external edits."""
        results = []
        for client, name in CLIENTS.items():
            if not (self.state_dir / f"{client}.json").exists():
                continue
            plan = self.plan(client)
            if plan.error or plan.status == "Needs review":
                results.append(f"{name}: Needs review (open /clients)")
            elif plan.status == "Out of date":
                try:
                    self.apply(plan)
                except (OSError, ValueError, RuntimeError):
                    results.append(f"{name}: sync failed (open /clients)")
                else:
                    results.append(f"{name}: models synced")
        return results

    def refresh_plan(self, plan):
        """Explicit Configure refreshes capabilities; previews and background sync do not."""
        if plan.action != "apply" or plan.error:
            raise ValueError(plan.error or "Only Configure refreshes the model catalog.")
        cache_path = self.settings.model_cache_path
        if self.settings_loader:
            self.settings = self.settings_loader()
        if self.settings.model_cache_path != cache_path:
            raise ValueError("VELA configuration location changed. Reopen /clients.")
        try:
            refresh_model_cache(cache_path)
        except httpx.HTTPError as exc:
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            detail = f"HTTP {status}" if status else "network error"
            raise ValueError(
                f"Model catalog refresh failed ({detail}). Client files were not changed. "
                "Check Copilot login/network and retry Configure."
            ) from None
        except (OSError, ValueError, RuntimeError) as exc:
            raise ValueError(
                f"Model catalog refresh failed: {exc}. Client files were not changed."
            ) from None
        if read_bytes(plan.state_path) != plan.state_before or any(
            read_bytes(change.path) != change.before for change in plan.changes
        ):
            raise ValueError("Client configuration changed during refresh. Reopen /clients.")
        fresh = self.plan(plan.client, selected_models=plan.selected_models)
        if fresh.error:
            raise ValueError(fresh.error)
        before = {change.path: change.before for change in plan.changes}
        if fresh.state_before != plan.state_before or any(
            change.path not in before or change.before != before[change.path]
            for change in fresh.changes
        ):
            raise ValueError("Client configuration changed during refresh. Reopen /clients.")
        return fresh

    def apply(self, plan: ClientPlan) -> None:
        if plan.error or (not plan.changes and not (plan.action == "remove" and plan.state_before)):
            raise ValueError(plan.error or "No configuration to apply.")
        with ExitStack() as stack:
            # Lock VELA's transaction state; never leave .lock files alongside
            # DSH's own exclusive-create locks, which use a different protocol.
            stack.enter_context(config_lock(plan.state_path))
            for change in plan.changes:
                if read_bytes(change.path) != change.before:
                    raise ValueError(
                        "Configuration changed after opening this page. Go back and reopen it."
                    )
            if read_bytes(plan.state_path) != plan.state_before:
                raise ValueError("Another VELA window updated this client. Go back and reopen it.")
            fresh = self.plan(
                plan.client, selected_models=plan.selected_models, remove=plan.action == "remove"
            )
            if (
                fresh.error
                or fresh.record != plan.record
                or [c.after for c in fresh.changes] != [c.after for c in plan.changes]
            ):
                raise ValueError("VELA settings changed. Go back and reopen the configuration.")
            backup = self.state_dir / "backups" / f"{plan.client}-{uuid4().hex}"
            changed = [c for c in plan.changes if c.before != c.after]
            if changed or plan.action == "remove" and plan.state_before is not None:
                backup.mkdir(parents=True, mode=0o700)
                manifest = {}
                if plan.action == "remove" and plan.state_before is not None:
                    atomic_write(backup / "state.json", plan.state_before)
                    manifest["state.json"] = str(plan.state_path)
                for index, change in enumerate(changed):
                    manifest[str(index)] = str(change.path)
                    if change.before is not None:
                        # Exact-byte backup, with owner-only permissions on POSIX.
                        target = backup / str(index)
                        with target.open("xb") as stream:
                            os.chmod(target, 0o600)
                            stream.write(change.before)
                atomic_write(backup / "paths.json", json.dumps(manifest, indent=2))
            written = []
            try:
                for change in changed:
                    if read_bytes(change.path) != change.before:
                        raise ValueError(
                            "Client configuration changed during save. Reopen and review it."
                        )
                    if change.after is None:
                        change.path.unlink(missing_ok=True)
                    else:
                        atomic_write(change.path, change.after)
                    written.append(change)
                if plan.action == "remove":
                    plan.state_path.unlink(missing_ok=True)
                else:
                    atomic_write(plan.state_path, json.dumps(plan.record, indent=2) + "\n")
            except Exception:
                # Do not overwrite an external edit made after our write.
                for change in reversed(written):
                    if read_bytes(change.path) == change.after:
                        if change.before is None:
                            change.path.unlink()
                        else:
                            atomic_write(change.path, change.before)
                raise ValueError(
                    "Unable to save configuration. Original files were backed up; reopen to review."
                ) from None


def sync_clients_after_refresh(settings):
    """Catalog refresh succeeds independently of client configuration failures."""
    try:
        return ClientManager(settings).sync_configured()
    except (OSError, ValueError, TypeError, RuntimeError):
        return ["Client sync needs review (open /clients)"]
