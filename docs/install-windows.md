# Install on Windows

Local Image Studio runs on Windows through **Docker Desktop with its WSL 2 backend**, using the same
CUDA engine as Linux. You use the normal Windows browser. No Windows Python, Git, CUDA toolkit or
separate Ubuntu installation is needed.

This installation path has automated launcher checks, but **has not yet been tested end to end on a
Windows NVIDIA machine**. The installer checks GPU access and runs a small CUDA operation before
starting the model downloads.

## Prepare once

- An x64 Windows PC with an **NVIDIA GPU with at least 6 GB VRAM** and **Windows NVIDIA driver 580+**.
  The launcher uses the first NVIDIA GPU exposed to Docker.
- An up-to-date Windows 11 installation, WSL 2 and **Docker Desktop for Windows**. Follow the
  [official installation guide](https://docs.docker.com/desktop/setup/install/windows-install/).
  In Docker Desktop, use **Linux containers** and **Settings → General → Use the WSL 2 based engine**.
  [Docker's GPU requirements](https://docs.docker.com/desktop/features/gpu/) explain the driver setup.
- **32 GB RAM recommended**, with about 16 GB available to WSL. WSL's default memory limit is half
  the PC's memory, so 16 GB Windows PCs can be too constrained for these models. See Microsoft's
  [WSL memory settings](https://learn.microsoft.com/en-us/windows/wsl/wsl-config#main-wsl-settings).
- About **50 GB free** for models, container images and build cache, on the drive used by Docker
  Desktop. The model download is about 27 GB. Optional video needs about 25 GB more.

Open Docker Desktop once and finish its setup. Update WSL if needed with `wsl --update`, then restart
Windows if prompted. Install the GPU driver on **Windows**, not a Linux NVIDIA driver inside WSL.
You do not need to install the NVIDIA Container Toolkit separately inside Docker Desktop.

## Install

Open **PowerShell** and paste:

```powershell
$installer = Join-Path $env:TEMP 'local-image-studio-install.ps1'
Invoke-WebRequest -UseBasicParsing https://raw.githubusercontent.com/aliarabbasi5155/local-image-studio/main/install.ps1 -OutFile $installer
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer
```

The execution-policy flag applies only to that new PowerShell process; it does not change your
machine's execution policy.

The installer downloads the project to `%LOCALAPPDATA%\LocalImageStudio`, builds the containers,
checks CUDA, downloads and verifies the models, prints your PIN and opens <http://localhost:7878>.
The first installation can take a while. Progress stays visible; **do not wait for a browser tab
before checking the PowerShell window**. Re-running the installer resumes verified downloads.

Already downloaded or cloned the repository? Open PowerShell in that folder and run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

Optional flags: `-WithVideo`, `-WithSnofs`, `-NoShortcut`, `-NoBrowser`. SNOFS is a third-party style
adapter under its author's [personal-use license](models-and-licenses.md). A custom install folder
for the standalone installer can be supplied with `-InstallDirectory 'D:\Local Image Studio'`.

## Open it next time

Double-click the **Local Image Studio** desktop shortcut, or **Local Image Studio.cmd** in the
project folder. The window keeps your PIN visible after opening the browser.

From PowerShell in the project folder (if needed, prefix commands with
`powershell.exe -NoProfile -ExecutionPolicy Bypass -File`):

```powershell
.\scripts\windows.ps1 start       # start, wait until ready, print PIN, open browser
.\scripts\windows.ps1 stop        # stop; keeps models and library
.\scripts\windows.ps1 status      # show containers
.\scripts\windows.ps1 pin         # show PIN
.\scripts\windows.ps1 logs        # follow setup / runtime logs (Ctrl+C to leave)
.\scripts\windows.ps1 rebuild     # rebuild after changing or updating the code
```

Containers restart when Docker Desktop starts. For automatic availability after Windows sign-in,
enable Docker Desktop's **Start Docker Desktop when you sign in** setting.

## Files, backups and updates

Models and your library are in Docker **named volumes**, rather than Windows bind mounts. They keep
Linux permissions and are reused across rebuilds. The library volume is
`local-image-studio-windows_library`; the other volumes store models, engine state, helper token and
the website certificates. The website cannot read the certificate-authority private key.

Back up the library while the website is stopped:

```powershell
docker compose -f compose.windows.yaml stop studio
docker compose -f compose.windows.yaml cp studio:/data .\library-backup
docker compose -f compose.windows.yaml start studio
```

To update a Git checkout, run `git pull --ff-only`, then the installer again. For an archive install,
download the latest repository ZIP, extract it into a new folder and run its `install.ps1`. The fixed
Compose project name reuses your existing models and library, and the desktop shortcut is updated.
Copy your `.env` to the new folder if you customized it. Re-running the standalone installer against
an existing installation repairs/starts its current code; it does not overwrite that code.

`docker compose -f compose.windows.yaml down` removes containers and retains your data. **Do not add
`--volumes`** unless you intend to delete the saved library and models.

## Phone access (optional)

The Windows install initially listens on **localhost only**. To enable your home Wi-Fi devices, set
`LIS_BIND=0.0.0.0` and `LIS_ADDRESS` to your Windows PC's home-network IPv4 address in `.env`, then run
the launcher again. On a private home network, allow TCP ports 7878 and 7443 through Windows
Defender Firewall for the **Private** profile. Follow [Use it on your phone](phone.md) to trust the
home certificate and install the app. The installer does not change your firewall or router.

## If setup stops

- **Docker not running:** open Docker Desktop and wait for its engine to finish starting.
- **Windows containers / wrong backend:** switch to Linux containers and the WSL 2 engine.
- **GPU check fails:** update the Windows NVIDIA driver and WSL, restart, then run the launcher.
- **Still downloading:** `.\scripts\windows.ps1 logs` shows progress. A startup timeout leaves the
  download running; launch again later. Models are verified and reused.
- **Out of memory:** check the memory available to WSL and try Fast quality. Close other large apps.
- **Port in use:** set another `LIS_PORT`, such as `7879`, in `.env`. The launcher opens that port.
- **Remote Docker context:** use Docker Desktop's local context. The launcher refuses to upload your
  project or library to a remote engine.
