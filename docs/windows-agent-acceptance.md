# Native Windows Agent acceptance

The backend with Windows path support must run before pairing the Windows Agent.
Test the `develop` source and backend together. Clone `develop` on the Windows
machine and run `scripts\install-matoloop-agent.ps1` from that checkout, or pass
`-Branch develop` to the downloaded installer. A clone of `main` does not contain
the changes until a release. Run the installer as Administrator under the intended
task account. Git and Internet access are needed to bootstrap `uv`.

The installer checks the new Agent executable before activating it. Shared
filesystem helpers must remain importable on Windows; the Linux `renameat2`
binding is loaded only on Linux.

1. Install with `powershell.exe -NoProfile -ExecutionPolicy Bypass -File
   C:\path\to\checkout\scripts\install-matoloop-agent.ps1 -Source
   C:\path\to\checkout -InstanceUrl https://your-matoloop-host`.
   Enter the installing account's password only in the Task Scheduler prompt.
2. Run `& "$env:LOCALAPPDATA\matoloop-agent\installer.ps1" status`. Verify
   `Get-ScheduledTask -TaskName 'MatoLoop Agent'` is running and the home is
   `%USERPROFILE%\mato-loop-agent\workspace` (or the chosen external directory).
3. Run the native tests with the active environment from `install.json`:
   `$state = Get-Content "$env:LOCALAPPDATA\matoloop-agent\install.json" | ConvertFrom-Json;`
   `& (Join-Path $state.activeVenv 'Scripts\python.exe') -m unittest discover
   -s C:\path\to\checkout\apps\matoloop-agent\tests -p test_windows_runtime.py -v`.
4. Create a computer in **Einstellungen > Computer** and run
   `& "$env:LOCALAPPDATA\matoloop-agent\installer.ps1" pair`. Enter the
   displayed computer ID and pairing token and the WebSocket URL printed by
   the installer. Confirm the platform is `win32` and system info says Windows.
5. Select the Windows workspace. Browse and create a folder, create/edit/read a
   UTF-8 file, search it, upload/download it, and use an approved PowerShell
   `Get-Content` and `Set-Content` command. Confirm `C:/` breadcrumbs and
   outside-workspace approvals. The native tests also reject file symlinks.
6. Restart Windows **without signing in** and check that the task becomes
   running and the computer reconnects. If the source is a clean, upstream-
   tracking `develop` branch, use **Einstellungen > Computer > Agent aktualisieren**.
   Verify the maintenance result becomes completed, the Agent reconnects, and the
   pairing and workspace stay intact. `MatoLoop Agent Maintenance` must run independently
   of the boot-time Agent task and return to Ready when the update finishes.
   A failing update (for example a local checkout edit) must report failure, not success.
7. Run `uninstall`; confirm the workspace and pairing data remain. Repeat an
   installation and run `uninstall -Purge`; confirm the managed directory is
   removed while the workspace remains.

Do not treat the native Windows behavior as verified until these steps pass on
the target Windows installation. Linux Docker tests exercise protocol and
backend compatibility but cannot run Windows APIs or Scheduled Tasks.

Filesystem RPCs traverse directories through no-reparse handles and perform
file mutations relative to those handles. Relative renames use
`NtSetInformationFile` with `FILE_RENAME_INFORMATION` and the open destination
directory handle. Recursive directory creation reopens an existing directory
handle with an empty relative name instead of `"."`. Native tests swap a parent
directory after opening its parent handle but before opening the leaf file, and
check that only the originally opened directory is used for reads, writes and
deletes. Windows rejects parent-directory renames while a leaf file is open.
This Windows-specific behavior still needs to pass
on the target VM before the no-follow contract can be considered verified.
