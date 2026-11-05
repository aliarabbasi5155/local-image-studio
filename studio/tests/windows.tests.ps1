# No test modules, Docker daemon, GPU or network needed. Run in PowerShell 5.1 or 7.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
foreach ($file in @('install.ps1', 'scripts/windows.ps1', 'studio/tests/windows.tests.ps1')) {
    $tokens = $null; $errors = $null
    [Management.Automation.Language.Parser]::ParseFile((Join-Path $repo $file), [ref]$tokens, [ref]$errors) | Out-Null
    if ($errors.Count) { throw ($errors | Out-String) }
}
. (Join-Path $repo 'scripts/windows.ps1')
$nativeDocker = ${function:Invoke-LisDocker}
$tests = 0
function Assert-LisTest { param($Condition, [string] $Message); if (-not $Condition) { throw $Message }; $script:tests++ }
function Assert-LisThrows {
    param([scriptblock] $Code, [string] $Message)
    $caught = $null
    try { & $Code } catch { $caught = "$_" }
    Assert-LisTest ($caught -and $caught.Contains($Message)) "Expected error containing '$Message'; got '$caught'"
}

# Native progress on stderr is normal; a nonzero exit code must never be swallowed.
$script:DockerPath = (Get-Process -Id $PID).Path
$out = Invoke-LisDocker -Arguments @('-NoProfile', '-Command', "[Console]::Error.WriteLine('pulling'); [Console]::Out.WriteLine('ready'); exit 0") -Capture
Assert-LisTest ($out.Contains('ready')) 'Native stderr incorrectly failed a successful command.'
Assert-LisThrows { Invoke-LisDocker -Arguments @('-NoProfile', '-Command', 'exit 7') -Capture } 'exit 7'

$script:Calls = New-Object 'System.Collections.Generic.List[object]'
$script:MockInfo = @{OSType='linux'; OperatingSystem='Docker Desktop'; KernelVersion='6.6.87-microsoft-standard-WSL2'}
$script:GpuReport = "580.82, 6144`n"
function Invoke-LisDocker {
    param([string[]] $Arguments, [switch] $Capture)
    $script:Calls.Add($Arguments)
    switch ($Arguments[0]) {
        'context' { return '"npipe:////./pipe/dockerDesktopLinuxEngine"' }
        'info' { return ($script:MockInfo | ConvertTo-Json -Compress) }
        'run' { return $script:GpuReport }
        default { if ($Capture) { return 'captured' } }
    }
}
$script:LisRoot = Join-Path ([IO.Path]::GetTempPath()) "Windows user's studio"
Invoke-LisCompose -Arguments @('up', '--detach') | Out-Null
$call = $script:Calls[0]
Assert-LisTest ($call[2] -eq $script:LisRoot -and $call[4] -eq (Join-Path $script:LisRoot 'compose.windows.yaml')) 'Paths with spaces/apostrophes were split into shell text.'
Assert-LisTest ($call[5] -eq '--env-file' -and $call[6] -eq (Join-Path $script:LisRoot '.env') -and $call[8] -eq 'local-image-studio-windows') 'A foreign working directory or Compose project name could select another installation.'
Assert-LisTest ($call[9] -eq 'up' -and $call[10] -eq '--detach') 'Compose arguments were lost.'

$originalHost = $env:DOCKER_HOST
try {
    $env:DOCKER_HOST = 'tcp://remote.example:2376'; $script:Calls.Clear()
    Assert-LisThrows { Assert-LisLocalDocker } 'DOCKER_HOST'
    Assert-LisTest ($script:Calls.Count -eq 0) 'A remote host was contacted before the privacy check.'
    $env:DOCKER_HOST = $null
    Assert-LisLocalDocker
    $script:MockInfo.OSType = 'windows'
    Assert-LisThrows { Assert-LisLocalDocker } 'Linux containers'
    $script:MockInfo.OSType = 'linux'; $script:MockInfo.KernelVersion = '6.12-linuxkit'
    Assert-LisThrows { Assert-LisLocalDocker } 'WSL 2'
} finally { $env:DOCKER_HOST = $originalHost }

