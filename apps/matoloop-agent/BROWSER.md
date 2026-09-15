# Linux Browser Runtime

The agent advertises 58 operations, including 18 `integration.browser.*` operations.
The browser is headed Playwright-managed Chromium, with an authenticated private Xvfb
display, Openbox and x11vnc in inetd mode. VNC has no TCP listener or public port.
Xvfb starts with a 3840x2160 allocation, then uses exact generated RandR modes through
`xrandr` (the `x11-xserver-utils` package). Debian's development Xvfb was verified to
accept added modes, including 1001x701 below its startup maximum; no alternate X server
is needed. Readiness actually shrinks and grows the display, not just checks binaries.
Chromium uses `no_viewport=True` and a fullscreen native window, not a fixed emulated
viewport. Chromium advertises a 500px minimum window width; before a resize, `xprop`
(`x11-utils`) removes `WM_NORMAL_HINTS` from windows on this private desktop so Openbox
can fit the native window even at 320px without changing page scale or restarting it.
Readiness also exercises 320x240 and restores 1280x800 after browser launch.
Private x11vnc uses `-xrandr resize` to retain resize-capable RFB streams.
Each view uses an outbound binary WebSocket to the configured backend's
`/api/browser-relay/{relayId}`, authenticated with the supplied short-lived bearer token.
The agent never accepts a view URL or port. Disconnecting a view leaves the browser
running; main agent disconnect closes the runtime but not its persistent profile.

The CLI handles SIGTERM/SIGINT once and reaches browser cleanup even during connection
retries. Repeated signals cannot interrupt that cleanup. Worker cleanup is bounded to
10 seconds, manager termination to 12 seconds plus final reaping, and the complete CLI
shutdown has a 25-second hard deadline. Compose grants 30 seconds. A normal stop releases
Chromium's profile singleton; SIGKILL and host failures cannot run finalizers.

Known singleton conflicts use `browser.profileInUse`. Chromium remains the sole profile
lock mechanism; the agent does not delete or replace singleton entries. Any exceptional
operator cleanup must establish that no browser still uses the profile, then remove only
the verified orphaned links, not their targets or profile data. Logs distinguish fixed
startup stages and temporary versus persistent profiles without raw subprocess text.

Root agents start a subprocess as the dedicated `matoloop-browser` account, with no
supplementary groups and an allowlisted environment containing no agent credentials.
The worker accepts typed JSON commands over inherited private pipes. Host-authorized
`run_code` explicitly accepts unrestricted Python and `evaluate` accepts page JavaScript.
Both require the backend's read/write/code permissions; all default to AUTO for public and
private models. Python has the worker account's file/process/network/profile rights, not a
browser-only sandbox. A clean environment does not hide credentials accessible on disk.
Model arguments cannot choose the parent-managed browser endpoint or installation.
Chromium always uses `chromium_sandbox=True`. Sandbox launch failures are reported as
`browser.sandboxUnavailable`; there is no `--no-sandbox` fallback. Hosts must permit
unprivileged user namespaces in their kernel, container seccomp and AppArmor policy.
Compose uses `apps/matoloop-agent/docker/browser-seccomp.json`, a deny-by-default
userspace profile permitting Chromium's `clone`, `setns` and `unshare` sandbox calls,
following Playwright's Docker guidance. Deploy this companion file at the same relative
path when using the prepared Compose stack. No added capabilities or unconfined mode
are used; a host AppArmor user-namespace restriction can still prevent launch.

Container images include pinned Playwright 1.59.0 and its matching Chromium build at
`/usr/local/lib/matoloop-agent/tools/browser`, outside the development `/opt` dependency
volume. `MATOLOOP_AGENT_CONTAINER=1` selects this fixed installation; RPC arguments cannot
override it. Native managed installation is `<config-parent-parent>/tools/browser`,
independent of the checkout and Python virtual environment location.
Native setup uses the bundled script to verify host prerequisites without changing
system packages, then installs the managed Chromium as the runtime user. Both container and native setup finish
with a disposable headed launch, screenshot and private RFB handshake as the runtime
user. A zero setup exit code means this functional probe succeeded, not just that
packages exist. Missing prerequisites, inaccessible storage and launch failures exit
nonzero.
The setup PTY has a 1 MiB rolling replay
buffer and survives viewer and main agent WebSocket disconnects, not agent process
termination. `setupread.offset` is the next absolute byte offset, including any skipped
bytes when the requested output has expired. Never log setup input.

