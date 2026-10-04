# system-prompt-injection

**简体中文** | [English](README-en.md)

[![License: PolyForm-NC-1.0.0](https://img.shields.io/badge/License-PolyForm--NC--1.0.0-blue)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-blue?logo=python&logoColor=white)](pyproject.toml)
[![Tests: 33 unittest cases](https://img.shields.io/badge/Tests-33%20unittest%20cases-brightgreen)](tests)


一个极简单文件 TUI：把一份公共提示词块同步进本机所有 agent 配置文件。单文件、零依赖、改前自动备份。

## 为什么需要它

一台机器上往往同时跑着好几个 agent：Codex、Claude Code、Gemini CLI……每个都在自己家目录里放一份配置文件。总有些规则对所有 agent 都成立：不许推公网、凭据不进对话。这些规则目前只能手工抄进每一个文件。

手工抄有两种坏法。抄漏了，某个 agent 会继续用旧规则，直到出事才发现；整文件覆盖省事，却会抹掉各客户端自己的专属配置。缺的不是一份好规则，是一个只动该动部分的方法。

托管块（managed block）就是这个方法。公共规则只写一处：`SYSTEM_PROMPT.md`。同步时只替换目标文件标记区间内的内容，区间之外一个字节不碰。改一处、按一键、全部到位。边界也说清楚：它同步共享片段，不做配置合并，也不理解各家配置字段的含义。

## 30 秒上手

要求：Python 3.9+，只用标准库，无需安装依赖。进入仓库目录即可运行：

```bash
./prompt_sync.py          # TUI：看状态、diff、apply、undo、编辑源文件
./prompt_sync.py check    # 无界面看状态（有待同步时退出码 1）
./prompt_sync.py apply    # 无界面直接同步
```

首次使用两步：

1. 把公共规则写进 `SYSTEM_PROMPT.md` 的标记区间内。
2. 在 `targets.json` 里列出目标文件，路径支持 `~` 与 `${VAR:-default}`。

## 工作机制

`SYSTEM_PROMPT.md` 是唯一真源。目标文件用成对标记圈出托管区间，区间外内容一概不动：

```text
<!-- system-prompt-injection:shared:start -->
…同步内容…
<!-- system-prompt-injection:shared:end -->
```

Pi 的 `SYSTEM.md` 用另一对标记（`targets.json` 里声明 `"format": "pi-system"`）：

```text
== SYSTEM_PROMPT_INJECTION:shared:START ==
…同步内容…
== SYSTEM_PROMPT_INJECTION:shared:END ==
```

扫描后每个目标处于五种状态之一：

| 状态 | 含义 |
| --- | --- |
| `ok` | 已同步 |
| `out` | 标记在，内容过期 |
| `new` | 区块待插入（新建文件或追加标记） |
| `skip` | 宿主未安装（父目录不存在，跳过） |
| `err` | 无法读取（软链、非普通文件或标记异常） |

安全底线三条：

- `apply` 前先把被改文件备份到 `~/.config/system-prompt-injection/backups/<时间戳>/`。
- 写入走临时文件 + `os.replace` 原子替换，不产生半截文件。
- TUI 里按 `u` 恢复最近一次备份；`check` 只读不写。

## TUI 按键

| 按键 | 作用 |
| --- | --- |
| `↑/↓` `j/k`，`g`/`G` | 移动选择，跳到首行 / 末行 |
| `d` 或回车 | 查看选中目标的应用前 diff |
| `p` | 只应用到选中的这一个目标 |
| `a` | 应用到所有待同步目标 |
| `e` | 用 `$EDITOR`（回退 `VISUAL`、`vi`）编辑 `SYSTEM_PROMPT.md` |
| `u` | 恢复最近一次备份 |
| `r` | 重新扫描 |
| `q` / `ESC` | 退出 |

## 配置

`targets.json` 里 `targets` 数组的每一项有三个字段：

```json
{
  "targets": [
    { "id": "codex", "path": "~/.codex/AGENTS.md" },
    { "id": "pi-system", "path": "~/.pi/agent/SYSTEM.md", "format": "pi-system" }
  ]
}
```

- `id`：标识符，用于显示与单目标 apply。
- `path`：目标文件路径。支持 `~` 与 `${VAR:-default}`，例：`${MINIMAX_DATA_DIR:-~/.minimax}/AGENTS.md`。
- `format`：标记格式。缺省为 markdown 注释；`pi-system` 用 `== … ==` 行标记。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

## 许可证

代码按 [PolyForm Noncommercial 1.0.0](LICENSE) 授权：个人与非商业用途可自由使用，商业使用需另行书面授权。
