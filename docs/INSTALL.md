# AnsiWEB installation guide

Two supported ways to run AnsiWEB:

| | [Option A: Ubuntu Server](#option-a-ubuntu-server-recommended) | [Option B: WSL on a Windows PC](#option-b-wsl-on-a-windows-pc) |
|---|---|---|
| Best for | production use | trying it out, small sites, no spare hardware |
| Runs when nobody is logged in | yes | only with extra setup |
| Reachable by PCs | directly | needs port forwarding on the Windows host |
| Survives a Windows reboot | n/a | needs a scheduled task |

If AnsiWEB will manage more than a handful of PCs, use Option A. WSL works, but its networking is an extra moving part, and every hop between your PCs and the cache is one more thing that can break at 2 a.m.

---

## Before you start

**Hardware / VM:** 2 vCPU, 4 GB RAM, and about 10 GB of free disk for the installer cache. A small VM on any hypervisor is plenty.

**Network:**

| Direction | Port | Purpose |
|---|---|---|
| AnsiWEB → each PC | TCP 5986 | WinRM over HTTPS (deployments) |
| Each PC → AnsiWEB | TCP 80 | downloading cached installers, drivers, scripts and registry files |
| Admin browser → AnsiWEB | TCP 443 | the web interface (HTTPS) |
| AnsiWEB → internet | TCP 443 | update checks and installer downloads |

The AnsiWEB server needs a **fixed IP address**. PCs need fixed addresses too (DHCP reservations are fine): without a domain and DNS, AnsiWEB reaches PCs by IP.

**On the PCs:** Windows 10 or 11, PowerShell 5.1 (built in), and local administrator rights to run the one-time prep script.

---

## Option A: Ubuntu Server (recommended)

### A1. Install Ubuntu

Install **Ubuntu Server 24.04 LTS** (22.04 also works). During setup, choose "Install OpenSSH server" so you can administer it remotely. No other snaps or packages are needed.

### A2. Give the server a fixed IP

Either reserve its address on your DHCP server (simplest), or set it statically. To set it statically, find the interface name and current address:

```bash
ip -brief address
```

Then edit the netplan file (the file name varies; list `/etc/netplan/` first):

```bash
ls /etc/netplan/
sudo nano /etc/netplan/50-cloud-init.yaml
```

Replace its contents, adjusting the interface name, addresses and gateway:

```yaml
network:
  version: 2
  ethernets:
    ens18:                          # your interface name from "ip -brief address"
      dhcp4: false
      addresses: [192.168.1.10/24]  # the address AnsiWEB will use
      routes:
        - to: default
          via: 192.168.1.1          # your router
      nameservers:
        addresses: [192.168.1.1, 1.1.1.1]
```

Apply it and confirm:

```bash
sudo netplan apply
ip -brief address
ping -c3 1.1.1.1
```

> If you are connected over SSH, a wrong netplan file will cut you off. Use `sudo netplan try` instead of `apply`: it reverts automatically after 120 seconds unless you confirm.

### A3. Update the system and install AnsiWEB

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git
git clone https://github.com/mzuogha/AnsiWEB.git
cd AnsiWEB
sudo ./install.sh
```

The installer takes a few minutes. It:

- installs Python, nginx and build tools
- creates the `ansiweb` service account
- copies the application to `/opt/ansiweb` and builds its Python environment
- installs the Ansible collections
- creates the data folder `/var/lib/ansiweb` (configuration, secrets, cache, logs)
- creates a self-signed HTTPS certificate in `/etc/ssl/ansiweb`
- asks you to choose the web admin password
- adds the `ansiweb` command
- starts the `ansiweb` service and configures nginx

It ends with the address to open, for example:

```
AnsiWEB is running:  https://192.168.1.10/
```

The certificate is self-signed, so your browser warns the first time. That is expected on an internal
server. Compare the fingerprint shown by the installer with the one in the browser, then accept it:

```bash
sudo openssl x509 -in /etc/ssl/ansiweb/server.crt -noout -fingerprint -sha256
```

To use a certificate from your own authority instead, replace `/etc/ssl/ansiweb/server.crt` and
`server.key` and run `sudo systemctl reload nginx`. To regenerate the self-signed one — after changing
the server's IP, for example:

```bash
sudo /opt/ansiweb/deploy/make-cert.sh 192.168.1.10
sudo systemctl reload nginx
```

### A4. Optional: restrict access with a firewall

If you enable `ufw`, allow SSH and HTTP. Restrict the web interface to your admin machine or subnet if you can:

```bash
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp     # installer cache, used by the PCs
sudo ufw allow 443/tcp    # the web interface
sudo ufw enable
sudo ufw status
```

Port 80 only serves `/software/` and redirects everything else to HTTPS. For a tighter setup, keep the cache open to all PCs but limit the admin pages by editing the nginx site (see [Hardening](#hardening)).

### A5. Check it is healthy

```bash
systemctl status ansiweb --no-pager      # should be "active (running)"
systemctl status nginx --no-pager
curl -skI https://localhost/login | head -1  # HTTP/1.1 200 OK
sudo ansiweb plan                          # rebuilds the plan, prints a summary
```

Now skip to [First-time configuration](#first-time-configuration).

---

## Option B: WSL on a Windows PC

AnsiWEB runs inside Ubuntu on WSL2. Two things are different from a real server:

- **WSL2 has its own internal IP address** that other machines on your network cannot reach. You forward port 80 from Windows into WSL.
- **WSL only runs while Windows is logged in**, unless you add a scheduled task.

Use a Windows machine that stays on. Do not use a PC that AnsiWEB itself manages: a reboot triggered by a deployment would take the server down mid-run.

### B1. Install WSL and Ubuntu

In an **Administrator** PowerShell on the Windows host:

```powershell
wsl --install -d Ubuntu-24.04
wsl --update
wsl --version
```

Reboot if asked. Launch **Ubuntu** from the Start menu and create your Linux username and password when prompted.

### B2. Enable systemd inside WSL

AnsiWEB runs as a background service, which needs systemd. In the Ubuntu window:

```bash
printf '[boot]\nsystemd=true\n' | sudo tee -a /etc/wsl.conf
```

Then, in **PowerShell**:

```powershell
wsl --shutdown
```

Reopen Ubuntu and confirm systemd is running:

```bash
systemctl is-system-running          # "running" or "degraded" are both fine
```

If this says `offline` or the command is missing, your WSL version is too old: run `wsl --update` in PowerShell and repeat.

### B3. Install AnsiWEB

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git
git clone https://github.com/mzuogha/AnsiWEB.git
cd AnsiWEB
sudo ./install.sh
```

The installer detects WSL and prints the port-forwarding commands at the end. Note the WSL address it reports (something like `172.24.x.x`).

### B4. Forward ports 80 and 443 from Windows into WSL

Get the current WSL address:

```bash
hostname -I | awk '{print $1}'
```

Then in an **Administrator** PowerShell on the Windows host (replace the address):

```powershell
netsh interface portproxy add v4tov4 listenport=80  listenaddress=0.0.0.0 connectport=80  connectaddress=172.24.58.101
netsh interface portproxy add v4tov4 listenport=443 listenaddress=0.0.0.0 connectport=443 connectaddress=172.24.58.101
New-NetFirewallRule -DisplayName "AnsiWEB" -Direction Inbound -Protocol TCP -LocalPort 80,443 -Action Allow
netsh interface portproxy show v4tov4
```

Port 80 carries the cache the PCs download from; 443 is the dashboard.

Find the **Windows** machine's own LAN address, which is what your PCs will use:

```powershell
ipconfig | findstr /i "IPv4"
```

Check it from another machine on the network: `https://<windows-ip>/` should show the AnsiWEB login page.

> **WSL's address changes** whenever WSL restarts, which breaks the forwarding. Fix it once with the scheduled task in B5, or re-run the `netsh` command after each restart.

### B5. Make it survive reboots

Create `C:\Scripts\ansiweb-wsl.ps1` on the Windows host:

```powershell
# Starts WSL and re-points the port forwarding at its current address.
wsl -d Ubuntu-24.04 -u root -e /bin/true          # boot the distribution
Start-Sleep -Seconds 15
$ip = (wsl -d Ubuntu-24.04 hostname -I).Trim().Split(' ')[0]
foreach ($port in 80, 443) {
    netsh interface portproxy delete v4tov4 listenport=$port listenaddress=0.0.0.0 2>$null
    netsh interface portproxy add    v4tov4 listenport=$port listenaddress=0.0.0.0 `
          connectport=$port connectaddress=$ip
}
Write-Output "$(Get-Date -Format s)  AnsiWEB forwarded to $ip" |
    Out-File -Append C:\Scripts\ansiweb-wsl.log
