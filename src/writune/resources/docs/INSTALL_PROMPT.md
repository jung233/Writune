# Writune 自动安装 Prompt

将本文件交给具有终端能力的 Codex、Claude Code 或 OpenCode，并说：

> 请按这份 Prompt 从 PyPI 安装 Writune，完成配置、MCP 注册、Skill 安装和验收。

## 执行目标

在当前用户的专用 Python 环境中安装 Writune 0.1.2 或更高版本。从 PyPI 安装；后续配置全部通过包内的 CLI 完成，无需源码仓库或项目安装脚本。最后报告环境位置、配置目录、客户端、模型和验收结果，并隐藏所有凭据。

## 先确定必要配置

只询问尚未提供的信息：客户端（`codex`、`claude-code`、`opencode`，可提供多个）、新环境路径、Writune 配置目录、Responses API Base URL、API Key 的安全输入方式、中英文首选模型、可选备用模型以及授权工作区。模型名称应使用上游的真实 ID，不要猜测名称。可复用明确指定的现有 Writune 配置；读取时不要输出密钥。

推荐并发 4，首文本超时 25 秒，总超时 120 秒，重试 1 次，任务 TTL 24 小时。若用户已有选择，则按该选择设置。说明：真实 Provider 验证会向所选服务发送合成短文本，并产生少量使用量。

## 1. 创建环境并从 PyPI 安装

先以只读方式核对已有 Writune 的 MCP 命令、解释器和包路径；确认它是专用的独立环境后，在原环境中升级。否则，以只读方式使用已有 Python 3.11+ 创建新的 Writune 专用环境。其他已有目录保持原样，另选新路径。不修改其他 Python/Conda 环境，不永久修改 PATH、注册表或 shell 配置，不使用管理员权限或系统级 pip。没有合适解释器时，先获取用户允许的 Python 来源和位置，不自动升级系统 Python。

Windows 示例（将目录替换为实际新路径）：

```powershell
python -I -m venv C:/Users/YOU/Tools/writune-env
& C:/Users/YOU/Tools/writune-env/Scripts/python.exe -I -m pip --isolated install --upgrade "writune>=0.1.2"
```

macOS/Linux 示例：

```sh
python3 -I -m venv /home/YOU/tools/writune-env
/home/YOU/tools/writune-env/bin/python -I -m pip --isolated install --upgrade 'writune>=0.1.2'
```

后续命令中的 `<PYTHON>` 均指新环境的绝对 Python 路径，`<CONFIG_HOME>` 指所选配置目录；不要原样执行占位符。确认 `sys.prefix != sys.base_prefix`，并记录实际解释器的位置。

## 2. 用同一套 CLI 完成配置

交互式配置可运行 `<PYTHON> -m writune init --home <CONFIG_HOME>`，由用户在隐藏字段中输入 Key。

自动配置时，在系统临时目录写入不含 Key 的 JSON，示例结构为：

```json
{
  "providers": {"primary": {"base_url": "https://provider.example/v1"}},
  "zh_primary": "primary/ACTUAL_CHINESE_MODEL_ID",
  "en_primary": "primary/ACTUAL_ENGLISH_MODEL_ID",
  "zh_secondary": "",
  "en_secondary": "",
  "allowed_workspaces": ["ABSOLUTE_WORKSPACE_PATH"],
  "auto_update": true,
  "max_concurrency": 4,
  "first_token_timeout": 25,
  "total_timeout": 120,
  "retry_count": 1
}
```

通过安全的进程环境注入 `WRITUNE_PROVIDER_PRIMARY_API_KEY`，或由用户预先在配置目录的私有 `.env` 中写入同名变量。不要把 Key 写入命令参数、聊天回显、临时 JSON 或 MCP 配置。运行：

```text
<PYTHON> -m writune init --non-interactive --config-file <TEMP_JSON> --home <CONFIG_HOME>
<PYTHON> -m writune mcp register --client codex --home <CONFIG_HOME> --install-skill
<PYTHON> -m writune doctor --home <CONFIG_HOME>
```

按所选客户端替换 `--client`，多个客户端分别执行注册。CLI 自动选择当前环境的解释器；Windows 使用 `pythonw.exe`，MCP 和 worker 无窗口启动，不通过 shell 包装器运行。注册保留其他设置和 Codex TOML 注释，并备份已有配置。遇到已有不同的 Writune 条目时，先核对它，只有用户已授权替换该条目时才加 `--replace`。定制 Skill 文件冲突时保留原文件。

## 3. 验收与交付

运行 `<PYTHON> -m writune config show --home <CONFIG_HOME>` 检查脱敏后的配置，再运行 `<PYTHON> -m writune test-provider --home <CONFIG_HOME>` 验证上游。保留配置中的现有密钥，不要在输出中显示这些密钥。清理自行生成的临时配置文件，不要删除用户文件。

提示用户重新连接客户端 MCP，确认可发现 `writune_submit`、`writune_status`、`writune_result` 等工具。应分别报告 CLI 的 stdio 自检结果与客户端实际发现结果，不能用其中一项冒充另一项。报告所有失败；以原文兜底不算润色成功。

自动更新由包本身通过 PyPI HTTPS 接口检查正式稳定版本并校验下载哈希，在任务完成后复用原目录更新，不调用 Git 或 pip。当依赖、Python 要求或入口发生变化时，需要在同一环境中执行完整的 pip 升级。首次从 0.1.1 升级到 0.1.2 时，也请使用 pip，以安装新版依赖和资源布局。`writune update-status` 显示更新状态。

如需采用完全手动编辑文件的方式安装，请参阅 [中文指南](MANUAL_INSTALL.zh-CN.md) 或 [English guide](MANUAL_INSTALL.md)。
