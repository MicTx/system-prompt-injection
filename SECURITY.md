# 安全策略

## 支持版本

只修复最新 release 上的问题。

## 报告漏洞

请用 GitHub 的私密漏洞报告（Security → Report a vulnerability），不要开公开 issue。

## 范围

本工具是本地单文件程序：只读写 `targets.json` 声明的本机文件，以及 `~/.config/system-prompt-injection/` 下的备份。它不联网、不执行外部内容。凡涉及你本机文件内容或路径的细节，报告时请先自行脱敏。

## English

Please report vulnerabilities via GitHub private vulnerability reporting, not public issues. This is a local single-file tool: it only reads and writes files declared in `targets.json` plus its own backup directory; it makes no network calls. Supported: the latest release only.
