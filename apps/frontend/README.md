# MatoLoop Frontend

The MatoLoop frontend is a Vue 3 SPA using Quasar, Pinia, and Vue Router. In the development stack it runs as a separate HMR process and proxies `/api` to the backend.

## Development

The frontend normally starts from the repository root as part of the development stack:

```bash
./scripts/dev.sh up --build -d
./scripts/dev.sh logs frontend
```

The Web UI is then available at `http://localhost:10020`. Installed packages live in the `frontend_node_modules` named volume; `frontend_yarn_cache` provides an additional cache mount.

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

## UI Rules

- Prefer Quasar components and utilities over new custom CSS.
- Pages, dialogs, and drawers must be usable on desktop and mobile.
- Support both light and dark mode.
- Write visible user-facing text in German.
- Stores manage server state; components coordinate presentation and user interaction.
- Display effective runtime state, not only stored desired values.
- Display Webfetch policies separately for public and private targets; instance-controlled tool and target availability remain hard boundaries.
- Attachment URLs must be authenticated internal MatoLoop paths. External URLs and `v-html` are forbidden for tool results.
- Render assistant responses only through the central Markdown component. It disables raw HTML, sanitizes generated HTML, and is the only permitted `v-html` exception.
- The Markdown component renders KaTeX-supported LaTeX with `$...$`, `$$...$$`, `\(...\)`, and `\[...\]`. Trust-dependent KaTeX commands are disabled; required styles remain confined to generated KaTeX subtrees.
- External Markdown images remain inactive until an explicit session-scoped origin approval. Each concrete URL must then pass browser loading and image decoding; failures and timeouts remain visible as retryable placeholders. Internal images load automatically only through validated MatoLoop attachment paths.

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
