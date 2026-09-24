"""Catalog adapters for Kimi and Codex behind one configuration workflow."""

import copy
from collections.abc import Mapping

from client_config import (
    DELETE,
    USE_SAVED,
    ClientPlan,
    FileChange,
    digest,
    get_value,
    parse,
    read_bytes,
    serialize,
    set_value,
)
from dsh_config import projection
from model_capabilities import (
    default_effort,
    kimi_capabilities,
    model_display_name,
    reasoning_efforts,
    supports,
)

PROVIDERS = ("vela", "vela-chat")


def codex_operations(manager, plan, entries, documents, saved, remove):
    config, _ = documents
    catalog_path = str(manager.paths("codex")[1])
    restore = copy.deepcopy(saved.get("restore", {}))
    if not isinstance(restore, dict):
        raise ValueError("Invalid Codex restoration record.")
    fields = ("model", "model_provider", "model_context_window", "model_catalog_json")
    if not restore and not remove:
        for name in fields:
            # Legacy adapters did not save pre-VELA startup settings. Never
            # restore their VELA pointers after removing the VELA provider.
            owned = config.get("model_provider") == "vela"
            if name == "model_catalog_json":
                owned = config.get(name) == catalog_path
            restore[name] = {
                "present": name in config and not owned,
                "value": config.get(name) if not owned else None,
            }
    plan.record["restore"] = restore
    if remove:
        ops = {("model_providers", "vela"): DELETE}
        active = config.get("model_provider") == "vela"
        for name in fields:
            should_restore = (
                active
                if name in ("model", "model_provider")
                else config.get(name) == catalog_path
                if name == "model_catalog_json"
                else active and name not in config
            )
            if not should_restore:
                continue
            original = restore.get(name, {})
            if not isinstance(original, dict) or original.get("present") not in (True, False, None):
                raise ValueError("Invalid Codex restoration record.")
            ops[(name,)] = original.get("value") if original.get("present") else DELETE
        # Remove references left by the pre-0.134 profile format without
        # touching unrelated profiles or new named profile files.
        profiles = config.get("profiles", {})
        if isinstance(profiles, Mapping):
            for profile, values in profiles.items():
                if isinstance(values, Mapping) and values.get("model_provider") == "vela":
                    for name in fields:
                        ops[("profiles", profile, name)] = DELETE
        return [ops, {("models",): DELETE}]
    ops = {
        ("model_provider",): "vela",
        ("model_catalog_json",): catalog_path,
        ("model_context_window",): DELETE,
        ("model_providers", "vela"): {
            "name": "VELA",
            "base_url": plan.endpoint,
            "wire_api": "responses",
            "experimental_bearer_token": manager.settings.local_api_key,
            "requires_openai_auth": False,
        },
    }
    names = {entry["name"] for entry in entries}
    if config.get("model") not in names:
        default = manager.settings.default_model
        ops[("model",)] = default if default in names else entries[0]["name"]
    return [ops, {("models",): manager.codex_catalog(entries)}]


def operations(manager, plan, entries, documents, saved, remove):
    client = plan.client
    if client == "codex":
        return codex_operations(manager, plan, entries, documents, saved, remove)
    legacy = manager.kimi_layout()[1]
    existing = documents[0].get("models", {})
    if not isinstance(existing, Mapping):
        raise ValueError("Kimi models must be a mapping.")
    ops = {("providers", name): DELETE for name in PROVIDERS}
    owned = {name for name in existing if name == "vela" or name.startswith("vela/")}
    for name in owned:
        ops[("models", name)] = DELETE
    added = set()
    if not remove:
        for provider, (api, models) in manager.provider_groups(entries).items():
            ops[("providers", provider)] = {
                "type": "openai_responses"
                if api == "responses"
                else ("openai_legacy" if legacy else "openai"),
                "base_url": plan.endpoint,
                "api_key": manager.settings.local_api_key,
            }
            for entry in models:
                name = "vela/" + entry["name"]
                added.add(name)
                ops[("models", name)] = {
                    "provider": provider,
                    "model": entry["name"],
                    "max_context_size": manager.context_budget(entry),
                    "capabilities": kimi_capabilities(entry),
                }
                if not legacy:
                    info = ops[("models", name)]
                    info["display_name"] = model_display_name(entry)
                    info["max_input_size"] = manager.context_budget(entry)
                    output = entry.get("max_output_tokens")
                    if type(output) is int and output > 0:
                        info["max_output_size"] = output
                    if supports(entry).get("tool_calls") is True:
                        info["capabilities"].append("tool_use")
                    efforts = reasoning_efforts(entry)
                    if efforts:
                        enabled = [effort for effort in efforts if effort != "none"]
                        if enabled:
                            info["support_efforts"] = enabled
                            info["default_effort"] = default_effort(enabled)
                        if "none" in efforts:
                            info["off_effort"] = "none"
    default = documents[0].get("default_model")
    if (
        isinstance(default, str)
        and (default == "vela" or default.startswith("vela/"))
        and default not in added
    ):
        previous = existing.get(default, {})
        replacement = (
            "vela/" + str(previous.get("model", "")) if isinstance(previous, Mapping) else ""
        )
        # Preserve the underlying selection when upgrading an old single-model alias.
        ops[("default_model",)] = replacement if replacement in added else "" if legacy else DELETE
    return [ops]


