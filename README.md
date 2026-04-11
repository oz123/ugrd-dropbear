# ugrd-dropbear

A [ugrd](https://github.com/desultory/ugrd) module to unlock LUKS-encrypted root
filesystems remotely via SSH (dropbear) during early boot, while keeping the
local console passphrase prompt working simultaneously.

## How it works

During initramfs boot, a named FIFO pipe is created at `/run/ugrd/crypt_fifo`.
Two unlock paths race to write the passphrase into it:

- **Console**: a prompt appears on `tty0` for physical access
- **SSH**: dropbear accepts connections and runs a forced `/unlock.sh` 
  command which prompts for the passphrase

`cryptsetup` reads the passphrase directly from the FIFO.
Whichever path provides it first wins.
After unlock, dropbear and the console prompt are killed and normal boot continues.

This approach was inspired by https://github.com/dracut-crypt-ssh/dracut-crypt-ssh.
This approach requires a one-line patch to ugrd core to add an `init_unlock`
hook level (see Installation).

## Requirements

- [ugrd](https://github.com/desultory/ugrd) — initramfs generator (tested against v2.0.2, might work with 2.2.0).
- `dropbear` — lightweight SSH server
- `mkfifo`, `chmod` — from coreutils (included automatically)
- A static IP configured for the initramfs network (via `ugrd.net.static`)

## Installation

### 1. Patch ugrd core

ugrd needs a new `init_unlock` hook level added between `init_debug` and `init_main`.
This allows dropbear to perform unlock before LVM/mount hooks run, without
interfering with ugrd's hook ordering system:

```bash
sudo python3 -c "
path = '/usr/lib/python3.13/site-packages/ugrd/initramfs_generator.py'
content = open(path).read()
old = 'self.init_types = [\"init_debug\", \"init_main\", \"init_mount\"]'
new = 'self.init_types = [\"init_debug\", \"init_unlock\", \"init_main\", \"init_mount\"]'
open(path, 'w').write(content.replace(old, new))
print('Patched')
"
```

> **Note**: This patch should ideally be submitted upstream to ugrd.
  Until then it must be reapplied after ugrd updates.

### 2. Install the module

```bash
UGRD_PATH=$(python3 -c "import ugrd; print(ugrd.__path__[0])")
sudo -E cp -v dropbear.py $UGRD_PATH/dropbear.py
sudo -E cp -v dropbear.toml $UGRD_PATH/dropbear.toml
```

### 3. Set up host keys

Convert your existing system SSH host keys to dropbear format.
This ensures the initramfs SSH fingerprint stays stable across initramfs rebuilds:

```bash
sudo dropbearconvert openssh dropbear \
    /etc/ssh/ssh_host_ed25519_key \
    /etc/dropbear/dropbear_ed25519_host_key

sudo dropbearconvert openssh dropbear \
    /etc/ssh/ssh_host_rsa_key \
    /etc/dropbear/dropbear_rsa_host_key
```

The `/etc/dropbear` directory is automatically copied into the initramfs.

## Configuration

Add the following to `/etc/ugrd/config.toml`:

```toml
kmod_autodetect_lspci = true
cryptsetup_trim = true

modules = [
    "ugrd.base.base",
    "ugrd.crypto.cryptsetup",
    "ugrd.fs.lvm",
    "ugrd.net.static",
    "dropbear",
]

net_device = "eth0"
net_device_mac = "aa:bb:cc:dd:ee:ff"
ip_address = "192.168.0.100/24"
ip_gateway = "192.168.0.1"

dropbear_authorized_keys = "/root/.ssh/authorized_keys"

[cryptsetup.luks-<UUID>]
uuid = "<UUID>"
```

Replace `net_device`, `net_device_mac`, `ip_address`, `ip_gateway`,
and the LUKS UUID with your system's values.

> **Note**: Do not include `ugrd.base.console` — dropbear handles the console prompt directly.

### Options

| Option | Description | Required |
|--------|-------------|----------|
| `dropbear_authorized_keys` | Path to the authorized_keys file for SSH access | Yes |

## Building the initramfs

```bash
sudo ugrd /boot/initramfs-$(uname -r).img
sudo grub-mkconfig -o /boot/grub/grub.cfg
```

## Usage

### Console unlock

On reboot, a passphrase prompt will appear on the physical console (`tty0`).
Enter the LUKS passphrase and press Enter.

### Remote SSH unlock

```bash
ssh -p 22 root@<ip_address>
```

You will be dropped directly into the unlock prompt — no shell access is given.
Enter the passphrase and the connection will close once unlock succeeds.

To use a non-default key:
```bash
ssh -i ~/.ssh/your_key -p 22 root@<ip_address>
```

## Bugs fixed in ugrd / ugrd-dropbear

The following bugs were identified and patched during development:

### ugrd `net/net.py` — KeyError on missing route metric
`route["metric"]` raised `KeyError` when routes had no metric field.
Fixed with `route.get("metric", 0)`.

### ugrd `net/net.py` — Spurious ERROR log for `net_device`
`@unset("net_device", log_level=40)` logged an ERROR even when `net_device` was intentionally set.
Changed to `log_level=10`.

### ugrd `initramfs_dict.py` — `Path()` default overwrites configured value
`custom_parameters` was initializing keys to `Path()` after the config queue had
already processed them, silently discarding the configured value.
Fixed by not setting a default if the key already exists.

### ugrd-dropbear — `readvar` vs `cat` for SWITCH_ROOT_TARGET
Original module used `cat /run/vars/SWITCH_ROOT_TARGET` but ugrd stores
vars under `/run/ugrd/`. Fixed to use `readvar SWITCH_ROOT_TARGET`.

### ugrd-dropbear — TOML table sections swallowing subsequent keys
Placing `[table]` sections before top-level keys in TOML caused subsequent
keys to be parsed as part of the table. Fixed by moving all table sections 
to the end of the config file.

## Security considerations

- SSH host keys are stored on the **unencrypted** `/boot` partition inside the initramfs.
  Keep this in mind for your threat model.
- Only public-key authentication is allowed — password authentication and port forwarding are disabled.
- The SSH session runs a forced command (`/unlock.sh`) — no shell access is provided.
- Consider using a separate `authorized_keys` file for initramfs access, different from your normal one.
