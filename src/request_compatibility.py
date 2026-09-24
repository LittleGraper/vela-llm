"""Request-scoped compatibility for client defaults and resumed conversations."""

import logging
from collections.abc import Mapping

from model_capabilities import default_effort, input_modalities, reasoning_efforts, supports

logger = logging.getLogger(__name__)


class CompatibilityError(ValueError):
    status_code = 400
    code = "unsupported_model_parameter"


def normalize_request(body, registry, endpoint):
    """Preserve conversation content; only repair incompatible reasoning selections."""
    entry = next(
        (item for item in registry if body.get("model") in (item["name"], item["upstream"])), None
    )
    if entry is None:
        return {}
    endpoints = entry.get("supported_endpoints")
    if endpoints and endpoint not in {value.removeprefix("/v1") for value in endpoints}:
        raise CompatibilityError(
            f"{entry['name']} does not support {endpoint}. Refresh and reconfigure the client."
        )
    if supports(entry).get("vision") is False and "image" not in input_modalities(entry):
        messages = body.get("input", body.get("messages", []))
        if isinstance(messages, list) and any(
            isinstance(message, Mapping)
            and isinstance(message.get("content"), list)
            and any(
                isinstance(part, Mapping) and part.get("type") in ("input_image", "image_url")
                for part in message["content"]
            )
            for message in messages
        ):
            raise CompatibilityError(
                f"{entry['name']} cannot accept images in this conversation. "
                "Choose an image-capable model or start a text-only conversation."
            )
    try:
        efforts = reasoning_efforts(entry)
    except ValueError as exc:
        raise CompatibilityError(str(exc)) from None
    if efforts is None:
        return {}
    if endpoint == "/responses":
        reasoning = body.get("reasoning")
        if reasoning is None:
            reasoning = {}
        if not isinstance(reasoning, dict):
            raise CompatibilityError("reasoning must be an object with an effort field.")
        supplied = reasoning.get("effort")
    else:
        reasoning = None
        supplied = body.get("reasoning_effort")
    if supplied is not None and not isinstance(supplied, str):
        raise CompatibilityError("Reasoning effort must be a string.")
    if supplied in efforts:
        return {}
    # An omitted effort is filled for reasoning models so downstream defaults
    # cannot silently become an unsupported 'none'.
    replacement = default_effort(efforts) if efforts else None
    if reasoning is not None:
        if replacement is None:
            reasoning.pop("effort", None)
        else:
            reasoning["effort"] = replacement
        if reasoning:
            body["reasoning"] = reasoning
        else:
            body.pop("reasoning", None)
    elif replacement is None:
        body.pop("reasoning_effort", None)
    else:
        body["reasoning_effort"] = replacement
    if supplied is None:
        return {}
    logger.warning(
        "Adjusted unsupported reasoning effort for %s to %s; update the client's selection.",
        entry["name"],
        replacement or "omitted",
    )
    return {"X-VELA-Reasoning-Effort": replacement or "omitted"}
