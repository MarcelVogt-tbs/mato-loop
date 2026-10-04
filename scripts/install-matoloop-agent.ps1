param(
    [ValidateSet('install', 'update', 'status', 'uninstall', 'pair', 'run', 'maintenance-run')]
    [string] $Command = 'install',
    [string] $InstanceUrl,
    [string] $Home,
    [string] $Branch = 'main',
    [string] $Source,
    [switch] $Purge,
    [switch] $Yes
)

$ErrorActionPreference = 'Stop'
$root = Join-Path $env:LOCALAPPDATA 'matoloop-agent'
if ($PSCommandPath -and (Split-Path -Leaf $PSCommandPath) -eq 'installer.ps1') {
    $root = $PSScriptRoot
}
$data = Join-Path $root 'data'
$stateFile = Join-Path $root 'install.json'
$taskName = 'MatoLoop Agent'
$maintenanceTask = 'MatoLoop Agent Maintenance'
$marker = Join-Path $root '.managed-by-matoloop-agent'
$env:UV_CACHE_DIR = Join-Path $root 'cache\uv'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $root 'tools\python'
$env:UV_PYTHON_INSTALL_BIN = '0'
$env:UV_MANAGED_PYTHON = '1'
$uv = Join-Path $root 'tools\bin\uv.exe'

