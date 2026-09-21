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
输入上限和输出上限注册到 LiteLLM，并按每模型设置解析有效上下文额度。

模型查询和推理统一使用 Copilot API `2026-08-01`，兼容客户端标识为 VS Code
`1.137.0` / Copilot Chat `0.65.0`。GitHub 账号查询独立使用 REST API `2022-11-28`。
`/v1/models` 保留 `capabilities`、`billing` 和 `supported_endpoints`，并在目录提供
相应信息时返回 `default_context_size` 和 `context_size_options`。选项取默认计费输入
阈值和最大输入能力；长上下文计费阈值单独保留在原始 `billing` 中，不混作能力上限。
元数据上限不代表已实测最大长度推理。升级后需重启 VELA，刷新模型缓存和请求头。

## CLI 展示

在终端直接运行 `vl` 进入常驻 Textual 工作台：顶部固定显示随机选定的一款 Banner、
代理状态、地址和默认模型，底部固定命令输入框，中间仅显示当前页面或当前命令结果，
不累积历史页面。小窗口自动使用紧凑 Banner，退出后恢复终端。

输入 `/` 查看建议，↑↓ 选择，Tab 补全，Enter 执行；支持 `/models`、`/start`、
`/stop`、`/api`、`/test`、`/login`、`/whoami`、`/logout`、`/update`、`/about` 等。
VELA 分类包含 `/update` 和 `/about`；关于页面显示项目简介、版本和项目链接。
命令可带原有参数，例如 `/test --model gpt-4.1`、`/api --show-key`。
在列表或配置页按 `/`，直接聚焦输入框并开始输入命令；输入框内的 `/` 保持正常输入。
`/api` 页面显示两种接口地址及共用的 API Key，默认脱敏。点击 Show key 或在命令输入框外
按 K 显示或隐藏完整 Key；离开页面后恢复脱敏。
Esc 返回上一级，命令结果页返回首页。
仅在首页，3 秒内连续按两次 Esc 退出：第一次在输入框下方显示提示，页面和焦点保持不变。
按其他键、点击、粘贴或等待超时会取消确认。Ctrl+C 会返回首页显示该提示，再按 Esc 确认。
通过上述确认正常退出工作台不会停止后台代理，停服请用 `/stop`。
在 Windows 上，工作台运行期间直接关闭终端窗口，或强制结束工作台进程，
会自动终止该工作台启动的命令和代理进程。其他工作台或独立 `vl start` 启动的代理不受影响。
关闭终端自动清理目前仅支持 Windows；其他系统请显式使用 `/stop`。
退出工作台会取消当前执行中的命令；
同一时间只执行一条外部命令，登录验证码会在等待授权时实时显示。
工作台内 `/start` 启动后台代理；前台服务仍使用终端命令 `vl start --foreground`。

`vl help` 及所有子命令的 `--help` 使用统一的 Rich 分组帮助页。
无参数 `vl` 在非交互或重定向环境中仍打印帮助。
API 地址、账户信息、设备登录指引、启动和进程信息、更新指引、模型测试结果
采用一致的配色与对齐排版。耗时检查在终端中显示临时进度提示；重定向输出时
不包含动画或 ANSI 控制序列。API Key 默认脱敏，可在 `/api` 页面主动显示，或使用 `vl api --show-key`。
预期的命令失败会显示简短错误并以非零状态退出。
单独执行 `vl start`、`vl api` 等仍保持普通 CLI 输出，便于脚本使用。

## 每个模型的上下文配置

运行 `uv run vl models`（已安装版本使用 `vl models`）进入模型管理。
上下键选择，Enter 编辑，D 设置默认模型，R 刷新目录，P 只读预览已保存的 `models.toml`
（显示实际路径、语法高亮和行号）。不再提供独立的 `/refresh` 命令。Esc 返回上一级，
仅在首页连续按两次 Esc 退出工作台。移动光标不会保存。