Profiles are bound to the configured backend URL, agent UUID and owner UUID. Data is
always `<config-parent>/tools/browser`, hence `/data/tools/browser` in the container,
using the existing single `/data` mount. Inside it, a SHA-256 backend/agent namespace
holds the protected `binding` marker and a runtime-user-owned `browser/profile`. The
separate owned child prevents a root-launched worker from replacing its binding marker.
Only browser/tool-owned parent directories receive traversal permission; the agent data
directory and its ancestors are never chmodded or chowned. The worker tests actual write
access to its own data directory before any readiness probe. A root-run native agent
with private ancestors must have explicitly administrator-granted, execute-only traversal
for `matoloop-browser`; otherwise setup/readiness fails closed. Do not make credential
directories readable to solve traversal. Ordinary non-root native agents use their own
user and need no permission widening. Runtime HOME/XDG directories are private temporary
directories, not the account's passwd home. Native browser installation uses the owned
browser data directory as HOME. Changed owner bindings are refused, not silently reused.
Reset deletes runtime
profile data only, with symlink-resistant traversal, and retains the binding and browser
installation. Status performs a disposable headed launch, renderer capture and RFB handshake when idle;
it never interprets an installed executable alone as readiness.

## Resource Limits

The browser does not impose global memory/process caps on the whole agent container:
those would also constrain unrelated agent tools. Core dumps are disabled in the browser
worker and descendants. The existing file, staging and tab limits remain.

Operators may configure a systemd service/scope or container resource limits appropriate
for their entire agent workload. Such limits cover the agent and all descendants. The
agent does not create delegated cgroups
or silently change a user service. Per-process `RLIMIT_AS` is not a safe Chromium memory
budget because V8 reserves large virtual address ranges; `RLIMIT_NPROC` would affect
other processes of a native user's account. Without an operator-configured service/scope,
native browser memory and process counts have no OS-enforced aggregate bound.

Raw worker, Chromium and managed installer stdout/stderr are never forwarded to host
logs. Worker stdout is bounded typed IPC; only allowlisted browser error codes are logged.
The setup terminal shows apt/sudo output and controlled installation/probe messages.
Page console data remains bounded, explicitly requested `diagnostics` tool data, not logs.
Network diagnostics record `response <status> <URL>` and `requestfailed <URL>` in the
same 50-entry, 500-character-per-entry buffer. URL userinfo, queries and fragments are
removed; oversized, invalid and non-HTTP(S) URLs are omitted. No request/response headers,
bodies or raw network failure strings are collected. Resource paths are still private
page data and these entries are returned only by the diagnostics tool, never host logs.

Service workers are enabled and request/WebSocket interception is not installed. Browser
compatibility must not depend on a partial HTTP-only interception pretending to enforce
egress. `allowNetwork=false` refuses opening or closes the runtime before execution;
the host must propagate policy changes promptly. This is a runtime availability policy,
not a per-request firewall. A live human-controlled browser retains normal browser/OS
access, including local schemes permitted to its OS user. Network diagnostics report
events surfaced by Playwright, not a complete audit of Chromium or service-worker egress.

## RPC Additions

The shared contract is implemented with these explicit additions:

- `inspect.text` is Playwright 1.59's public AI accessibility snapshot when the result fits
  32,768 characters. It includes semantic hierarchy, accessible names and states, current
  form values, iframe content and Playwright `ref` markers. Callers target a marker with
  `aria-ref=e12` or its frame-qualified form. Explicit inspection optionally accepts a native
  Playwright root `selector` and tree `depth` from 1 through 100. The result reports
  `data.snapshotScope=element` and `data.snapshotDepth` when applicable. Normal AUTO calls do
  not require a source hash. The implementation uses `page.aria_snapshot(mode="ai")` or the
  corresponding locator API, including Playwright's native `depth` option, rather than a local
  accessibility approximation or viewport heuristic. `inspect.data.snapshotSha256` is an
  opaque observation identity using a per-runtime key and page/document identity. Host-only
  `guard=true` additionally captures bounded fields, checked/selected/disabled/read-only state,
  form targets/methods, focus, desktop/viewport geometry and scroll position. That extra scan
  is reserved for actual ASK approval guards; it is not run for normal observations.
  The key prevents guessing private low-entropy field values from guard digests. Individual
  values above 65,536 characters or private state above 512 KiB are rejected, not silently
  truncated. Playwright constructs the accessibility tree before the worker applies its
  transport limit; the worker process boundary, 64 MiB file limit and eight-second capture
  timeout bound this behavior. The host must compare agent-produced tokens,
  not recompute SHA256(text).
  This bounded inspection is not a complete page semantic guarantee, does not cover
  arbitrary JavaScript, and does not enforce a stale-approval
  precondition itself; the host must re-inspect and compare before execution.
- Host-authorized write actions may set `observe=true` to return the resulting accessibility
  snapshot in the same RPC. These observations and oversized explicit inspections stage the
  complete UTF-8 snapshot as `snapshot: {fileId, size}`. The backend reads and deletes the
  staging file through bounded `integration.browser.file` chunks, then creates a run-bound
  session file exposed as `snapshotFile` with `root=files`, path, size and SHA-256. Small
  explicit inspections remain inline. Generated snapshot staging is hidden from downloads and
  uploads and is removed after transfer, on the next capture, or at runtime close. Snapshot
  files are read or searched with the ordinary file tools and are never loaded into model
  context automatically. A post-write capture or persistence failure returns
  `observationFailed=true` without changing the completed action into a retryable failure.
- Host-only `settle=true` requests bounded settling before `inspect`; it defaults to false
  for immediate final guards. Post-write observations always settle. Isolated-world observers
  require 150 ms DOM/request quiet, document readiness and completed finite animations, within
  600 ms for normal observations or 1.8 seconds for approval guards, plus bounded cleanup. Infinite animations and WebSockets are ignored. Normal reads
  and post-write observations continue with the current snapshot and `settled=false` when bounded
  settling expires. Host approval guards remain strict and raise `browser.observationUnstable`.
  Private worker metrics record the controlled settling outcome and settling/snapshot durations
  and sizes without page text or exception data.
- `fill_form` accepts `tabId` and 1-20 `fields` with `name`, bare `ref`, `type` and string
  `value`; refs must be unique and combined values at most 32,768 characters. Supported types
  are textbox, checkbox (`true`/`false`), radio (`true`) and native single-select combobox
  (exact unique option label). All targets are preflighted into fixed handles; each field is rechecked
  before dispatch without rejecting the batch's own earlier edits. The host binds the whole
  ordered list to the browser binding and permissions. Result `batch` contains `completed`,
  `total` and optional `errorCode`; the completed prefix must never be replayed. Target/document
  changes before a later dispatch report `browser.snapshotChanged`; dispatched failures report
  `browser.outcomeUnknown`. Handles are disposed after success, failure or cancellation.
  One optional final observation covers the batch. This is not a general script/submit API.
- `run_code` requires a `tabId` and `code` defining `async def run(page)`; `evaluate` requires
  `tabId` and a JavaScript `function`, optionally with a selector. Source limits are 32,768
  characters. `script` returns finite JSON `value` (32,768 UTF-8 bytes, depth 32), bounded
  combined print output, `outputTruncated`, and optional controlled `errorCode`, `failureReason`,
  `failureDetail`, and Python source `line`. Invalid results distinguish `resultTooLarge`,
  `resultTooDeep`, and `resultTypeUnsupported`. Failure diagnostics never include exception text,
  paths, stack traces, source excerpts, or local values. Python runs
  once in a fresh unrestricted namespace; syntax/function-shape checks are a contract, not
  a language sandbox. A 120-second cooperative deadline, 140-second manager deadline, and
  160-second backend deadline protect normal operation without preempting transport or cleanup;
  blocking or hostile code can force a browser restart.
  Errors after execution starts report an unknown outcome, never an automatic retry. User
  code can deliberately create background work or write outside managed staging, so ordinary
  output capture and process cleanup are not adversarial containment.
  Scripts should wait for concrete postconditions after state changes. For example, after a
  dialog action use `await dialog.wait_for(state="hidden")`; `dialog.count()` can remain one
  during a leave transition and does not prove that the next iteration may reuse the locator.
