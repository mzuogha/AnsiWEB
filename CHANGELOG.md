# Changelog

All notable changes to AnsiWEB. This file is generated from
`ansiweb/defaults/release_notes.yml`, which the Release notes page in the web
interface also renders - edit that file, then run `python3 tools/make_changelog.py`.


## 1.12.1 - 2026-09-15

Fixes the password prompt in the simple .cmd prep script.


### Fixed

- The account commands had their output sent to the log, which also hid the password prompt that 'net user *' prints - so there was nothing to type into. Those commands now write to the screen.
- The script no longer depends on wmic, which recent Windows 11 builds do not ship. It falls back for both the "password never expires" setting and finding the Administrators group, and carries on if neither works.


## 1.12.0 - 2026-09-15

A second way to prepare a PC, for when the PowerShell script will not run.


### New

- A simple .cmd prep script that uses only built-in Windows commands: no certificate, no execution policy, nothing to unblock. Right-click and Run as administrator; the window stays open so you can read what it did, and every run is logged.
- A connection setting on the PCs page: HTTPS on 5986 for PCs prepared with the PowerShell script, or NTLM on 5985 for PCs prepared with the .cmd. NTLM encrypts each message, and both ends refuse unencrypted traffic, so there is no certificate to manage.


### Changed

- The PowerShell script now waits for a keypress before closing, so a message is not lost when it is started by double-clicking, and its early checks report the problem the same way instead of throwing.


## 1.11.5 - 2026-09-15

The PC prep script is rewritten. The checks added in 1.9.0 reported failure on PCs that were in fact prepared correctly.


### Changed

- The script is rewritten around named steps, so a failure says which step and why, rather than only quoting an error.
- It writes a log to C:\ProgramData\AnsiWEB\prepare-log.txt for diagnosis, and runs with strict mode so a mistake like the one above stops the script instead of passing silently.
- The certificate now covers the computer name and its DNS name, and the Administrators group is addressed by SID throughout.


### Fixed

- The final check used a variable that was never set, so it always concluded the account was not an administrator and exited with an error.
- It also opened a WinRM session to 'localhost' over HTTPS, which fails the certificate name check on any correctly prepared PC, because the certificate is issued for the computer name. The check is now a local port test, which is what it was trying to establish.


## 1.11.4 - 2026-09-15

Fixes an installer failure introduced in 1.11.1.


### Changed

- install.sh keeps the previous nginx site file and says where it is if the new one is rejected, instead of leaving you to work it out.


### Fixed

- The nginx site set server_tokens at file level, which clashes with the same setting in nginx.conf on a stock Ubuntu install and made install.sh stop with 'directive is duplicate'. It is set inside the server blocks now, where it cannot clash.


## 1.11.3 - 2026-09-14

More tidy-up. No change in behaviour.


### Changed

- The list of sites and groups an item applies to, and the recent-jobs table, are each written once and shared, instead of five and two copies.
- Looking up a user before changing them, and reading an uploaded item's fields from a form, are each in one place now.


## 1.11.2 - 2026-09-14

Tidy-up, and two things that were quietly leaving files behind.


### Changed

- The "choose individual PCs" list is one shared piece of markup rather than four copies.


### Fixed

- The Inventory page was rendering its uninstall section twice, because a template edit caught the page title as well.
- Deleting a printer now deletes the driver package staged with it, and an update check tidies away uploads whose entry has been removed. Both were left on disk before.


## 1.11.1 - 2026-09-14

Hardening after a security review of the code and the deployment.


### New

- Sign-in throttling: five failed attempts for one account from one address lock it for five minutes, so a password cannot be guessed at machine speed. A correct sign-in clears the count.
- A Security notes section in the README covering what is exposed and what each role can reach.


### Changed

- Restoring a backup now also applies Python's own archive filter, on top of AnsiWEB's checks, so a crafted archive cannot write outside the data folder.
- The deployment plan, cache manifest and reports are no longer world-readable on the server; only the service account can read them.
- The service runs with more of systemd's protections enabled, and nginx no longer advertises its version.
- The nginx site explains that everything under /software/ is served without a sign-in, and shows how to limit it to your own PCs.


## 1.11.0 - 2026-09-12

Uninstalling is now part of the Inventory page, and drivers can be tied to particular PCs or to a printer.


### New

- Link a driver to individual PCs, not just to all PCs, a site or a group.
- Link a printer to a driver already uploaded on the drivers page, instead of attaching a second copy to the printer. A linked driver is used in preference, and the plan records which one was chosen.


### Changed

- Uninstalling lives on one page: the Inventory page now carries both the per-PC uninstall on each row and the standing uninstall entries. The old /uninstalls address redirects there.
- An operator browsing the inventory can therefore remove software from a PC, or add a standing rule, without leaving the page.


## 1.10.0 - 2026-09-12

Printers are network-only, can carry their own driver, and can be pushed to the exact PCs you pick. The sign-in page shows the version.


### New

- Stage a printer driver with the printer: attach the vendor's .zip of .inf files, and AnsiWEB installs it on each PC before creating the printer, so nothing has to be prepared by hand.
- Push a printer to individual PCs, chosen from a list, as well as to a site or group. A printer can also be set to target one PC permanently.
- The version number is shown at the bottom left of the sign-in page.


### Removed

