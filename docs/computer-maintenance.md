# Computer maintenance

## Computer management and version comparison

The Computer overview shows connectivity, the reported Agent version, and its
comparison with the running backend. **Verwalten** opens the shared configuration
dialog on **Einstellungen**; **Aktionen** contains version details, pairing,
updates, uninstallation, and revocation. Settings drafts survive tab changes, and
reopening starts on the settings tab. Adding uses the same settings form and
starts with separate Linux/Windows icon chips and the selected installation command and
instructions, followed by the optional alias and access settings. Installation
commands are not part of the overview.
Open management dialogs resolve the computer by ID from the existing polled store.

An omitted or blank alias is stored as an empty string in the existing column.
`displayName` resolves the custom alias, the last reported computer name, or
`Computer <ID8>` before the first connection. Editing always uses the raw `alias`;
clearing it restores the automatic name. The same name resolution supplies UI
selections, permission labels, protected administrator questions, and model context.

The existing computer-list response includes `backendVersion` and the nullable
`installationTarget`. A missing target disables WebUI updates without preventing
version comparison or other actions. Only matching clean date/SHA versions are
shown as the same source version. Dirty Agent builds are marked locally changed; unknown
or legacy versions cannot be compared. Different UTC dates indicate older or
newer commits, not Git ancestry or the latest published release. Different commits
on the same day remain different source versions. Offline versions are last reported data.
No additional version inventory, schema migration, or remote update probe is used.

## Maintenance admission

The Agent admits maintenance only when no requests, live terminals, browser
sessions, or browser setup are active. Admission is reserved before launching the
independent worker; cancellation waits for the actual launch outcome. Requests
are rejected with `maintenance.busy` while the launcher or installer is active,
including after Agent restart or reconnect. A definite busy rejection restores
the previous backend maintenance record instead of creating a failed update.

Native Linux mutations take a non-blocking exclusive lock on the managed root
directory. The CLI update passes the open lock descriptor to its fetched installer,
while a WebUI worker holds it before acknowledging the private socket handoff.
The Agent probes the same lock; process exit releases it without stale lock files.
Native Windows mutations use a reentrant, cross-session named mutex derived from
the installation path. This serializes CLI and Task Scheduler operations; the
Agent probes that mutex and the queued maintenance request. Managed containers
retain their existing launcher lock, exclusive request file, and activation journal.

## Native maintenance workers

Installed native Linux Agents advertise `maintenance.start`; older installations
need one local installation of a compatible Agent. Requests carry an
action, job ID, and the full backend commit SHA for updates. The existing installer state and `paths()` select every managed
path.

The Linux adapter starts a transient systemd unit as the installation user so the
worker survives stopping its own service. When sudo requires a password, a masked
one-time prompt passes it over a private Unix socket, then to sudo via stdin. No
password is stored or placed in service arguments or the environment. Sudo rights
must already exist; cached tickets are not shared across services.

The worker uses the existing installer for commit-pinned updates with activation
rollback or for uninstall without purging credentials and workspaces. It reports the
result with the Agent credential and job ID. The backend stores the last job on the
computer; a disconnect is not success, and an unconfirmed result becomes unknown
after the worker's runtime limit. Revoking the computer remains separate. Future OS
adapters implement the same operation with their native service and authorization
mechanisms.

Native Windows installations configure a second, on-demand Scheduled Task during
setup, using the same account and Task Scheduler-managed password as the boot
task. Both run with limited privileges; task ACLs permit that account to manage
its own tasks. Only the fixed installer action runs: the Agent writes an action
and job ID, plus the pinned revision for updates, to its private data directory and starts the maintenance task. That
PowerShell worker reads its DPAPI-protected Agent credential, invokes the existing
installer for update or uninstall without purge, and sends the completion through
the same authenticated backend endpoint. It survives stopping the Agent task;
Git runs without interactive credential prompts. Missing task setup omits the
maintenance capability. Portal maintenance requires no Windows password request.
Local and portal updates resolve Git through the same installer: system Git first,
then private MinGit under the installation's `tools\git` directory, downloading the
pinned, checksum-verified archive only when neither is available.

The Windows installer's `-Home` option is an alias for `-HomeDirectory`; it never
assigns to PowerShell's read-only `$HOME` automatic variable.

## Managed container updates

Production Docker Agents advertise `maintenance.update`, which permits updates
but not native uninstallation. The WebUI shows only supported actions; the backend
and Agent enforce the same restriction. Development containers keep their existing
bind-mounted source and reload workflow and do not advertise managed updates.

The image includes a complete initial installation, started by the container mode
of `scripts/matoloop-agent-installer.py`. Its independent launcher accepts one fixed
update request at a time, prepares a new source checkout and non-editable Python
environment, stamps the source version, and installs the locked `browser` group.
`container-browser-setup.sh` is shared by production builds, development builds,
interactive container browser setup, and updates. It installs the Chromium version
matching that environment's Playwright and retains older browser versions for
activation rollback. Browser execution still uses the unprivileged browser user.

Preparation and a disposable browser probe run before stopping the old Agent.
The launcher activates the prepared environment, checks startup, and restores the
previous environment on failure. An activation journal also restores the previous
release after an interrupted activation. The existing authenticated completion
endpoint records the update result; disconnected Agents are not assumed successful.
Python environments and source releases are rolled back; shared OS packages are
not a transactional snapshot. Docker image recreation restores the base filesystem.

The running backend selects the exact target commit for every WebUI update.
`docker exec <container> matoloop-agent update` obtains that target from the paired
backend before queueing the same update. No Agent branch configuration is required.
Updates live in the container layer: restarting preserves them, recreating the
container restores the selected image. Pairing/configuration under `/data` and the
workspace mount are preserved. Remove the container through the deployment system.
Existing Docker Agents need one image update to acquire the managed launcher.

Backend and Agent versions use the same UTC commit date/SHA helper. Installed
packages contain a build stamp and require no Git to report their version. The
WebUI reports the running Agent version, which can differ from its original image
after a WebUI update. Protocol compatibility remains independent of version equality.

## Installation target

`GET /api/execution-agents/installation-target` exposes public build metadata:
`revision` (full commit SHA) and `version` (UTC date/SHA version). It requires no
pairing and is not cached. The backend refuses missing, dirty, or inconsistent
build metadata with `maintenance.targetUnavailable`; there is no fallback branch.
Production images stamp the full revision together with the display version.

Standalone Linux and Windows setup asks for the instance URL, retrieves this target
once, and fetches and verifies exactly that commit before installing. Local CLI
updates query the paired instance (or installation URL before pairing); WebUI jobs
receive the same target directly from the backend. Each operation retains its SHA
through preparation, restart and completion, even if the backend changes meanwhile.
An update may move forward or backward between supported source revisions. Changing
the backend image from a main release to develop or back therefore needs no Agent
branch change. Database downgrade compatibility remains a backend concern.

Updates prepare a new managed checkout, even when the original installation came
from a developer checkout; user sources and local edits are not changed. Installing
directly from a local checkout remains available for development and dirty source.
Both sides must support this update protocol, but do not require equal versions to
connect: an older Agent must remain reachable long enough to be synchronized.
