# AnsiWEB

A web interface for deploying a standard set of applications to Windows PCs with Ansible, from a **local installer cache**.

Built for workgroup environments with **no Active Directory and no Intune**. A single Linux server downloads every installer once and serves it to the PCs over the LAN, so PCs never need internet access to install or update apps. The cache checks vendors for new versions on a schedule, replaces outdated installers, and upgrades PCs at the next deployment.

```
                    internet (server only)
             winget manifests · vendor downloads
                              │
 ┌────────────────────────────▼─────────────────────────────┐
 │ AnsiWEB server (Ubuntu)                                   │
 │  web UI :80  ─ configure apps, PCs, schedules, run jobs   │
 │  /software/  ─ installer cache (SHA256-verified)          │
 │  Ansible     ─ deploys over WinRM HTTPS                    │
 └─────┬──────────────────────────┬──────────────────────────┘
       │ WinRM 5986               │ HTTP /software/
       ▼                          ▼
 ┌───────────┐  ┌───────────┐  ┌───────────┐
 │  PC-001   │  │  PC-002   │  │  PC-...   │   no internet needed
 └───────────┘  └───────────┘  └───────────┘
```

## Features

- **Local cache.** Installers are downloaded once, SHA256-verified, and served to PCs from the server.
- **Automatic updates.**
  - New versions are found through Microsoft's public [winget package manifests](https://github.com/microsoft/winget-pkgs), which give the download URL and SHA256 for each release.
  - Newer versions replace the cached installer; the previous version is kept for rollback.
  - PCs are upgraded at the next deployment.
- **Pinned apps.** An app can be held at a fixed version, which is then installed only where it's missing and never upgraded.
- **Version-aware deployment.**
  - Each PC's installed apps are read from the Windows registry and compared with the cache.
  - Only missing or outdated apps are installed, so repeat runs are fast and safe.
- **Web interface.**
  - Pages: Dashboard, Apps & cache, PCs (including CSV import), live job logs, Settings.
  - Built-in schedules for update checks and deployments.
- **Per-PC reports** show installed vs. cached versions after every deployment.
- **Security.**
  - Secrets are encrypted with Ansible Vault.
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

1. **Settings.** Enter the server's IP address and the `ansible_svc` password. Choose that password now; you'll use the same one on every PC in step 2.
2. **Prepare each PC once.** On the PCs page, click **Download PC prep script**; the script already contains the server IP. On each PC, open PowerShell as Administrator and run:
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\Prepare-AnsibleHost.ps1
   ```
   Enter the `ansible_svc` password from step 1. The script creates that local admin account, enables WinRM over HTTPS, and opens port 5986 to the server only.
3. **PCs page.** Add each PC by name and IP, or paste a CSV list (`name,ip,site,group1;group2`). Give PCs DHCP reservations: without company DNS, AnsiWEB reaches PCs by IP.
4. **Test the connections.** Click **Test all connections**. Every PC should report `pong`.
5. **Fill the cache.** On the Apps page, click **Check for updates now**.
6. **Deploy.** Deploy to one PC first (PCs page → **Deploy**), then to a site, then to all. Run it again afterwards: nothing should change.
7. **Schedules.** In Settings, enable the deployment schedule, e.g. weekdays at 19:00.

## How updates work

1. On schedule (default every 24 h) the server checks each auto-update app's newest version in the winget catalogue.
2. If it's newer than the cached version, the server downloads it, verifies the SHA256 against the manifest, and replaces the cached installer.
3. At the next deployment, each PC compares its installed version with the cache and installs the newer one.
4. If a download fails or the checksum doesn't match, the previous cached version stays in use and the error is shown on the Apps page. It's retried at the next check.

GitHub allows 60 unauthenticated catalogue lookups per hour, which is plenty for daily checks. For frequent manual checks, add a read-only GitHub token in Settings.

**Adding an app:** Apps → Add app. You need the app's winget ID (search on [winget.run](https://winget.run) or run `winget search` on any PC), silent install arguments (empty for MSIs), and a detection pattern. The detection pattern is a regular expression matching the app's name under *Installed apps*, e.g. `^Notepad\+\+`.

**Software not in winget:** choose **Uploaded file** as the source and upload the installer on the app's page.

## Pinned Firefox 65

Firefox is pinned to **65.0.2**, the last 65.x release and the first Firefox version Mozilla published as an MSI. AnsiWEB:

- installs it only on PCs without Firefox, and never upgrades it
- writes `distribution\policies.json` with `DisableAppUpdate`, so Firefox doesn't update itself
- flags PCs that already have a *different* Firefox version as **version mismatch** in the PC reports, and leaves them unchanged (downgrading Firefox can damage user profiles)

To upgrade later, edit the app: untick **Pin**, or change the URL and version.

> Firefox 65 dates from 2019 and no longer receives security fixes. Where possible, limit its use to the application that requires it.

## Security

- **This repository is public. Never commit anything from `/var/lib/ansiweb`.** It holds your configuration, the vault key and the encrypted secrets.
- The web UI uses plain HTTP on the LAN. Restrict it to your admin network by uncommenting the `allow`/`deny` lines in `/etc/nginx/sites-available/ansiweb`, or put it behind HTTPS.
- Every PC shares the `ansible_svc` password. The WinRM firewall rule (server IP only) is the main protection; keep the server locked down.
- PCs use self-signed WinRM certificates, so certificate validation is off. The traffic is still encrypted.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Test shows `UNREACHABLE` / timeout | Prep script not run on the PC, wrong IP, or the server IP in the prep script was wrong. On the PC: `winrm enumerate winrm/config/listener`. |
| `credentials were rejected` | The `ansible_svc` password in Settings differs from the one used on that PC. Re-run the prep script on the PC. |
| App shows **error: SHA256 mismatch** | The vendor published a new build before the winget catalogue caught up. It's retried at the next check; the previous version stays in use. |
| App shows **GitHub rate limit** | Add a GitHub token in Settings, or wait an hour. |
| An app reinstalls on every run | The detection pattern doesn't match the name under *Installed apps*. Check the exact name on a PC under Windows Settings → Apps → Installed apps, and adjust the pattern. |
| Service logs | `journalctl -u ansiweb -f` |

More, including backup, uninstall and WSL networking: [docs/INSTALL.md](docs/INSTALL.md#troubleshooting).

## Command line

```bash
sudo ansiweb <command>
  init            create data folders and default configuration
  set-password    change the web admin password
  update-cache    check vendors and refresh the cache
  deploy [target] deploy apps (all, site:HQ, group:finance, pc:PC-HQ-001)
  plan            rebuild the inventory and deployment plan
```

## Project layout

```
ansiweb/            Flask web app, cache manager, job runner, scheduler
  defaults/         initial configuration (your standard app list)
  templates/ static/
ansible/
  playbooks/        deploy.yml
  roles/ansiweb_apps/  detection (PowerShell), install, Firefox policy
scripts/            Prepare-AnsibleHost.ps1 (one-time PC setup)
docs/INSTALL.md     installation guide (Ubuntu Server and WSL)
deploy/             systemd unit and nginx site
install.sh          Ubuntu installer
```

Runtime data in `/var/lib/ansiweb`:

- `config.yml`
- `manifest.json`, the cache index
- `deploy_plan.json`
- `inventory/`, which includes the encrypted `vault.yml`
- `cache/`, served at `/software/`
- `logs/`
- `reports/`

## License

See [LICENSE](LICENSE).