- `fill` accepts `slowly` and `submit`, both default false. `select` selects a known `tabId`.
  The backend's small public `browser_*` tools translate to this existing internal execute
  envelope. Historical `browser(action=...)` calls remain readable but are no longer offered.
- Ordinary action results may contain `playwrightCode: {completed, attempted, truncated,
  errorCode}`. The worker records confirmed Python action patterns and the next dispatched
  operation whose completion is unknown, using actual selectors and `repr`-escaped literals.
  Form batches report only their confirmed prefix and a next attempted field for unknown
  outcomes, not for pre-dispatch guard failures. The combined code limit is 32,768 characters;
  omit whole oversized lines instead of returning broken Python. Feedback is equivalent code,
  not a replay guarantee, application-success proof, or a complete trace of internal guards.
  Internal inspections and free-code callbacks do not add feedback. A failed observation or
  title read does not discard already confirmed statements. This text is tool-result data,
  never logged or automatically executed.
- `screenshot` returns `screenshot: {fileId, size}` instead of inline base64. The worker
  captures the viewport as PNG and uses Pillow LANCZOS resizing with Playwright MCP's bounds:
  at most 1568 pixels per edge and `1.15 * 1024 * 1024` pixels total, never upscaling or
  changing the viewport. Only the scaled PNG is staged; no original is saved. The special
  500 KB capture limit is removed without increasing RPC limits. Read the image through
  `integration.browser.file` chunks and delete it afterward, including on errors/cancellation.
  Screenshot staging is hidden from downloads and cannot be selected for upload; abandoned
  captures are removed at the next capture or runtime close. The backend keeps screenshots
  only in active-run memory, not durable session attachments.
- `tabs` and `diagnostics` enumerate/read without bringing any page to the foreground.
- Host-only `integration.browser.navigation` takes `{browserId:UUID,action,tabId?:UUID,
  url?:string,nativeChrome?:bool}`. `state` accepts no optional fields; `new` optionally
  accepts `url`; `chrome` requires only `nativeChrome`; `navigate` requires `tabId` and
  `url`; `select`, `close`, `back`, `forward`, `reload`, and `stop` require only `tabId`.
  Omit unused fields entirely: explicit nulls, unrelated fields and coerced values fail
  validation. URLs are credential-free HTTP(S) or exactly `about:blank`; special native
  pages remain accessible through the explicit native browser interface, not this RPC.
  The result is exactly `{tabs:[{id:UUID,url:string,title:string,loading:bool}],
  activeTabId:UUID|null,canGoBack:bool,canGoForward:bool,loading:bool,nativeChrome:bool}`.
  Tabs are limited to 32, URLs to 4096 characters and titles to 500 characters.
  Unknown/closed target IDs return `browser.invalidTarget`; adding at the cap or any
  mutating navigation while manual control is disabled returns `browser.busy`. State
  reads remain allowed while model-attached. Browser binding and host authorization
  still apply. No native navigation actions are added to the model execute contract.
  Model execution without an explicit tab uses the observed active selection.
  Native tab selection, popup pages, SPA changes, redirects and closes are observed using
  private CDP sessions and isolated-world focus checks, shared with manual file choosing.
  State reads never activate a window or page; omnibox/menu focus retains the last selected
  visible page. History is Chromium's `Page.getNavigationHistory`, loading is tracked from
  host CDP events. Renderer reads have short deadlines and retain the last observed focus
  and history during stalled navigation. Navigation acknowledgement is acceptance, not a
  page-load barrier; subsequent state reports completion. Late failures add only a
  controlled diagnostic. The host should coalesce polling, not queue reads behind actions.
  `chrome` changes only the active page's native window, using CDP fullscreen or normal
  then maximized bounds. `nativeChrome` observes that window's current presentation,
  rather than echoing the last request. New browser windows
  receive the current presentation once; status reads never reapply it and native
  permission dialogs are not fullscreen targets. Custom closing of the last tab creates
  a fresh blank before closing it, retaining the runtime. Native explicit quit is unchanged.
  This keeps the same Chromium process, persistent profile, private desktop and VNC socket.
  Installed Chromium 145.0.7632.6 was verified at 1280x800: fullscreen content is 1280x800;
  restored native chrome content is 1280x713, with live input values preserved. Native
  Settings and Ctrl+Shift+I DevTools remain usable. No F11 injection or new dependency is needed.
