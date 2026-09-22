# Changelog

All notable changes to AnsiWEB. This file is generated from
`ansiweb/defaults/release_notes.yml`, which the Release notes page in the web
interface also renders - edit that file, then run `python3 tools/make_changelog.py`.


## 2.12.3 - 2026-09-22

Fixes the missing tickbox for installation media.


### Fixed

- 'This upload is a .zip of extracted installation media' and its command box were written inside another field's label, which is invalid and left them with nothing to tick. They now sit in their own section under Installing and detecting, on both the add and edit forms.


## 2.12.2 - 2026-09-22

The undo script no longer removes the management account.


### Changed

- Undo-AnsibleHost leaves the management account in place. On many PCs it is the only administrator, so removing it could lock you out of the machine the script was meant to tidy up. The script now lists it among the things it deliberately leaves behind, to be deleted by hand once another administrator is known to work.


## 2.12.1 - 2026-09-22

A job log now distinguishes a PC that was not answering from work that failed.


### Fixed

- A PC that could not be reached was listed among the failures, so a health check that succeeded on three PCs and never reached a fourth read as a failed job. Unreachable PCs are now reported on their own, with what to check, and the work itself is judged on the PCs that answered.
- A health check, inventory or connection test is no longer told 'nothing was applied' and pointed at the app cache. Applying nothing is what those jobs do.
- A job that only reads a PC no longer creates AnsiWEB's folders on it. Those are made when there is something to download, not on every visit.


## 2.12.0 - 2026-09-21

An undo for the preparation script, a time limit on steps that hang, and several interface fixes.


### New

- An Undo script beside the preparation ones. It removes the WinRM listeners, firewall rules, check-in tasks, the certificate AnsiWEB made and the management account, and says plainly what it leaves alone - software already deployed, the computer name, the time zone and any printers.
- A time limit for a single step, thirty minutes by default. An installer waiting on a dialogue nobody will answer no longer holds the whole deployment: that step is abandoned, its part is recorded as failed, and the rest carries on. Windows updates are exempt, being slow by nature.


### Fixed

- Coming back to Deploy after a preview lost the parts that were ticked. Preview now submits the same form, and the way back carries the choices.
- The Save button on a PC's page sat above a long page and was easy to miss. It stays in view at the foot of the form, says 'Save changes', and notes that nothing below it needs saving.
- Every table on every page scrolls sideways within its own area rather than pushing the page out of shape on a narrow window.


## 2.11.0 - 2026-09-21

A prepared PC can add itself to the PCs page.


### New

- When a PC that nobody has added reports in, AnsiWEB can hold it on the PCs page for someone to accept into a site, add it straight away to a chosen site, or ignore it. Holding it is the default.
- A PC accepted from that list keeps the address it reported, so it is reachable immediately, and carries a note saying it registered itself.
- Adding one straight away is recorded in the audit trail, which matters because a PC in the list receives that site's deployments without anyone having looked at it.


## 2.10.1 - 2026-09-21

Tidies the winget search results.


### Fixed

- The guidance about how winget organises packages sat inside the search box's own label, which stretched the row and left the Search button adrift. It now sits under the form, and the box has a Clear beside it.
- The results table scrolls sideways instead of running off the page, and the Add column is no longer held on a single line, so a long package ID no longer pushes the button out of view.
- The number of matching packages is shown above the results.


## 2.10.0 - 2026-09-21

Software that comes as an ISO can be deployed, the inventory can be read per PC, and the installer says what it supports.


### New

- An app can be a .zip of extracted installation media plus the command that installs it, which is how anything shipped as an ISO has to work - Office 2019 among them. The PC fetches the zip, unpacks it, runs the command in that folder and clears the files away afterwards.
- The inventory can be grouped by PC: a collapsible section per machine listing what is installed on it, alongside the existing view of who has a given program. Searching narrows both.


### Changed

- install.sh works out which distribution it is on and stops on anything that is not Debian or Ubuntu, naming the packages to install by hand instead of running apt where it does not belong. AnsiWEB itself is ordinary Python, Ansible and nginx and runs anywhere those do; the manual steps are in the installation guide.


## 2.9.0 - 2026-09-21

A health check that changes nothing, a queue so jobs wait instead of being refused, and activation errors in plain words.


### New

