"""Separate local configuration state from transport and inference checks."""

from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class ClientCheck:
    ok: bool
    message: str


def check_client(manager, client, *, inference=False):
    plan = manager.plan(client)
    if plan.error or plan.status != "Configured":
        return ClientCheck(False, "Configure this client before checking its connection.")
    entries = manager.catalog(client)
    if plan.selected_models is not None:
        entries = [entry for entry in entries if entry["name"] in plan.selected_models]
    if not entries:
        return ClientCheck(False, "No configured models are available.")
    try:
        with httpx.Client(
            base_url=plan.endpoint.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {manager.settings.local_api_key}"},
            timeout=60 if inference else 5,
            trust_env=False,
        ) as transport:
            response = transport.get("models")
            response.raise_for_status()
            payload = response.json()
            models = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(models, list):
                return ClientCheck(False, "Proxy returned an invalid model catalog.")
            available = {item.get("id") for item in models if isinstance(item, dict)}
            if any(entry["name"] not in available for entry in entries):
                return ClientCheck(False, "Proxy model catalog differs. Refresh and reconfigure.")
            if not inference:
                return ClientCheck(True, "Proxy and API key reachable; inference not tested.")
            entry = next(
                (entry for entry in entries if entry["name"] == manager.settings.default_model),
                entries[0],
            )
            protocol = manager.protocol(client, entry)
            if client in ("dsh", "kimi"):
                protocol = next(iter(manager.provider_groups([entry]).values()))[0]
            body = {"model": entry["name"], "stream": False}
            if protocol == "responses":
                body.update(input="Reply with exactly OK.", max_output_tokens=1024)
                route = "responses"
            else:
                body.update(
                    messages=[{"role": "user", "content": "Reply with exactly OK."}],
                    max_tokens=1024,
                )
                route = "chat/completions"
            response = transport.post(route, json=body)
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict) or result.get("error"):
                return ClientCheck(False, "Inference returned an error or invalid response.")
            if protocol == "responses":
                output = result.get("output")
                text = [
                    part.get("text")
                    for message in (output if isinstance(output, list) else [])
                    if isinstance(message, dict) and message.get("type") == "message"
                    for part in message.get("content", [])
                    if isinstance(part, dict) and part.get("type") == "output_text"
                ]
                complete = result.get("status") == "completed" and any(text)
            else:
                choices = result.get("choices")
                first = choices[0] if isinstance(choices, list) and choices else {}
                complete = (
                    isinstance(first, dict)
                    and first.get("finish_reason") == "stop"
                    and isinstance(first.get("message"), dict)
                    and first["message"].get("content")
                )
            if not complete:
                return ClientCheck(False, "Inference did not return a completed text answer.")
            return ClientCheck(
                True,
                f"Text request passed for {entry['name']}; images, tools and old sessions "
                "are not certified by this check.",
            )
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in (401, 403):
            message = "Authentication failed. Check the VELA API key and Copilot login."
        elif status == 429:
            message = "Provider rate/quota limit reached. Retry later."
        else:
            message = f"Connection check failed (HTTP {status}). Check the VELA proxy log."
        return ClientCheck(False, message)
    except httpx.HTTPError:
        return ClientCheck(False, "Proxy unreachable or timed out. Run /start and retry.")
    except (ValueError, TypeError):
        return ClientCheck(False, "Proxy returned a malformed response. Check the VELA proxy log.")
