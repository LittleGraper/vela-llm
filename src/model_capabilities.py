"""Shared interpretation of Copilot's advertised model capabilities."""

from collections.abc import Mapping

REASONING_DESCRIPTIONS = {
    "none": "No reasoning",
    "minimal": "Minimal reasoning effort",
    "low": "Low reasoning effort",
    "medium": "Medium reasoning effort",
    "high": "High reasoning effort",
    "xhigh": "Extra high reasoning effort",
    "max": "Maximum reasoning effort",
}


def supports(entry):
    capabilities = entry.get("capabilities")
    value = capabilities.get("supports") if isinstance(capabilities, Mapping) else None
    return value if isinstance(value, Mapping) else {}


def reasoning_efforts(entry):
    """None means unknown; an empty list means explicitly no offered efforts."""
    efforts = supports(entry).get("reasoning_effort")
    if efforts is None:
        return None
    if not isinstance(efforts, list) or any(
        not isinstance(effort, str) or effort not in REASONING_DESCRIPTIONS for effort in efforts
    ):
        raise ValueError(
            f"Invalid or unsupported reasoning efforts for {entry['name']}. "
            "Refresh the model catalog or deselect this model."
        )
    return list(dict.fromkeys(efforts))


def default_effort(efforts):
    return "medium" if "medium" in efforts else efforts[0]


def model_display_name(entry):
    published = entry.get("display_name")
    if isinstance(published, str) and published.strip():
        return published.strip()
    words = [word.capitalize() for word in entry["name"].split("-")]
    if words[0] == "Gpt":
        words[0] = "GPT"
        if len(words) > 1 and words[1][:1].isdigit():
            words[:2] = ["-".join(words[:2])]
    return " ".join(words)


def input_modalities(entry):
    capabilities = entry.get("capabilities")
    if not isinstance(capabilities, Mapping):
        return ["text"]
    modalities = {"text"}
    for key in ("input_modalities", "modalities", "supported_input_modalities"):
        values = capabilities.get(key)
        if isinstance(values, (list, tuple, set)):
            modalities.update(value for value in values if isinstance(value, str))
    if (
        capabilities.get("vision") is True
        or capabilities.get("image_input") is True
        or supports(entry).get("vision") is True
    ):
        modalities.add("image")
    return [modality for modality in ("text", "image") if modality in modalities]


def kimi_capabilities(entry):
    result = ["image_in"] if "image" in input_modalities(entry) else []
    efforts = reasoning_efforts(entry)
    if efforts and any(effort != "none" for effort in efforts):
        result.append("thinking")
        if "none" not in efforts:
            result.append("always_thinking")
    return result
