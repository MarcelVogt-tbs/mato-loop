# Webfetch and JavaScript

`webfetch` has one optional `javascript` argument. Omitted or `null` uses the global
`web_fetch.javascript_hosts` setting (`javascriptHosts` in the administration API).
Administrators open **JavaScript-Konfiguration** next to **Nur öffentliche Adressen zulassen**
under **Administration > Tools > Einstellungen**. The dialog's **JavaScript aktivieren**
switch controls `web_fetch.javascript_enabled` (`javascriptEnabled` in the API),
defaulting to true for existing settings. When disabled, omitted/null defaults use HTTP
and an explicit `javascript=true` fails with `webfetch.javascriptDisabled` before
network access or approval; there is no silent fallback. The host list remains saved
but is hidden in the dialog. When enabled, **Websites mit automatischem JavaScript**
edits the list below; it is a default-selection list, not an access allowlist.
**Übernehmen** updates
the settings form; **Speichern** on the page persists it. Closing or cancelling the
dialog discards its draft. Use one exact DNS hostname per line, without
URLs, IP addresses, ports or wildcards. The default list is `reddit.com` and `www.reddit.com`;
existing settings receive that default. Entries are trimmed, normalized to lowercase
IDNA ASCII without trailing dots, and deduplicated. At most 100 entries are accepted.
An empty list defaults all hosts to HTTP. Only the normalized requested hostname is
matched, not subdomains or redirect destinations. While JavaScript is enabled,
explicit `true` or `false` overrides this choice. The list is neither model-facing
nor learned automatically.
Selection happens once, before authorization, with no automatic retries or engine fallback.

There is no administrator engine selection. Previously stored `engine` fields are
ignored on load and removed on the next settings save; other unknown Webfetch settings
remain invalid. Availability, User-Agent, response size, and address restrictions stay
under **Administration > Tools > Einstellungen**. Neither fetch mode requires a
computer or personal integration. The existing computer browser is independent.

An initial bounded HTTP request determines the content type. PDFs, images,
structured data, plain text, and Markdown keep their existing processing.
HTTP requests prefer the requested Markdown, text, or HTML format using weighted
Accept headers. The actual response content type determines processing. With
JavaScript enabled for HTML, Lightpanda loads the original URL itself, including scripts, cookies,
binary request bodies, redirects, and hash routes. This means HTML is requested
again after the content-type check. The resulting DOM is sanitized and converted
by the existing extraction code. Native browser subrequests retain Lightpanda's
own Accept headers rather than overriding scripts and API requests with a page-format preference.

For missing dynamic content, the model can explicitly request JavaScript. An HTTP
error or consent page alone does not establish that need. General research should
prefer another suitable source; browser control is appropriate when the task requires
interaction with a specific page or browser-authenticated access.

## Model result size

The complete result remains persisted. Before token estimation, the model projection
limits large Webfetch content to 15,000 original characters: approximately 75% from
the beginning and 25% from the end, cut at line boundaries when possible. An explicit
omission marker and `toolResultReference` point to `session_tool_result_read` for the
full stored result. Its offsets count characters in the complete serialized result,
not just the page body. Source metadata and attachments remain available. This applies
to current results and retained results from the preceding turn; older results still
follow historical-reference rules. Further context-budget shortening preserves the
original reference. Inner JSON or other structured text may be incomplete in the
projection; the outer result envelope remains valid.

Reading omitted content uses the stored result, not another fetch. A new `webfetch`
still makes a fresh network request. There is no separate result cache or LLM summary.

## Authorization and networking

With JavaScript enabled, `network.fetch` authorizes the initial canonical URL
once. That page authorization includes its scripts, data requests, and redirects;
there are no separate approvals for subresources. The initial transport still
validates and pins addresses, including after approval. The browser uses its own
native transport, not CDP interception or replacement HTTP responses.

**Nur öffentliche Adressen zulassen** enables Lightpanda's native
`--block-private-networks` plus explicit `--block-cidrs` enforcement after DNS
resolution. The CIDR policy matches MatoLoop's public-address classification,
including CGNAT/metadata, documentation, multicast, reserved IPv4/IPv6, and IPv6
site-local ranges, while retaining the IANA public exceptions. A boundary test
checks parity with the Python runtime; mapped IPv6 addresses use IPv4 rules.
The final URL is also
validated before returning content. Disabling the setting deliberately permits
targets reachable from the backend. Without JavaScript, HTTP retains exact-URL
authorization and DNS pinning on every redirect.

Fragments are preserved in browsing URLs, results, source identities, saved tool
arguments, and displayed citations. Network requests and permission matchers
remain fragment-free. A page's own network actions are part of loading it; no
public POST or browser-interaction arguments are added to webfetch.

## Runtime

The backend copies the finished binary from official Lightpanda 1.0.0, pinned to
`sha256:5b84708cb3d9bef841aba4a4cd299f4de0609ac1bd7d4c6fbfcbf168d56b685e`.
Lightpanda is AGPL-3.0 licensed; source: <https://github.com/lightpanda-io/browser>.
There is no local engine patch, compiler, Node, Playwright, or additional service.

Each render runs `lightpanda fetch --json --dump html` in a fresh process with a
minimal environment, disabled telemetry and core dumps, and no shared profile or
disk cache. Cookies last only for that process. Two renders may run concurrently
per backend process. Queue time and execution consume the remaining tool timeout;
approval wait does not. Cancellation, errors, and timeouts kill and reap the process.

The configured response-size limit applies to each native HTTP response and to
the final DOM output, not the aggregate of all resources. Captured JSON output is
bounded with an allowance for JSON escaping. V8 heap is limited to 128 MiB (not the
whole process), and a five-second watchdog limits stalled JavaScript. Native
network-idle waiting explicitly uses the remaining tool budget (`--wait-ms`),
alongside the native HTTP and termination deadlines; it does not use the
engine's five-second page-wait default. Stylesheets and workers
are enabled; images and iframes use the engine's disabled defaults.

Both engines use the configured User-Agent, default `MatoLoop/1.0`. The exact old
shipped Chrome default is normalized when loading settings; custom values remain.
Lightpanda rejects User-Agents containing `Mozilla`; this is reported only when an
HTML JavaScript fetch needs it. HTTP and non-HTML fetches accept those values. Web search
is separate and its clients manage their own headers.

## Compatibility

Native redirects work without the former CDP redirect workaround. Lightpanda
1.0.0 still has an upstream issue with relative imports in redirected ES modules;
an explicit expected-failure regression records it for future binary upgrades.
The engine may return partial page content when a script or subresource fails.
It does not guarantee Chromium compatibility, passage through bot challenges,
or support for every layout-dependent application.
