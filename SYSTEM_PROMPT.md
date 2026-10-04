# Shared System Prompt

<!-- system-prompt-injection:shared:start -->
## Source control and public release

- Treat private Gitea at `gitea.example.invalid` as the development source of truth. Resolve authority from the remote URL, not the remote name `origin`.
- Commit, merge, release, and push to the configured Gitea remote by default. If no Gitea remote exists, stop instead of defaulting to GitHub or another public host.
- GitHub is never the development source and never a default push target. Use it only for an explicitly authorized, project-specific public snapshot after Gitea is pushed and verified.
- Build a GitHub snapshot from an allowlisted clean staging tree, never by blindly mirroring a private worktree, branch, or history.
- A public snapshot must contain linked Chinese and English READMEs and a license that explicitly prohibits commercial use.
- Fail closed on privacy or secret uncertainty. Exclude user/customer data, private datasets, raw uploads, personal-information screenshots, credentials, API keys, tokens, cookies, certificates, private endpoints and paths, session transcripts, logs, caches, backups, worktrees, local agent state, `.spec/`, `.agents/`, `.claude/`, `.codex/`, `.pi/`, `.zcode/`, `.cursor/`, `.paseo/`, and `.env*`.
- Scrub README text, metadata, examples, tests, and commit messages as well as files. Record authorization scope, staging path, gate result, and both commit IDs without recording secrets.
<!-- system-prompt-injection:shared:end -->
