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
- **Windows updates.** Install Windows updates on the PCs by category, with exclusions, optional reboots and a weekly schedule.
- **Time and time zone.** Push a time zone and your own time servers to the PCs, and force a clock resync.
- **Windows activation.** Store a product key once and have AnsiWEB install and activate it on the PCs it manages, either with your own key or against a KMS host.
- **Rename PCs.** Rename a PC in AnsiWEB, and optionally have Windows renamed to match at the next deployment.
- **Version-aware deployment.**
  - Each PC's installed apps are read from the Windows registry and compared with the cache.
  - Only missing or outdated apps are installed, so repeat runs are fast and safe.
- **Web interface over HTTPS.**
  - Pages: Dashboard, Apps & cache, PCs, Shared folders, Printers, Drivers/scripts/registry, Inventory & uninstall, Reports & audit, Jobs, Settings, Users, Help.
  - Built-in schedules for update checks and deployments; every job has a live log you can download.
- **Reports.** Each PC reports after every deployment: installed versions, driver/script/registry results, OS, model, serial, pending reboots. Exportable as CSV.
- **Shared folders.** Create and share a folder on the PCs you choose, with the access you specify, and turn Windows file sharing on.
- **Find a PC by its user.** Record who each PC is assigned to and search by person, name, address or site.
- **Printers.** Push network printers or shared queues to the PCs you choose, including which is the default.
- **Software inventory.** Every program installed on every PC, searchable and exportable, not just the ones AnsiWEB manages.
- **Scoped roles.** Limit someone to particular sites or groups.
- **Choose what to deploy.** Deploy opens a page to pick which parts to apply and which PCs to apply them to.
- **Uninstall from PCs.** Remove an app from the PCs it was installed on, with a preview first and a typed confirmation before anything goes.
- **Audit log.** Every change through the web interface, with the account that made it.
- **Stale PC detection.** PCs that stop reporting are counted and flagged rather than quietly disappearing.
- **Roles.** Four levels of access, so you can let someone run deployments or manage apps without giving them administrator rights.
- **Built-in help.** A Help page with the deployment commands for Ubuntu Server and WSL.
- **Release notes.** The web interface shows what changed in each version, and says so on the dashboard after an upgrade.
- **Your own logo** on the sign-in page.
- **Session timeout.** The web session signs itself out after a period of inactivity, set in Settings.
- **Backup and restore.** One click to download your configuration, secrets and uploaded files; restore them into a fresh install from the same page.
- **Security.**
  - The dashboard is served over HTTPS; secrets are encrypted with Ansible Vault.
  - The web interface requires a login and has CSRF protection.
  - WinRM on each PC only accepts connections from the server.

## Where to run it

Run AnsiWEB on a **Hyper-V virtual machine** or a dedicated server. A Hyper-V VM set with `-AutomaticStartAction Start` comes back on its own after a restart or a power cut, with nobody signed in, and keeps its own address on your network. Any other hypervisor or a spare machine does just as well.

**WSL is for trying it out only.** It does not start with Windows, it stops when the signed-in user logs out, and its address changes on every restart — so scheduled deployments and overnight update checks quietly do not happen. The Help page inside AnsiWEB has the full explanation and the Hyper-V setup commands.

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

