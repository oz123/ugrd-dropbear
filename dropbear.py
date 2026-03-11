__version__ = "0.5.0"

from pathlib import Path
from typing import Union

from zenlib.util import contains


UNLOCK_SCRIPT = "unlock.sh"
CONSOLE_PROMPT_SCRIPT = "console_prompt.sh"
CRYPT_FIFO = "/run/ugrd/crypt_fifo"


def _process_dropbear_authorized_keys(self, authorized_key_path: Union[str, Path]):
    """Sets the dropbear_authorized_keys to the path of the authorized_keys file"""
    authorized_key_path = Path(authorized_key_path)
    if not authorized_key_path.exists():
        raise FileNotFoundError(f"[dropbear] Authorized_keys file not found at: {authorized_key_path}")
    self.data["dropbear_authorized_keys"] = str(authorized_key_path)


@contains("dropbear_authorized_keys", raise_exception=True)
def add_dropbear_keys(self):
    """Adds public keys to the dropbear authorized_keys file"""
    self["copies"] = {
        "dropbear_authorized_keys": {
            "source": self["dropbear_authorized_keys"],
            "destination": "/root/.ssh/authorized_keys",
        }
    }


def add_unlock_script(self):
    """SSH forced-command script: prompts for passphrase, writes to FIFO."""
    lines = [
        "#!/bin/sh -l",
        'einfo "Remote LUKS unlock - enter passphrase:"',
        f'if [ ! -p "{CRYPT_FIFO}" ]; then',
        '    eerror "Unlock FIFO not ready"',
        '    exit 1',
        'fi',
        'stty -echo',
        'printf "Passphrase: "',
        'read -r PASSPHRASE',
        'stty echo',
        'printf "\\n"',
        f'printf "%s" "$PASSPHRASE" > {CRYPT_FIFO}',
        'einfo "Passphrase sent, you may close this session."',
    ]
    script_path = self._get_build_path(UNLOCK_SCRIPT)
    script_path.parent.mkdir(parents=True, exist_ok=True)
    self._write(UNLOCK_SCRIPT, "\n".join(lines) + "\n")
    script_path.chmod(0o755)


def add_console_prompt_script(self):
    """Console script: prompts for passphrase on tty0, writes to FIFO."""
    lines = [
        "#!/bin/sh",
        'exec </dev/tty0 >/dev/tty0 2>/dev/tty0',
        'stty -echo',
        'printf "\\nEnter LUKS passphrase: "',
        'read -r PASSPHRASE',
        'stty echo',
        'printf "\\n"',
        f'printf "%s" "$PASSPHRASE" > {CRYPT_FIFO}',
    ]
    script_path = self._get_build_path(CONSOLE_PROMPT_SCRIPT)
    script_path.parent.mkdir(parents=True, exist_ok=True)
    self._write(CONSOLE_PROMPT_SCRIPT, "\n".join(lines) + "\n")
    script_path.chmod(0o755)


def dropbear_finalize(self):
    """chmod 0600 the authorized_keys file and ensure passwd has root entry."""
    self._write("etc/passwd", "root:x:0:0:root:/root:/bin/sh\n", append=True)
    authorized_keys_file = self._get_build_path(self["copies"]["dropbear_authorized_keys"]["destination"])
    authorized_keys_file.chmod(0o600)


def dropbear_init(self):
    """Custom init using FIFO: console and SSH race to provide passphrase.
    All post-unlock steps (lvm, mount, switch_root) run in PID 1 context."""

    names = list(self["cryptsetup"].keys())

    # custom_init_contents is just a dummy - we don't use agetty/SSH session for init
    # It won't be called since run_init handles everything inline in PID 1
    custom_init_contents = [
        self["shebang"],
        '# This file is not used - all init happens in PID 1',
    ]

    run_init = [
        "print_banner",
        '# Create FIFO for passphrase',
        f'mkfifo {CRYPT_FIFO}',
        f'chmod 600 {CRYPT_FIFO}',
        '# Start console passphrase prompt in background',
        f'/{CONSOLE_PROMPT_SCRIPT} &',
        'CONSOLE_PID=$!',
        '# Start dropbear for SSH unlock',
        'ip_addr=$(ip addr show | awk \'/inet / {print $2}\' | grep -v "127.0.0.1")',
        'if [ -n "$ip_addr" ]; then',
        '    einfo "Network is UP: $ip_addr - SSH unlock available on port 22"',
        'else',
        '    ewarn "Network does not appear to be ready"',
        'fi',
        f"dropbear -R -E -j -k -s -c /{UNLOCK_SCRIPT} -P /run/dropbear.pid || ewarn 'Failed to start dropbear'",
    ]

    # Unlock each LUKS volume using FIFO
    for name in names:
        run_init += [
            f'if ! cryptsetup status {name} > /dev/null 2>&1; then',
            f'    einfo "Waiting for passphrase to unlock: {name}"',
            f'    cryptsetup open --tries 1 --key-file {CRYPT_FIFO} $(get_crypt_dev {name}) {name} || rd_fail "Failed to unlock {name}"',
            f'else',
            f'    ewarn "Device already open: {name}"',
            f'fi',
        ]

    run_init += [
        '# Unlock done - kill console prompt and dropbear',
        'kill "$CONSOLE_PID" 2>/dev/null || true',
        'if [ -f /run/dropbear.pid ]; then',
        '    kill -9 $(cat /run/dropbear.pid) 2>/dev/null || true',
        '    rm -f /run/dropbear.pid',
        'fi',
        f'rm -f {CRYPT_FIFO}',
        '# Now run LVM, mount and the rest of init inline in PID 1',
        'handle_resume',
        'init_lvm',
        'mount_root',
        'ext4_fsck',
    ]

    return run_init, custom_init_contents