def is_user_selection(client, index, keys):
    return (client == "codex" and index == 0 and keys == ("model",)) or (
        client == "kimi" and keys == ("default_model",)
    )


def plan_harness(manager, client, *, selected_models=USE_SAVED, remove=False):
    from cli import local_api_urls

    plan = ClientPlan(
        client, "", manager.state_dir / f"{client}.json", action="remove" if remove else "apply"
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
            raise ValueError("Invalid model selection. Reopen the checklist.")
        plan.selected_models = None if selection is None else sorted(set(selection))
        paths = manager.paths(client)
        if client == "kimi" and not paths[0].exists() and paths[0].with_suffix(".json").exists():
            raise ValueError("Run Kimi once to migrate config.json, then reopen this page.")
        raw_files = [read_bytes(path) for path in paths]
        documents = [parse(path, raw) for path, raw in zip(paths, raw_files, strict=True)]
        provider_root = "model_providers" if client == "codex" else "providers"
        plan.has_configuration = bool(saved) or any(
            get_value(documents[0], (provider_root, name)) is not None
            for name in (("vela",) if client != "kimi" else PROVIDERS)
        )
        if client == "kimi":
            models = documents[0].get("models", {})
            if not isinstance(models, Mapping):
                raise ValueError("Kimi models must be a mapping.")
            plan.has_configuration |= any(
                name == "vela" or name.startswith("vela/") for name in models
            )
        if client == "codex":
            plan.has_configuration |= raw_files[1] is not None
        entries = manager.catalog(client)
        if plan.selected_models is not None:
            entries = [e for e in entries if e["name"] in plan.selected_models]
        if client == "kimi":
            known = [e for e in entries if manager.context_budget(e) is not None]
            if len(known) != len(entries):
                plan.summary.append("Models without context metadata are omitted from Kimi.")
            entries = known
        plan.catalog_count = len(entries)
        if not remove and not entries:
            raise ValueError("Select at least one available model, or delete the configuration.")
        if not remove and not manager.settings.local_api_key:
            raise ValueError("VELA has no API key. Run /start to initialize its configuration.")
        ops = operations(manager, plan, entries, documents, saved, remove)
        owned_paths = {}
        for index, (path, raw, doc, fields) in enumerate(
            zip(paths, raw_files, documents, ops, strict=True)
        ):
            change = FileChange(path, raw, copy.deepcopy(doc), fields)
            before = change.projection(doc)
            for keys, value in fields.items():
                old = get_value(doc, keys)
                if old == (None if value is DELETE else value):
                    continue
                plan.summary.append(f"{'Remove' if value is DELETE else 'Update'} {'.'.join(keys)}")
                set_value(change.document, keys, value)
            after = change.projection(change.document)
            change.after = (
                raw
                if raw is not None and before == after
                else serialize(path, change.document).encode()
            )
            if remove and client == "codex" and index == 1 and not change.document:
                change.after = None
            if raw is None and (remove or not fields):
                continue
            plan.changes.append(change)
            owned_paths[str(path)] = [
                list(keys)
                for keys, value in fields.items()
                if value is not DELETE and not is_user_selection(client, index, keys)
            ]
        current = {str(p): doc for p, doc in zip(paths, documents, strict=True)}
        desired = {str(c.path): c.document for c in plan.changes}
        plan.record.update(
            selected_models=plan.selected_models,
            schema="catalog-v1",
            managed_paths=owned_paths,
            managed_digest=digest(projection(desired, owned_paths)),
        )
        prior = saved.get("managed_paths")
        if saved and prior is None:
            prior = manager.legacy_managed_paths(client, paths, documents)
        if prior is not None and (
            not isinstance(prior, dict)
            or any(
                not isinstance(fields, list)
                or any(
                    not isinstance(keys, list)
                    or not keys
                    or not all(isinstance(key, str) for key in keys)
                    for keys in fields
                )
                for fields in prior.values()
            )
        ):
            raise ValueError("Invalid VELA client configuration record.")
        if client == "kimi" and prior and set(prior) != {str(path) for path in paths}:
            plan.summary.insert(
                0,
                "Kimi configuration location changed. Configure the displayed file; "
                "the previous file is left untouched.",
            )
        drift = bool(saved) and saved.get("managed_digest") != digest(projection(current, prior))
        if prior is not None:
            drift |= any(
                get_value(doc, keys) is not None and list(keys) not in prior.get(str(path), [])
                for index, (path, doc, fields) in enumerate(zip(paths, documents, ops, strict=True))
                for keys in fields
                if not is_user_selection(client, index, keys)
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
        return plan
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        plan.status = "Needs review"
        plan.error = (
            str(exc)
            if isinstance(exc, ValueError)
            else "Unable to read client configuration. Check its path and structure."
        )
        return plan
