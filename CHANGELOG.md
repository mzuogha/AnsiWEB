# Changelog

All notable changes to AnsiWEB. This file is generated from
`ansiweb/defaults/release_notes.yml`, which the Release notes page in the web
interface also renders - edit that file, then run `python3 tools/make_changelog.py`.


## 1.4.0 - 2026-09-12

Roles, so you can give people the access they need without handing out full administrator rights, and a Help page with the deployment commands.


### New

- Four roles - Administrator, Operator, Helpdesk and Viewer. Operators manage what gets deployed and run jobs; Helpdesk can run deployments and read reports; Viewers are read-only.
- A Users page for adding accounts, changing roles, resetting passwords and disabling or removing people. AnsiWEB always keeps one administrator.
- Everyone can change their own password, whatever their role.
- A Help page with copyable commands for deploying on Ubuntu Server and under WSL, preparing a PC, backups and common problems.


### Changed

- Buttons and forms people cannot use are hidden rather than failing when pressed, and Settings is read-only for non-administrators.
- A role change or a disabled account takes effect on the next click, even for a session that is already open.
- The command line gained add-user and list-users, and set-password now creates the first administrator or resets anyone's password.


## 1.3.0 - 2026-09-12

Windows updates, activation, clock settings and an idle timeout for the web session. Everything here is optional and off until you turn it on.


### New

- Install Windows updates by category, with exclusions by KB number or title, a per-PC timeout, optional reboots and a weekly schedule.
- Activate Windows with a product key you store once, or against a KMS host on your network. The key is encrypted and stays out of the plan and logs.
- Push a time zone and your own time servers to the PCs and force a clock resync. Clocks more than two minutes off the server are flagged.
- Sign the web session out after a period of inactivity, set in Settings between 5 minutes and 24 hours.
- Remove an app straight from the Apps & cache page.


### Changed

- Removing an app now deletes its cached installers immediately and reports how much disk space that freed, instead of waiting for the next update check.
- Reports, PC pages and the CSV export show activation state and the reported time zone.


### Fixed

- Watching a live job log no longer keeps a web session signed in, so an unattended job page times out as it should.


## 1.2.0 - 2026-09-12

Drivers, scripts and registry files, per-PC reporting, an HTTPS dashboard, and backup and restore.


### New

- Upload device drivers as a .zip of .inf files; PCs unpack them and add them to the Windows driver store with pnputil.
- Upload PowerShell or command scripts and run them on chosen PCs, with arguments, a timeout, extra success exit codes and captured output.
- Upload .reg files to be merged on chosen PCs.
- Choose whether each item runs once per PC, again when its file changes, or at every deployment; a single item can be applied with Run now.
- Reports page with per-PC app versions, item results, OS, model, serial and pending reboots, plus a CSV export and downloadable job logs.
- Rename a PC, optionally renaming Windows to match at the next deployment.
- Backup and restore of the configuration, secrets and uploaded files.
- Upload an offline installer from the Apps page for software that is not in any catalogue.


### Changed

- The dashboard is served over HTTPS with a certificate the installer generates; the installer cache stays on HTTP for the PCs.
- The account AnsiWEB uses on the PCs is configurable and now defaults to Admin.
- Job logs are kept for a configurable number of days.


### Fixed

- The installer no longer stalls when run without a terminal, works where sudo is absent, and starts nginx on hosts without IPv6.


## 1.1.0 - 2026-09-11

A full installation guide, and Microsoft Office deployment removed.


### New

- Step-by-step installation guide for Ubuntu Server and WSL, covering fixed IPs, port forwarding, upgrades, backup, uninstall and troubleshooting.


### Removed

- Microsoft Office deployment, along with the helper-PC mechanism it needed. PCs that already have Office keep it; they simply stop being managed.


## 1.0.0 - 2026-09-11

First release: a local installer cache and a web interface for deploying a standard set of applications to Windows PCs.


### New

- Installers downloaded once to the server, SHA256-verified and served to the PCs, so PCs need no internet access.
- Scheduled update checks against the winget catalogue, replacing outdated installers and upgrading PCs at the next deployment.
- Apps can be pinned to a fixed version and installed only where missing.
- Version-aware deployment that reads each PC's installed apps and only installs what is missing or outdated.
- Web interface with a dashboard, apps and cache, PCs with CSV import, live job logs and settings.
- One-time PC preparation script, Ansible Vault-encrypted secrets, and a login with CSRF protection.
