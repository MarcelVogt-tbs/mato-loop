# Managed Shared Bash

`terminal.shared.*` is a separate capability. It opens a persistent Bash 5 shell
with Readline 8 in emacs mode on Linux. It does not use `shellExecutable`, change
`shell.run` or `terminal.run`, or synchronize its directory with an isolated
workspace. Missing Bash, an incompatible ABI, or a missing native module makes
shared open unsupported. Personal `terminal.open` remains unchanged.

## Protocol

All identifiers are UUID strings. Revisions are nonnegative integers. Commands
are limited to 256 KiB of UTF-8 and cannot contain NUL. Existing terminal
read/write/resize/close operations work with shared terminal IDs. Only trusted
human input may use terminal.write; none of these changes expose it as a model
tool. The backend owns terminal lifetime, browser reconnection, policy decisions,
and durable approval state.

| Operation | Arguments | Result |
| --- | --- | --- |
| `terminal.shared.open` | `rows`, `columns`, optional `terminalId` | `{terminalId}` |
| `terminal.shared.status` | `terminalId`, optional `redisplay=false` | Status below |
| `terminal.shared.stage` | `terminalId`, `commandId`, `command` | Status below |
| `terminal.shared.inspect` | `terminalId`, `commandId`, optional `freeze=true` | `{command,currentDirectory,revision,contextRevision,inspection}` |
| `terminal.shared.execute` | `terminalId`, `commandId`, `command`, `expectedRevision`, `expectedContextRevision`, `expectedInspectionRevision`, optional `timeoutSeconds` | Existing ProcessResult and stdout events |
| `terminal.shared.cancel` | `terminalId`, `commandId` | `{}` |

When open omits `terminalId` (or supplies null), the agent allocates it. A caller
can instead allocate a UUID before dispatch and close that known ID after an
uncertain response. Concurrent/repeated opens of the same live shared ID with
the same original rows/columns reuse one shell, including during initialization.
Retries do not resize the terminal. Different dimensions or a collision with a
non-shared terminal return `terminal.exists`; a retained closed entry returns
`terminal.closed`. Idempotency is scoped to this agent connection/manager, not
persisted across reconnects. Use a new UUID for every new terminal lifetime.

Status always includes these fields, including null proposal fields:

```json
{
  "terminalId": "...",
  "currentDirectory": "/absolute/physical/path",
  "state": "idle",
  "contextRevision": 1,
  "commandId": null,
  "command": null,
  "revision": 0,
  "submitted": false,
  "cancelled": false
}
```

States are `idle`, `busy`, `editing`, `checking`, `running`, and `closed`. An idle
primary prompt can contain personal partial input: stage still rejects it with
`terminal.busy`. Status never collects unrelated personal input or history;
history explicitly selected into a proposal becomes part of that draft. The last
proposal remains identifiable after completion/cancellation until another stage.
Stage resets submitted/cancelled and inserts only at an empty primary prompt.

Inspect freezes the real draft by default, whether invoked by the UI or after Enter.
`freeze=false` instead snapshots the current proposal for initial permission
inspection without entering checking. It never unfreezes an already-frozen draft.
Edits or context changes during either inspection reject that snapshot as stale;
concurrent inspections cannot silently authorize different text. Execute still
requires native checking state: Enter or a subsequent freezing inspection must
freeze the exact draft before authorization. Enter (CR or LF) sets
`submitted=true` but does not execute, including after an inspection has already
frozen the line. Repeated Enter remains a single submission, not another execution.
While checking, editing input is ignored; Enter and native interruption remain
available. Ctrl+C or cancel discards the pending real buffer,
invalidates approval, and restores personal bindings. After completion, personal
commands retain native Bash execution, typeahead, quoting, aliases, functions,
variables, and directory state.

The backend must inspect and display the returned exact snapshot before creating
an approval. Execute requires all three revisions and that exact command; it
never substitutes the original proposal for an edited draft. The native module
checks again at acceptance. Stale or cancelled approval returns `terminal.stale`.
Initial/edited invalid syntax and hard filesystem errors retain existing agent
error codes. Temporary Readline submodes or foreground activity can return
`terminal.busy`; operations never inject a wake sequence into the PTY.

