# AnsiWEB

A web interface for managing Windows PCs with Ansible from a **local cache**: standard applications, device drivers, scripts and registry settings, with per-PC reporting.

Built for workgroup environments with **no Active Directory and no Intune**. A single Linux server downloads every installer once and serves it to the PCs over the LAN, so PCs never need internet access to install or update apps. The cache checks vendors for new versions on a schedule, replaces outdated installers, and upgrades PCs at the next deployment.

```
                    internet (server only)
             winget manifests · vendor downloads
                              │
 ┌────────────────────────────▼──────────────────────────────┐
 │ AnsiWEB server (Ubuntu)                                    │
 │  web UI :443 ─ HTTPS dashboard (apps, drivers, scripts,    │
 │                registry, PCs, reports, schedules, backup)  │
 │  /software/  ─ cache on :80 (every file SHA256-verified)   │
 │  Ansible     ─ deploys over WinRM HTTPS                     │
 └─────┬───────────────────────────┬──────────────────────────┘
       │ WinRM 5986                │ HTTP /software/
       ▼                           ▼
 ┌───────────┐  ┌───────────┐  ┌───────────┐
 │  PC-001   │  │  PC-002   │  │  PC-...   │   no internet needed
 └───────────┘  └───────────┘  └───────────┘
```

## Features

- **Local cache.** Installers are downloaded once, SHA256-verified, and served to PCs from the server. PCs never need internet access.
- **Automatic updates.**
  - New versions are found through Microsoft's public [winget package manifests](https://github.com/microsoft/winget-pkgs), which give the download URL and SHA256 for each release.
  - Newer versions replace the cached installer; the previous version is kept for rollback.
  - PCs are upgraded at the next deployment.
- **Pinned apps.** An app can be held at a fixed version, which is then installed only where it's missing and never upgraded.
- **Offline apps.** Upload an .msi or .exe straight from the Apps page for software that isn't in any catalogue.
- **Device drivers.** Upload a .zip containing the .inf files; PCs unpack it and add it to the Windows driver store with pnputil.
- **Scripts.** Upload .ps1, .cmd or .bat files and run them on chosen PCs, once, on change, or at every deployment, with exit codes and output captured.
- **Registry files.** Upload .reg files and have them merged on chosen PCs.
- **Rename PCs.** Rename a PC in AnsiWEB, and optionally have Windows renamed to match at the next deployment.
- **Version-aware deployment.**
  - Each PC's installed apps are read from the Windows registry and compared with the cache.
  - Only missing or outdated apps are installed, so repeat runs are fast and safe.
- **Web interface over HTTPS.**
  - Pages: Dashboard, Apps & cache, PCs (including CSV import), Drivers, Scripts, Registry, Reports, Jobs, Settings.
  - Built-in schedules for update checks and deployments; every job has a live log you can download.
- **Reports.** Each PC reports after every deployment: installed versions, driver/script/registry results, OS, model, serial, pending reboots. Exportable as CSV.
- **Backup and restore.** One click to download your configuration, secrets and uploaded files; restore them into a fresh install from the same page.
- **Security.**
  - The dashboard is served over HTTPS; secrets are encrypted with Ansible Vault.
  - The web interface requires a login and has CSRF protection.
  - WinRM on each PC only accepts connections from the server.

## Default app set

| App | Source | Policy |
|---|---|---|
| Microsoft Visual C++ 2015-2022 Redistributable (x64) | winget `Microsoft.VCRedist.2015+.x64` | auto-update |
| 7-Zip | winget `7zip.7zip` | auto-update |
| Adobe Acrobat Reader (64-bit) | winget `Adobe.Acrobat.Reader.64-bit` | auto-update |
| Google Chrome | winget `Google.Chrome` (enterprise MSI) | auto-update |
| Mozilla Firefox **65.0.2** | fixed URL from Mozilla's release archive | **pinned**, Firefox's own updater disabled |
| Java 8 Runtime (Oracle, 64-bit) | winget `Oracle.JavaRuntimeEnvironment` | auto-update |
| VLC media player | winget `VideoLAN.VLC` | auto-update |

Everything is editable in the web interface.

Microsoft Office is not deployed by AnsiWEB and is installed separately.

Alongside apps you can upload drivers, scripts and registry files, each targeted at all PCs, a site or a group.

> **Licensing note:** Oracle Java 8 updates released after early 2019 require a paid Oracle Java SE subscription for commercial use. Confirm your licensing, or switch the Java app to Eclipse Temurin (`EclipseAdoptium.Temurin.8.JRE`) if your application supports OpenJDK.

---

## Installation

