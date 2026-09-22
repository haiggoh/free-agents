# `lowkey` — Terminal Local-Agent Dispatcher

`lowkey` is a terminal-native companion interface for the local models registered in the `free-agents` stack. It lets you work with local agents independently of Claude Code while reusing the repository's existing model registry, hotswap layer, and librarian dispatch infrastructure.

The dispatcher is included alongside the main `free-agents` tooling as a useful alternative launch surface. It is not required for the primary plugin workflow, but users who prefer terminal-based interaction can invoke it directly.

## Two interfaces

`lowkey` provides both a **command-line interface** (for scripting, one-shot, and conversation) and an **interactive picker menu** (for visual model selection and settings).

### Command-line interface

One-shot prompt:

```zsh
lowkey --prompt "What does this module do?"
```

Attach files:

```zsh
lowkey --prompt "Review the attached code." --files ./src/main.py
```

Interactive conversation:

```zsh
lowkey --convo
```

Choose a model:

```zsh
lowkey --model qwen-3.8-operator --prompt "Summarize this change."
```

### Interactive picker menu

Launch without arguments to open the interactive picker:

```zsh
lowkey
```

The picker provides:
- **Model selection** with roles, backend, thinking mode, and effort displayed
- **Effort override** (low/medium/high/xhigh/max)
- **Advanced settings submenu** (`+`) for progress mode, token limits, history/file size limits, session continuity
- **Session management** — named session creation/resume
- **Launch modes**: Conversation (`c`) or One-shot (`o`)
- **CSL-style emoji labels** for each option

The picker shows only **modified settings** in the main view (settings that deviate from defaults). Each advanced setting appears individually when changed.

## Conversation features

Conversation mode supports:

- structured user/assistant message history;
- model-derived terminal labels such as `qwen:`;
- compact, verbose, and quiet progress modes;
- multiline input with `:paste` and `:end`;
- accidental leading `You>` cleanup;
- rolling context summaries;
- `:context` and `:summary` inspection commands;
- `:file PATH` attachments during a conversation;
- per-file character limits through `--max-file-chars`;
- named session save/resume through `--session`;
- explicit `:save` and `:session` commands;
- atomic owner-only JSON session files.

### Multiline input

Start multiline mode explicitly:

```text
You> :paste
Paste multiline text now. Finish with :end on its own line.
Review this code:

```python
print("hello")
```
:end
```

The `:end` terminator must be on its own line, unless it is appended to the final clipboard line without a trailing newline.

A known follow-up usability improvement is to make fast clipboard pastes display more clearly and prevent terminal input/output from appearing interleaved. The current delimiter-based collection works, but that terminal presentation refinement remains planned.

### Named sessions

Create or resume a session:

```zsh
lowkey --convo --session my-session
```

Useful commands:

```text
:session
:save
exit
```

Sessions are stored by default under:

```text
~/.local/share/lowkey/sessions/
```

Set `LOWKEY_SESSION_DIR` to use another location. Session files contain conversation content and are not encrypted; the dispatcher attempts to enforce owner-only permissions.

## Progress modes

Compact is the default:

```zsh
lowkey --progress compact --prompt "Explain recursion."
```

Use verbose diagnostics when debugging dispatch:

```zsh
lowkey --progress verbose --prompt "Reply exactly: verbose works"
```

Use quiet mode when only model output is wanted:

```zsh
lowkey --progress quiet --prompt "Reply exactly: quiet works"
```

## Important options

```text
--model ALIAS
--prompt TEXT
--files FILE [FILE ...]
--max-tokens N
--max-history-chars N
--max-file-chars N
--session NAME
--allow-session-model-mismatch
--progress {compact,verbose,quiet}
--convo
```

`--max-history-chars` is a soft compaction threshold, not a strict context ceiling. The newest complete turn may exceed it. Use `0` for unlimited history.

`--max-file-chars` rejects oversized attachments before dispatch. Use `0` only when deliberately allowing unlimited file size.

## Relationship to the main project

The dispatcher is a companion terminal interface to the same infrastructure used by `free-agents`. It is intentionally located in this repository because it depends on the sibling hotswap and librarian scripts, model registry, and local launch conventions.

It can be installed with the rest of the repository even when users do not invoke it. The main plugin workflow does not require it, while terminal users gain an independent way to interact with the registered local models.

## Development status

The dispatcher is an actively developed pre-1.0 terminal companion.

Current development version:

```text
0.10.0
```

The principal workflows have been tested manually against the local model infrastructure and are committed locally for review. This is a development checkpoint, not a public dispatcher release or Git tag.

A known follow-up concerns rapid clipboard pastes: isolated `:paste` collection works, but terminal input echoes and model output can sometimes appear visually interleaved. Automated regression tests, paste-burst UX refinement, and final release review remain before the first formal dispatcher release.

See the project release plan and changelog for the reconstructed feature history and pending work.