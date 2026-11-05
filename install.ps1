#Requires -Version 5.1
<#
Windows installer. Requires an NVIDIA GPU and Docker Desktop using WSL 2.
No Windows Python, Git, CUDA toolkit or Linux distribution setup is needed.
Run a downloaded copy with: powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
#>
[CmdletBinding()]
param(
    [string] $InstallDirectory = '',
    [switch] $WithVideo,
    [switch] $WithSnofs,
    [switch] $NoShortcut,
    [switch] $NoBrowser
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-LisWindowsProject {
    param([string] $Destination)
    if (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'scripts\windows.ps1')) { return $PSScriptRoot }
    if (-not $Destination) { $Destination = Join-Path $env:LOCALAPPDATA 'LocalImageStudio' }
    $Destination = [IO.Path]::GetFullPath($Destination)
    if (Test-Path -LiteralPath (Join-Path $Destination 'scripts\windows.ps1')) { return $Destination }
    if (Test-Path -LiteralPath $Destination) {
        if (@(Get-ChildItem -LiteralPath $Destination -Force).Count) {
            throw "The destination is not an empty Local Image Studio folder: $Destination. Choose another -InstallDirectory."
        }
    }
    $staging = Join-Path ([IO.Path]::GetTempPath()) ('lis-install-' + [Guid]::NewGuid().ToString('N'))
    try {
        New-Item -ItemType Directory -Path $staging | Out-Null
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        Write-Host "Downloading Local Image Studio into $Destination"
        $archive = Join-Path $staging 'studio.zip'
        Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/aliarabbasi5155/local-image-studio/archive/refs/heads/main.zip' -OutFile $archive
        Expand-Archive -LiteralPath $archive -DestinationPath $staging
        $source = Join-Path $staging 'local-image-studio-main'
        if (-not (Test-Path -LiteralPath (Join-Path $source 'scripts\windows.ps1'))) { throw 'The downloaded archive is missing the Windows launcher.' }
        New-Item -ItemType Directory -Force -Path $Destination | Out-Null
        Get-ChildItem -LiteralPath $source -Force | Copy-Item -Destination $Destination -Recurse -Force
        return $Destination
    } finally {
        if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
    }
}

function Set-LisWindowsOption {
    param([string] $File, [string] $Key)
    $text = [IO.File]::ReadAllText($File)
    $pattern = '(?m)^' + [Regex]::Escape($Key) + '=[^\r\n]*'
    if ([Regex]::IsMatch($text, $pattern)) { $text = [Regex]::Replace($text, $pattern, "$Key=1") }
    else { $text += "`r`n$Key=1`r`n" }
    [IO.File]::WriteAllText($File, $text, (New-Object Text.UTF8Encoding($false)))
}

function Install-LisWindows {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or -not [Environment]::Is64BitOperatingSystem) {
        throw 'This installer needs 64-bit Windows with an NVIDIA GPU. Use install.sh on a Mac or Linux.'
    }
    $project = Get-LisWindowsProject -Destination $InstallDirectory
    $settings = Join-Path $project '.env'
    if (-not (Test-Path -LiteralPath $settings)) {
        # Start local-only. Phone access is an explicit setting in the Windows guide.
        $defaults = [IO.File]::ReadAllText((Join-Path $project '.env.example')) -replace '(?m)^LIS_BIND=[^\r\n]*', 'LIS_BIND=127.0.0.1'
        [IO.File]::WriteAllText($settings, $defaults, (New-Object Text.UTF8Encoding($false)))
    }
    if ($WithVideo) { Set-LisWindowsOption -File $settings -Key 'LIS_VIDEO' }
    if ($WithSnofs) { Set-LisWindowsOption -File $settings -Key 'LIS_SNOFS' }
    if (-not $NoShortcut) {
        $desktop = [Environment]::GetFolderPath('Desktop')
        if ($desktop -and (Test-Path -LiteralPath $desktop)) {
            $shell = New-Object -ComObject WScript.Shell
            $shortcut = $shell.CreateShortcut((Join-Path $desktop 'Local Image Studio.lnk'))
            $shortcut.TargetPath = Join-Path $project 'Local Image Studio.cmd'
            $shortcut.WorkingDirectory = $project
            $shortcut.Save()
        }
    }
    $launch = @('-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $project 'scripts\windows.ps1'), '-Action', 'start')
    if ($NoBrowser) { $launch += '-NoBrowser' }
    & powershell.exe @launch
    if ($LASTEXITCODE -ne 0) { throw 'The studio could not start. Read the message above, fix it, then run this installer again.' }
}

if ($MyInvocation.InvocationName -ne '.') {
    try { Install-LisWindows } catch { Write-Host "`nCannot continue: $_" -ForegroundColor Red; exit 1 }
}