function Fail([string] $Message) { throw "MatoLoop Agent: $Message" }
# Managed state and executable paths must never traverse directory junctions.
$current = [IO.DirectoryInfo] $root
while ($current) {
    if ($current.Exists -and ($current.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        Fail 'The installation path contains a reparse point.'
    }
    $current = $current.Parent
}
function State {
    if (-not (Test-Path -LiteralPath $stateFile -PathType Leaf)) { return $null }
    return (Get-Content -LiteralPath $stateFile -Raw -Encoding UTF8 | ConvertFrom-Json)
}
function Save-State($Value) {
    $temporary = "$stateFile.new"
    [IO.File]::WriteAllText($temporary, ($Value | ConvertTo-Json -Depth 5), [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $temporary -Destination $stateFile -Force
}
function Task { return Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue }
function Runtime($Value) {
    if (-not $Value) { Fail 'The agent is not installed.' }
    return Join-Path $Value.activeVenv 'Scripts\matoloop-agent.exe'
}
function Stop-Agent {
    $job = Task
    if ($job -and $job.State -eq 'Running') {
        Stop-ScheduledTask -TaskName $taskName
        for ($attempt = 0; $attempt -lt 20; $attempt++) {
            if ((Task).State -ne 'Running') { return }
            Start-Sleep -Milliseconds 500
        }
        Fail 'The startup task did not stop; no files were removed.'
    }
}

# One on-demand Task Scheduler worker survives stopping the boot-time Agent task.
if ($Command -eq 'maintenance-run') {
    $requestFile = Join-Path $data 'maintenance-request.json'
    $request = Get-Content -LiteralPath $requestFile -Raw -Encoding UTF8 | ConvertFrom-Json
    if ((($request.PSObject.Properties.Name | Sort-Object) -join ',') -ne 'action,jobId' -or
        $request.action -notin @('update', 'uninstall') -or
        ([Guid]::Parse($request.jobId)).ToString() -cne $request.jobId) { Fail 'Invalid maintenance request.' }
    Remove-Item -LiteralPath $requestFile -Force
    Add-Type -AssemblyName System.Security
    $encrypted = [IO.File]::ReadAllBytes((Join-Path $data 'agent-state\credential.dat'))
    $plain = [Security.Cryptography.ProtectedData]::Unprotect(
        $encrypted, $null, [Security.Cryptography.DataProtectionScope]::CurrentUser)
    $connection = [Text.Encoding]::UTF8.GetString($plain) | ConvertFrom-Json
    $completionUrl = ($connection.backendUrl -replace '^wss:', 'https:' -replace '^ws:', 'http:')
    $completionUrl = $completionUrl -replace '/connect$', "/$($connection.agentId)/maintenance/$($request.jobId)/complete"
    $result = 'failed'
    $env:GIT_TERMINAL_PROMPT = '0'
    $env:GCM_INTERACTIVE = 'Never'
    try {
        & $PSCommandPath $request.action -Yes
        if ($LASTEXITCODE -ne 0) { Fail 'Maintenance failed.' }
        $result = 'succeeded'
    } catch { Write-Host 'MatoLoop Agent maintenance failed.' }
    $reported = $false
    for ($attempt = 0; $attempt -lt 5; $attempt++) {
        try {
            Invoke-RestMethod -Method Post -Uri $completionUrl -TimeoutSec 15 `
                -Headers @{Authorization="Bearer $($connection.accessKey)"} `
                -ContentType 'application/json' -Body (@{status=$result} | ConvertTo-Json -Compress) | Out-Null
            $reported = $true
            break
        } catch { Start-Sleep -Seconds 2 }
    }
    if (-not $reported) { Write-Host 'Maintenance result could not be delivered.' }
    exit $(if ($result -eq 'succeeded') { 0 } else { 1 })
}

if ($Command -eq 'run') {
    $state = State
    $env:MATOLOOP_AGENT_CONFIG = Join-Path $data 'config.json'
    & (Runtime $state) run
    exit $LASTEXITCODE
}
if ($Command -eq 'pair') {
    $state = State
    $env:MATOLOOP_AGENT_CONFIG = Join-Path $data 'config.json'
    & (Runtime $state) pair
    exit $LASTEXITCODE
}
if ($Command -eq 'status') {
    $state = State
    if (-not $state) { Fail 'The agent is not installed.' }
    Write-Host "Source: $($state.source)"
    Write-Host "Home: $($state.homeDirectory)"
    Write-Host "Data: $data"
    Task | Select-Object TaskName, State
    Get-ScheduledTaskInfo -TaskName $taskName | Select-Object LastRunTime, LastTaskResult
    exit
}
if ($Command -eq 'uninstall') {
    $state = State
    if (-not $state -and -not $Purge) { Fail 'The agent is not installed.' }
    if (-not (Test-Path -LiteralPath $marker -PathType Leaf)) { Fail 'Installation marker is missing.' }
    $job = Task
    $expected = Join-Path $root 'installer.ps1'
    if ($job -and ($job.Actions.Count -ne 1 -or $job.Actions[0].Arguments -notlike "*${expected}*")) {
        Fail 'The startup task was replaced; no files were removed.'
    }
    if (-not $Yes -and (Read-Host 'Remove MatoLoop Agent? [y/N]') -notin @('y', 'Y', 'yes', 'ja')) { exit }
    Stop-Agent
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $maintenanceTask -Confirm:$false -ErrorAction SilentlyContinue
    if ($Purge) {
        Remove-Item -LiteralPath $root -Recurse -Force
    } else {
        foreach ($name in @('venvs', 'source', 'tools', 'cache', 'install.json', 'installer.ps1')) {
            Remove-Item -LiteralPath (Join-Path $root $name) -Recurse -Force -ErrorAction SilentlyContinue
        }
        foreach ($child in Get-ChildItem -LiteralPath $data -ErrorAction SilentlyContinue) {
            if ($child.Name -ne 'agent-state') { Remove-Item -LiteralPath $child.FullName -Recurse -Force }
        }
    }
    Write-Host 'MatoLoop Agent removed. The workspace was preserved.'
    exit
}

if ($Command -eq 'update' -and -not (State)) { Fail 'The agent is not installed.' }
$existing = State
if ($existing -and -not (Test-Path -LiteralPath $marker -PathType Leaf)) { Fail 'Installation marker is missing.' }
if (-not $existing -and -not (Test-Path -LiteralPath $marker) -and ((Test-Path -LiteralPath $root) -and
    (Get-ChildItem -LiteralPath $root -Force | Select-Object -First 1))) { Fail 'The installation directory is not empty.' }
if (-not $existing -and (Task)) { Fail 'An unmanaged MatoLoop Agent task already exists.' }

$checkout = if ($Source) { (Resolve-Path -LiteralPath $Source).Path } elseif ($existing) {
    $existing.source
} else {
    $candidate = Join-Path (Split-Path -Parent $PSScriptRoot) 'apps\matoloop-agent\pyproject.toml'
    if (Test-Path -LiteralPath $candidate) { Split-Path -Parent $PSScriptRoot } else { $null }
}
if (-not $checkout -and -not (Get-Command git -ErrorAction SilentlyContinue)) { Fail 'Git is required.' }
if (-not $checkout) { $checkout = Join-Path $root 'source' }
if ($Command -eq 'update') {
    $env:GIT_TERMINAL_PROMPT = '0'
    $env:GCM_INTERACTIVE = 'Never'
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Fail 'Git is required to update.' }
    if ((& git -C $checkout status --porcelain)) { Fail 'Source checkout has local changes; use install instead.' }
    if ($LASTEXITCODE) { Fail 'Could not inspect the source checkout.' }
    & git -C $checkout symbolic-ref --quiet HEAD | Out-Null
    if ($LASTEXITCODE) { Fail 'The source checkout must be on a branch.' }
    & git -C $checkout rev-parse --abbrev-ref --symbolic-full-name '@{u}' | Out-Null
    if ($LASTEXITCODE) { Fail 'The selected branch has no upstream.' }
    & git -C $checkout pull --ff-only
    if ($LASTEXITCODE) { Fail 'Git could not fast-forward the selected branch.' }
} elseif (-not (Test-Path -LiteralPath (Join-Path $checkout 'apps\matoloop-agent\pyproject.toml'))) {
    New-Item -ItemType Directory -Path $root -Force | Out-Null
    $sourceUrl = if ($env:MATOLOOP_AGENT_SOURCE_URL) { $env:MATOLOOP_AGENT_SOURCE_URL } else {
        'https://git.devmato.net/devmato/MatoLoop.git'
    }
    & git clone --branch $Branch --single-branch $sourceUrl $checkout
    if ($LASTEXITCODE) { Fail 'Could not clone the selected source branch.' }
}
if (-not (Test-Path -LiteralPath (Join-Path $checkout 'apps\matoloop-agent\pyproject.toml'))) {
    Fail 'Source is not a MatoLoop checkout.'
}
if (-not $Home) { $Home = if ($existing) { $existing.homeDirectory } else {
    Join-Path $env:USERPROFILE 'mato-loop-agent\workspace'
} }
$Home = [IO.Path]::GetFullPath($Home).TrimEnd('\')
if ($Home.Equals($root, [StringComparison]::OrdinalIgnoreCase) -or
    $Home.StartsWith($root + '\', [StringComparison]::OrdinalIgnoreCase) -or
    $root.StartsWith($Home + '\', [StringComparison]::OrdinalIgnoreCase)) { Fail 'Workspace must be outside the installation.' }
New-Item -ItemType Directory -Path $Home -Force | Out-Null
if (-not $InstanceUrl) { $InstanceUrl = if ($existing) { $existing.instanceUrl } else {
    Read-Host 'MatoLoop instance URL (https://host)'
} }
try { $instance = [Uri] $InstanceUrl } catch { Fail 'Invalid instance URL.' }
if ($instance.Scheme -notin @('https', 'http') -or -not $instance.Host -or $instance.UserInfo -or
    $instance.Query -or $instance.Fragment) { Fail 'Invalid instance URL.' }
$websocket = $InstanceUrl.TrimEnd('/') -replace '^https:', 'wss:' -replace '^http:', 'ws:'
$websocket += '/api/execution-agents/connect'

New-Item -ItemType Directory -Path $root, $data, (Join-Path $root 'tools\bin'),
    (Join-Path $root 'venvs') -Force | Out-Null
if (-not (Test-Path -LiteralPath $marker)) {
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    & icacls $root /inheritance:r /grant:r "*${sid}:(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' | Out-Null
    if ($LASTEXITCODE) { Fail 'Could not restrict the installation directory ACL.' }
    New-Item -ItemType File -Path $marker -Force | Out-Null
}
if (-not (Test-Path -LiteralPath $uv)) {
    $bootstrap = Join-Path $env:TEMP 'matoloop-uv-install.ps1'
    Invoke-WebRequest -UseBasicParsing -Uri 'https://astral.sh/uv/0.10.9/install.ps1' -OutFile $bootstrap
    try {
        $env:UV_UNMANAGED_INSTALL = Join-Path $root 'tools\bin'
        & $bootstrap
        if ($LASTEXITCODE) { Fail 'Could not install private uv.' }
    } finally { Remove-Item -LiteralPath $bootstrap -Force -ErrorAction SilentlyContinue }
}
& $uv python install 3.13
if ($LASTEXITCODE) { Fail 'Could not install private Python.' }
$newVenv = Join-Path $root ("venvs\" + [Guid]::NewGuid().ToString('N'))
$installer = Join-Path $root 'installer.ps1'
$previousInstaller = if (Test-Path -LiteralPath $installer) { [IO.File]::ReadAllBytes($installer) } else { $null }
$env:VIRTUAL_ENV = $newVenv
try {
    & $uv sync --directory (Join-Path $checkout 'apps\matoloop-agent') --frozen --no-dev --no-editable --active
    if ($LASTEXITCODE) { Fail 'Could not prepare the new Agent environment.' }
    $executable = Join-Path $newVenv 'Scripts\matoloop-agent.exe'
    & $executable --help | Out-Null
    if ($LASTEXITCODE) { Fail 'The new Agent executable failed its startup check.' }
    $configFile = Join-Path $data 'config.json'
    $config = if (Test-Path -LiteralPath $configFile) {
        Get-Content -LiteralPath $configFile -Raw -Encoding UTF8 | ConvertFrom-Json
    } else { [pscustomobject] @{} }
    $config | Add-Member -NotePropertyName homeDirectory -NotePropertyValue $Home -Force
    $config | Add-Member -NotePropertyName allowInsecure -NotePropertyValue ($instance.Scheme -eq 'http') -Force
    $config | Add-Member -NotePropertyName shellExecutable -NotePropertyValue (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') -Force
    $previousConfig = if (Test-Path -LiteralPath $configFile) { Get-Content -LiteralPath $configFile -Raw } else { $null }
    $sourceInstaller = Join-Path $checkout 'scripts\install-matoloop-agent.ps1'
    if (-not (Test-Path -LiteralPath $sourceInstaller -PathType Leaf)) {
        Fail 'The source checkout has no Windows installer.'
    }
    if (-not [string]::Equals($sourceInstaller, $installer, [StringComparison]::OrdinalIgnoreCase)) {
        Copy-Item -LiteralPath $sourceInstaller -Destination $installer -Force
    }
    Stop-Agent
    try {
        [IO.File]::WriteAllText($configFile, ($config | ConvertTo-Json -Depth 5), [Text.UTF8Encoding]::new($false))
        $newState = [pscustomobject] @{activeVenv=$newVenv; source=$checkout;
            homeDirectory=$Home; instanceUrl=$InstanceUrl.TrimEnd('/'); maintenanceReady=$false}
        Save-State $newState
        if (-not (Task) -or -not (Get-ScheduledTask -TaskName $maintenanceTask -ErrorAction SilentlyContinue)) {
            $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
            $credential = Get-Credential -UserName $identity -Message 'Password for the MatoLoop Agent tasks'
            if (-not $credential) { Fail 'Task setup was cancelled.' }
            if ($credential.UserName -ne $identity) { Fail 'Task account must be the installing user.' }
            $powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
            if (-not (Task)) {
                $action = New-ScheduledTaskAction -Execute $powershell -Argument (
                    '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $installer + '" run')
                $trigger = New-ScheduledTaskTrigger -AtStartup
                $settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
                    -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
                Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings `
                    -User $identity -Password ($credential.GetNetworkCredential().Password) -RunLevel Limited | Out-Null
            }
            if (-not (Get-ScheduledTask -TaskName $maintenanceTask -ErrorAction SilentlyContinue)) {
                $action = New-ScheduledTaskAction -Execute $powershell -Argument (
                    '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $installer + '" maintenance-run')
                $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 60) `
                    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
                Register-ScheduledTask -TaskName $maintenanceTask -Action $action -Settings $settings `
                    -User $identity -Password ($credential.GetNetworkCredential().Password) -RunLevel Limited | Out-Null
            }
            # Both tasks run without elevation. Their account can start/stop/manage its own tasks.
            $scheduler = New-Object -ComObject 'Schedule.Service'
            $scheduler.Connect()
            $folder = $scheduler.GetFolder('\')
            $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
            foreach ($name in @($taskName, $maintenanceTask)) {
                $folder.GetTask($name).SetSecurityDescriptor("D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GA;;;$sid)", 0)
            }
        }
        $newState.maintenanceReady = $true
        Save-State $newState
        Start-ScheduledTask -TaskName $taskName
        Start-Sleep -Seconds 2
        if ((Task).State -ne 'Running') { Fail 'The Agent task did not remain running.' }
    } catch {
        Stop-Agent
        if ($existing) { Save-State $existing } else { Remove-Item -LiteralPath $stateFile -ErrorAction SilentlyContinue }
        if ($previousConfig) { [IO.File]::WriteAllText($configFile, $previousConfig, [Text.UTF8Encoding]::new($false)) } else {
            Remove-Item -LiteralPath $configFile -ErrorAction SilentlyContinue
        }
        if (-not $existing) {
            Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
            Unregister-ScheduledTask -TaskName $maintenanceTask -Confirm:$false -ErrorAction SilentlyContinue
        }
        if ($existing) { Start-ScheduledTask -TaskName $taskName }
        throw
    }
    if ($existing -and $existing.activeVenv -ne $newVenv -and
        ([IO.Path]::GetFullPath($existing.activeVenv)).StartsWith((Join-Path $root 'venvs') + '\', [StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $existing.activeVenv -Recurse -Force -ErrorAction SilentlyContinue
    }
    Write-Host "MatoLoop Agent installed. Workspace: $Home"
    if (-not (Test-Path -LiteralPath (Join-Path $data 'agent-state\credential.dat'))) {
        Write-Host 'Create a computer under Einstellungen > Computer, then pair it with:'
        Write-Host "  & '$installer' pair"
        Write-Host "  Backend WebSocket URL: $websocket"
    }
} catch {
    Remove-Item -LiteralPath $newVenv -Recurse -Force -ErrorAction SilentlyContinue
    if ($previousInstaller) { [IO.File]::WriteAllBytes($installer, $previousInstaller) } else {
        Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue
    }
    throw
}
