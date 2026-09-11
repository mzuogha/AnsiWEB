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
| Each PC → AnsiWEB | TCP 80 | downloading cached installers |
| Admin browser → AnsiWEB | TCP 80 | the web interface |
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
- asks you to choose the web admin password
- adds the `ansiweb` command
- starts the `ansiweb` service and configures nginx

It ends with the address to open, for example:

```
AnsiWEB is running:  http://192.168.1.10/
```

### A4. Optional: restrict access with a firewall

If you enable `ufw`, allow SSH and HTTP. Restrict the web interface to your admin machine or subnet if you can:

```bash
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw enable
sudo ufw status
```

For a tighter setup, keep the cache open to all PCs but limit the admin pages by editing the nginx site (see [Hardening](#hardening)).

### A5. Check it is healthy

```bash
systemctl status ansiweb --no-pager      # should be "active (running)"
systemctl status nginx --no-pager
curl -sI http://localhost/login | head -1  # HTTP/1.1 200 OK
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

### B4. Forward port 80 from Windows into WSL

Get the current WSL address:

```bash
hostname -I | awk '{print $1}'
```

Then in an **Administrator** PowerShell on the Windows host (replace the address):

```powershell
netsh interface portproxy add v4tov4 listenport=80 listenaddress=0.0.0.0 connectport=80 connectaddress=172.24.58.101
New-NetFirewallRule -DisplayName "AnsiWEB 80" -Direction Inbound -Protocol TCP -LocalPort 80 -Action Allow
netsh interface portproxy show v4tov4
```

Find the **Windows** machine's own LAN address, which is what your PCs will use:

```powershell
ipconfig | findstr /i "IPv4"
```

Check it from another machine on the network: `http://<windows-ip>/` should show the AnsiWEB login page.

> **WSL's address changes** whenever WSL restarts, which breaks the forwarding. Fix it once with the scheduled task in B5, or re-run the `netsh` command after each restart.

### B5. Make it survive reboots

Create `C:\Scripts\ansiweb-wsl.ps1` on the Windows host:

```powershell
# Starts WSL and re-points the port forwarding at its current address.
wsl -d Ubuntu-24.04 -u root -e /bin/true          # boot the distribution
Start-Sleep -Seconds 15
$ip = (wsl -d Ubuntu-24.04 hostname -I).Trim().Split(' ')[0]
netsh interface portproxy delete v4tov4 listenport=80 listenaddress=0.0.0.0 2>$null
netsh interface portproxy add    v4tov4 listenport=80 listenaddress=0.0.0.0 connectport=80 connectaddress=$ip
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

Reboot Windows and check `http://<windows-ip>/` still answers.

> **Mirrored networking** (`networkingMode=mirrored` in `.wslconfig`) can remove the need for port forwarding on Windows 11 22H2 and later, but it has a long list of open issues around VPNs, loopback and DNS. The port-proxy approach above works on every WSL2 version, so start there.

### B6. Keep Windows Update from interrupting deployments

Set active hours or a maintenance window on the host, so a restart doesn't land in the middle of a scheduled deployment.

---

## First-time configuration

These steps are the same for both options. Open `http://<server-ip>/` and sign in as `admin`.

### 1. Settings

- **Server IP address:** the address PCs use to reach AnsiWEB. On a server, that's its own IP. **Under WSL, use the Windows host's LAN IP**, not the WSL address.
- **ansible_svc password:** choose it now; you will type the same one on every PC in step 2. Store it in your password manager.
- Leave forks and batch size at 20 to start with.

### 2. Prepare each PC (once per PC)

On the **PCs** page, click **Download PC prep script**. The downloaded script already contains your server IP.

Copy it to each PC (USB stick or a share), then in an **Administrator** PowerShell on that PC:

```powershell
cd C:\Users\<you>\Downloads
powershell -ExecutionPolicy Bypass -File .\Prepare-AnsibleHost.ps1
```

Enter the `ansible_svc` password from step 1 when prompted. The script:

- creates the local `ansible_svc` administrator account
- enables WinRM and creates an HTTPS listener with a self-signed certificate
- allows local administrator accounts to work remotely (`LocalAccountTokenFilterPolicy`)
- opens TCP 5986 **only** to your AnsiWEB server, and closes the unencrypted WinRM port 5985

It prints the PC name and IP to enter in AnsiWEB. It is safe to run again.

You can also build this into your imaging process, so new PCs arrive ready.

### 3. Add the PCs

On the **PCs** page, add each PC by name and IP, or paste a list:

```
PC-HQ-001,192.168.1.21,HQ
PC-HQ-002,192.168.1.22,HQ,finance
PC-BR1-001,192.168.2.21,Branch1
```

### 4. Test the connections

Click **Test all connections**. Every PC should answer `pong`. Fix any failures now — a PC that can't be reached can't be deployed to.

### 5. Fill the cache

On the **Apps** page, click **Check for updates now**. Watch the live log: each app is downloaded once and verified against its published SHA256 fingerprint. This takes a while on the first run, and it's the only step that needs internet.

Firefox is pinned to 65.0.2 and is downloaded from Mozilla's release archive.

### 6. Deploy

Start with one PC (PCs page → **Deploy**), then a site, then everything:

```
PCs page → Deploy (one PC)
PCs page → Sites → Deploy (one site)
Dashboard → Deploy to all PCs
```

Run it a second time against the same PC. The second run should report no changes; that's your proof the setup is correct.

### 7. Turn on the schedules

In **Settings**, enable:

- **Check vendors for new versions** every 24 hours
- **Deploy to all PCs** at, for example, 19:00 on weekdays

PCs that are switched off are picked up on the next run.

---

## Day-to-day commands

```bash
sudo ansiweb plan                     # rebuild inventory and deployment plan
sudo ansiweb update-cache             # check vendors and refresh the cache
sudo ansiweb deploy all               # deploy to everything
sudo ansiweb deploy site:HQ           # one site
sudo ansiweb deploy pc:PC-HQ-001      # one PC
sudo ansiweb set-password admin       # change the web password

sudo systemctl restart ansiweb        # restart the service
sudo journalctl -u ansiweb -f         # follow the service log
```

## Upgrading

```bash
cd ~/AnsiWEB
git pull
sudo ./install.sh
```

Your configuration, secrets and cache live in `/var/lib/ansiweb` and are kept.

## Backup

Everything that matters is in one folder. Stop the service first so the database is consistent:

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
sudo rm -rf /opt/ansiweb
sudo rm -rf /var/lib/ansiweb      # deletes configuration, secrets and cache
sudo userdel ansiweb
```

This leaves your PCs untouched. To undo the PC side as well, on each PC remove the `ansible_svc` account, the `Ansible WinRM HTTPS` firewall rule and the WinRM HTTPS listener.

## Hardening

- **Limit the admin pages.** Edit `/etc/nginx/sites-available/ansiweb`, and inside `location / { ... }` add your admin subnet:
  ```nginx
  allow 192.168.1.0/24;
  deny all;
  ```
  Leave `location /software/` open, or PCs cannot download installers. Then `sudo nginx -t && sudo systemctl reload nginx`.
- **Change the `ansible_svc` password periodically.** Re-run the prep script on the PCs with the new password, then update it in Settings.
- **Keep the server patched:** `sudo apt update && sudo apt upgrade`.
- The web interface uses plain HTTP on the LAN. If you need HTTPS, put a certificate on nginx.

## Troubleshooting

| Symptom | Check |
|---|---|
| Web page doesn't load | `systemctl status ansiweb nginx`; then `curl -sI http://localhost/login` on the server itself. If that works but remote browsers fail, it's a firewall or (on WSL) the port forwarding. |
| `502 Bad Gateway` | The app isn't running: `sudo journalctl -u ansiweb -n 50`. |
| Under WSL, PCs can't reach the server | `netsh interface portproxy show v4tov4` on Windows — the `connectaddress` must match `hostname -I` inside WSL. Re-run the B4 command with the current address. |
| Connection test says `UNREACHABLE` | The prep script wasn't run, the IP is wrong, or the script was given the wrong server IP. On the PC: `winrm enumerate winrm/config/listener`. |
| `credentials were rejected` | The password in Settings differs from the one used on that PC. Re-run the prep script there. |
| PC can't download installers | On the PC: `curl http://<server-ip>/software/apps/` should give `403` (listings are off, which means the server is answering). If it times out, check the route and firewalls. |
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
| `/var/lib/ansiweb/manifest.json` | what is cached, with versions and checksums |
| `/var/lib/ansiweb/logs/` | job logs |
| `/var/lib/ansiweb/reports/` | per-PC installed-app reports |
| `/etc/systemd/system/ansiweb.service` | service definition |
| `/etc/nginx/sites-available/ansiweb` | web server configuration |