- Host-only `integration.browser.resize` takes exactly `{browserId:UUID,width:int,height:int}`
  and returns `{width:int,height:int}` verified against the applied X screen geometry.
  Width is 320..3840 and height is 240..2160; unknown fields and coerced values are refused.
  The current X server maximum is checked as well. Display, output and mode timings are
  fixed host code, never RPC inputs. Resizing preserves live pages, form values, profile,
  input locks and existing VNC streams. Superseded viewer requests must be coalesced by
  the host, not cancelled; if dispatch is cancelled, the agent drains its resize response
  under the runtime lock rather than killing Chromium or desynchronizing the next RPC.
- Host-only `integration.browser.control` takes `{browserId,manual:bool}` and returns
  `{ok:true}`. New runtimes start with manual input enabled. Before attaching an agent,
  call `manual:false` and wait for success. Each existing x11vnc inetd process receives
  a synchronous private-X-property `viewonly` command followed by a query confirming
  `viewonly:1`; held keys and mouse buttons are released. New views start with `-viewonly`.
  Failed confirmation closes affected views and leaves manual input disabled.
- After detaching, call `control` with `manual:true`. On a false-to-true transition all
  existing RFB streams are closed before new manual viewers are accepted. The host must
  reconnect the view using the same live browser ID. This deliberately discards queued
  and partial input instead of replaying it on unlock or implementing another RFB parser.
  The browser/profile, main control connection and independent setup PTY remain alive.
  Repeating the current mode is idempotent and does not close views.
- Host-only `integration.browser.clipboard` takes `{browserId,action:"read"}` or
  `{browserId,action:"write",text}` and returns `{text}`. Text is limited to 16,384 UTF-8
  bytes, not characters. It uses bounded, timed xclip `UTF8_STRING` selection transfers
  on the private X display; no text is persisted or logged. Read returns an empty string
   when no UTF-8 text selection is available. Write sets clipboard contents only, never
   synthesizes keys or pastes. The human uses Ctrl+V for literal Unicode/multiline paste.
   A read observes the current selection immediately; it is not a completion barrier for
   a preceding asynchronous RFB Ctrl+C chord and may still return the previous selection.
   The host-only `{browserId,action:"copy"}` instead subscribes to XFixes CLIPBOARD
   ownership notifications before injecting a fixed, balanced native Ctrl+C with XTest.
   It copies the focused native window, including Chromium's address bar and DevTools,
   without page JavaScript selection inspection or changing focus. A fresh, non-synthetic
   owner-set event is required within two seconds, including when text is unchanged.
   The returned UTF-8 selection is bounded to 16 KiB and must retain the observed owner
   and timestamp through the transfer. Duplicate owner/timestamp events from Chromium's
   omnibox are allowed. No event, no UTF-8 selection, or an unsupported extension returns
   `browser.actionFailed`, never the old clipboard as a successful copy. Missing manual
   control returns `browser.busy`. Both key releases are retried in `finally`; failed
   releases disable manual control through the existing input-lock path.
   Copy checks `libX11.so.6`, `libXfixes.so.3`, `libXtst.so.6`, and the private display's
   XFIXES and XTEST extensions on each request. These libraries already ship with the
   existing x11vnc/native prerequisites; no new package or image rebuild is needed for
   copy. Copy-only prerequisites do not change status/readiness or close a healthy runtime
   when unavailable; callers must offer the explicit clipboard-dialog fallback on failure.
   Direct XTest is used because the bundled x11vnc scripted keysym path failed the live
   probe; the existing private x11vnc input-lock protocol is unchanged. RFB selection
   exchange remains disabled, and neither clipboard contents nor key commands are logged.
  Clipboard writes and manual `file.choose` are rejected with `browser.busy` while
  manual input is disabled; clipboard reads remain available to the authorized host.
  Legacy RFB clipboard exchange is disabled with `-nosel` and `-input KMB`, so the UI
  must use the explicit host REST operations rather than Latin-1 CutText or key injection.
  xclip is included in both images and in the fixed native setup prerequisites.