To upgrade later: `git pull && sudo ./install.sh`. Configuration and cache are kept. After an upgrade the dashboard says which version you came from and links to the **Release notes** page; [CHANGELOG.md](CHANGELOG.md) and the [GitHub releases](https://github.com/mzuogha/AnsiWEB/releases) have the same list.

> Upgrading from v1.0, which deployed Office: the installer deletes the unused Office cache (about 4 GB), and the Office settings are dropped from your configuration. PCs that already have Office keep it; they simply stop being managed by AnsiWEB. Office then updates itself from Microsoft again, unless you removed that setting. To re-enable AnsiWEB's Office support, check out commit `5df483f`.

## First-time setup

The short version is below; [docs/INSTALL.md](docs/INSTALL.md#first-time-configuration) covers each step in detail.

1. **Settings.** Enter the server's IP address. Then, on the **PCs** page, set the management account (`Admin` by default) and its password. Choose that password now; you'll use the same one on every PC in step 2.
2. **Prepare each PC once** — with the simple `.cmd` (right-click, Run as administrator; built-in commands only, no certificate) or the PowerShell script (creates a certificate and an HTTPS listener). Set **How AnsiWEB connects** on the PCs page to match: NTLM on 5985 for the `.cmd`, HTTPS on 5986 for PowerShell. On the PCs page, click **Download PC prep script**; the script already contains the server IP. On each PC, open PowerShell as Administrator and run:
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

All three live on one page, **Drivers, scripts & registry**, and work the same way: upload the file once, choose which PCs it applies to (all PCs, a site or a group), and choose when it should run. AnsiWEB tells the kinds apart by the file you upload.

| Upload | What PCs do with it |
|---|---|
| `.zip` containing the `.inf` (plus its `.cat`/`.sys` files) | Unpacks it and adds every `.inf` to the Windows driver store with `pnputil /add-driver /install` |
| `.ps1`, `.cmd` or `.bat` | Runs it as SYSTEM, with your arguments; exit code and output are recorded |
| `.reg` exported from Registry Editor | Merges it with `reg import` |

Each kind has its own **Apply** button on that page, plus **Apply all** to do the lot in one job.

Each item applies to all PCs, a site, a group, or individual PCs you tick — useful for a driver that belongs to one machine or one printer.

"When it runs" is one of:

- **Once per PC** — skipped on later deployments. Use for one-off fixes and driver installs.
- **Again whenever the file changes** — re-runs after you upload a new version of the file.
- **Every deployment** — runs each time, for anything that enforces a setting.

Each PC keeps a marker file per item under `C:\ProgramData\AnsiWEB\state`, which is how "once" and "on change" survive reboots and re-runs. **Run now** on any item applies just that one item, so you don't have to wait for a full deployment. Scripts can be given extra success exit codes, a timeout, and a "reboot afterwards" flag.

## Shared folders

The **Shared folders** page creates a folder on the PCs you choose and shares it. You give the share name, the folder path, and which accounts get read, change or full access. The folder is created if it isn't there, and setting one up again does nothing when it's already correct.

Two things worth knowing:

- **File and printer sharing has to be on**, and Windows blocks it by default. The switch at the top of the page opens the right firewall rules and makes sure the Server service is running, optionally with network discovery too. Nothing is reachable until this is on.
- **Access is enforced, not just granted.** Anyone given access by hand who isn't on your list is removed, so the share matches what AnsiWEB says it should be. Leave all three access fields empty to accept whatever Windows sets by default.

An entry can also **take a share down** instead of creating it; the folder and its contents are left alone.

## Printers

The **Printers** page pushes network printers — a printer with its own IP address — to the PCs you choose. AnsiWEB creates the port and the printer using the Windows driver name you give.

**Give the printer its driver.** Either attach the vendor's `.zip` of `.inf` files to the printer entry, or link a driver already uploaded on the drivers page — a linked driver wins if both are set. Either way AnsiWEB installs it into each PC's driver store before creating the printer. Without one, the named driver must already be on the PC.

**Push it where you want.** A printer has standing targets (all PCs, a site, a group, or a named PC), and the **Set up on these** button pushes it to individual PCs you tick, for a one-off.

You can mark one as the default, add a location and comment, or tick **Remove this printer from the PCs** to take one off instead. Setting a printer up again does nothing when it's already correct, so it's safe to run after adding a PC.

Shared print-server queues are not supported: a network printer is the kind AnsiWEB can set up identically on every PC without per-user connections.

## Choosing what to deploy

**Deploy** opens a page rather than starting immediately. Everything is ticked by default — the same as a plain deployment — so you can untick whatever you want left alone this time: applications, drivers, scripts, registry files, shared folders, printers, time, activation, computer name, Windows updates, inventory. Parts with nothing configured are marked, so an empty tick is obvious.

Under it you choose where: all PCs, a site, a group, or individual PCs from a list. Picking everything runs the whole deployment; picking some runs only those parts.

## Software inventory

AnsiWEB already reads each PC's installed-programs registry to work out what to install. The **Inventory** page keeps the whole list, not just the managed apps.

Search by program or publisher and you get every matching program, which PCs have it and at which versions — the question worth answering the morning a vulnerability is announced. The list exports to CSV for an asset register, and a PC's own page shows everything on that machine.

The inventory is refreshed at the end of every deployment, or on its own with the **Collect inventory** job, which reads the PCs without changing anything. Collection can be switched off in Settings, and the list is capped per PC so one unusual machine cannot bloat its report.

## Uninstalling apps from the PCs

Both kinds of uninstall live on the **Inventory** page.

Removing an app from the standard set stops AnsiWEB installing it, but leaves it on the PCs. The Inventory page removes it from them: each program row has a **Preview** and an **Uninstall** button for a one-off on a chosen PC, and the **Standing uninstalls** section below holds the rules for software that should be gone everywhere and stay gone.

A standing entry is a name, a pattern matching the program's name in Windows "Installed apps", and which PCs it applies to. AnsiWEB finds the program and runs its own uninstaller silently — the MSI product code where there is one, otherwise the vendor's quiet uninstall command. A program with no silent uninstaller is reported and left alone, rather than left sitting on a prompt nobody can see.

Two deliberate safeguards, since this is the one destructive thing AnsiWEB does:

- **A normal deployment never uninstalls anything.** It only reports what would go. Removal happens only when you run the uninstall job.
- **Running it needs REMOVE typed in**, and **Preview** shows the exact command per PC beforehand, changing nothing.

A pattern that would match every installed program is refused. When you remove an app from the standard set you can tick a box to queue it for removal at the same time, which copies the app's own detection pattern.

## Audit log

Admins get an **Audit log** section on the Reports page recording every change made through the web interface: what was done, by which account and role, what was submitted, and whether it was refused. Sign-ins, failed sign-ins and backup downloads are included. It filters by user, action and period, and exports to CSV.

Passwords, product keys and stored secrets are never written to it — those fields are recorded as hidden. Entries are kept for the same number of days as job logs.

Every state-changing request is logged automatically rather than route by route, so a new feature is covered without anyone remembering to add a call.

## Stale PC detection

A PC writes a report at the end of every deployment. If one stops reporting it is switched off, off the network, or gone — and previously it simply vanished from the reports quietly.

Now the dashboard counts PCs that have not reported within **Treat a PC as not reporting after** days (14 by default, set in Settings) plus any that never have, and links straight to them. The Reports page shows each PC's freshness, filters to just the problem ones, and the CSV export carries both the state and the days since the last report.

## Security notes

Worth understanding before you deploy:

- **The cache on port 80 is unauthenticated.** Anyone who can reach the server can download anything under `/software/` — not just installers, but uploaded scripts, `.reg` files and driver packages. Never put a password or key inside a script. To limit it to your own PCs, add `allow`/`deny` lines to the `location /software/` block in `/etc/nginx/sites-available/ansiweb`.
- **Uploading a script, driver or app is equivalent to running code as SYSTEM on the PCs it targets.** That is what Operator means; Helpdesk and Viewer cannot do it.
- **Every PC shares one management password.** The prep script limits WinRM to this server's address, which is the main protection. Change the password periodically.
- **WinRM certificates are self-signed**, so traffic is encrypted but the PC's identity is not verified. Issue certificates from your own CA and tick "Verify each PC's WinRM certificate" if that matters to you.
- **The dashboard certificate is self-signed too**, so browsers warn once. Replace it with your own in `/etc/ssl/ansiweb/`.
- **Secrets are encrypted** with Ansible Vault and never appear in the deployment plan, job logs or the audit log. The vault key sits in `/var/lib/ansiweb/.vault_pass`, readable only by the service account, and is included in backups — so treat a backup file as a secret.
- **Sign-ins are throttled**: five failures for a user from one address triggers a five-minute lockout.

## Roles

AnsiWEB has four roles, so you can hand out what someone actually needs. Manage them on the **Users** page.

| Role | Can do |
|---|---|
| **Administrator** | Everything: settings, stored secrets, backup and restore, and managing accounts. |
| **Operator** | Manage apps, drivers, scripts, registry files and PCs, and run any job. No settings, secrets or accounts. |
| **Helpdesk** | Run deployments and connection tests against PCs, and read reports. Cannot change what is deployed. |
| **Viewer** | Read-only: dashboard, apps, PCs, reports and job logs. |

**Scopes.** A non-administrator can be limited to particular sites or groups. They see only those PCs on every page, and any job they start is narrowed to that set before it runs, so it cannot reach a PC outside the scope even if the form is tampered with. Administrators are never scoped, and promoting someone clears any scope they had.

Everyone can change their own password. Controls a role cannot use are hidden rather than failing when pressed, and a role change or a disabled account takes effect on that person's next click. AnsiWEB always keeps at least one administrator, so the last one cannot be demoted, disabled or removed.

Every route is mapped to a permission, and anything unmapped requires an administrator, so a new feature is never accidentally exposed to a lesser role.

Anyone who can manage apps, drivers, scripts or registry files can cause code to run on the PCs they target, so Operator is a trusted role. Helpdesk and Viewer cannot change what gets deployed.

From the command line:

```bash
sudo ansiweb list-users
sudo ansiweb add-user jane operator
sudo ansiweb set-password jane
```

## Branding the sign-in page

**Settings → Sign-in page** takes a logo (PNG, JPG, GIF or WEBP, under 2 MB) and a short name, both shown on the sign-in page so people can tell which server they are on. The logo is served without a sign-in, since the sign-in page itself needs it, and it is included in backups. SVG is deliberately not accepted, because an SVG can carry scripts.

## Built-in help

The **Help** page in the web interface has copyable commands for deploying on Ubuntu Server and under WSL, preparing a PC, taking backups, and checking common problems. It fills in your own server address.

## Windows updates

Optional, and off by default. **Settings → Windows updates** takes:

- **Which updates** — security, critical, rollups, Defender definitions, drivers and so on. At least one is required.
- **Where updates come from** — whatever each PC is already set to, Microsoft Windows Update directly, or the PC's WSUS only. AnsiWEB does not change which update service a PC uses; to point PCs at a WSUS server, upload a `.reg` file on the Drivers, scripts & registry page.
- **Give up after** — a per-PC timeout in minutes, 180 by default.
- **Skip these updates** — KB numbers or parts of an update title, e.g. `KB5001234`.
- **Let Windows reboot the PC** — off by default, in which case PCs needing a reboot are listed at the end of the job instead.
- **Install on** and a **schedule** — updates run last in a deployment, or on their own with **Install updates now**, or weekly at a time you set.

Each PC reports how many updates were found, installed and failed, which update titles went on, and whether a reboot is still needed. A PC where an update fails is marked failed so it stands out on the Reports page.

Updates genuinely can take hours on a PC that is behind, which is why the timeout is generous and a weekly window outside working hours is the usual choice.

## Time and time zone

Optional, and off by default. **Settings → Time and time zone** takes:

- **Time zone** — picked from a list of common Windows time zone IDs, or typed in. Run `tzutil /l` on a PC for the full list. Leave it empty to leave the PCs' zone alone.
- **Time servers** — one or more, comma- or space-separated, in order of preference. Windows is pointed at them with `w32tm` and set to sync from them only.
- **Force a clock resync** — runs `w32tm /resync` each time.
- **Apply to** — all PCs, a site, or a group.

It runs as part of a deployment, or on its own with **Set the time now**. Each PC reports its resulting time zone, local time and time source, and the job flags any PC whose clock is more than two minutes away from the server's. A PC that cannot reach a time source is reported rather than failing the run.

Clocks are worth keeping straight: a PC more than a few minutes out starts failing certificate checks and some authentication.

## Windows activation

Optional, and off by default. **Settings → Windows activation** takes:

- **How to activate** — a product key (MAK or retail) that you own, or a **KMS host** on your network.
- **Product key** — stored encrypted with Ansible Vault, never shown again, and never written to the deployment plan or the job logs.
- **KMS host and port** — for KMS mode; the default port is 1688.
- **Skip PCs that are already activated** — on by default, so activated PCs are left alone.
- **Activate on** — all PCs, a site, or a group.

Activation then runs as part of a deployment, or on its own with **Activate Windows now**. Each PC reports the licence state before and after, and the Reports page flags any PC Windows does not consider activated.

A MAK or retail key has to reach Microsoft to activate, so those PCs need internet access at least once. KMS activation stays inside your network. Each MAK activation uses one of the activations your key allows.

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
| Updates take forever or time out | Normal on a PC that is far behind; raise the timeout and run it again. The job log shows progress per PC. |
| Updates report failures | The job log lists each update and its error. Failures are often fixed by rebooting the PC and running updates again. |
| A PC reports "resync did not succeed" | It cannot reach the time servers. Check the names and that UDP 123 is open to them. |
| Activation reports "still in notification mode" | Windows accepted the key but could not activate. For a MAK key the PC needs internet access; for KMS check the host name, port 1688 and that the PC has a KMS client key. |
| Activation says no product key is stored | Add one in Settings, or switch to KMS mode. |
| Signed out while working | The session times out after the inactivity period set in Settings (60 minutes by default). Watching a job log does not count as activity, so an unattended job page still times out. |
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
  defaults/release_notes.yml  release notes, rendered in the web interface
  release.py        reads the release notes
  util.py           small shared helpers
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
tools/make_changelog.py  regenerates CHANGELOG.md from the release notes
tools/publish_release.py publishes a version's notes as a GitHub Release
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