- PC Health: a job that reads each PC and changes nothing - disk space, whether it is waiting for a reboot, how long since it restarted, and Windows Defender's signature age. The page flags a disk under a tenth free, a pending reboot, a PC up more than a month, signatures over a week old, and real-time protection switched off.
- A job queue. A second job now waits behind the one running rather than being turned away, and starts by itself when the first finishes. A job that has not started can be cancelled; a running one cannot be stopped this way. Twenty-five may wait at once.


### Changed

- Activation failures are reported in plain words rather than a bare HRESULT - a key for the wrong edition, no KMS host reachable, a MAK whose activations are used up, or the PC unable to reach the activation service.


## 2.8.0 - 2026-09-21

One part failing no longer costs the rest, a deployment can be previewed, and a job can be run again on just the PCs it failed on.


### New

- Preview, from the Deploy page: what each PC would have applied, worked out on this server without touching any PC. It says how recently each PC reported, so it is clear where the picture is stale.
- A job that failed on some PCs offers 'Run again on just those' - the same job, the same parts, only the PCs that failed or were unreachable.


### Fixed

- A failure in one part of a deployment stopped everything after it: a PC with no product key got no applications, drivers or printers either. Each part is now attempted on its own; whatever fails is recorded, the rest still runs, and the job ends by naming the parts that did not apply and why.
- An activation failure said nothing useful, because hiding the product key hid the reason with it. The reason is now reported and the key still never reaches the log.


## 2.7.2 - 2026-09-21

Fixes applications being detected but never installed.


### Fixed

- The list of applications could reach the PC folded into a single entry, with every app's details collapsed into arrays. Detection then produced one result covering everything, with all the names run together, nothing matched the list of what to install, and the job finished having installed nothing. The script now takes a folded list apart again.
- If it still cannot make sense of what arrived, it stops and says so rather than quietly installing nothing, and the play checks it received one result per application and a plain list of ids before installing anything.


## 2.7.1 - 2026-09-21

Fixes applications never installing when a deployment covers only part of the work.


### Fixed

- Tags on an include cover the include itself, not the tasks it pulls in, so three of them - checking installed app versions, re-checking for the report, and renaming the PC - had their tasks dropped from any run limited to particular parts. A deployment therefore found no installed versions, concluded nothing was due, and finished having installed nothing. Every include now passes its tags inward, and a test refuses any that does not.


## 2.7.0 - 2026-09-20

PCs on DHCP can report their own address, a security policy, and fixes from an audit of the new code.


### New

- A PC's address is now optional. Each PC reports where it is when it starts and every hour, so DHCP can move it about; AnsiWEB uses the address it last reported. With nothing reported it falls back to what you typed, and then to the PC's name.
- The prep script registers that check-in as a scheduled task and reports in straight away. The PC proves itself with a token that only permits reporting an address.
- SECURITY.md: how to report a vulnerability privately, what is in scope, and the design decisions that are deliberate rather than bugs.


### Fixed

- Found in the audit of the check-in endpoint: the X-Forwarded-For header is set by whoever sends the request, so trusting it would have let anyone holding the token point a PC's address anywhere - sending that PC's deployment, and the management credentials, to an address of their choosing. It is now only trusted from our own nginx on this machine.
- The check-in token was being written into the audit trail. It is treated as a secret there now, like passwords and product keys.
- Downloads are restricted to http and https: a file:// URL in an app entry would have made the server read its own filesystem.


## 2.6.0 - 2026-09-20

A failed app deployment now says what went wrong.


### New

- Before downloading anything, a PC checks it can reach this server's cache on port 80. If it cannot, the job says so plainly and lists what to check, instead of every app failing in turn with a download error.
- Common installer exit codes are explained in the job log - already part-installed, another installation in progress, a truncated download, policy blocking it - along with where to put an extra success code or different silent switches.
- A download that does not match its checksum now points at re-running the update check rather than leaving a bare mismatch.


## 2.5.0 - 2026-09-20

PCs can be moved between sites, editing one is easier to find, and there is a dark mode.


### New

- Move a PC to another site from its row, or tick several and move them together. The message says what that changes: what a PC is given is decided by its site.
- An Edit button on each PC row. The page behind it was always there - behind the PC's name - but nothing said so.
- A light and dark switch at the foot of the sidebar. It follows the system setting until you choose, is remembered in that browser, and is applied before the page is drawn so a dark page never flashes white.


### Changed

- Panels, code, inputs and the coloured notices all take their colours from variables now, so both themes stay consistent.


## 2.4.0 - 2026-09-20

Tooltips that are no longer clipped, printing defaults per printer, and sites can be renamed.


### New

