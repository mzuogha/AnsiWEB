# Security policy

AnsiWEB deploys software to Windows PCs with administrative rights, so a
weakness here is a weakness on every PC it manages. Reports are welcome.

## Reporting a vulnerability

Please **do not open a public issue** for a security problem.

Use GitHub's private reporting — **Security → Report a vulnerability** on
https://github.com/mzuogha/AnsiWEB — or, if that is unavailable, open an issue
saying only that you have a security report and asking for a contact address.

Please include what you can: the version (shown at the foot of the sidebar), what
an attacker would need to already have, what they gain, and the steps to
reproduce. A proof of concept helps but is not required.

What to expect: an acknowledgement within a week, an assessment of whether it is
a genuine issue and how serious, a fix released as soon as it is ready, and
credit in the release notes unless you would rather not be named.

## Supported versions

The latest release is the supported one. AnsiWEB is a small project with no
long-term support branches; fixes go into the next release.

## What is in scope

- The web interface: authentication, roles and scopes, CSRF, session handling
- The installer cache served to PCs, and the files uploaded into it
- The Ansible role and the PowerShell that runs on PCs
- The prep scripts and the check-in endpoint
- Secret handling: the vault, backups, logs and the audit trail

## What is not a vulnerability

These are documented design decisions, not oversights:

- **The cache on port 80 is unauthenticated.** PCs fetch installers before they
  can authenticate anywhere. Anything under `/software/` is readable by anyone
  who can reach the server, which is why the documentation says never to put a
  secret in an uploaded script, and shows how to restrict it by address.
- **Uploading a script, app or driver runs code as SYSTEM on the targeted PCs.**
  That is the purpose of the Operator role, not an escalation.
- **Every PC shares one management account.** The prep script limits WinRM to
  this server's address, which is the compensating control.
- **WinRM and dashboard certificates are self-signed by default.** Traffic is
  encrypted; identity is not verified. Both can be replaced with your own.
- **A backup file contains the vault key.** Treat it as a secret; that is stated
  where backups are offered.

If you think one of these is worse than described, please do report it — the
reasoning may be wrong.

## Hardening worth doing

Running AnsiWEB sensibly matters as much as the code:

- Restrict `/software/` to your PCs' subnet in the nginx site file
- Replace the self-signed dashboard certificate
- Give people the smallest role that works; Helpdesk and Viewer cannot change
  what is deployed
- Keep backups somewhere safe, and rotate the PC management password
- Keep the server patched, and run it somewhere that starts unattended
