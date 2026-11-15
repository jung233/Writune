# Writune 一键安装 Prompt

将本文件交给 Codex、Claude Code 或其他具有终端与文件编辑能力的 coding harness，并说：

> 请执行这份安装 Prompt，完成 Writune 的独立安装、配置、MCP 注册与实际验收。

下面是交给 harness 的完整任务。用户已经授权按本文安装 Writune、编辑所选客户端的 Writune MCP 条目，以及安装配套 Skill。请直接执行；只询问确实缺失的配置，不要反复确认已明确的选择。

## 目标与交付

在当前用户账户下安装 Writune，使所选客户端能够调用它进行文本润色。完成环境准备、依赖安装、Provider 配置、MCP 注册、Skill 安装和端到端验证。最终报告给出环境路径、配置路径、启用的客户端、模型 ID、测试结果和临时输出位置，隐藏全部 API Key。

## 必须保持的环境隔离

- 不安装、卸载、升级或降级任何现有 Python 环境中的包，包括系统 Python、用户 site-packages、Conda、其他项目虚拟环境和当前激活环境。
- 不替换现有解释器，不运行系统级 `pip install`、`pip install --user`、`sudo pip`、`conda install` 或 `uv pip install --system`。不要调用 `uv sync` 去接管已有项目环境。
- 不修改系统或用户 PATH、Python 文件关联、注册表、shell profile、全局 pip/uv/Conda 配置。不要使用管理员权限。
- Windows 自动执行必须无窗口：直接调用专用环境的 `pythonw.exe` 运行后台任务；需要控制台程序时设置 `CREATE_NO_WINDOW`、重定向三条标准流。不要通过会新开窗口的 PowerShell 工具、`start`、可见终端或 shell 包装器启动 MCP 和 Worker。配置注册通过文件编辑或 CLI 完成，不操作 ChatGPT/Codex App 界面。
- 新建 Writune 专用的 `.writune-venv`，或用户选定的全新绝对路径。现有目录没有匹配的 `.writune-owner` 标识时，选择新目录，保留原目录。
- 可以只读调用已有 Python 3.11+ 的 `-I -m venv` 来创建新环境；包安装只能通过新环境解释器或显式指定该环境的 uv 命令执行。
- 缺少合适 Python 时，使用 Writune 私有目录中的 standalone uv 和 managed Python。uv 二进制只下载到安装目录，Python 放在 `.writune-runtime/python`，依赖缓存放系统临时目录。下载来源使用 [uv 官方发布页](https://github.com/astral-sh/uv/releases)，核对平台、架构与官方校验值；不要运行会修改 PATH 的系统安装器。
- uv 分支使用 `UV_PYTHON_INSTALL_REGISTRY=false`、`UV_PYTHON_NO_REGISTRY=true`、`UV_PYTHON_INSTALL_BIN=false`、`UV_NO_MODIFY_PATH=true`，并按安装脚本设置私有目录。使用支持这些选项的 uv 版本；若当前版本不支持，下载独立新版到 Writune 目录，不升级已有 uv。
- 所有原文快照、润色结果、默认 apply 副本、合并预览、安装诊断与润色测试文件使用系统临时目录。最终用户文档按明确指定的路径保存。Writune 配置、解释器和队列数据库保存在各自独立位置。

## 1. 发现源码与已有配置

1. 判断操作系统、CPU 架构、当前项目目录和可用终端。
2. 如果本文件位于完整 Writune 源码目录，使用该目录；否则请用户提供真实仓库 URL、源码压缩包路径或本地源码路径。本文件不预设仓库发布地址，不要猜测 URL。将源码放到用户可写的专用目录。
3. 检查 `pyproject.toml`、`src/writune`、`scripts/install.ps1` 或 `scripts/install.sh`、`scripts/register_mcp.py` 是否齐备。
4. 读取所选配置中与 Writune 有关的非敏感字段，并识别已有安装。不要输出整个客户端配置或 `.env`。保留其他 MCP、模型、快捷键、项目和插件配置。

## 2. 一次收集必要配置并提供指导

汇总以下缺失信息，一次询问用户；已提供或可可靠读取的字段直接复用：

| 项目 | 询问或指导 |
| --- | --- |
| 启用客户端 | Codex、Claude Code，或两者；其他客户端按其官方 MCP 文档另行注册 |
| 安装目录 | 推荐当前 Writune 源码目录；环境默认新建 `.writune-venv` |
| Writune 配置目录 | 默认平台用户配置目录；也可选独立绝对路径，并在所有 MCP 启动参数中使用同一 `--home` |
| 上游地址 | 完整 Responses API Base URL，包含实际端口和 `/v1` 等路径；按服务商文档填写 |
| API Key | 通过 harness 的安全输入、密钥注入或用户直接写入 Writune 私有 `.env`；不放入聊天回显、命令参数或客户端 MCP 配置 |
| 中英文模型 | 精确的中文首选、英文首选模型 ID；允许两者相同，使用服务商实际支持的名称 |
| 备用模型 | 可选，可与首选共用 Provider，也可使用独立 URL/Key；没有则保存空字符串 |
| 授权工作区 | 用户要读写的项目目录绝对路径列表；只授权实际需要的目录 |
| 风格与性能 | 建议 `natural`、并发 4、首文本超时 25 秒、总超时 120 秒、重试 1 次、临时任务 TTL 24 小时；按用户选择调整 |

说明 `provider/model` 是 Writune 的模型引用：左侧是本地 Provider ID，右侧是上游原始模型 ID，即使右侧还含 `/` 也须完整保留。润色请求会发送给用户选择的 Provider，合成短文本测试使用该 Provider 的 API。

## 3. 创建专用环境并安装

优先使用仓库脚本，不激活任何环境，不改已有 `.venv`。

Windows，已有 Python 3.11+：

```powershell
.\scripts\install.ps1
# 可显式指定只读使用的解释器和新环境位置：
.\scripts\install.ps1 -PythonExecutable 'C:/path/to/python.exe' -VenvPath 'C:/path/to/new-writune-env'
```

macOS / Linux：

```bash
bash scripts/install.sh
# 可显式指定解释器和全新环境绝对路径：
bash scripts/install.sh --python /path/to/python3 --venv /path/to/new-writune-env
```

需要私有 managed Python 时，让 standalone uv 仅出现在本次安装进程的 PATH 中，再执行 Windows 的 `-UseUv` 或 Bash 的 `--uv` 分支。不要永久修改 PATH。

验证专用环境的 `sys.executable`、`sys.prefix`、`sys.base_prefix`，确保 `sys.prefix != sys.base_prefix`；验证可以导入 `writune`。记录安装前后已有环境的解释器位置和包版本清单，确认没有改变。

## 4. 保存 Writune 配置

所有命令都使用刚创建的专用环境的绝对解释器路径。交互式安装可让用户在 `python -m writune init --home <CONFIG_HOME>` 的隐藏字段中输入 Key。

harness 自动配置时，在系统临时目录生成不含 Key 的 JSON，再运行：

```text
<VENV_PYTHON> -m writune init --non-interactive --config-file <TEMP_CONFIG_JSON> --home <CONFIG_HOME>
```

将 `<VENV_PYTHON>` 等占位符替换为实际路径，不要原样执行。JSON 结构示例：

```json
{
  "providers": {
    "primary": {
      "base_url": "https://provider.example/v1",
      "capabilities": {},
      "parameters": {}
    }
  },
  "zh_primary": "primary/ACTUAL_CHINESE_MODEL_ID",
  "en_primary": "primary/ACTUAL_ENGLISH_MODEL_ID",
  "zh_secondary": "",
  "en_secondary": "",
  "allowed_workspaces": ["/absolute/user-selected/project"],
  "default_style": "natural",
  "max_concurrency": 4,
  "first_token_timeout": 25,
  "total_timeout": 120,
  "retry_count": 1,
  "task_ttl_hours": 24
}
```

用 `WRITUNE_PROVIDER_PRIMARY_API_KEY` 安全注入 Key，或预先将该变量写入 `<CONFIG_HOME>/.env`。有备用 Provider 时，为其添加相同结构和对应的 Key 环境变量。只将服务商已确认支持的可选参数标为 `capabilities=true`。初始化会验证首选与备用引用，随后将设置和凭据分别保存到 `config.json`、`.env`。

`data_dir` 是队列与统计目录。`Settings.task_dir` 自动定位系统临时目录；不要再把临时结果改到项目目录。临时文件被操作系统清理后，重新提交源文即可。

## 5. 自动注册 MCP 与 Skill

在源码目录执行 `scripts/register_mcp.py`，用绝对路径替换占位符。脚本会解析并合并配置，仅修改 Writune 条目；Codex TOML 的其他配置和注释会保留，修改前备份到系统临时目录，重复运行相同配置不再写入。

Codex：

```text
<VENV_PYTHON> scripts/register_mcp.py --client codex --executable <WRITUNE_EXECUTABLE> --home <CONFIG_HOME> --install-skill
```

- 默认修改 `~/.codex/config.toml`；设置了 `CODEX_HOME` 时使用该目录。配置文件不同可显式加 `--config <ACTUAL_CONFIG_PATH>`。
- Windows 的注册脚本会将 `writune.exe` 自动转换为同环境的 `pythonw.exe`，参数为 `-m writune serve --home <CONFIG_HOME>`；macOS / Linux 使用 `bin/writune serve --home <CONFIG_HOME>`。MCP 直接走 stdio，不经过 PowerShell、cmd 或终端窗口。
- Skill 安装到 `~/.agents/skills/writune-polish`。

Claude Code：

```text
<VENV_PYTHON> scripts/register_mcp.py --client claude-code --executable <WRITUNE_EXECUTABLE> --home <CONFIG_HOME> --install-skill
```

- 用户级 MCP 配置实际在 `~/.claude.json` 的 `mcpServers.writune`，不是 `~/.claude/settings.json`。如用户使用自定义配置位置，传 `--config`。
- Skill 安装到 `~/.claude/skills/writune-polish`。完整复制 Skill 及 `references/`。
- 如果已存在不同的 Writune 启动配置，核实它确实属于本次迁移后，使用 `--replace` 更新该条目；其他条目保持原样。已有自定义 Skill 与仓库版本不同时保留原文件，报告差异供用户选择。

保存后由 harness 刷新 MCP 配置并检查工具发现。需要重建 Codex 条目时，先备份该条目，再通过 `codex mcp remove writune` 和 `codex mcp add` 重新添加，Windows 使用上述 `pythonw.exe` 命令及完整参数；也可直接原子编辑配置文件。Claude Code 用 `/mcp` 检查连接。全程不操作 ChatGPT/Codex App 界面，不退出正在处理其他工作的整个客户端。

## 6. 实际验收

1. 用专用环境执行 `python -m writune doctor --home <CONFIG_HOME>`，确认依赖、凭据状态、临时任务目录及真实 MCP stdio 初始化与工具发现通过。
2. 确认当前客户端实际发现 Writune 工具；只看到配置文件不能判定已接入。如果当前 harness 无法刷新本轮工具目录，完成独立 stdio 检查后明确告知用户所需的刷新动作，不能把它写成当前客户端已调用成功。
3. 在 `Settings.task_dir` 下写入短的合成原文和占位符模板，创建上下文，调用 `writune_submit(source_path=...)`。只提交一次；等待时做独立检查，按返回 ETA 查询状态。
4. 用 `writune_result` 获取元数据，要求 `outcome=polished`；若是 `original_fallback`，交付原文并诊断配置，不能将回退当作润色成功。
5. 用 `writune_apply` 将结果插入临时模板，检查数字和必要术语保留，确认 `result_path`、默认 `output_path` 都位于系统临时目录。再次 apply 验证 `idempotent=true`。
6. 最后报告：所选客户端与工具发现结果、独立环境与配置路径、模型名称、测试 outcome、临时输出路径、备份位置及是否还有用户必须执行的动作。不要输出 Key，不输出长篇测试正文。

## 官方配置参考

- [Codex MCP](https://developers.openai.com/codex/mcp/)
- [Claude Code MCP 与作用域](https://code.claude.com/docs/en/mcp)
- [uv 的 managed Python](https://docs.astral.sh/uv/concepts/python-versions/)
- [uv 环境变量](https://docs.astral.sh/uv/reference/environment/)
