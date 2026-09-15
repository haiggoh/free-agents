# Local Agents Scripts

This directory contains helper scripts that support the local-agents plugin functionality.

## local-queue-stop-hook.py
Enhanced version of the global session stop hook that:
- Implements the agreed marker system for queued prompts (`[[QUEUE_ANSWERED:<8-char-hash>]]`)
- Provides clear guidance in its blocking messages on how to use the marker
- Maintains all existing functionality (watcher, auto-mode, telemetry detection)
- Includes proper loop guards and safety mechanisms

## queue-marker-helper.py
Deterministic helper for generating QUEUE_ANSWERED markers:

**Usage:**
```bash
queue-marker-helper.py "<queued prompt content>"
```

**Example:**
```bash
$ queue-marker-helper.py "/run-to-completion"
[[QUEUE_ANSWERED:ae414ae0]]
```

This eliminates manual hash calculation and formatting errors, providing a reliable way to generate the exact marker format expected by the enhanced stop hook.

Both scripts work together to provide a robust system for addressing queued prompts in local Claude Code sessions.
