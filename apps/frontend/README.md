# MatoLoop Frontend

The MatoLoop frontend is a Vue 3 SPA using Quasar, Pinia, and Vue Router. In the development stack it runs as a separate HMR process and proxies `/api` to the backend.

## Development

The frontend normally starts from the repository root as part of the development stack:

```bash
./scripts/dev.sh up --build -d
./scripts/dev.sh logs frontend
```

The Web UI is then available at `http://localhost:10020`. Installed packages live in the `frontend_node_modules` named volume; downloaded packages are reused from `frontend_yarn_cache`.

## Structure

- `src/pages`: routable pages
- `src/layouts`: shared navigation and page frames
- `src/components`: reusable UI components
- `src/stores`: Pinia stores for server and runtime state
- `src/app`: application-shell styles and other app-level concerns
- `src/features`: feature-owned state helpers, route metadata, and styles
- `src/shared`: cross-feature foundation and responsive styles
- `src/boot`: Quasar boot files, Axios, and global directives
- `src/composables`: reusable Composition API logic
- `src/directives`: global Vue directives
- `src/utils`: stateless helpers such as error localization
- `src/css`: the ordered global stylesheet entry point and theme variables
- `tests`: Vitest tests

## API Errors and Toasts

The backend returns language-neutral error codes. `src/utils/api-errors.js` translates them into German messages and formats optional `field` and `params` values.

The Axios response interceptor in `src/boot/axios.js` displays API errors centrally as negative Quasar toasts. Components must not display a duplicate notification for the same error.

Failures outside Axios, such as `run.failed` over SSE or a failed OAuth status, use `notifyError` from `src/utils/notifications.js`.

Rules:

- Never render `response.data`, `detail`, arrays, or error objects directly.
- Never compare backend text to control program flow.
- Add new backend codes to `api-errors.js` with a safe message.
- Insert dynamic values only from `params`.
- Use a generic fallback based on HTTP status for unknown codes.
- Roll back optimistic UI entries after failed requests.

The code-to-text mapping can later be replaced by an i18n library. Backend codes and parameters remain unchanged.

## Confirmations and Dialogs

Simple destructive actions use `useConfirm` from `src/composables/confirm.js` or the `v-confirm` directive. This includes deleting sessions, files, and directories.

Quasar dialogs are used when additional input or multiple domain decisions are required. Human-in-the-loop approvals use persistent approval state and must not be denied automatically when the dialog closes.

Menus and dialog surfaces share `app-popup-surface` in `src/shared/styles/_menus.scss`. Existing dialog cards inherit the same surface through the direct Quasar dialog-card selector, including mobile select dialogs. Maximized dialogs retain square, borderless edges.

Inputs, selects (including chat-start controls), file uploads and textareas share responsive sizing in `src/shared/styles/_fields.scss`: 34px and 14px text on desktop mouse devices, 44px and 16px on narrow or touch devices. Textareas and fields with multiple selection/file chips can grow beyond this base height. Only composer controls keep their existing sizing. Field widths follow their form layout without a global cap. Only grouped session settings in the Control Center use equal-width columns when their container can fit two 20rem fields and a 16px gap; narrower panels use one column. Hints and errors wrap in normal flow. The centralized Quasar-internal selectors must be reviewed when upgrading Quasar.

## UI Rules

- Prefer Quasar components and utilities over new custom CSS.
- Pages, dialogs, and drawers must be usable on desktop and mobile.
- Support both light and dark mode.
- Write visible user-facing text in German.
- Stores manage server state; components coordinate presentation and user interaction.
- Display effective runtime state, not only stored desired values.
- Display the unified Webfetch policy and the instance-controlled public-address restriction as separate controls.
- Attachment URLs must be authenticated internal MatoLoop paths. External URLs and `v-html` are forbidden for tool results.
- Render assistant responses only through the central Markdown component. It disables raw HTML, sanitizes generated HTML, and is the only permitted `v-html` exception.
- The Markdown component renders KaTeX-supported LaTeX with `$...$`, `$$...$$`, `\(...\)`, and `\[...\]`. Trust-dependent KaTeX commands are disabled; required styles remain confined to generated KaTeX subtrees.
- External Markdown images remain inactive until an explicit session-scoped origin approval. Each concrete URL must then pass browser loading and image decoding; failures and timeouts remain visible as retryable placeholders. Internal images load automatically only through validated MatoLoop attachment paths.

## Browser Integration

The Browser app uses the locally bundled `@novnc/novnc` 1.5.0 client (`lib/rfb.js`). Version 1.6.0's published CommonJS package contains an invalid top-level `await` and does not build with this Vite setup. The setup dialog uses the existing xterm 6.0.0 and fit addon, independently of chat and the shared Bash terminal.

The Browser app opens once when its selected, visible tab and open panel are confirmed in the persisted session Control Center state. Hidden `v-show` mounts, unconfirmed tab changes, and draft sessions never start it. Errors do not trigger reconnect loops; the retry icon or a new tab activation can reconnect. Matching connecting/open control and noVNC sockets survive tab switches, collapse, and responsive panel remounts. Agent attachment remains an explicit footer action. Sessionless browser use is not supported.

