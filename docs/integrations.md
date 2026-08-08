# Integrations and Tool Contributions

Integrations extend the MatoLoop harness without bypassing the provider-neutral agent loop or permission boundary. A persisted instance names an integration type and a host-known adapter. The adapter validates its configuration and produces only statically implemented contributions.

## Contribution Types

An adapter can:

- contribute a new tool with a stable name, Pydantic input model, and host handler,
- add values to an explicitly extensible host configuration field of an existing tool,
- provide a static host-controlled prompt fragment for a tool that is actually offered.

Every contributed tool declares its host-controlled group, German UI metadata, and security actions. A configurable action requires a host-side authorization factory that creates typed permission requests from validated arguments. Integration configuration and remote responses cannot provide groups, capabilities, resource matchers, or authorization behavior.

Authorization factories also provide the German approval title, concise action summary, and resource display from validated host data. The frontend renders this common permission metadata and does not interpret integration-specific tool arguments. Display text never participates in authorization matching.

Persisted configuration and external responses must not provide handlers, arbitrary JSON schemas, or prompt instructions. Duplicate tool names and contributions to undeclared configuration fields are programming errors. Host configuration values never become model arguments merely because an adapter contributes them.

## Resource Policies

An adapter may optionally declare host-controlled resource actions and an asynchronous resource loader. Resource actions must exactly match actions of a tool contributed by that adapter and that tool must have an authorization factory; tool-option integrations are unsupported until their built-in tool explicitly binds resource policy targets. The loader receives the validated integration instance and an optional decrypted credential payload, but no database session. It returns only stable adapter-generated keys and display labels, for example calendars or repositories. External services never define capabilities, policy modes, matchers, or prompt text. Resource keys use a bounded host-safe character set and labels are rendered only as untrusted UI text.

Global action defaults remain under **Settings > Tools**. Per-resource overrides are configured from the integration and use `disabled`, `ask`, or `auto`; an absent value inherits the global default. Overrides are stored in `ToolSettings` by integration UUID and resource key rather than mixed into connection configuration. Deleting an integration removes its overrides, while disabling it preserves them.

An integration may also define sparse instance defaults. Policy resolution is global action, integration instance, then resource. A globally disabled action and a disabled integration are hard limits. Remote ACLs are mapped only to statically declared host capabilities and may make an action unavailable, but can never define or enable actions. The API and runtime use the same resolver and expose the effective policy rather than asking the frontend to reproduce precedence rules.

Tools declare policy actions independently of tool names. Multiple tools may share one action when they have the same security meaning, for example calendar create and update both using `calendar.write`. A concrete permission request keeps its policy target, integration plus resource, separate from its exact session-grant scope.

Resource discovery occurs before the settings transaction. Saving then locks integration settings, verifies that the instance revision is unchanged, locks tool settings, replaces only that integration's overrides, and commits once. Existing adapters without a resource loader expose no access editor and keep their current behavior.

## Effective Tool Set

Before every model request, the registry combines built-ins, session mode, model capabilities, tool policies, and active integration contributions into an immutable tool set. This object exports provider schemas and validates returned calls. A provider therefore cannot execute a tool that was not offered for that request.

A binding also records allowed option values and the associated integration ID. Immediately before external access, the handler reloads the current integration state. Deleted or disabled instances are therefore ineffective even if they were offered in the preceding model request.

The binding also records a canonical revision of the source instance. Configuration, credential, or identity changes invalidate the stale call rather than executing its captured handler. Provider options selected by host settings are normalized before binding and must still resolve to the same values at execution time.

Adapters with multiple active instances may contribute one stable shared tool set. The effective binding contains only the instances offered for that model request. A validated instance argument can select only one of those host-bound instances; aggregate discovery tools receive the complete immutable bound set. The selected binding is checked before authorization and again after approval.

## CalDAV

The CalDAV adapter is the first production user of shared integration tools and resource policies. Each account is an integration instance and each discovered calendar is a resource. The static actions are `calendar.read`, `calendar.write`, and `calendar.delete`; create and update share the write action. Basic or app-password credentials are encrypted separately from settings.

The adapter discovers calendars live and maps DAV privileges to hard unavailable host actions. Models receive only integration UUIDs, opaque calendar keys, and encrypted event tokens, never DAV URLs, ETags, credentials, XML, or iCalendar source. Every returned event includes its integration UUID and calendar key so later update or delete calls can reuse the exact authenticated token binding without discovery. Timed values are canonical UTC instants at the CalDAV boundary; all-day dates remain date values, and ambiguous remote timed values are rejected. Timed event results additionally contain host-derived `localTiming` for `Europe/Berlin`, including DST-correct offsets and weekdays, so models do not perform calendar arithmetic for user-facing output. Creates use a host-generated deterministic UID and `If-None-Match`; updates and deletes use ETags with `If-Match`. Event-token versions prevent pre-change timing snapshots from being reused after serialization semantics change. Recurring or scheduling-bearing events and events with unrepresented properties are not mutated.

`ToolSettings.default_calendar` may select one installation-wide account and calendar resource. The effective query and create schemas then allow the model to omit both target arguments, and the system prompt requires this path unless the user explicitly names another calendar. Discovery tools are not used only to choose a target when the default applies. The bound host inserts the configured pair before validation, authorization, and live resource resolution. A complete explicit pair always wins; partial target arguments never mix with the default. Disabled or stale targets fail closed, and changing or deleting the selected integration removes the setting.