```

Register it to run at startup, as SYSTEM, without anyone logging in:

```powershell
$action  = New-ScheduledTaskAction -Execute 'powershell.exe' `
           -Argument '-NoProfile -ExecutionPolicy Bypass -File C:\Scripts\ansiweb-wsl.ps1'
$trigger = New-ScheduledTaskTrigger -AtStartup
Register-ScheduledTask -TaskName 'AnsiWEB WSL' -Action $action -Trigger $trigger `
    -User 'SYSTEM' -RunLevel Highest
Start-ScheduledTask -TaskName 'AnsiWEB WSL'
```

Reboot Windows and check `https://<windows-ip>/` still answers.

> **Mirrored networking** (`networkingMode=mirrored` in `.wslconfig`) can remove the need for port forwarding on Windows 11 22H2 and later, but it has a long list of open issues around VPNs, loopback and DNS. The port-proxy approach above works on every WSL2 version, so start there.

### B6. Keep Windows Update from interrupting deployments

Set active hours or a maintenance window on the host, so a restart doesn't land in the middle of a scheduled deployment.

---

## First-time configuration

These steps are the same for both options. Open `https://<server-ip>/`, accept the self-signed certificate, and sign in as `admin`.

### 1. Settings

- **Server IP address:** the address PCs use to reach AnsiWEB. On a server, that's its own IP. **Under WSL, use the Windows host's LAN IP**, not the WSL address.
- Leave forks and batch size at 20 to start with. **Keep job logs for** controls how long job logs and history are kept.
- **Treat a PC as not reporting after** flags PCs that have gone quiet for that many days (14 by default) on the dashboard and Reports page.
- **Sign-in page** takes your own logo and a name, shown to anyone opening the sign-in page.
- **Sign out after inactivity** ends the web session after the minutes you set (60 by default, between 5 minutes and 24 hours). Running jobs carry on regardless; only the browser session ends.

