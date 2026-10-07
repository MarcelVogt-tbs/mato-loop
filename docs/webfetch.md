# Webfetch engines

Administrators select **Lightpanda – mit JavaScript (Standard)** or
**HTTP – ohne JavaScript (Kompatibilität)** under **Administration > Tools > Einstellungen**.
This instance-wide choice needs neither a computer nor a personal integration.
The existing computer browser is independent.

An initial bounded HTTP request determines the content type. PDFs, images,
structured data, plain text, and Markdown keep their existing processing.
For HTML, Lightpanda loads the original URL itself, including scripts, cookies,
binary request bodies, redirects, and hash routes. This means HTML is requested
again after the content-type check. The resulting DOM is sanitized and converted
by the existing extraction code. There is no automatic engine fallback.

## Authorization and networking

With the Lightpanda engine, `network.fetch` authorizes the initial canonical URL
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
targets reachable from the backend. HTTP compatibility mode retains exact-URL
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
Lightpanda rejects User-Agents containing `Mozilla`; HTTP accepts them. Web search
is separate and its clients manage their own headers.

## Compatibility

Native redirects work without the former CDP redirect workaround. Lightpanda
1.0.0 still has an upstream issue with relative imports in redirected ES modules;
an explicit expected-failure regression records it for future binary upgrades.
The engine may return partial page content when a script or subresource fails.
It does not guarantee Chromium compatibility, passage through bot challenges,
or support for every layout-dependent application.
