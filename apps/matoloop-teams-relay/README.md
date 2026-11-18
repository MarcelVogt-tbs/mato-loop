# MatoLoop Teams relay

Private MatoLoop instances connect outbound over HTTPS to this standalone
Python/FastAPI transport, using `microsoft-teams-apps` 2.2.1. The relay never runs
models or tools. Follow the German [operator procedure](../../docs/user/kanaele.md#teams-verbinden)
for Microsoft registration, deployment, pairing, and verification.

## Deployment boundary

Run one Uvicorn worker and one replica per instance. The exclusive spool lock
rejects concurrent replicas sharing the same volume. Microsoft credentials are
secret files; the private API has its own independent random bearer token.
The [explicit deployment template](compose.yaml) builds from this checkout,
runs as UID/GID 10001, mounts a persistent spool, and binds only loopback.
It is not part of either MatoLoop production stack. Put a TLS reverse proxy
in front of it; no public endpoint or inbound connection to MatoLoop is needed.

Set `MATO_TEAMS_APP_ID`, `MATO_TEAMS_TENANT_ID`,
`MATO_TEAMS_SECRET_DIRECTORY` (absolute directory containing `client-secret` and
`relay-token`), and optionally `MATO_TEAMS_HOST_PORT`. Set
`MATO_TEAMS_MATOLOOP_URL` to the browser-facing MatoLoop HTTPS origin to add a
pairing button to both the installation welcome card and unpaired-message
replies. This does not require making MatoLoop publicly reachable. The relay
keeps welcome jobs and receipts for up to 24 hours in a separate, bounded
queue on the spool volume, outside the chat spool quota. It retries only
explicit throttling responses; ambiguous send outcomes are not replayed.
Secret files must be
readable by container UID 10001 but not unrelated host users. Keep this
directory outside the checkout. The relay token must have at least 43
characters; generate at least 32 cryptographically random bytes.

```bash
# Deployment only; development uses scripts/dev.sh instead.
docker compose -f apps/matoloop-teams-relay/compose.yaml up -d --build
```

Proxy HTTPS requests to the loopback port, allow 25-second long polls with a
timeout above 40 seconds, and permit bodies up to 20 MiB. Disable request-body
and authorization-header logging. Keep Microsoft callbacks on `/api/messages`;
do not disable SDK JWT authentication. This first version supports commercial
Microsoft 365 tenants, not sovereign clouds.

For Portainer Docker Standalone with Nginx Proxy Manager on the same host, use
[compose.portainer.yaml](compose.portainer.yaml) instead. Select a matching
[published image or local archive](../../docs/user/kanaele.md) with
`MATO_TEAMS_RELAY_IMAGE`. The template joins the existing
`npm_access` network as `matoloop-teams-relay`, exposing port 3978 internally
without publishing a host port. Only trusted containers should share this
network. The secret directory is a read-only host bind mount and must already
exist; its default is `/opt/matoloop-teams-relay/secrets`.

## Wire contract

`libs/python/teams-relay` contains the version-1 Pydantic contract shared with
the backend. All `/relay/v1` routes require a bearer token over verified TLS:

| Route | Meaning |
| --- | --- |
| `GET /status` | Protocol version, configured tenant/app, receiver readiness |
| `GET /events?receiver=<uuid>` | Up to 20 events, 25-second long poll; renew 40-second receiver lease |
| `POST /receipts` | Committed admission or rejection for an event/generation |
| `POST /deliveries` | Idempotent text/card/image/file-offer delivery |
| `GET /deliveries/{id}` | Persistent receipt; missing receipt is not proof of non-delivery |
| `POST /deliveries/{id}/accept` | Backend authorization of an already accepted file offer |
| `PUT /blobs/{id}?operation=<uuid>` | Upload temporary bytes bound to an outgoing delivery |
| `GET /blobs/{id}?operation=<uuid>` | Download bytes bound to the admitted incoming event |

Table paths are relative to `/relay/v1`. `/health` is public and contains no
configuration. `/api/messages` uses Microsoft SDK authentication instead of the
private bearer credential.

Identity includes configured tenant/app UUIDs, the verified Entra user object
UUID, and an opaque personal-conversation ID. Display names never grant access.
Stable UUIDs deduplicate handoffs and output parts. A backend-committed inbox
is authoritative; the relay acknowledgement alone does not start a run.

The receiver lease is process-local and fenced by a generation UUID. New
messages are rejected while offline. Handoffs expire after 30 seconds; after a
disconnect or restart, only previously committed backend admissions reconcile.
They never become newly executable offline work.

Sending is recorded before the Microsoft effect. A timeout or crash leaves an
unknown outcome; neither side blindly resends it. File offers remain pending
until decline, expiry, or upload completion. Consent does not itself authorize
an upload: the backend rechecks the owner, active configuration and pairing
before calling `/accept`. Sending/uploading after a crash becomes unknown.
Teams delivery is not exactly-once.

## Limits, privacy, and recovery

- The spool defaults to 256 MiB and 2,000 records. Capacity failure rejects work
  explicitly. At most eight HTTP operations and 100 requests per second are
  admitted, with bounded request bodies; upstream rate controls are still
  recommended.
- JSON bodies are limited to 2 MiB. Individual files and combined inbound
  attachments are limited to 20 MiB; MatoLoop may impose smaller limits.
- Temporary file bytes expire after 30 minutes. Minimal receipts expire after
  24 hours. Trusted conversation metadata expires after 30 days.
- Acknowledged event content is discarded. Delivered message payloads and
  completed upload URLs are discarded; pending transfer payloads expire.
  Cleanup runs periodically and on access, including crash-orphan cleanup.
- Inline PNG/JPEG/non-animated GIF images use embedded bytes, at most 1,000,000
  bytes and 1024×1024 pixels. Other images use file consent. No public image
  hosting is introduced.
- Only validated Microsoft commercial-cloud service, attachment and upload
  hosts are contacted, without redirects. Message text/HTML is never treated
  as a download URL.

Microsoft and the relay can read transferred content. The relay is a trusted
gateway: compromise of its instance credential or host permits forged channel
input, not just message observation. Existing tool policies still apply but
cannot attest the human behind compromised transport.

Preserve the spool across container replacement. Do not copy it or credentials
between instances. A lost spool cannot safely prove that a prior send did not
happen; inspect Teams and use the canonical MatoLoop session. There is no
automatic recovery by re-executing a model request.

Rotate the Microsoft client secret by replacing its secret file and restarting
the relay. To rotate the relay token, replace that file, restart the relay,
and explicitly save the new token in MatoLoop. Any saved configuration change
invalidates personal pairings and pending deliveries; users must pair again.
Deleting the spool destroys receipts and pending file offers, not MatoLoop's
canonical sessions.

## Pilot rollout and recovery

- Use a separate production Entra registration, Azure Bot, relay bearer token,
  spool volume, and HTTPS hostname. Mount the secrets read-only as in
  [the Portainer example](compose.portainer.yaml); do not use the test stack's
  environment-variable substitution. Restrict access to the spool and its
  backups because it can hold message content and files.
- Build the backend, relay, and tenant-specific app package from the same
  reviewed commit. Record the backend and relay image digests, app package
  version and checksum, and database revision. An image archive can be
  imported into Portainer without publishing it to a registry.
- Use **Gespeicherte Verbindung testen** in MatoLoop to check both relay
  connectivity and receiver readiness. Public `/health` alone does not
  confirm that MatoLoop can receive requests. For delivery failures, review
  the backend's `Teams delivery failed` log and the affected user's MatoLoop
  notification rather than replaying failed requests. External alerting is
  optional.
- Before widening access, verify pairing and unlinking, command menu and chat
  switching, requests and approvals, file uploads and downloads, and
  ordinary replies with streaming on and off. Repeat with MatoLoop offline
  and with a relay restart during a reply. Check that the canonical response
  is available in MatoLoop and an uncertain delivery is not blindly retried.
- If only streaming fails, disable **Live-Antworten in Teams** in the relay
  settings; this preserves pairings. For a relay rollback, stop the current
  container and start the previous pinned image with the same secrets and
  spool volume, only if the previous image supports the current spool and
  protocol. Recheck the receiver lease and send a fresh test message. Replacing
  the spool with an older snapshot loses delivery receipts newer than the
  snapshot; do not resume processing as if those sends had never occurred.
  For a backend rollback, follow the database and application-data backup
  procedure in [CI and Releases](../../docs/ci-and-releases.md).

## Development

```bash
./scripts/dev.sh check teams-relay
./scripts/dev.sh test teams-relay
./scripts/dev.sh teams-package --help
```

The opt-in development service uses worktree-owned `app_data/teams/`, never
the download cache, for secrets and spool data. See the
[development guide](../../docs/development.md#teams-relay) for local setup.
Real Microsoft acceptance still requires an authorized tenant, app installation,
valid credentials, and publicly reachable relay TLS; unit tests do not establish it.