On the **PCs** page, in *Management account on the PCs*:

- **Account name:** the local administrator account AnsiWEB signs in with. The default is `Admin`.
- **Password:** choose it now; you will type the same one on every PC in step 2. Store it in your password manager.

> If any of your PCs already has a local account with that name, the prep script resets that account's password.
> Pick a name of your own (for example `AnsiAdmin`) if that would be a problem.

### 2. Prepare each PC (once per PC)

On the **PCs** page, click **Download PC prep script**. The downloaded script already contains your server IP.

Copy it to each PC (USB stick or a share), then in an **Administrator** PowerShell on that PC:

```powershell
cd C:\Users\<you>\Downloads
powershell -ExecutionPolicy Bypass -File .\Prepare-AnsibleHost.ps1
```

Enter the password from step 1 when prompted. The script:

- creates the local administrator account you configured (`Admin` by default)
- enables WinRM and creates an HTTPS listener with a self-signed certificate
- allows local administrator accounts to work remotely (`LocalAccountTokenFilterPolicy`)
- opens TCP 5986 **only** to your AnsiWEB server, and closes the unencrypted WinRM port 5985

It then checks its own work — the account, the WinRM service, the HTTPS listener, the firewall rule and the port —
and prints the PC name and IP to enter in AnsiWEB. If anything is wrong it names the step that failed and why, and
exits with an error rather than finishing quietly. Every run is logged to
`C:\ProgramData\AnsiWEB\prepare-log.txt`. It is safe to run again.

You can also build this into your imaging process, so new PCs arrive ready.

### 3. Add people, if others will use AnsiWEB (optional)