One icon toolbar contains clipboard/file actions and an overflow menu for integration selection, status, setup, disconnect, and scaling. A content-box `ResizeObserver` requests actual desktop dimensions in CSS pixels via the control socket: `{type: 'resize', width, height}`. Dimensions are rounded down and clamped to width 320..3840 and height 240..2160, after rejecting hidden/zero boxes. Requests are debounced by 200 ms with one in-flight request and one latest target, including across panel remounts. The server must acknowledge every request with `{type: 'resized', browserId, width, height}` (actual applied size) or `{type: 'error', operation: 'resize', error: {code, message}}`. Resize failure does not close a healthy connection or retry the same size automatically; the menu offers an explicit resolution retry. The backend/agent must preserve the browser and VNC stream and resize the real desktop/window, not a fixed Playwright viewport. noVNC `resizeSession` stays false. Default `scaleViewport: true` supplies fit fallback while resizing and beyond the supported bounds; at matching actual dimensions this is 1:1. The separate menu scaling preference changes only rendering, never substitutes a fixed 1280x800 desktop for adaptive resolution.

The live store owns both control and noVNC connections, including the viewer host, so collapsing or remounting the panel does not reconnect or detach. Leaving the chat/session context releases the view with a 30-second runtime grace period; only the explicit Browser close action requests immediate runtime shutdown. When occupied, the app offers an explicit takeover without restarting Chromium, and the old view becomes replaced without automatic reclaim. Agent attachment always requires an explicit user action. Manual takeover from the agent sends detach even while busy; manual input stays locked until the server confirms both detached and no longer busy. The server's `attached`, `busy`, `canAttach`, and `attachReason` remain authoritative; profile exclusion never adds a second enable setting. Ctrl+Alt+Escape returns keyboard focus from the remote canvas to the local controls.

The backend must accept the persisted Control Center app ID `browser`. Personal integration CRUD uses `/api/integrations/browser?scope=personal` and its `/{id}`, `/{id}/status`, `/{id}/test`, and `/{id}/reset` routes; list and mutation responses contain `{configured, instances}`. The integration store keeps these under `browser.instances`.

Control uses `/api/browsers/{integrationId}?sessionId=...`, and noVNC uses the binary `/{integrationId}/view?sessionId=...&browserId=...&viewId=...` stream. The current control state supplies `viewId`; takeover replaces it, and stale views and HTTP results cannot affect the successor. An announced control message `{type: 'view.reset'}` invalidates and disconnects only the old noVNC view, preserving the control socket, browser ID, view ID, and viewer parent. A subsequent state with `attached: false` and `busy: false` reconnects that view without reopening the browser or attaching the agent. The backend must send this state only after manual unlock and old-stream cleanup; unannounced view loss still releases the connection. Setup uses `/{integrationId}/setup`, JSON state/input/resize/start/status messages, and binary terminal output. It never sends `start` when opening a viewer and never stops installation when closing it. The complete takeover and release contract is documented in [Browser architecture and operations](../../docs/browser.md).

Transfers use `/api/browsers/{integrationId}/downloads`, `/upload`, and `/download/{downloadId}`, always with `browserId`, `sessionId`, and `viewId`. The downloads response is the browser action result object with a `downloads` array of `{id, filename, size}`. Upload sends multipart field `file` and receives `{uploadId, chosen}`. Users first click the website's upload field, then select a local file. If `chosen` is false, the UI retains the opaque staging ID and offers an explicit POST to `/upload/{uploadId}/choose`, returning `{ok: true}`, after opening a website file chooser. No agent, model, filesystem path, or arbitrary selector is involved. Successful selection clears the staged ID and local file selection. Download returns an attachment; blob errors use the existing central Axios decoder.

Clipboard access is manual-only and limited to less than 16 KiB of UTF-8 text. Existing GET `/api/browsers/{integrationId}/clipboard?browserId=...&sessionId=...&viewId=...` and PUT with `{text}` both return `{text}`. Ctrl shortcuts are captured on the focused visible viewer's ancestor before noVNC's canvas keyboard handlers. Ctrl+V leaves the native paste default intact, accepts only a trusted `text/plain` paste event without files, awaits PUT, and revalidates view generation, focus, visibility, and manual access before a balanced RFB Ctrl+V chord. Text characters are never injected as keys. A missing native paste event opens the existing dialog; its explicit PUT only sets the clipboard, without sending a chord.

Ctrl+C immediately POSTs an empty body to `/api/browsers/{integrationId}/clipboard/copy?browserId=...&sessionId=...&viewId=...`. The agent subscribes to native CLIPBOARD completion before sending a fixed native Ctrl+C to the focused remote window, then returns confirmed `{text}`, including when the content is unchanged. The frontend sends no RFB copy chord and does not wait for RFB clipboard events. In secure contexts, a promise-backed `ClipboardItem` reserves local write permission in the original user gesture; without that API, `writeText` is attempted after the response as best effort. View/context, focus, and manual-access changes discard pending results before local copying. Denied or unsupported local writes retain the confirmed text in the dialog, ready for explicit local copy without another GET. Remote failures use the central Axios error notification and offer an explicit clipboard read. The paste dialog instructs users to insert staged text using the remote context menu, because Ctrl+V intentionally transfers the local clipboard again. Local UI controls retain native shortcuts; Meta shortcuts remain deferred.

## Checks and Tests

From the repository root:

```bash
./scripts/dev.sh check frontend
./scripts/dev.sh test frontend
```

Directly from `apps/frontend`, when Yarn dependencies are already installed:

```bash
yarn lint
yarn test
yarn build
```

New error codes require tests for the translated message and safe fallback. Verify UI changes on desktop and mobile in both light and dark mode.
