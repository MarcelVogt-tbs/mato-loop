# Integrations and Tool Contributions

Integrations extend the MatoLoop harness without bypassing the provider-neutral agent loop or permission boundary. A persisted instance names an integration type and a host-known adapter. The adapter validates its configuration and produces only statically implemented contributions.

## Contribution Types

An adapter can:

- contribute a new tool with a stable name, Pydantic input model, and host handler,
- add values to an explicitly extensible host configuration field of an existing tool,
- provide a static host-controlled prompt fragment for a tool that is actually offered.

Every contributed tool declares its host-controlled group, German UI metadata, and security actions. A configurable action requires a host-side authorization factory that creates typed permission requests from validated arguments. Integration configuration and remote responses cannot provide groups, capabilities, resource matchers, or authorization behavior.

Persisted configuration and external responses must not provide handlers, arbitrary JSON schemas, or prompt instructions. Duplicate tool names and contributions to undeclared configuration fields are programming errors. Host configuration values never become model arguments merely because an adapter contributes them.

## Effective Tool Set

Before every model request, the registry combines built-ins, session mode, model capabilities, tool policies, and active integration contributions into an immutable tool set. This object exports provider schemas and validates returned calls. A provider therefore cannot execute a tool that was not offered for that request.

A binding also records allowed option values and the associated integration ID. Immediately before external access, the handler reloads the current integration state. Deleted or disabled instances are therefore ineffective even if they were offered in the preceding model request.

The binding also records a canonical revision of the source instance. Configuration, credential, or identity changes invalidate the stale call rather than executing its captured handler. Provider options selected by host settings are normalized before binding and must still resolve to the same values at execution time.

## Web Search

For the model, `web_search` exposes only the `query` field. The host selects primary and fallback providers from `ToolSettings.web_search`; model arguments cannot override this choice. The no-key metasearch is always present in the tool-provider catalog as `no_key`, but it is not an integration. Every active SearXNG instance adds `searxng:<uuid>`. Display names are labels only; renaming does not change the stable tool identifier.

Each normalized result contains `sourceId`, `title`, `url`, `snippet`, and `publishedDate`. Only the host derives `sourceId` from the canonical URL; providers cannot supply it. Results with the same canonical URL are deduplicated. Snippets are untrusted discovery data, not verified page content. Invalid provider JSON produces a stable integration error rather than technically successful empty evidence.

Primary and fallback providers are configured under **Einstellungen > Tools**. The fallback is optional and must differ from the primary provider. When SearXNG returns no results and reports unresponsive default engines, MatoLoop retries once through the same instance with `bing`, `mojeek`, and `presearch`. The configured fallback is used only after a remaining transient error: network failure, timeout, HTTP 408, HTTP 429, HTTP 5xx, or still-unresponsive SearXNG engines. Authentication, configuration, and protocol failures, as well as genuine empty result sets, do not trigger fallback. Disabling or deleting a selected integration makes the service transaction normalize the primary provider to no-key metasearch and remove an invalid fallback.

The built-in no-key metasearch uses the Python `ddgs` client with automatic backend selection and runs the synchronous call outside the async loop with a bounded timeout. Different public search services may respond depending on current availability. The persisted legacy key `duckduckgo` is normalized to `no_key` when loaded. Search queries leave the installation and are sent to the service in use.