- `open` and `execute` accept `allowNetwork: bool = true`. The host must pass its effective
  policy. `false` refuses open or closes the active runtime before refusing execution;
  a manually browsable offline runtime is intentionally not offered as a network sandbox.
- `integration.browser.file` is a direct UI staging operation, not a model tool.
  All arguments include `browserId`, and all file identifiers are agent-generated UUIDs
  scoped to that live runtime. No paths or caller-chosen identifiers are accepted.
- `file` arguments: `{browserId, action, fileId?:UUID, filename?:str, size?:int,
  offset:int=0, data:base64="", maxBytes:int=65536}`. Unknown fields are rejected.
- `begin` requires a plain `filename` and declared `size`; returns
  `{fileId,offset:0,size,complete:false}`. `fileId` must be omitted.
- `write` requires the returned `fileId`, the exact current append `offset`, and `data`;
  returns `{fileId,offset:<next>,size:<declared>,complete:false}`.
- `finish` requires `fileId` and exact declared size already written; returns
  `{fileId,offset,size,complete:true}`. Pass that UUID as `execute.uploadId` with an
  input selector. Chromium receives the original plain filename, staged bytes, and MIME
  type inferred with Python `mimetypes` (unknown extensions use `application/octet-stream`).
- `choose` takes `{browserId,action:"choose",fileId}` and returns exactly `{ok:true}`.
  This is a human-only operation, not a model action. A website file-input click creates
  a pending Playwright file chooser and suppresses the remote OS file dialog. `choose`
  applies a completed staged upload only to a pending chooser on the focused live page;
  it accepts no selector or tab identifier. No pending/focused chooser, an incomplete
  upload, a download ID, or a stale target returns `browser.invalidTarget`.
- Manual workflow: instruct the human to click the website's upload control, then choose
  a local file in the UI. The host stages `begin`/`write`/`finish`, then invokes `choose`.
  If no chooser is pending, retain `fileId`: after clicking the website control, retry
  `choose` with that same ID without uploading again. Staged data remains until `delete`
  or runtime close, including after successful selection. The pending chooser is consumed
  when selection is attempted; a newly opened chooser is not overwritten by completion.
  Pending choosers are page-scoped, invalidated on frame navigation/page close, and all
  cleared on runtime close. A background page's pending chooser is not selected. Native
  focus is checked with Playwright focus emulation disabled in a private CDP session and
  an isolated JavaScript world, not a page-script-overridable `document.hasFocus` function.
- `read` accepts a completed download UUID from any execute result's `downloads[].id`
  or a temporary `screenshot.fileId`,
  `offset` and `maxBytes`; returns `{fileId,filename,size,offset:<next>,complete:<eof>,data}`.
- `delete` requires `fileId`; removes a staged upload, retained download or screenshot and
  returns `{ok:true}`. Closing the runtime removes all staging but preserves the profile.
  File results are a strict union of the chunk result and `{ok:true}` for `choose`/`delete`.
- Chunk data is at most 65,536 decoded bytes. Files are at most 64 MiB, at most 32
  entries and 256 MiB reserved/retained staging per runtime. Each pending download
  reserves 64 MiB until its actual size is known. Download processes also
  inherit a 64 MiB per-file OS limit; incomplete downloads are never listed as ready.
- Tabs are limited to 32, text to 32,768 characters, and diagnostics to 50 entries of
  500 characters. Scaled screenshots use the ordinary staging limits. Downloads are captured from both
  native clicks and model actions. User uploads are ordinary file-input selections.