The account created by the installer is an administrator. To give someone less, go to **Users**:

You can also limit someone to certain sites or groups with a **scope**, so a branch technician only sees and
touches their own PCs.

- **Operator** — manages apps, drivers, scripts, registry files and PCs, and runs jobs. No settings or secrets.
- **Helpdesk** — runs deployments and connection tests, and reads reports.
- **Viewer** — read-only.

Or from the command line:

```bash
sudo ansiweb add-user jane operator
sudo ansiweb list-users
```

Anyone who can manage apps or scripts can cause code to run on the PCs they target, so treat Operator as trusted.
The **Help** page inside AnsiWEB repeats the commands on this page, with your own addresses filled in.

### 4. Add the PCs

On the **PCs** page, add each PC by name and IP, or paste a list:

```
PC-HQ-001,192.168.1.21,HQ
PC-HQ-002,192.168.1.22,HQ,finance
PC-BR1-001,192.168.2.21,Branch1
```

### 5. Test the connections

Click **Test all connections**. Every PC should answer `pong`. Fix any failures now — a PC that can't be reached can't be deployed to.

### 6. Fill the cache

On the **Apps** page, click **Check for updates now**. Watch the live log: each app is downloaded once and verified against its published SHA256 fingerprint. This takes a while on the first run, and it's the only step that needs internet.

Firefox is pinned to 65.0.2 and is downloaded from Mozilla's release archive.

### 7. Deploy

Start with one PC (PCs page → **Deploy**), then a site, then everything:

```
PCs page → Deploy (one PC)
PCs page → Sites → Deploy (one site)
Dashboard → Deploy to all PCs
```

Run it a second time against the same PC. The second run should report no changes; that's your proof the setup is correct.

### 8. Add drivers, scripts and registry files (optional)

They share one page in the sidebar, **Drivers, scripts & registry**: upload the file, pick which PCs it applies to,
and choose when it runs. AnsiWEB tells the kinds apart by the file you upload.

| What to upload | What PCs do |
|---|---|
| A `.zip` containing the driver's `.inf` file, plus its `.cat`/`.sys` files | Unpacks it and adds each `.inf` to the Windows driver store with `pnputil` |
| `.ps1`, `.cmd` or `.bat` | Runs it as SYSTEM with your arguments; exit code and output are recorded |
| `.reg` exported from Registry Editor | Merges it with `reg import` |

To build a driver package, take the vendor's *driver* download (not its setup program), and zip the folder that
contains the `.inf` files:

```powershell
Compress-Archive -Path C:\Drivers\IntelNIC\* -DestinationPath C:\Drivers\intel-nic.zip
```

"When it runs" is one of **once per PC**, **again whenever the file changes**, or **every deployment**. Each PC keeps
a marker file per item under `C:\ProgramData\AnsiWEB\state`, which is what makes "once" and "on change" work
across reboots and repeat runs.

Test on one PC first with **Run now** on the item, then check the **Reports** page for the outcome before widening
the target.

> Scripts run with full system rights on every PC they target. Read anything you did not write yourself, and test it
> on one PC before pointing it at a site.

### 9. Install Windows updates (optional)

**Settings → Windows updates**:

1. Tick **Install Windows updates during deployments**.
2. Choose the categories you want — security, critical and rollups are a sensible baseline.
3. Leave **Where updates come from** on the default unless you want to bypass or insist on a WSUS server.
4. Decide whether Windows may reboot PCs. With it off, PCs needing a reboot are listed at the end of the job.
5. Set a schedule, for example Saturdays at 22:00, then try it on one PC first with **Install updates now**.

Updates can take hours per PC, so they run last in a deployment and have their own timeout. AnsiWEB does not change
which update service a PC uses; if you want PCs pointed at a WSUS server, upload a `.reg` file on the Drivers, scripts & registry page.

### 10. Set the time zone and clock (optional)

**Settings → Time and time zone** pushes clock settings to the PCs:

1. Tick **Apply these clock settings during deployments**.
2. Choose a **time zone** from the list, or type a Windows time zone ID. `tzutil /l` on any PC lists them all.
3. Enter your **time servers** — your domain controllers, a local appliance, or `time.windows.com`.
4. Pick which PCs it applies to, save, then press **Set the time now**.

