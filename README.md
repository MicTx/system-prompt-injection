**简体中文** | [English](README-en.md)

# system-prompt-injection

把 `SYSTEM_PROMPT.md` 里的一份公共提示词块，同步进本机所有 agent 配置文件。单文件、零依赖、带 TUI。

```bash
./prompt_sync.py          # TUI：看状态、diff、apply、undo、编辑源文件
./prompt_sync.py check    # 无界面看状态（有待同步时退出码 1）
./prompt_sync.py apply    # 无界面直接同步
```

TUI 按键：

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

## 设计

- `SYSTEM_PROMPT.md` 是唯一真源，`targets.json` 列出目标文件（`~` 与 `${VAR:-default}` 可用）。
- 只管理标记内的区块，目标文件其余内容不动：

  ```text
  <!-- system-prompt-injection:shared:start -->
  ...同步内容...
  <!-- system-prompt-injection:shared:end -->
  ```

  Pi 的 `SYSTEM.md` 用 `== SYSTEM_PROMPT_INJECTION:shared:START/END ==` 标记（`format: "pi-system"`）。

- 状态：`ok` 已同步 · `out` 内容过期 · `new` 待插入 · `skip` 宿主未安装 · `err` 无法读取。
- `apply` 前先把被改文件备份到 `~/.config/system-prompt-injection/backups/<时间戳>/`，再用临时文件 + `os.replace` 原子替换；TUI 里按 `u` 恢复最近一次备份。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

## 许可证

PolyForm Noncommercial 1.0.0，见 [LICENSE](LICENSE)；商用需另行书面授权。
