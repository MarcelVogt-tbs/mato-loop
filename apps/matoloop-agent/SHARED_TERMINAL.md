# Managed Shared Bash

`terminal.shared.*` opens one persistent interactive Bash on Linux. The human and
the agent take exclusive turns in the same PTY, so both retain its directory,
environment, variables, functions, aliases, history, and foreground programs.
It does not change `shell.run` or `terminal.run`, synchronize the directory with a
workspace, or expose terminal input as a model tool.

This is approval coordination around a trusted shell, not a sandbox. Human commands
run directly with the MatoLoop Agent operating-system user's rights. Agent commands
still use the ordinary shell inspector, policies, grants, and approval flow.

## Protocol

All terminal and command identifiers are UUID strings. Commands are limited to
256 KiB of UTF-8 and cannot contain NUL. Existing `terminal.read`,
`terminal.resize`, and `terminal.close` operations work with shared terminal IDs.
`terminal.write` accepts browser input only while the human owns the shell.

| Operation | Arguments | Result |
| --- | --- | --- |
| `terminal.shared.open` | `rows`, `columns`, optional `terminalId` | `{terminalId}` |
| `terminal.shared.status` | `terminalId` | Status below |
| `terminal.shared.acquire` | `terminalId` | Status below |
| `terminal.shared.release` | `terminalId` | Status below |
| `terminal.shared.prepare` | `terminalId`, `commandId`, `command` | `{command,currentDirectory,inspection}` |
| `terminal.shared.execute` | `terminalId`, `commandId`, `expectedInspectionRevision`, optional `timeoutSeconds` | Existing process result and stdout events |
| `terminal.shared.cancel` | `terminalId`, `commandId` | `{}` |

Status is:

```json
{
  "terminalId": "...",
  "currentDirectory": "/absolute/physical/path",
  "state": "human",
  "commandId": null
}
```

States are `human`, `agent`, `running`, and `closed`. `acquire` succeeds only at an
empty primary Bash prompt and changes `human` to `agent`. Browser input is then
rejected by the agent. `release` requires no pending or running command and returns
control to the human. Closing the shell or losing its control channel makes the
outcome closed rather than silently creating an isolated replacement.

`prepare` stores the exact immutable model command, inspects it in the shell's
physical current directory, and returns that inspection. It neither displays the
source in the PTY nor executes it. The backend may execute only the same command ID
and inspection revision after authorization. `execute` re-inspects immediately
before dispatch and rejects changed targets with `terminal.stale`.

The command runs in the persistent Bash and merged PTY stdout/stderr is streamed as
stdout. Capture is bounded by `maxOutputBytes`; ordinary terminal buffering remains
independent. Cancel and timeout interrupt the foreground process group. Shell or
control-channel loss can produce `exitCode=null`; callers must not automatically
replay an uncertain execution.

## Linux Integration

The agent starts Bash with a generated private rcfile and an inherited Unix socket.
A reserved `bind -x` sequence transfers control only while Bash Readline is at an
empty primary prompt. The sequence is sent through the PTY, but requests,
acknowledgements, command source, exit status, and prompt-state notifications use
the separate framed socket and never depend on parsing visible terminal output.

During the agent's turn, Bash evaluates only the command supplied over that control
channel. The browser cannot write to the PTY. The human receives control again only
after the shell reports a completed primary prompt and the backend explicitly
releases it. Background-job output may interleave as in any interactive shell.

The implementation uses only Bash, the PTY, and Python's standard library. It has
no compiler, development-header, loadable-module, or separate Readline ABI
dependency. Future platform adapters must preserve the protocol's exclusive-control
and immutable-command guarantees using their native shell facilities.