交互菜单由 Textual 管理布局和重绘：模型表格整行高亮，列自动对齐，支持滚动和窗口缩放。
存在不可用模型时，Tab 可切换到 **Unavailable models** 按钮。
`vl models` 直接打开同一个全屏工作台的模型页；`--fullscreen` 参数保留兼容。
启动摘要、模型测试进度和结果、非交互模型列表使用 Rich；重定向输出时保留纯文本，
不输出终端控制序列。

- **Auto**：跟随上游默认上下文额度，Enter 保存并返回列表。
- **Maximum**：跟随上游最大上下文额度，Enter 保存并返回列表。
- **Custom**：进入可选值列表，上下键选择一个具体额度，Enter 保存。
  固定保存准确 token 数，不是多选，也不接受任意数字。

上下文选项沿用 Copilot 的输入上下文额度语义，不等于输入加输出的总窗口。
菜单不提供独立的最大输入、最大输出设置。缺失的元数据显示 `Unknown`；
Maximum 未知时不能保存，Custom 无选项时显示说明，不生成虚构档位。
存在长上下文计费信息时显示费用提示。

### 获取与生效时机

| 触发 | 行为 |
| --- | --- |
| `vl start` | 启动前刷新一次完整目录 |
| 在终端打开 `vl models` | 先显示缓存，后台刷新一次；Esc 无需等待网络 |
| 主菜单 R | 手动刷新；已有刷新进行中时不重复请求 |
| 进入子页面或切换选项 | 使用页面快照，不改变正在编辑的选项 |
| Enter 保存 | 对最新缓存再次校验，然后原子保存配置 |
| 后续模型查询或推理 | 重新读取本地目录与偏好，不远程获取目录，无需重启 |
| 输出重定向／非交互终端 | 仅打印缓存快照，不发起刷新或登录 |

`/v1/models` 保留原始上游上限，额外提供 `context_mode`、
`configured_context_size`、`context_size`（有效值）和 `context_status`。
状态为 `ok`、`unknown` 或 `needs_review`。后续推理将有效额度注册为
LiteLLM 的 `max_input_tokens`，总窗口和输出上限保留原始值。
本功能不新增长度拦截、不裁剪历史、不修改请求输出参数，也不能扩大上游权限。
客户端若缓存模型信息需要重新获取；仅识别标准字段的客户端不一定使用这些扩展字段。

### 保存、更新与删除

沿用现有配置目录：Windows 默认 `%APPDATA%\vela-llm`，可通过
`VELA_LLM_CONFIG_DIR` 覆盖。`VELA_LLM_MODELS_CONFIG` 指定其他配置文件时，
目录缓存放在该文件旁边。

- `models-cache.json` 保存最近成功获取的目录及 `fetched_at` 时间。
  失败、无效响应、空目录都保留旧缓存和用户偏好。刷新不启动交互登录；需要时先运行 `vl login`。
- `models.toml` 按准确模型 ID 保存用户偏好，保留其他模型、无关配置与注释。
  并发 CLI 保存使用文件锁串行合并，原子替换避免半写文件。

```toml
[models]
default = "model-a"

[models.context."model-a"]
mode = "custom"
size = 272000

[models.context."model-b"]
mode = "maximum"
```

Auto 不保存单独配置块；选择 Auto 等同恢复默认并删除该模型的上下文覆盖。
从 Custom 切到 Maximum 会移除旧数值。成功刷新后，Auto/Maximum 跟随新目录；
Custom 若不再可选，显示 `Needs review`，保留原值并暂用 Auto；Auto 未知时有效值为 `Unknown`。

模型消失后移到 `Unavailable models`，不自动删除配置；模型恢复时重新校验。
在不可用模型详情中按 Enter 执行 `Delete saved context` 才删除上下文偏好。
这不会修改默认模型：默认模型不可用时提示重新选择，未指定模型的推理返回 404，
不擅自换模型。显式指定的模型仍按原有方式透传给上游。
