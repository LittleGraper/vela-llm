# vela-llm

[![PyPI](https://img.shields.io/pypi/v/vela-llm.svg)](https://pypi.org/project/vela-llm/)
[![English](https://img.shields.io/badge/lang-English-blue.svg)](https://github.com/LittleGraper/vela-llm/blob/main/README.md)

`vela-llm` 是一个面向 GitHub Copilot Models 的本地 LiteLLM 代理，提供 OpenAI 和 Anthropic 兼容接口。服务默认仅监听 `127.0.0.1`。

## 安装

需要 Python 3.11+ 和 [uv](https://docs.astral.sh/uv/)：

```bash
uv tool install vela-llm
```

## 快速入门

```bash
# 登录 GitHub Copilot
vl login

# 启动本地代理
vl start

# 查看 API Key 和 Base URL（默认隐藏完整密钥）
vl api --show-key

# 更新到 PyPI 最新稳定版
vl update
```

每次执行 `vl start` 都会随机展示四款静态 VELA 字标中的一款。支持颜色的终端使用青蓝配色，
重定向输出或设置 `NO_COLOR` 时使用纯文本，窄终端则显示紧凑字标。

每次执行 `vl start` 都会重新获取 GitHub Copilot 模型目录，并将公开能力信息缓存到
vela-llm 配置目录下的 `models-cache.json`。代理会把 Copilot 返回的最大上下文窗口、
输入上限和输出上限注册到 LiteLLM，不使用较小的默认上下文窗口。

模型查询和推理统一使用 Copilot API `2026-08-01`，兼容客户端标识为 VS Code
`1.137.0` / Copilot Chat `0.65.0`。GitHub 账号查询独立使用 REST API `2022-11-28`。
`/v1/models` 保留 `capabilities`、`billing` 和 `supported_endpoints`，并在目录提供
相应信息时返回 `default_context_size` 和 `context_size_options`。选项取默认计费输入
阈值和最大输入能力；长上下文计费阈值单独保留在原始 `billing` 中，不混作能力上限。
元数据上限不代表已实测最大长度推理。升级后需重启 VELA，刷新模型缓存和请求头。