- Printing defaults on each printer: two-sided, colour, collate and paper size, applied to every PC the printer goes to. The printer itself decides what it supports - anything it refuses is left alone and noted in the job log.
- AnsiWEB reads a staged driver package and lists the features it mentions - two-sided, colour, stapling, hole punch, collate, extra trays - beside those settings. It is a hint drawn from the package's own files, not a promise about the model in front of you.
- Sites can be renamed. The PCs at that site follow it, as does anything aimed at it - apps, drivers, scripts, registry files, printers, shares, uninstalls, the time and activation settings, and any user limited to that site - so nothing quietly stops being deployed.


### Fixed

- Tooltips were drawn inside the element they belonged to, so a table that scrolls sideways or a panel that clips its contents cut them off. They are now one floating box attached to the page, above everything, which flips below the element when there is no room above and stays on screen at the edges. Hover, keyboard focus and a tap all show it.


## 2.3.0 - 2026-09-20

A deployment that installs nothing now says so instead of reporting success.


### New

- The dashboard warns when apps are switched on but have no installer cached, with a link to run an update check, so it is visible before a deployment rather than after.


### Fixed

- An app with no installer in the cache is left out of the plan, so a deployment had nothing to do and finished as a success with an empty report. The job now names the PCs where nothing was applied and why - usually apps that are configured but not yet downloaded - and is recorded as a warning rather than a success.
- The PC itself is told too: when a deployment finds no applications ready for it, the log lists the ones waiting to be cached, or says that none are enabled or that none are targeted at that PC.


## 2.2.0 - 2026-09-20

Pick a printer model out of a driver package, clearer controls on the Inventory page, and shared folder permissions can be edited.


### New

- A driver package usually covers a dozen printer families. AnsiWEB now reads the .inf files inside it and lists the models, so a printer can be pointed at one by picking it from a list instead of typing the exact name. Only the .inf offering that model is installed on the PCs, rather than the whole archive.
- The models in an uploaded driver package are listed on its own page too.
- Shared folder permissions can be edited: read, change and full access are changed on the share's own page instead of deleting and re-adding it.


### Fixed

- The Remove button on each row of the Inventory page sat inside a collapsed section, so the page looked as though it had none. It is a plain button now, and the standing entries say 'Remove entry'.
- Wide tables on that page scroll rather than pushing their last column off the side.


## 2.1.0 - 2026-09-19

Driver packages that hold more than drivers now install properly, and an option that could never have worked is gone.


### Fixed

- A vendor package usually holds several .inf files, and some are not signed drivers at all - installer stubs and model lists the catalogue does not cover. Windows refuses those, and AnsiWEB was treating the first refusal as the whole package failing, so packages whose drivers had in fact installed were reported as failed. Each .inf is now handled on its own: the drivers Windows accepts are installed, anything refused is skipped and named in the report, and the job only fails if nothing installed at all.
- Files that appear twice in a package - common where a vendor ships an extracted copy beside the original - are imported once rather than twice.
- Vendor packages often hold several .inf files, and not all are meant to be installed - Ricoh's oemsetup.inf, for instance, is not covered by its own catalogue. AnsiWEB stopped at the first one Windows refused and reported the whole package as failed, discarding the model drivers that had just installed successfully. Each .inf is now tried on its own; the run succeeds if the package produced a usable driver, and the ones Windows would not accept are listed in the report.


### Removed

- The 'install even if Windows rejects the signature' option, which could not work: it used DISM /ForceUnsigned, which applies only to an offline image and fails on a running PC. In its place each driver and printer has 'Stop if Windows rejects any file in the package', off by default, for anyone who would rather a partial package were treated as a failure.
- 'Install even if Windows rejects the signature'. It used DISM /ForceUnsigned, which only works on an offline image and failed with 'This command can only be used with an offline image' on a running PC. Windows 10 and 11 enforce driver signing and AnsiWEB cannot turn that off, so the option is gone rather than left promising something it cannot do.


## 2.0.0 - 2026-09-19

Windows updates are no longer part of AnsiWEB, and a printer's driver signature check can be switched without re-making the printer.


### New

- Allow unsigned driver, on each printer: switch the driver signature check off - or back on - for a printer that already exists, rather than having to delete it and add it again. A printer with the check off is marked on the page.


### Removed

- Windows updates: both the per-PC settings that told Windows to fetch its own updates, and the page that cached particular updates on the server. Nothing on the PCs changes - Windows keeps whatever update arrangement it already has, and updates already installed stay installed. WSUS or Windows Update remain the way to do this; a .reg file on the drivers, scripts and registry page can point PCs at a WSUS server.


