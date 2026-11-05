#Requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('start', 'stop', 'status', 'logs', 'pin', 'rebuild')]
    [string] $Action = 'start',
    [string] $ProjectDirectory = (Split-Path -Parent $PSScriptRoot),
    [switch] $NoBrowser,
    [ValidateRange(1, 86400)] [int] $ReadyTimeoutSeconds = 7200
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Invoke-LisDocker {
    param([string[]] $Arguments, [switch] $Capture)
    # Windows PowerShell treats native stderr as ErrorRecords. Exit codes, not stderr,
    # decide success (Docker writes normal pull/build progress to stderr).
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        if ($Capture) {
            $output = & $script:DockerPath @Arguments 2>&1
            $nativeExit = $LASTEXITCODE
        } else {
            & $script:DockerPath @Arguments
            $nativeExit = $LASTEXITCODE
        }
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($nativeExit -ne 0) {
        $detail = if ($Capture) { ($output | ForEach-Object { "$_" }) -join "`n" } else { 'See the Docker output above.' }
        throw "Docker failed (exit $nativeExit). $detail"
    }
    if ($Capture) { return (($output | ForEach-Object { "$_" }) -join "`n") }
}

function Invoke-LisCompose {
    param([string[]] $Arguments, [switch] $Capture)
    # Explicit paths/name prevent another folder's .env or COMPOSE_PROJECT_NAME
    # from changing this installation's ports, settings or persistent volumes.
    $prefix = @('compose', '--project-directory', $script:LisRoot, '--file', (Join-Path $script:LisRoot 'compose.windows.yaml'), '--env-file', (Join-Path $script:LisRoot '.env'), '--project-name', 'local-image-studio-windows')
    Invoke-LisDocker -Arguments ($prefix + $Arguments) -Capture:$Capture
}

function Assert-LisLocalDocker {
    # Never send the build context, prompts or library to a remote Docker host.
    if ($env:DOCKER_HOST -and $env:DOCKER_HOST -notmatch '^npipe://') {
        throw 'DOCKER_HOST points outside the local Windows named pipe. Use Docker Desktop locally and remove this override.'
    }
    $endpoint = Invoke-LisDocker -Arguments @('context', 'inspect', '--format', '{{json .Endpoints.docker.Host}}') -Capture
    $endpoint = $endpoint | ConvertFrom-Json
    if ($endpoint -notmatch '^npipe://') {
        throw 'Select a local Docker Desktop context: docker context use desktop-linux. Remote engines are not supported by this launcher.'
    }
    try {
        $info = (Invoke-LisDocker -Arguments @('info', '--format', '{{json .}}') -Capture) | ConvertFrom-Json
    } catch {
        throw 'Open Docker Desktop, finish its first-run setup, wait until its engine is running, then try again.'
    }
    if ($info.OSType -ne 'linux') { throw 'In Docker Desktop, switch to Linux containers.' }
    if ($info.OperatingSystem -notmatch 'Docker Desktop' -or $info.KernelVersion -notmatch 'microsoft|WSL2') {
        throw 'Enable Docker Desktop > Settings > General > Use the WSL 2 based engine. NVIDIA GPU access needs WSL 2.'
    }
    Invoke-LisDocker -Arguments @('compose', 'version') | Out-Host
}

function Assert-LisGpu {
    Write-Host "`nChecking NVIDIA GPU access before downloading models..."
    $report = Invoke-LisDocker -Arguments @('run', '--rm', '--gpus', 'all', 'ubuntu:24.04', 'nvidia-smi', '--query-gpu=driver_version,memory.total', '--format=csv,noheader,nounits') -Capture
    $devices = @($report -split '\r?\n' | Where-Object { $_ -match '^\s*\d+\.\d+[^,]*,\s*\d+\s*$' })
    $usable = $devices.Count -gt 0 -and $devices[0] -match '^\s*(\d+)\.\d+[^,]*,\s*(\d+)\s*$' -and [int]$Matches[1] -ge 580 -and [int]$Matches[2] -ge 6000
    if (-not $usable) {
        throw "The first NVIDIA GPU exposed to Docker needs at least 6 GB VRAM and Windows driver 580+. Update the Windows NVIDIA driver and run wsl --update. GPU report: $report"
    }
    Write-Host $report
}

function Get-LisServiceState {
    param([string] $Service)
    $id = Invoke-LisCompose -Arguments @('ps', '--all', '--quiet', $Service) -Capture
    if (-not $id.Trim()) { throw "The $Service container is missing. Run Local Image Studio.cmd again." }
    return ((Invoke-LisDocker -Arguments @('inspect', '--format', '{{json .State}}', $id.Trim()) -Capture) | ConvertFrom-Json)
}

