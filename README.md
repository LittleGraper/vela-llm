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