## 1.19.0 - 2026-09-19

A driver Windows will not vouch for can be installed anyway, and the signature error now says what is actually wrong.


### New

- 'Install even if Windows rejects the signature', on an uploaded driver and on a printer's staged driver. AnsiWEB retries with DISM's signature check bypassed and records that it did so. Off unless asked for.


### Changed

- The signature error now reads the .inf, names the catalogue it asks for, and says whether that file is beside it, elsewhere in the package, or missing. A package whose .inf sits above its catalogue - common with printer drivers that ship x86 and x64 subfolders - is now obvious rather than a guess.


### Fixed

- A successful driver install was reported as a failure: the loop used 'continue' inside a switch, which leaves the switch rather than the loop, so pnputil's success fell through to the error path.


## 1.18.1 - 2026-09-18

Clearer job summaries when something fails, and a fix for staging a printer driver.


### Changed

- The Help page and the installation guide explain the driver error 'the hash for the file is not present in the specified catalog file': the .zip is incomplete or something in it was altered, so the vendor's folder needs re-zipping exactly as extracted.


### Fixed

- A job summary counted AnsiWEB's own error handling as failed tasks, so one failure on a PC read as three. Only the task that actually failed is listed now.
- Staging a driver with a printer failed with an internal error, because the warning about a missing .cat file looked for a helper that was not there.
- A job that failed on a PC is recorded as failed rather than a warning; an unreachable PC stays a warning, which is a different thing.


## 1.18.0 - 2026-09-17

Particular Windows updates can be cached on the server and installed from it, with links to what Microsoft and others say about them.


### New

- A Windows Updates page. Cache a specific update by KB number - upload the .msu from the Microsoft Update Catalog - and install it on the PCs you choose, from this server. For PCs with no route to Microsoft, or when one particular fix is wanted on one particular set of PCs.
- Each cached update links out to Microsoft's article for that KB, its Update Catalog entry, the Windows release health page for known issues, and a search for what others are reporting. AnsiWEB links rather than repeats: there is no reliable source of reviews it could quote.
- Cached updates are a tickbox on the deploy page and a job of their own, and the page shows which PCs have already reported each KB.
- A best-effort Look up button asks the catalogue what it holds for a KB. That page has no supported interface and the lookup sometimes cannot read it, which it says plainly; uploading always works.


### Changed

- The page states what this is not: AnsiWEB caches the updates you name, not the whole catalogue. WSUS remains the answer for wholesale mirroring, and PCs can still be pointed at one under Settings.


## 1.17.0 - 2026-09-17

Apps can be found in the winget catalogue from inside AnsiWEB.


### New

- Find in winget, on the Apps & Cache page: search the catalogue by publisher, product or package ID and add a result to the standard set with one press. Anything already in the set is marked rather than offered again.
- An added app arrives with a suggested detection pattern to check, and its installer is downloaded by the next update check.


### Changed

- Catalogue listings are remembered for a day, so searching repeatedly does not use up the GitHub API allowance. A rate limit or outage is reported on the page rather than silently returning nothing.


## 1.16.0 - 2026-09-17

Tooltips, a forced password change for new accounts, corrected task tags, and the hidden bits are no longer hidden.


### New

- Tooltips - a small ? next to a field or column heading explains it, on hover, on keyboard focus, or on a tap.
- A new account, or one whose password an administrator has reset, has to choose its own password at the next sign-in. Nothing else in AnsiWEB is reachable until it does. An account created at the command line is not affected, since that person typed the password themselves.
- A few things put in for fun, now written down: /coffee, searching the PCs page for xyzzy, the Konami code, five quick clicks on the version number, a line in the job summary at exactly 42 PCs, and X-Clacks-Overhead on every response. None of them affect a deployment.


### Changed

- The test suite now proves a restored backup still deploys - the configuration, uploaded files, inventory and encrypted secrets all come back, the plan is rebuilt, and Ansible still accepts the result.


### Fixed

- Four tasks in the role were untagged, so any job covering only part of a deployment skipped them: creating the working folders on the PC, the reboot a script or update asks for, the list of PCs still needing one, and the app re-check. A printers-only job could therefore leave a PC waiting for a reboot nobody was told about.
- Reports were only written by jobs covering apps or inventory, so a printers-only or shares-only deployment recorded nothing. Every job now writes a report, and keeps the parts it did not gather rather than blanking them.