- Host-only `integration.browser.control` may additionally include both `origin` (a
  non-empty opaque string of at most 255 characters) and `controlEpoch` (a non-negative
  integer). Omitting both preserves compatibility. Supplying only one is invalid. Each
  download event synchronously captures the current pair before asynchronous transfer work.
  The origin is the controller active when Playwright emits the download event, not causal
  attribution to an earlier click. Delayed site activity emitted after a control handoff
  belongs to the new control epoch; the agent does not attempt impossible causal tracking.
- Host-only `integration.browser.files` takes `{browserId}` and returns
  `{downloads:[{id,filename,status,origin?,controlEpoch?,size?,sha256?,errorCode?}]}`.
  Status is `pending`, `ready`, or `failed`; ready entries have verified size and SHA-256,
  failed entries have a controlled browser error code, and the list remains bounded by the
  existing 32-entry staging cap. Failed entries have no backing bytes and do not consume file
  or byte admission capacity; the oldest failures are evicted when metadata needs room.
  Existing execute results still list ready downloads as
  `{id,filename,size}`.
- Host-only `integration.browser.publish` takes
  `{browserId,fileId,path,expectedOrigin}`. `path` must be an exact absolute direct child of
  `<homeDirectory>/browser/downloads`. The manager creates that directory through the safe
  filesystem API, reads checked worker chunks into a private temporary file, validates the
  ready entry's size and SHA-256, and calls `Filesystem.import_file` with overwrite disabled.
  It caches the `{path,size,sha256}` receipt before deleting worker staging, so an identical
  retry is idempotent; reuse of the same file ID with another origin or path is rejected.
  An existing destination preserves only the controlled `path.exists` agent code, which the
  backend protocol maps to `file.pathAlreadyExists`; other filesystem errors remain masked.
- Host-only `integration.browser.upload` takes `{browserId,path}` and returns the existing
  completed file chunk descriptor. It holds `Filesystem.open_export(path)` while streaming
  its bytes through existing browser `begin`/`write`/`finish` chunks. No durable upload copy
  or additional transport is created; failed staging is deleted when possible.

Native setup downloads the matched Chromium build into the managed installation.
Development smoke tests perform a real sandboxed headed launch and return controlled
readiness codes if namespaces are blocked. Never weaken the browser sandbox to make a
smoke test pass. Tests may explicitly select an existing image's bundled assets to
exercise new code without rebuilding; this is not a runtime fallback.

The development container smoke test also exercises the setup PTY, native
file-input upload, direct browser download, screenshot, persisted cookies across close
and reopen, staging invalidation, network denial, and profile reset. The private binary
relay reconnect and rolling setup replay have separate focused checks.

The additional real headed navigation tests run against the existing dev container without
rebuilding or touching the active user's profile:

```bash
source ./scripts/dev.sh help >/dev/null
compose exec -T --user matoloop-browser -e MATOLOOP_BROWSER_NAVIGATION_TEST=1 \
  matoloop-agent /opt/matoloop-agent/venv/bin/python -m unittest discover \
  -s tests -p test_browser_navigation.py -v
```

They use disposable profiles and verify native chrome hide/show, real native tab clicks,
omnibox focus preservation, native Settings/DevTools, form value retention, exact mobile
and desktop content geometry, new/select/close/navigation/history/SPA/reload/stop/redirects,
popup discovery and focus, manual locks, the 32-tab cap, and the same browser PID/VNC socket.

## Fullscreen Presentation

At startup, the worker disables automatic translation offers with Chromium's
`translate.enabled` preference through the native settings API, then closes the temporary
settings tab before exposing the browser. It does not rewrite profile files or disable
manual translation from the context menu. Existing cookies and unrelated preferences stay
intact. This applies to newly opened runtimes; already running browsers must be reopened.