Status requests take a native snapshot through the control channel at Readline.
Foreground programs do not run Readline's hook. A bounded control timeout reports
busy/running, never cached idle, and obtains the live Linux shell cwd from procfs
when available. No readiness or permission decision relies on this fallback.
Control requests expire in the native module, so a request cannot unexpectedly
stage/accept after a foreground program finishes.

`status(redisplay=true)` also requests a native Ctrl+L-style screen redraw at a
stable primary prompt. It preserves the real line and cursor, never submits it,
and sends only ordinary terminal rendering to terminal.read. This deliberately
clears the visible screen instead of appending a duplicate prompt at an unknown
browser cursor position. Request it once after queueing retained output for a
new browser emulator. Foreground programs and transient Readline submodes return
busy without a redraw; ordinary status never clears the screen.

## Inspection Boundary

Shared inspection returns the ordinary `process.inspect_shell` result unchanged,
except for the bound inspection revision. A recognized write is `shell.write`;
every command without a recognized write remains `shell.read`, including unknown
commands and incomplete analysis. Classification, analysis completeness, command
prefixes, targets (including `toolchain_metadata` and `discard`), and hard path
errors follow that existing inspector. No shared-specific alias or variable
classifier is applied.

The inspection revision binds the terminal, proposal, exact text, physical cwd,
draft revision, context revision, and inspection targets. Targets are re-inspected
before execution. Context revisions advance at native Readline boundaries and
observed physical cwd changes within this shell lifetime. They do not fingerprint
variables, aliases, functions, or arbitrary callbacks. There is no inherited DEBUG
trap or all-signals veto. The mutable shell is trusted under the accepted ordinary
inspection heuristic: `shell.read` means no recognized mutation, not guaranteed
side-effect-free execution. Unknown effects and incomplete analysis are accepted;
this is approval coordination, not an unrestricted-state sandbox.

## Native Integration

`native/shared.c` is a Bash loadable builtin. A framed, nonblocking inherited
socket carries requests and snapshots independently of terminal output. The
module chains/restores Readline hooks, observes the real line buffer, and swaps
in a filtered native editing keymap only while a proposal is pending. Completion,
shell expansion, external-editor commands, bind-x callbacks, keyboard macros,
and other functions outside the editing allowlist are disabled while pending.
Native editing, history navigation/search, quoted insertion, and bracketed paste
remain available. History expansion is disabled at managed prompts. Vi-mode
proposal staging is unsupported; personal bindings are otherwise restored.

Authorized acceptance is dispatched through Readline's internal queue and its
native accept handler. No bytes are written to the PTY for control operations.
Bash receives the accepted source at top level; there is no eval loop or Python
line editor. A terminal-deprepare hook also prevents pending source escaping
through alternative native acceptance paths and fences secondary prompts/REPLs.
The host honors Bash's pending interrupt state before processing control requests.

One PTY reader fans out raw browser output and bounded command capture. Shared
browser buffering retains the latest 1 MiB rather than blocking command progress
when the browser is disconnected. Tool capture is independently bounded by
`maxOutputBytes`, and PTY stdout/stderr are merged. Output boundaries account for
kernel-buffered bytes when the managed prompt reports the real exit status.
Background-job output can interleave, as in any shared terminal.

Cancel/timeout interrupts the foreground process group. If the shell cannot
recover, it is closed. Loss of the shell/control channel (`exit`, `exec`, crash,
or uncertain acceptance acknowledgement) yields `exitCode=null` when completion
cannot be established. This outcome must never be automatically replayed.

## Build

Build dependencies are a C compiler, libc headers, `bash-builtins` headers, and
Readline headers (Debian/Ubuntu: `gcc libc6-dev bash-builtins libreadline-dev`).
There is no compiler invocation at agent runtime and no second linked Readline
library. Docker builds the module in a separate stage; the native installer builds it
before activating a new environment. Bash/Readline remain system dependencies.

Source installations can build explicitly with:

```sh
sh build-native.sh src/matoloop_agent/native/shared.so
```

For development, rebuild only the agent image when native source changes:

```sh
./scripts/dev.sh up --build -d --no-deps matoloop-agent
./scripts/dev.sh test matoloop-agent
./scripts/dev.sh check matoloop-agent
```

The tests also compile the current native source into temporary storage and run
real PTYs, so native regressions are checked independently of the installed
development-image module.
