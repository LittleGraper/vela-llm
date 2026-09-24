"""DSH provider catalog management; DSH owns the user's active model selection."""

import copy
import json
from collections.abc import Mapping

from client_config import (
    DELETE,
    USE_SAVED,
    ClientPlan,
    FileChange,
    digest,
    dsh_credential_operations,
    get_value,
    parse,
    read_bytes,
    serialize,
    set_value,
)

PROVIDERS = ("vela", "vela-chat")


def references_vela_key(value):
    if isinstance(value, Mapping):
        return value.get("apiKeyEnv") == "VELA_API_KEY" or any(
            references_vela_key(item) for item in value.values()
        )
    if isinstance(value, list):
        return any(references_vela_key(item) for item in value)
    return False


def projection(documents, paths):
    return {
        path: {json.dumps(keys): get_value(documents.get(path, {}), keys) for keys in fields}
        for path, fields in paths.items()
    }


def plan_dsh(manager, *, selected_models=USE_SAVED, remove=False):
    from cli import local_api_urls

    plan = ClientPlan(
        "dsh", "", manager.state_dir / "dsh.json", action="remove" if remove else "apply"
    )
    try:
        if manager.settings_loader:
            manager.settings = manager.settings_loader()
            if manager.settings.model_cache_path.parent / "clients" != manager.state_dir:
                raise ValueError("VELA configuration location changed. Reopen /clients.")
        plan.endpoint = local_api_urls(manager.settings)[1]
        plan.state_before = read_bytes(plan.state_path)
        saved = parse(plan.state_path, plan.state_before)
        selection = (
            saved.get("selected_models") if selected_models is USE_SAVED else selected_models
        )
        if selection is not None and (
            not isinstance(selection, list) or not all(isinstance(name, str) for name in selection)
        ):
            raise ValueError("Invalid DSH model selection. Reopen the model checklist.")
        plan.selected_models = None if selection is None else sorted(set(selection))
        paths = manager.paths("dsh")
        originals = [read_bytes(path) for path in paths]
        documents = [parse(path, raw) for path, raw in zip(paths, originals, strict=True)]
        settings, credentials = documents
        provider_paths = [("llm-pi-ai", "providers", name) for name in PROVIDERS]
        credential_path = (
            ("refs", "VELA_API_KEY") if "version" in credentials else ("VELA_API_KEY",)
        )
        plan.has_configuration = bool(saved) or any(
            get_value(settings, keys) is not None for keys in provider_paths
        )
        unrelated = copy.deepcopy(settings)
        for keys in provider_paths:
            if get_value(unrelated, keys) is not None:
                set_value(unrelated, keys, DELETE)
        shared_key = references_vela_key(unrelated)
        plan.has_configuration |= (
            get_value(credentials, credential_path) is not None and not shared_key
        )
        entries = manager.catalog("dsh")
        if plan.selected_models is not None:
            entries = [entry for entry in entries if entry["name"] in plan.selected_models]
        plan.catalog_count = len(entries)
        ops = {keys: DELETE for keys in provider_paths}
        if remove:
            credential_ops = {credential_path: DELETE}
            # Clear only a dangling VELA default, never choose another model for DSH.
            default = settings.get("agent-default-model", {})
            if isinstance(default, Mapping) and default.get("provider") in PROVIDERS:
                for name in ("provider", "model", "reasoningEffort"):
                    ops[("agent-default-model", name)] = DELETE
            # A separately configured route may share this reference. Keep it usable.
            if shared_key:
                credential_ops = {}
                plan.summary.append("Shared VELA_API_KEY retained for another provider.")
        else:
            if not entries:
                raise ValueError(
                    "Select at least one available model, or delete the configuration."
                )
            key = manager.settings.local_api_key
            if not key:
                raise ValueError("VELA has no API key. Run /start to initialize its configuration.")
            override = manager.environ.get("VELA_API_KEY")
            if override and override != key:
                raise ValueError(
                    "DSH's VELA_API_KEY override differs from VELA. Remove it before applying."
                )
            credential_ops = dsh_credential_operations(credentials, key)
            for name, (api, models) in manager.provider_groups(entries).items():
                ops[("llm-pi-ai", "providers", name)] = {
                    "displayName": "VELA" if name == "vela" else "VELA (Chat)",
                    "api": "openai-responses" if api == "responses" else "openai-completions",
                    "baseURL": plan.endpoint,
                    "apiKeyEnv": "VELA_API_KEY",
                    "models": [manager.model_info(entry) for entry in models],
                }
        operations = [ops, credential_ops]
        managed_paths = {}
        for path, raw, document, fields in zip(
            paths, originals, documents, operations, strict=True
        ):
            change = FileChange(path, raw, copy.deepcopy(document), fields)
            before = change.projection(document)
            for keys, value in fields.items():
                old = get_value(document, keys)
                if old == (None if value is DELETE else value):
                    continue
                verb = "Remove" if value is DELETE else "Add" if old is None else "Update"
                plan.summary.append(f"{verb} {'.'.join(keys)}")
                set_value(change.document, keys, value)
            after = change.projection(change.document)
            change.after = (
                raw
                if raw is not None and before == after
                else serialize(path, change.document).encode()
            )
            # Deleting absent configuration must not create empty client files.
            if remove and raw is None:
                continue
            plan.changes.append(change)
            managed_paths[str(path)] = [
                list(keys) for keys, value in fields.items() if value is not DELETE
            ]
        current = {str(path): doc for path, doc in zip(paths, documents, strict=True)}
        desired = {str(change.path): change.document for change in plan.changes}
        plan.record = {
            "selected_models": plan.selected_models,
            "schema": "dsh-catalog-v1",
            "managed_paths": managed_paths,
            "managed_digest": digest(projection(desired, managed_paths)),
        }
        prior_paths = saved.get("managed_paths")
        if saved and prior_paths is None:
            prior_paths = manager.legacy_managed_paths("dsh", paths, documents)
        if prior_paths is not None and (
            not isinstance(prior_paths, dict)
            or any(
                not isinstance(fields, list)
                or any(
                    not isinstance(keys, list)
                    or not keys
                    or not all(isinstance(key, str) for key in keys)
                    for keys in fields
                )
                for fields in prior_paths.values()
            )
        ):
            raise ValueError("Invalid VELA DSH configuration record.")
        drift = bool(saved) and saved.get("managed_digest") != digest(
            projection(current, prior_paths)
        )
        if saved and prior_paths is not None:
            drift |= any(
                get_value(doc, keys) is not None
                and list(keys) not in prior_paths.get(str(path), [])
                for path, doc, fields in zip(paths, documents, operations, strict=True)
                for keys in fields
                if keys[:1] != ("agent-default-model",)
            )
        if remove:
            plan.status = "Configured" if plan.has_configuration else "Not configured"
        elif drift:
            plan.status = "Needs review"
        elif not plan.changed:
            plan.status = "Configured"
        elif saved:
            plan.status = "Out of date"
        elif plan.has_configuration:
            plan.status = "Needs review"
        if drift and not remove:
            plan.summary.insert(
                0, "Client settings changed outside VELA. Review before configuring."
            )
        return plan
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        plan.status = "Needs review"
        plan.error = (
            str(exc)
            if isinstance(exc, ValueError)
            else "Unable to read DSH configuration. Check its path and structure."
        )
        return plan
