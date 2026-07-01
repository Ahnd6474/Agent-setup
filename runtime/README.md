# Runtime snapshot

This directory preserves the current local LLM server runtime:

- `agentic-local-20260701.tar.zst.enc`: encrypted `.agentic-local` state,
  consistent SQLite authentication database snapshots, logs, sessions, actual
  `cluster.env`/`model.env`, `.envrc`, and the local exo configuration.
- `python-envs-macos-arm64-20260701.tar.zst`: the `exo/.venv` and `llm_env`
  virtual environments for Apple Silicon.

The encryption key is intentionally outside this public repository:

```text
~/.config/agent-setup/runtime-backup.key
```

Back up that key separately. Without it, the encrypted runtime snapshot cannot
be recovered.

Restore into a temporary directory first:

```bash
scripts/restore_runtime_backup.sh secrets \
  runtime/agentic-local-20260701.tar.zst.enc /tmp/agent-restore

scripts/restore_runtime_backup.sh envs \
  runtime/python-envs-macos-arm64-20260701.tar.zst /tmp/env-restore
```

Review restored paths and permissions before replacing live server data.