Each PC reports its time zone, local time and time source afterwards, and the job log flags any PC whose clock is
more than two minutes off the server's. PCs need UDP 123 open to whatever time servers you name.

### 11. Activate Windows (optional)

If you want AnsiWEB to handle Windows licensing, go to **Settings → Windows activation**:

1. Tick **Activate Windows during deployments**.
2. Choose **Product key** and paste your MAK or retail key, or choose **KMS host** and enter the host name.
3. Pick which PCs it applies to, and leave **Skip PCs that are already activated** ticked.
4. Save, then press **Activate Windows now** to try it on the targeted PCs.

The key is stored encrypted and never appears in the deployment plan or job logs. A MAK or retail key must reach
Microsoft, so those PCs need internet access at least once; KMS activation stays on your own network. The Reports
page shows each PC's licence state afterwards.

### 12. Turn on the schedules

In **Settings**, enable:

- **Check vendors for new versions** every 24 hours
- **Deploy to all PCs** at, for example, 19:00 on weekdays

PCs that are switched off are picked up on the next run.

### 13. Add shared folders (optional)

The **Shared folders** page creates a folder on the PCs you choose and shares it. Turn on **file and printer
sharing** at the top of that page first — Windows blocks it by default, so a share is not reachable without it.

Give the share name, the folder path and which accounts get read, change or full access. Accounts you do not list
lose access, so the share ends up matching what AnsiWEB says it should be.

### 14. Add printers (optional)

The **Printers** page pushes network printers — a printer with its own IP address — to the PCs you choose. Give the
Windows driver name, and either attach the vendor's driver package (.zip of its .inf files) to the entry or link a
driver already uploaded on the drivers page: AnsiWEB installs it on each PC before creating the printer. Without it, the named driver must already be on the PC.

Each printer can target all PCs, a site, a group or a named PC, and **Set up on these** pushes it to individual PCs
you tick.

Setting a printer up again does nothing when it is already correct, so run it whenever you add PCs.

### 15. Removing an app from the PCs (when the time comes)

Taking an app out of the standard set stops AnsiWEB installing it, but leaves it on the PCs. To remove it from them,
use the **Inventory** page: in **Standing uninstalls**, add an entry with a pattern matching the program's name in Windows, press **Preview** to
see exactly what would run on each PC, then type REMOVE and press **Uninstall**.

For a one-off — taking a program off a single PC — use the program's own row higher up the same page: pick the PC,
preview, then type REMOVE. That creates no standing entry, and Helpdesk can do it.

A normal deployment never uninstalls anything — it only reports what would go — so entries are safe to add and check
before you commit to anything.

### 16. Download a backup

Once the configuration is how you want it, go to **Settings → Download backup**. The archive holds your
configuration, PC list, encrypted secrets and every uploaded driver, script and registry file. Cached app installers
are left out, since they can be downloaded again.

Keep it somewhere safe: it contains the key to your stored passwords. Restoring is on the same page, and replaces the
current configuration, so you sign back in with the password from that backup.

---

## Day-to-day commands

```bash
sudo ansiweb plan                     # rebuild inventory and deployment plan
sudo ansiweb update-cache             # check vendors and refresh the cache
sudo ansiweb deploy all               # apps, drivers, scripts and registry, everywhere
sudo ansiweb deploy site:HQ           # one site
sudo ansiweb deploy pc:PC-HQ-001      # one PC
sudo ansiweb set-password admin       # change the web password

sudo systemctl restart ansiweb        # restart the service
sudo journalctl -u ansiweb -f         # follow the service log
```

Jobs limited to one kind of item (apps only, scripts only, applying computer names) and single-item runs are started
from the web interface: the **Jobs** page, or **Run now** on an individual item.

### Renaming a PC

Change the name on the PC's page. That alone renames it inside AnsiWEB. To rename Windows as well, tick **Keep the
Windows computer name in sync** and run the **Apply computer names** job (or any deployment); the PC reboots to
finish. The PCs page flags any PC whose reported Windows name no longer matches.

