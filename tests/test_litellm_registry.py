from __future__ import annotations

from litellm_registry import register_litellm_model_metadata


def test_registers_copilot_context_limits() -> None:
    class FakeLiteLLM:
        registered: list[dict[str, dict[str, object]]] = []

        @classmethod
        def register_model(cls, model_cost: dict[str, dict[str, object]]) -> None:
            cls.registered.append(model_cost)

    register_litellm_model_metadata(
        FakeLiteLLM,
        [
            {
                "name": "gpt-test",
                "upstream": "github_copilot/gpt-test",
                "mode": "responses",
                "max_tokens": 400000,
                "max_input_tokens": 272000,
                "max_output_tokens": 128000,
            }
        ],
    )

    assert FakeLiteLLM.registered == [
        {
            "github_copilot/gpt-test": {
                "litellm_provider": "github_copilot",
                "mode": "responses",
                "max_tokens": 400000,
                "max_input_tokens": 272000,
                "max_output_tokens": 128000,
            }
        }
    ]
