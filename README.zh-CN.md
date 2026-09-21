# VELA

[![PyPI](https://img.shields.io/pypi/v/vela-llm.svg)](https://pypi.org/project/vela-llm/)
[![English](https://img.shields.io/badge/lang-English-blue.svg)](https://github.com/LittleGraper/vela-llm/blob/develop/README.md)

面向 GitHub Copilot 模型的本地 LiteLLM 代理，提供 OpenAI 和 Anthropic 兼容接口。默认监听 `127.0.0.1`。

## 安装

需要 Python 3.11+ 和 [uv](https://docs.astral.sh/uv/)：

```bash
uv tool install vela-llm
```

## 快速入门

```bash
vl
```

进入工作台后，依次执行 `/login` 登录、`/start` 启动代理。通过 `/api` 查看连接信息，`/models` 选择默认模型和配置上下文。

也可以直接使用命令：

```bash
vl login           # 登录 GitHub Copilot
vl start           # 启动代理
vl api --show-key  # 查看完整 API Key 和 Base URL
vl models          # 管理模型和上下文
vl stop            # 停止代理
vl update          # 更新到最新发布版本
```

更多选项见 `vl help` 或 `vl <command> --help`。
