# MatoLoop Frontend

Vue 3, Quasar, Pinia, and Vue Router provide the German application UI. Development runs as a separate HMR process proxying `/api` to the backend; production packages an installable PWA with MatoLoop.

## Development

From the repository root:

```bash
./scripts/dev.sh up --build -d
./scripts/dev.sh logs frontend
./scripts/dev.sh check frontend
./scripts/dev.sh test frontend
```

The default URL is `http://localhost:10020`. Installed packages live in `frontend_node_modules`; downloads use `frontend_yarn_cache`. Use `up --pwa --build -d` for PWA development. See [development](../../docs/development.md) for overrides and worktree isolation.

## Structure

- `src/app`: application shell, navigation, layout, and compiled Control Center manifest.
- `src/features`: domain-owned pages, components, stores, routes, and helpers.
- `src/shared`: cross-feature UI, foundations, and responsive styles.
- `src/router`: route composition and access guards.
- `src/boot`: Axios and Quasar boot integration.
- `src/stores` and moved `src/utils` entries: stable stateless compatibility re-exports; implementations belong to features/shared modules.
- `src/composables`, `src/directives`, and `src/css`: remaining shared entry points and global stylesheet composition.
- `tests`: Vitest component and contract tests.

See [module ownership](../../docs/modularity.md). Register Control Center apps in `src/app/control-center/manifest.js`; persisted IDs are inert data, not component paths or remote code.

## API Errors and Runtime State

The central error formatter, exposed through `src/utils/api-errors.js`, translates stable backend codes into German. The Axios response interceptor displays one negative Quasar toast; components must not duplicate it. Unknown codes use a safe HTTP-status fallback, dynamic values come only from `error.params`, and structured errors/raw backend text must never be rendered or compared directly.

Persistent agent failures appear as canonical MatoLoop chat messages. SSE `run.failed` ends the stream and synchronizes persisted state; it is not another toast for the same runtime error. Non-persistable transport failures and errors outside Axios use `notifyError` from `src/utils/notifications.js`.

Stores own server state and effective runtime policy. Components present it and coordinate interaction. Failed mutations must not leave optimistic state behind. Approvals/questions are loaded independently of SSE, survive UI reconnects, and stay pending when their UI is dismissed. See [questions](../../docs/questions.md) and [run continuations](../../docs/run-continuations.md).

## UI Rules

- Write visible application text in German; support desktop/mobile and light/dark mode.
- Prefer Quasar components and existing utilities. Use `useConfirm` or `v-confirm` for destructive actions; use Quasar dialogs for structured input and decisions.
- Dismissing an individual notification needs no confirmation; remove it only after successful server deletion.
- Use `shared/ui/AppPage.vue` for ordinary content pages. Existing shared field/menu styles supply responsive sizing and popup surfaces.
- Keep dialogs and drawers usable on small viewports. Avoid default tooltips; accessible names must stand independently.
- Display Webfetch policy and its instance-controlled public-address restriction separately.
- Render assistant Markdown only through the central sanitized renderer. Raw HTML is disabled; its sanitized output is the permitted `v-html` exception.
- KaTeX math supports `$...$`, `$$...$$`, `\(...\)`, and `\[...\]`; trust-dependent commands remain disabled.
- External Markdown images require explicit session-scoped origin approval and successful decoding. Internal images use validated authenticated MatoLoop paths.
- Keep file content and editor drafts out of Control Center layout metadata. Closing a dirty file tab requires confirmation; ordinary app-tab closure does not.

See [appearance and themes](../../docs/appearance.md) for branding, compiled theme bundles, and loading/fallback rules.

## Browser Integration

The feature store owns control and optional noVNC connections. Visible confirmed Browser-app activation opens the container viewer; hidden mounts do not start it. Native Linux agents automate headlessly. Model-requested control uses the backend approval/attachment path independently of a graphical viewer.

Website tabs and address/back/forward/reload controls accompany the file/clipboard toolbar. Panel collapse, remounts, and chat switches retain the original controlling browser and model attachment; another chat requires takeover. **Manuell übernehmen** revokes model control, and input stays locked until the server confirms the action has settled. Explicit close ends the runtime.

The bundled noVNC client is 1.5.0; 1.6.0's published CommonJS package contains a top-level `await` incompatible with this Vite setup. Desktop dimensions use bounded, coalesced control-socket resize requests rather than a fixed emulated viewport. Ctrl+Alt+Escape releases remote keyboard focus.

Manual upload picks an existing **Ablage** or **Computer-Dateien** file via `/upload-source`; local uploads first enter the ordinary file store. Completed downloads publish below the selected computer's `homeDirectory/browser/downloads`, with collision protection. Clipboard transfers are explicit/manual-only, bounded UTF-8, and revalidate current view/focus/control. They never persist clipboard content.

The complete RPC, resize, clipboard, transfer, takeover, and security contract lives in [Browser architecture and operations](../../docs/browser.md), with agent-local details in [BROWSER.md](../matoloop-agent/BROWSER.md).

## Validation

Use the development wrapper above. Add coverage only for a concrete missing regression protection. For UI changes, verify the affected interaction; check desktop/mobile when layout is affected and light/dark when appearance is theme-dependent. New API errors need both a controlled backend contract and readable frontend localization. See [AGENTS.md](../../AGENTS.md).