function Wait-LisReady {
    param([int] $TimeoutSeconds)
    $watch = [Diagnostics.Stopwatch]::StartNew()
    $nextNotice = 0
    while ($watch.Elapsed.TotalSeconds -lt $TimeoutSeconds) {
        $ready = $true
        foreach ($service in @('engine', 'studio')) {
            $state = Get-LisServiceState $service
            $health = if ($state.PSObject.Properties.Name -contains 'Health') { $state.Health.Status } else { 'starting' }
            if ($state.Status -in @('exited', 'dead') -or $state.Restarting -or $health -eq 'unhealthy') {
                throw "The $service container could not start. See: .\scripts\windows.ps1 logs"
            }
            if ($health -ne 'healthy') { $ready = $false }
        }
        if ($ready) { return }
        if ($watch.Elapsed.TotalSeconds -ge $nextNotice) {
            Write-Host 'Preparing the studio. First start downloads about 27 GB; later starts reuse verified models.'
            $nextNotice = $watch.Elapsed.TotalSeconds + 30
        }
        Start-Sleep -Seconds 3
    }
    throw 'Setup is still running. Downloads continue in Docker Desktop. Check .\scripts\windows.ps1 logs, then run the launcher again.'
}

function Get-LisUrl {
    # Read the effective Compose port, including .env and environment overrides.
    $config = (Invoke-LisCompose -Arguments @('config', '--format', 'json') -Capture) | ConvertFrom-Json
    $port = @($config.services.studio.ports | Where-Object { $_.target -eq 7878 })
    if ($port.Count -ne 1 -or "$($port[0].published)" -notmatch '^\d+$') { throw 'Set LIS_PORT in .env to one TCP port.' }
    return "http://localhost:$($port[0].published)"
}

function Show-LisPin {
    $pin = Invoke-LisCompose -Arguments @('exec', '-T', 'studio', 'cat', '/data/access-pin.txt') -Capture
    Write-Host "Studio PIN: $($pin.Trim())"
}

function Invoke-LisWindows {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw 'This launcher is for Windows. On macOS or Linux use ./install.sh.'
    }
    $script:LisRoot = (Resolve-Path -LiteralPath $ProjectDirectory).Path
    if (-not (Test-Path -LiteralPath (Join-Path $script:LisRoot 'compose.windows.yaml'))) {
        throw 'Keep this launcher inside the Local Image Studio folder.'
    }
    $settings = Join-Path $script:LisRoot '.env'
    if (-not (Test-Path -LiteralPath $settings)) {
        $defaults = [IO.File]::ReadAllText((Join-Path $script:LisRoot '.env.example')) -replace '(?m)^LIS_BIND=[^\r\n]*', 'LIS_BIND=127.0.0.1'
        [IO.File]::WriteAllText($settings, $defaults, (New-Object Text.UTF8Encoding($false)))
    }
    $docker = Get-Command docker.exe -ErrorAction SilentlyContinue
    if ($docker) { $script:DockerPath = $docker.Source } else {
        $candidates = @(
            "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin\docker.exe",
            "$env:ProgramFiles\Docker\Docker\resources\bin\docker.exe"
        )
        $script:DockerPath = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    }
    if (-not $script:DockerPath) {
        throw 'Install Docker Desktop for Windows with the WSL 2 backend, open it once, then run this installer again. https://docs.docker.com/desktop/setup/install/windows-install/'
    }
    Assert-LisLocalDocker
    switch ($Action) {
        'stop' { Invoke-LisCompose -Arguments @('stop'); return }
        'status' { Invoke-LisCompose -Arguments @('ps', '--all'); return }
        'logs' { Invoke-LisCompose -Arguments @('logs', '--follow', '--tail', '80'); return }
        'pin' { Show-LisPin; return }
    }
    Assert-LisGpu
    $build = @('build')
    if ($Action -eq 'rebuild') { $build += '--pull' }
    Invoke-LisCompose -Arguments $build
    # A real CUDA operation catches unsupported GPU architectures / driver problems
    # before the engine entrypoint starts its large model downloads.
    $probe = "import torch; x=torch.ones((16,16),device='cuda',dtype=torch.float16); y=x@x; torch.cuda.synchronize(); print('CUDA ready:',torch.cuda.get_device_name(0))"
    Invoke-LisDocker -Arguments @('run', '--rm', '--gpus', 'all', '--entrypoint', '/app/.venv/bin/python', 'local-image-studio-engine:latest', '-c', $probe)
    Invoke-LisCompose -Arguments @('up', '--detach')
    Wait-LisReady -TimeoutSeconds $ReadyTimeoutSeconds
    $url = Get-LisUrl
    Write-Host "`nLocal Image Studio is ready: $url"
    Show-LisPin
    if (-not $NoBrowser) { Start-Process $url }
}

if ($MyInvocation.InvocationName -ne '.') {
    try { Invoke-LisWindows } catch { Write-Host "`nCannot continue: $_" -ForegroundColor Red; exit 1 }
}
