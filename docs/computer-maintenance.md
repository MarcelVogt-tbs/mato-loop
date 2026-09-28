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