## 1.15.0 - 2026-09-17

The Help page gains a contents list, and says plainly where AnsiWEB should run.


### New

- A table of contents at the top of the Help page, linking to each section.
- A 'Where to run AnsiWEB' section recommending a Hyper-V virtual machine or a dedicated server, with the reasons WSL is not suitable: it does not start with Windows, stops when the signed-in user logs out, changes its address on every restart, and has systemd off by default.
- Commands for building the Hyper-V VM, including the setting that brings it back by itself after a restart or a power cut, an external switch so the PCs reach it directly, and a checkpoint before upgrades.


### Changed

- The WSL section now opens with a warning that it is for trying AnsiWEB out rather than running it, and the same guidance is in the README and the installation guide.


## 1.14.0 - 2026-09-17

PCs can be found by the person using them, and some tidying of the names in the sidebar.


### New

- Each PC can record who it is assigned to. The PCs page has a search box that matches a person, a PC name, an address or a site, so you no longer have to remember which machine is whose.
- The person is shown wherever a PC is listed or picked - the PC list, the PC's own page, the deploy and printer pickers, the Reports page and the CSV export - and a CSV import can carry it as a fifth column.


### Changed

- Sidebar entries now capitalise consistently: Apps & Cache, Shared Folders, Drivers, Scripts & Registry, Inventory & Uninstall, Reports & Audit.
- The separate What's new entry is gone; the release notes are still reached from the version number at the foot of the sidebar.
- Web passwords need eight characters rather than ten.


## 1.13.3 - 2026-09-17

The installer copes with Ubuntu's automatic updates holding the package manager.


### Changed

- The installer only calls apt at all when a package is actually missing, so upgrading an existing installation no longer touches the package manager.


### Fixed

- install.sh stopped with 'Could not get lock /var/lib/apt/lists/lock' when something else was using apt, which on Ubuntu is usually unattended-upgrades running in the background. It now waits up to five minutes, says what it is waiting for, and if it is still busy explains that rather than failing with apt's message.


## 1.13.2 - 2026-09-16

Job logs now say what each task did, and a skipped task can no longer make a later one fail.


### New

- Every deployment ends with a 'What each task did' list: each task marked done, changed, skipped or FAILED, with the count per PC. Failures are listed again at the end with the PCs they happened on.


### Fixed

- A task that was skipped - by a tag, a condition, or because the play stopped early - could leave a later task referring to a result that was never produced, failing with 'aw_detect is undefined' or similar. Those results now start out empty.


## 1.13.1 - 2026-09-16

Fixes jobs that apply only part of a deployment.


### Fixed

- Any job limited to one part - shared folders, printers, time, activation and so on, including the new choose-what-to-deploy page - failed with an undefined variable. The tasks that work out what a PC should have were skipped along with everything else outside the chosen part. They now run on every job, whatever it covers.


## 1.13.0 - 2026-09-16

Deploy now asks what to deploy, and a scoped user no longer sees other PCs' names in the job history.


### New

- Deploy opens a page where you choose what to apply - applications, drivers, scripts, registry files, shared folders, printers, time, activation, computer name, Windows updates, inventory - and where to apply it, including individual PCs. Everything is ticked by default, which is the same as before, and parts with nothing set up are marked.


### Changed

- The "apply everything on this page" form is written once and shared by the printers and shared-folders pages.


### Fixed

- A user limited to certain sites or groups could see PC names outside their scope in the recent-jobs list, because a job target names them. Those names are now hidden from the dashboard, the Jobs page and the Reports page.


## 1.12.3 - 2026-09-16

The dashboard notices can be dismissed.


### New

- A small x on the upgrade notice and the not-reporting notice hides it. Dismissing is per person, so it does not hide anything from your colleagues.
- The not-reporting notice is dismissed against the PCs it is about, so it comes back if a different PC goes quiet instead of staying hidden once and for all.


## 1.12.2 - 2026-09-16

Makes the connection setting hard to miss, and PCs removable from their own row.


### New

- A Remove button beside Test and Deploy on each PC, which takes the PC out of AnsiWEB and deletes its report. Nothing on the PC itself is changed.


### Changed

- 'How AnsiWEB connects' now sits with the prep scripts on the PCs page, with a line saying which port it will use and which script to run. It was previously tucked inside the management-account panel, where it was easy to miss - and a mismatch shows up only as a connection timeout.
- A failed connection test now says what to check, starting with the likeliest cause: a PC prepared with the other script, so it is listening on the other port.


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