**Full step-by-step guide, including WSL: [docs/INSTALL.md](docs/INSTALL.md).**

**Requirements:** Ubuntu 22.04 or 24.04 (a server, a VM, or WSL2 on a Windows PC), 2 vCPU, 4 GB RAM, about 10 GB free disk for the cache, a fixed IP address, and internet access.

```bash
sudo apt update && sudo apt install -y git
git clone https://github.com/mzuogha/AnsiWEB.git
cd AnsiWEB
sudo ./install.sh
```

The installer creates:

- the application in `/opt/ansiweb`
- the data folder `/var/lib/ansiweb` (configuration, secrets, cache, logs), which is never part of the Git repository
- a `systemd` service called `ansiweb`, with nginx in front of it on port 80
- the `ansiweb` command for maintenance

It asks you to choose the web admin password, then prints the address to open. Sign in as `admin`.

**Running under WSL?** Two extra steps are needed: enable systemd inside WSL, and forward port 80 from Windows into WSL so your PCs can reach the cache. The installer detects WSL and prints the commands; [docs/INSTALL.md](docs/INSTALL.md#option-b-wsl-on-a-windows-pc) has the full procedure, including keeping it working after a reboot.

To upgrade later: `git pull && sudo ./install.sh`. Configuration and cache are kept.

> Upgrading from v1.0, which deployed Office: the installer deletes the unused Office cache (about 4 GB), and the Office settings are dropped from your configuration. PCs that already have Office keep it; they simply stop being managed by AnsiWEB. Office then updates itself from Microsoft again, unless you removed that setting. To re-enable AnsiWEB's Office support, check out commit `5df483f`.

## First-time setup

The short version is below; [docs/INSTALL.md](docs/INSTALL.md#first-time-configuration) covers each step in detail.

1. **Settings.** Enter the server's IP address. Then, on the **PCs** page, set the management account (`Admin` by default) and its password. Choose that password now; you'll use the same one on every PC in step 2.
2. **Prepare each PC once.** On the PCs page, click **Download PC prep script**; the script already contains the server IP. On each PC, open PowerShell as Administrator and run:
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\Prepare-AnsibleHost.ps1
   ```
   Enter the password from step 1. The script creates that local administrator account (`Admin` by default), enables WinRM over HTTPS, and opens port 5986 to the server only.
3. **PCs page.** Add each PC by name and IP, or paste a CSV list (`name,ip,site,group1;group2`). Give PCs DHCP reservations: without company DNS, AnsiWEB reaches PCs by IP.
4. **Test the connections.** Click **Test all connections**. Every PC should report `pong`.
5. **Fill the cache.** On the Apps page, click **Check for updates now**.
6. **Deploy.** Deploy to one PC first (PCs page → **Deploy**), then to a site, then to all. Run it again afterwards: nothing should change.
7. **Schedules.** In Settings, enable the deployment schedule, e.g. weekdays at 19:00.
8. **Optional extras.** Upload device drivers, scripts and registry files on their own pages, then check the **Reports** page after the next deployment. Download a backup from Settings once you are happy with the setup.

## Drivers, scripts and registry files

Each of these has its own page and works the same way: upload the file once, choose which PCs it applies to (all PCs, a site or a group), and choose when it should run.

| Page | Upload | What PCs do with it |
|---|---|---|
| Drivers | `.zip` containing the `.inf` (plus its `.cat`/`.sys` files) | Unpacks it and adds every `.inf` to the Windows driver store with `pnputil /add-driver /install` |
| Scripts | `.ps1`, `.cmd` or `.bat` | Runs it as SYSTEM, with your arguments; exit code and output are recorded |
| Registry | `.reg` exported from Registry Editor | Merges it with `reg import` |

"When it runs" is one of:

- **Once per PC** — skipped on later deployments. Use for one-off fixes and driver installs.
- **Again whenever the file changes** — re-runs after you upload a new version of the file.
- **Every deployment** — runs each time, for anything that enforces a setting.

Each PC keeps a marker file per item under `C:\ProgramData\AnsiWEB\state`, which is how "once" and "on change" survive reboots and re-runs. **Run now** on any item applies just that one item, so you don't have to wait for a full deployment. Scripts can be given extra success exit codes, a timeout, and a "reboot afterwards" flag.

## Renaming PCs

Changing a PC's name on its page renames it inside AnsiWEB. Tick **Keep the Windows computer name in sync** and the next deployment (or the **Apply computer names** job) renames Windows to match, which requires a reboot to finish. The PCs page flags any PC whose reported Windows name differs from its AnsiWEB name.

## Reports

Every PC writes a report at the end of each deployment: installed versus cached app versions, the result of each driver, script and registry item, plus OS build, model, RAM, serial number and whether a reboot is pending. The Reports page shows all of it, exports to CSV, and links the log of every job. Job logs are kept for the number of days set in Settings.

## Backup and restore

**Settings → Download backup** produces a `.tar.gz` with your configuration, PC list, encrypted secrets and every uploaded driver, script and registry file. Cached app installers are excluded, since they can be downloaded again.

Restoring from the same page replaces the current configuration and signs you out; sign back in with the password from the backup. The archive contains the key to your stored passwords, so treat it as a secret.

## How updates work

1. On schedule (default every 24 h) the server checks each auto-update app's newest version in the winget catalogue.
2. If it's newer than the cached version, the server downloads it, verifies the SHA256 against the manifest, and replaces the cached installer.
3. At the next deployment, each PC compares its installed version with the cache and installs the newer one.
4. If a download fails or the checksum doesn't match, the previous cached version stays in use and the error is shown on the Apps page. It's retried at the next check.

GitHub allows 60 unauthenticated catalogue lookups per hour, which is plenty for daily checks. For frequent manual checks, add a read-only GitHub token in Settings.

**Adding an app:** Apps → Add app. You need the app's winget ID (search on [winget.run](https://winget.run) or run `winget search` on any PC), silent install arguments (empty for MSIs), and a detection pattern. The detection pattern is a regular expression matching the app's name under *Installed apps*, e.g. `^Notepad\+\+`.

**Software not in winget:** choose **Uploaded file** as the source and upload the installer on the app's page.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Test shows `UNREACHABLE` / timeout | Prep script not run on the PC, wrong IP, or the server IP in the prep script was wrong. On the PC: `winrm enumerate winrm/config/listener`. |
| `credentials were rejected` | The account password on the PCs page differs from the one used on that PC. Re-run the prep script on the PC. |
| App shows **error: SHA256 mismatch** | The vendor published a new build before the winget catalogue caught up. It's retried at the next check; the previous version stays in use. |
| App shows **GitHub rate limit** | Add a GitHub token in Settings, or wait an hour. |
| An app reinstalls on every run | The detection pattern doesn't match the name under *Installed apps*. Check the exact name on a PC under Windows Settings → Apps → Installed apps, and adjust the pattern. |
| Browser warns about the certificate | Expected: it is self-signed. Compare the fingerprint with `sudo openssl x509 -in /etc/ssl/ansiweb/server.crt -noout -fingerprint -sha256`, or install your own certificate in `/etc/ssl/ansiweb/`. |
| A driver install fails | The `.zip` must contain the `.inf` files themselves, not a vendor setup program. The job log shows the `pnputil` exit code. |
| A script is reported as failed | Its exit code isn't in the success list. Add the code on the script's page, or fix the script. Output is on the Reports page. |
| Changing the PC account locked AnsiWEB out | The account must exist on the PCs first. Download the prep script again and run it on each PC, or set the old name back. |
| Service logs | `journalctl -u ansiweb -f` |

More, including backup, uninstall and WSL networking: [docs/INSTALL.md](docs/INSTALL.md#troubleshooting).

## Command line

```text
sudo ansiweb <command>
  init            create data folders and default configuration
  set-password    change the web admin password
  update-cache    check vendors and refresh the cache
  deploy [target] deploy everything (all, site:HQ, group:finance, pc:PC-HQ-001)
  plan            rebuild the inventory and deployment plan
```

Targeted deployments and single items are run from the web interface (Jobs page, or **Run now** on an item).

## Project layout

```
ansiweb/            Flask web app, cache manager, job runner, scheduler
  defaults/config.yml  initial configuration (your standard app list)
  payloads.py       uploaded drivers, scripts and registry files
  backup.py         backup and restore
  templates/ static/
ansible/
  playbooks/        deploy.yml
  roles/ansiweb_apps/  version detection, apps, drivers, scripts, registry, rename
scripts/            Prepare-AnsibleHost.ps1 (one-time PC setup)
docs/INSTALL.md     installation guide (Ubuntu Server and WSL)
deploy/             systemd unit, nginx site, certificate script
tests/smoke_test.py offline test of the web interface
install.sh          Ubuntu installer
```

Runtime data in `/var/lib/ansiweb`:

- `config.yml`
- `manifest.json`, the cache index
- `deploy_plan.json`
- `inventory/`, which includes the encrypted `vault.yml`
- `cache/apps/`, `cache/drivers/`, `cache/scripts/`, `cache/registry/`, served at `/software/`
- `logs/`, job logs
- `reports/`, one JSON file per PC

## License

See [LICENSE](LICENSE).