- Shared print-server queues: printers are network printers only, which is the kind AnsiWEB can set up the same way on every PC. An existing shared entry is kept but switched off, with a note, rather than deleted.


## 1.9.0 - 2026-09-12

Shared folders, a switch for Windows file sharing, a hardened PC prep script, and the release notes in the sidebar for everyone.


### New

- A Shared folders page: create a folder on the PCs you choose and share it, with the accounts you name given read, change or full access. An entry can instead take a share down, leaving the folder alone.
- A switch for Windows file and printer sharing, which is off by default and needed before any share is reachable, with network discovery as an option.


### Changed

- The PC prep script now checks its own work: it confirms the account, the WinRM service, the HTTPS listener and the firewall rule, and reports exactly what is wrong instead of finishing quietly.
- The prep script addresses the Administrators group by SID and removes old listeners by path, both of which are more reliable on real Windows, and it refuses to run on PowerShell older than 5.1.
- The release notes have their own sidebar entry, so everyone can find them whatever their role.


## 1.8.0 - 2026-09-12

Printers, uninstalling straight from the inventory, and the scripts plus registry job is back.


### New

- A Printers page. Add a network printer with its own IP address, or a queue shared from a print server, and push it to the PCs you choose. Set one as the default, or use an entry to take a printer off the PCs. Setting a printer up again does nothing if it is already correct.
- Uninstall straight from the Inventory page: pick a PC on a program's row, press Preview to see what would happen, then type REMOVE to do it. No standing entry is created, and it is available to Helpdesk as an operational action.


### Changed

- The 'Run scripts and merge registry files' job is back, as a Scripts + registry button on the drivers, scripts and registry page.
- The test suite no longer calls Ansible for real, so it runs in seconds and jobs no longer overlap between checks.


## 1.7.1 - 2026-09-12

Internal tidy-up. Nothing changes in how AnsiWEB behaves.


### Changed

- Dead constants, unused imports and permission entries for routes that no longer exist were removed, and the three copies of the timestamp helper became one.
- Reports written before version 1.2 are no longer read; every PC has reported in the current format since.


### Removed

- The 'Run scripts and merge registry files' job, which the per-kind Apply buttons and Apply all replaced. Scripts and registry files can still be applied separately or together from that page.


## 1.7.0 - 2026-09-12

Your own logo on the sign-in page, separate apply buttons for each kind of upload, and the audit log folded into the Reports page.


### New

- Upload your organisation's logo and a name for the sign-in page, so people can see which server they are signing in to. The logo is served without a sign-in, because the sign-in page needs it, and is included in backups.


### Changed

- The Drivers, scripts & registry page now has an Apply button per kind - drivers, scripts or registry files - as well as Apply all.
- The audit log is a section of the Reports page rather than a page of its own; /audit redirects there. It is still visible only to administrators.


## 1.6.2 - 2026-09-12

Drivers now share the same page as scripts and registry files, so everything you upload for the PCs is in one place.


### Changed

- One page, Drivers, scripts & registry, lists all three kinds with a type column. Upload any of them there; AnsiWEB works out which it is from the file.
- Apply now on that page covers drivers, scripts and registry files in a single job.
- The old /drivers, /scripts and /registry addresses all redirect to it.
- Storage is unchanged, so everything already uploaded and everything already applied on the PCs is untouched.


## 1.6.1 - 2026-09-12

Scripts and registry files now share one page, since both are simply things to run on a PC.


### Changed

- Scripts & registry is a single page. Upload either kind there and AnsiWEB works out which it is from the file; each row shows its type.
- Apply now on that page runs scripts and merges registry files in one job.
- The old /registry address redirects to the merged page, so saved links still work.
- Nothing changed on the PCs, and existing scripts and registry files are untouched.


## 1.6.0 - 2026-09-12

A searchable inventory of everything installed on the PCs, and roles that can be limited to particular sites or groups.


### New

- An Inventory page listing every program Windows reports on each PC, not just the ones AnsiWEB manages. Search by program or publisher, see which PCs and versions have it, and export the lot as CSV.
- An inventory-only job that collects the list without deploying anything.
- Scopes - a non-administrator can be limited to certain sites or groups. They see only those PCs, and any job they start is narrowed to them, so it cannot reach a PC outside the scope even if the form is tampered with.


### Changed

- Inventory collection can be turned off in Settings, and is capped per PC so an unusual machine cannot bloat its report.
- Promoting someone to administrator clears any scope they had.


## 1.5.0 - 2026-09-12

Uninstalling apps from the PCs, an audit log, and PCs that have stopped reporting are now flagged instead of quietly disappearing.


### New

- Uninstall apps from the PCs. Entries are matched against the program name in Windows, then removed with the MSI product code or the vendor's quiet uninstall command. Nothing is removed until the uninstall job is run with a typed confirmation, and a preview reports what would go first.
- Removing an app from the standard set can queue it for removal from the PCs at the same time.
- An audit log of every change made through the web interface, with the account that made it, including sign-ins, refused attempts and backup downloads. Filterable, exportable as CSV, and never records secrets.
- PCs that have not reported for a configurable number of days, or have never reported, are counted on the dashboard, flagged on the Reports page and included in the CSV export.


### Changed

- A program with no silent uninstaller is reported and left alone rather than being left on a prompt nobody can see.
- Audit entries are kept for the same period as job logs.


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
