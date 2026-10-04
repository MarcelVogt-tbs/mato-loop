# Native computer maintenance

Installed native Linux Agents advertise `maintenance.start`; older installations
need one local update, and container Agents do not offer it. Requests carry only an
action and job ID. The existing installer state and `paths()` select every managed
path.

The Linux adapter starts a transient systemd unit as the installation user so the
worker survives stopping its own service. When sudo requires a password, a masked
one-time prompt passes it over a private Unix socket, then to sudo via stdin. No
password is stored or placed in service arguments or the environment. Sudo rights
must already exist; cached tickets are not shared across services.

The worker uses the existing installer for fast-forward updates with activation
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
and job ID to its private data directory and starts the maintenance task. That
PowerShell worker reads its DPAPI-protected Agent credential, invokes the existing
installer for update or uninstall without purge, and sends the completion through
the same authenticated backend endpoint. It survives stopping the Agent task;
Git runs without interactive credential prompts. Missing task setup omits the
maintenance capability. Portal maintenance requires no Windows password request.

The Windows installer's `-Home` option is an alias for `-HomeDirectory`; it never
assigns to PowerShell's read-only `$HOME` automatic variable.