Shared integration schemas enumerate the active host-bound integration UUIDs. Runtime binding and revision checks remain authoritative when a model or provider ignores that schema constraint. Calendar create idempotency is derived from the persisted assistant message and tool call instead of being supplied by the model.

Validated resource discovery stores bounded resource labels in `ToolSettings.resource_labels` for later approval display. CalDAV approvals combine that label with the configured account name and summarize the requested event fields and timing. Missing labels fall back to the stable opaque resource key; labels remain display metadata and never replace the key in policy or grant matching.

CalDAV tools release private integration data. A public model therefore needs an explicit session release in addition to the configured calendar action policy. This release is a data-transport boundary and cannot be replaced by `calendar.read=auto`.

## BookStack

The BookStack adapter is a knowledge integration. Each BookStack installation is an integration instance and each visible book is a resource. The static actions are `knowledge.read`, `knowledge.write`, and `knowledge.delete`; page, chapter, and book creation as well as page, chapter, and book updates share the write action. Book and chapter updates change name and description only; shelf membership is managed from the shelf side. API token ID and token secret are encrypted separately from settings and are sent as `Authorization: Token <id>:<secret>` over HTTPS only.

Content is exchanged as Markdown. Reading a page prefers the stored Markdown source and falls back to the Markdown export when the page was authored in the WYSIWYG editor. Responses are truncated at host-controlled size limits and remote text is treated as untrusted data.

Page updates replace the whole page, so the model must read the page immediately before updating it. The update response returns the stored Markdown, which lets the model verify what BookStack actually saved instead of trusting its own request.

Models receive only integration UUIDs, opaque book keys, and encrypted page and chapter tokens, never raw BookStack IDs, URLs, or credentials. A token carries the integration ID and the binding revision, so a stale or foreign token is rejected instead of executed. Page reads, updates, and deletes additionally verify that the resolved page still belongs to the book named by the resource key, so a token cannot escape its per-book policy.

Search and book listing are integration-wide discovery tools without a book key, analogous to calendar discovery. Per-book resource policies therefore do not narrow them; only the integration-level `knowledge.read` policy and the session data-release boundary apply. Deletion supports `disabled` and `ask` only.

Shelves are organizational containers, not resources. Shelf tools therefore take a plain `shelf:<id>` key instead of an encrypted token and are governed by the integration-level policy only, like book creation. Book creation accepts shelf keys so a new book is filed immediately, and book listings and reads report the shelves a book belongs to. BookStack does not expose shelf membership on the book endpoint, so the adapter derives it by scanning the shelves; when that scan fails the field is omitted instead of failing the whole call. Shelf updates support a full replacement as well as incremental add and remove lists, which avoids dropping books the model never saw. Combining both forms in one call is rejected. Deleting a shelf never deletes the books it contains.

Attachments belong to pages. The adapter can list, create, and delete them and returns encrypted attachment tokens bound to the same integration, binding revision, and book key as page tokens. An attachment is created from exactly one source: a URL that is downloaded and uploaded to BookStack, a file in session or workspace storage, or a plain link that is stored as a reference without any transfer. Downloads reuse the webfetch pipeline, so the same SSRF, redirect, content type, and size protections apply, the configured webfetch policy is enforced, and a URL approval is requested whenever a direct webfetch would need one. Uploads are capped at 32 MiB and at the configured webfetch response limit, whichever is smaller.

A failed download reports the underlying webfetch code, such as a timeout or a rejected request, instead of one opaque failure. The model then sees why the host refused and can fall back to a link attachment rather than guessing at an unrelated cause. Servers that label documents as `application/octet-stream` are accepted when the file name still names a supported type; the extension then decides the type, and image payloads are still signature checked.

## Web Search

For the model, `web_search` exposes only the `query` field. The host selects primary and fallback providers from `ToolSettings.web_search`; model arguments cannot override this choice. The no-key metasearch is always present in the tool-provider catalog as `no_key`, but it is not an integration. Every active SearXNG instance adds `searxng:<uuid>`. Display names are labels only; renaming does not change the stable tool identifier.

Each normalized result contains `sourceId`, `title`, `url`, `snippet`, and `publishedDate`. Only the host derives `sourceId` from the canonical URL; providers cannot supply it. Results with the same canonical URL are deduplicated. Snippets are untrusted discovery data, not verified page content. Invalid provider JSON produces a stable integration error rather than technically successful empty evidence.

Primary and fallback providers are configured under **Einstellungen > Tools**. The fallback is optional and must differ from the primary provider. When SearXNG returns no results and reports unresponsive default engines, MatoLoop retries once through the same instance with `bing`, `mojeek`, and `presearch`. The configured fallback is used only after a remaining transient error: network failure, timeout, HTTP 408, HTTP 429, HTTP 5xx, or still-unresponsive SearXNG engines. Authentication, configuration, and protocol failures, as well as genuine empty result sets, do not trigger fallback. Disabling or deleting a selected integration makes the service transaction normalize the primary provider to no-key metasearch and remove an invalid fallback.

The built-in no-key metasearch uses the Python `ddgs` client with automatic backend selection and runs the synchronous call outside the async loop with a bounded timeout. Different public search services may respond depending on current availability. The persisted legacy key `duckduckgo` is normalized to `no_key` when loaded. Search queries leave the installation and are sent to the service in use.
