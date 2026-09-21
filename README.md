# VELA

[![PyPI](https://img.shields.io/pypi/v/vela-llm.svg)](https://pypi.org/project/vela-llm/)
[![中文](https://img.shields.io/badge/lang-%E4%B8%AD%E6%96%87-red.svg)](https://github.com/LittleGraper/vela-llm/blob/develop/README.zh-CN.md)

A local LiteLLM proxy for GitHub Copilot models, with OpenAI- and Anthropic-compatible APIs. Listens on `127.0.0.1` by default.

## Installation

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/):

```bash
uv tool install vela-llm
```

## Quick start

```bash
vl
```

In the workspace, run `/login`, then `/start`. Use `/api` to view connection settings and `/models` to select the default model and configure its context.

You can also run commands directly:

```bash
vl login           # Authenticate with GitHub Copilot
vl start           # Start the proxy
vl api --show-key  # Show the API key and base URLs
vl models          # Manage models and context settings
vl stop            # Stop the proxy
vl update          # Update to the latest release
```

Use `vl help` or `vl <command> --help` for more options.
