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
limit with LiteLLM; it does not use a smaller default context window.

VELA uses Copilot API version `2026-08-01` for model discovery and inference,
with a shared VS Code `1.137.0` / Copilot Chat `0.65.0` compatibility profile.
Public GitHub account queries separately use REST API version `2022-11-28`.
`/v1/models` also preserves `capabilities`, `billing`, and `supported_endpoints`,
and exposes `default_context_size` and `context_size_options` when provided by
the catalog. Options use the default billing input threshold and maximum input
capability; the raw long-context billing threshold is preserved separately.
These metadata limits do not prove successful inference at the maximum size.
Restart VELA after upgrading to refresh the model cache and request headers.