### Changing the management account

On the **PCs** page, under *Management account on the PCs*, change the name or password. AnsiWEB starts using it
immediately, so the account must already exist on the PCs: download the prep script again, run it on every PC, then
use **Test all connections** to confirm. Do it on one PC first — if the account is wrong, AnsiWEB cannot reach that PC
to fix it, and you will have to run the prep script locally.

## Upgrading

```bash
cd ~/AnsiWEB
git pull
sudo ./install.sh
```

Your configuration, secrets and cache live in `/var/lib/ansiweb` and are kept.

After the upgrade the dashboard shows which version you came from, with a link to the **Release notes** page listing
what changed. The same list is in `CHANGELOG.md`.

## Backup

The simplest route is **Settings → Download backup** in the web interface, which covers the configuration, secrets
and uploaded payloads.

For a full copy from the command line, including the app cache, stop the service first so the database is
consistent:

```bash
sudo systemctl stop ansiweb
sudo tar czf ~/ansiweb-backup-$(date +%F).tar.gz \
     --exclude='cache' -C /var/lib ansiweb
sudo systemctl start ansiweb
```

The cache is excluded because it can be re-downloaded. Keep the archive somewhere safe: it contains `.vault_pass`, the key to your stored secrets.

To restore, put the folder back at `/var/lib/ansiweb`, run `sudo chown -R ansiweb:ansiweb /var/lib/ansiweb`, re-run `sudo ./install.sh`, then refresh the cache.

## Uninstalling

```bash
sudo systemctl disable --now ansiweb
sudo rm -f /etc/systemd/system/ansiweb.service /etc/nginx/sites-enabled/ansiweb \
           /etc/nginx/sites-available/ansiweb /usr/local/bin/ansiweb
sudo systemctl daemon-reload && sudo systemctl reload nginx
sudo rm -rf /opt/ansiweb /etc/ssl/ansiweb
sudo rm -rf /var/lib/ansiweb      # deletes configuration, secrets and cache
sudo userdel ansiweb
```

This leaves your PCs untouched. To undo the PC side as well, on each PC remove the management account (`Admin` by default), the `Ansible WinRM HTTPS` firewall rule and the WinRM HTTPS listener.

## Hardening

- **Limit the admin pages.** Edit `/etc/nginx/sites-available/ansiweb`, and inside the `location / { ... }` block of the `listen 443 ssl` server add your admin subnet:
  ```nginx
  allow 192.168.1.0/24;
  deny all;
  ```
  Leave `location /software/` open, or PCs cannot download installers. Then `sudo nginx -t && sudo systemctl reload nginx`.
- **Change the management-account password periodically.** Re-run the prep script on the PCs with the new password, then update it on the PCs page.
- **Keep the server patched:** `sudo apt update && sudo apt upgrade`.
- **Limit who can reach the cache.** Everything under `/software/` is served without a sign-in, including uploaded scripts and registry files. In `/etc/nginx/sites-available/ansiweb`, inside `location /software/`, add your PC subnet:
  ```nginx
  allow 192.168.1.0/24;
  deny all;
  ```
  Then `sudo nginx -t && sudo systemctl reload nginx`.
- **Replace the self-signed certificate** with one from your own authority if you have one, so browsers stop warning: drop `server.crt` and `server.key` into `/etc/ssl/ansiweb/` and reload nginx.
- **Scripts run as SYSTEM** on every PC they target, and anyone who can sign in to AnsiWEB can upload one. Treat the dashboard password as an administrative credential and keep the login restricted to your admin network.

## Troubleshooting