Assert-LisGpu
$script:GpuReport = '579.1, 24576'
Assert-LisThrows { Assert-LisGpu } '580+'
$script:GpuReport = "580.82, 4096`n580.82, 24576"
Assert-LisThrows { Assert-LisGpu } 'first NVIDIA GPU'

$script:StateCalls = 0
function Get-LisServiceState {
    param([string] $Service)
    $script:StateCalls++
    $health = if ($script:StateCalls -lt 3) { 'starting' } else { 'healthy' }
    return [pscustomobject]@{Status='running';Restarting=$false;Health=[pscustomobject]@{Status=$health}}
}
function Start-Sleep { param([int] $Seconds) }
Wait-LisReady -TimeoutSeconds 2
Assert-LisTest ($script:StateCalls -eq 4) 'Readiness returned before both services were healthy.'
function Get-LisServiceState { param([string] $Service); return [pscustomobject]@{Status='running';Restarting=$true;Health=[pscustomobject]@{Status='starting'}} }
Assert-LisThrows { Wait-LisReady -TimeoutSeconds 2 } 'could not start'
function Invoke-LisCompose { param([string[]] $Arguments, [switch] $Capture); return '{"services":{"studio":{"ports":[{"target":7878,"published":"9191"}]}}}' }
Assert-LisTest ((Get-LisUrl) -eq 'http://localhost:9191') 'The configured Windows port was ignored.'

# Exercise the standalone installer, not just the already-cloned repository branch.
$temp = Join-Path ([IO.Path]::GetTempPath()) ('lis-windows-tests-' + [Guid]::NewGuid().ToString('N'))
try {
    $fixture = Join-Path $temp 'fixture/local-image-studio-main'
    New-Item -ItemType Directory -Path (Join-Path $fixture 'scripts') -Force | Out-Null
    [IO.File]::WriteAllText((Join-Path $fixture 'scripts/windows.ps1'), '# fixture launcher')
    $script:FixtureZip = Join-Path $temp 'fixture.zip'
    Compress-Archive -LiteralPath $fixture -DestinationPath $script:FixtureZip
    $standalone = Join-Path $temp 'installer'
    New-Item -ItemType Directory -Path $standalone | Out-Null
    Copy-Item -LiteralPath (Join-Path $repo 'install.ps1') -Destination $standalone
    . (Join-Path $standalone 'install.ps1')
    function Invoke-WebRequest { param([switch] $UseBasicParsing, [string] $Uri, [string] $OutFile); Copy-Item -LiteralPath $script:FixtureZip -Destination $OutFile }
    $destination = Join-Path $temp "install with spaces and user's apostrophe"
    $installed = Get-LisWindowsProject -Destination $destination
    Assert-LisTest (Test-Path -LiteralPath (Join-Path $installed 'scripts/windows.ps1')) 'Fresh archive installation failed.'
    [IO.File]::WriteAllText((Join-Path $installed 'keep.txt'), 'user data')
    Get-LisWindowsProject -Destination $destination | Out-Null
    Assert-LisTest ((Get-Content -LiteralPath (Join-Path $installed 'keep.txt')) -eq 'user data') 'Re-running the installer replaced existing files.'
    $unrelated = Join-Path $temp 'unrelated'; New-Item -ItemType Directory -Path $unrelated | Out-Null
    [IO.File]::WriteAllText((Join-Path $unrelated 'keep.txt'), 'user data')
    Assert-LisThrows { Get-LisWindowsProject -Destination $unrelated } 'not an empty'
    $envFile = Join-Path $installed '.env'
    [IO.File]::WriteAllText($envFile, "LIS_VIDEO=0`r`nLIS_BIND=127.0.0.1`r`n")
    Set-LisWindowsOption -File $envFile -Key 'LIS_VIDEO'
    $settings = [IO.File]::ReadAllText($envFile)
    Assert-LisTest ($settings.Contains('LIS_VIDEO=1') -and $settings.Contains('LIS_BIND=127.0.0.1')) 'An optional download changed unrelated settings.'
    $bytes = [IO.File]::ReadAllBytes($envFile)
    Assert-LisTest (-not ($bytes[0] -eq 239 -and $bytes[1] -eq 187 -and $bytes[2] -eq 191)) 'The .env file acquired a UTF-8 BOM.'
} finally { if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Recurse -Force } }
Write-Host "Passed $tests Windows launcher checks."