The fixed `--force-app-mode` launch flag suppresses Chromium's browser-fullscreen entry
instruction and top-edge exit control, without changing the sandbox, profile or transport.
It is not `--app` or `--kiosk`: the ordinary tabbed browser and native DevTools are retained.
Page screenshots and viewport geometry cannot detect these separate native widgets.
`test_fullscreen_desktop_overlays` therefore captures the entire authenticated X root with
Xlib `XGetImage`, writes PNGs using the Python standard library, and compares all desktop
pixels against a uniform test page. It requires no new image packages or transport changes.
Screenshots are written inside the dev container to `/tmp/browser-navigation` (override
with the test-only `MATOLOOP_BROWSER_SCREENSHOTS` environment variable).

On bundled Chromium 145.0.7632.6, both a launch without app mode and `--test-type` showed 19,300
entry-overlay pixels and 2,304 top-edge-control pixels. `--kiosk` reduced both to zero and
CDP could still restore native chrome (1280x713 content on a 1280x800 desktop), but native
F12 DevTools was unavailable even after restoring chrome. The test verifies this negative
result as well as successful DevTools opening without kiosk. `--test-type` is a real
Chromium switch, but has no effect on these overlays; it is not a suppression solution.
The test waits for the baseline toast only to separately exercise the top-edge control;
the actual browser view is never delayed, cropped, masked, or hidden.

Source evidence at the exact bundled Chromium version:

- [ExclusiveAccessManager::GetExclusiveAccessExitBubbleType](https://github.com/chromium/chromium/blob/145.0.7632.6/chrome/browser/ui/exclusive_access/exclusive_access_manager.cc)
  suppresses browser-fullscreen instructions in app mode, not arbitrary site-fullscreen warnings.
- [FullscreenControlHost::IsExitUiEnabled](https://github.com/chromium/chromium/blob/145.0.7632.6/chrome/browser/ui/views/fullscreen_control/fullscreen_control_host.cc)
  disables mouse/touch exit controls only in app mode on Linux; no `test-type` gate exists.
- [IsRunningInAppMode](https://github.com/chromium/chromium/blob/145.0.7632.6/chrome/browser/app_mode/app_mode_utils.cc)
  recognizes kiosk and forced-app mode. The latter's allowlist is applied to native
  accelerators by `BrowserView::LoadAccelerators`, not to all native menu/address controls.
- [DevToolsWindow::AllowDevToolsFor](https://github.com/chromium/chromium/blob/145.0.7632.6/chrome/browser/devtools/devtools_window.cc)
  unconditionally rejects `--kiosk`, without a feature-flag exception.
- [BrowserHandler::SetWindowBounds](https://github.com/chromium/chromium/blob/145.0.7632.6/chrome/browser/devtools/protocol/browser_handler.cc)
  calls the native exclusive-access context directly, explaining why CDP can restore chrome
  in kiosk despite [FullscreenController::ExitFullscreenModeInternal](https://github.com/chromium/chromium/blob/145.0.7632.6/chrome/browser/ui/exclusive_access/fullscreen_controller.cc)
  refusing normal kiosk exits.

The `--force-app-mode` probe verifies that both overlays and reentry overlays have zero
pixels, native chrome restores at 1280x713,
and Ctrl+Shift+I opens native DevTools. Clicking the real native omnibox, selecting its text,
and pasting `chrome://settings` opens the normal Settings UI. Host new/select/navigation/
back/forward/reload and page popups work, and the native clipboard-permission chip and
Block/Allow dialog remain visible. The screenshots include these real native UI surfaces,
not just page screenshots. The comparison test strips the production presentation flag
before applying each candidate, while the default-runtime test verifies the real launch args.

The accepted tradeoff is shortcut availability: F12 (`IDC_DEV_TOOLS_TOGGLE`), Ctrl+L and Ctrl+T
are not registered in forced-app mode. Ctrl+Shift+I (`IDC_DEV_TOOLS`) is explicitly allowed;
the host navigation controls do not depend on these native accelerators. Native window
minimize/maximize controls are also absent. Native tab selection and address editing use
clickable controls. Use the explicit `nativeChrome` toggle to leave embedded fullscreen,
not native F11. The embedded frontend owns Ctrl+L for its address field and Ctrl+T/W for
host tab commands; its focused remote viewer maps F12 to balanced native Ctrl+Shift+I.
These host shortcuts do not broaden the agent's model tool or permission contracts.