| Symptom | Check |
|---|---|
| Web page doesn't load | `systemctl status ansiweb nginx`; then `curl -skI https://localhost/login` on the server itself. If that works but remote browsers fail, it's a firewall or (on WSL) the port forwarding. |
| Browser warns about the certificate | Expected with the self-signed certificate. Check the fingerprint with `sudo openssl x509 -in /etc/ssl/ansiweb/server.crt -noout -fingerprint -sha256`, or install your own certificate in `/etc/ssl/ansiweb/`. |
| Signed out unexpectedly | The idle timeout in Settings has passed. Raise it if it is too short for how you work; viewing a job log does not count as activity. |
| Signed out immediately after signing in | The session cookie is only sent over HTTPS. Use `https://`, or set `Environment=ANSIWEB_HTTPS=0` in `/etc/systemd/system/ansiweb.service` for a plain-HTTP install. |
| `502 Bad Gateway` | The app isn't running: `sudo journalctl -u ansiweb -n 50`. |
| Under WSL, PCs can't reach the server | `netsh interface portproxy show v4tov4` on Windows — the `connectaddress` must match `hostname -I` inside WSL. Re-run the B4 command with the current address. |
| Connection test says `UNREACHABLE` | The prep script wasn't run, the IP is wrong, or the script was given the wrong server IP. On the PC: `winrm enumerate winrm/config/listener`. |
| `credentials were rejected` | The password in Settings differs from the one used on that PC. Re-run the prep script there. |
| PC can't download installers | On the PC: `curl http://<server-ip>/software/apps/` should give `403` (listings are off, which means the server is answering). If it times out, check the route and firewalls. |
| A driver fails to install | The `.zip` must hold the `.inf` files themselves, not a vendor installer. The job log shows the `pnputil` exit code. |
| Updates time out | Expected on PCs that are far behind. Raise the timeout in Settings and run the job again. |
| A printer fails with "the driver is not available" | Either no driver package is staged with that printer, or the driver name does not match the one in the .inf. Attach the package on the Printers page and check the name. |
| An uninstall reports "no silent uninstaller" | That program can only be removed interactively, so AnsiWEB left it alone. Remove it by hand, or use a script with the vendor's own switches. |
| An uninstall matched nothing | The pattern does not match the name under Windows Settings → Apps → Installed apps on that PC. Check a PC's page in Reports for the exact names. |
| A PC shows as not reporting | It has been off or unreachable since its last deployment. Test the connection from its page; the threshold is in Settings. |
| A PC reports that the resync did not succeed | It could not reach the time servers you named. Check the names and that UDP 123 is open. |
| Activation leaves a PC unactivated | The job log shows what Windows reported. MAK and retail keys need internet access from the PC; KMS needs the host reachable on port 1688. |
| A script is marked failed | Its exit code isn't in the success list for that script. Add the code on the script's page, or fix the script; its output is on the Reports page. |
| Cache update fails with a GitHub rate limit | Add a read-only GitHub token in Settings. |
| An app shows a SHA256 mismatch | The vendor published a new build before the catalogue caught up. It retries on the next check; the previous version stays in use. |
| Deployment is slow | Raise forks in Settings, but keep in mind every PC downloads from the same server. |
| Service won't start after an upgrade | `sudo journalctl -u ansiweb -n 80`, then re-run `sudo ./install.sh`. |

## File locations

| Path | What it is |
|---|---|
| `/opt/ansiweb` | the application (replaced on upgrade) |
| `/opt/ansiweb/venv` | Python environment |
| `/var/lib/ansiweb/config.yml` | apps, PCs, sites, schedules |
| `/var/lib/ansiweb/inventory/` | generated Ansible inventory, including the encrypted vault |
| `/var/lib/ansiweb/.vault_pass` | key for the encrypted secrets — back this up, keep it private |
| `/var/lib/ansiweb/cache/apps/` | cached installers, served at `/software/apps/` |
| `/var/lib/ansiweb/cache/drivers/` | uploaded driver packages |
| `/var/lib/ansiweb/cache/scripts/` | uploaded scripts |
| `/var/lib/ansiweb/cache/registry/` | uploaded registry files |
| `/etc/ssl/ansiweb/` | the dashboard's HTTPS certificate and key |
| `/var/lib/ansiweb/manifest.json` | what is cached, with versions and checksums |
| `/var/lib/ansiweb/logs/` | job logs |
| `/var/lib/ansiweb/reports/` | per-PC reports (apps, drivers, scripts, registry, hardware) |
| `/etc/systemd/system/ansiweb.service` | service definition |
| `/etc/nginx/sites-available/ansiweb` | web server configuration |
