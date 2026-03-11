__version__ = "0.4.0"

from pathlib import Path
from typing import Union

from zenlib.util import contains


UNLOCK_SCRIPT = "unlock.sh"


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
    """Generates the SSH forced-command unlock script."""
    lines = [
        "#!/bin/sh -l",
        'einfo "Remote LUKS unlock session"',
    ]
    for name in self["cryptsetup"]:
        lines += [
            f"if cryptsetup status {name} > /dev/null 2>&1; then",
            f'    einfo "Device already unlocked: {name}"',
            "else",
            f'    einfo "Unlocking: {name}"',
            f"    cryptsetup open $(get_crypt_dev {name}) {name} --tries 3 || exit 1",
            "fi",
        ]
    lines += [
        'einfo "Unlock complete, you may close this session."',
    ]
    script_path = self._get_build_path(UNLOCK_SCRIPT)
    script_path.parent.mkdir(parents=True, exist_ok=True)
    self._write(UNLOCK_SCRIPT, "\n".join(lines) + "\n")
    script_path.chmod(0o755)
    self.logger.info("Wrote unlock script to: %s" % script_path)


def dropbear_finalize(self):
    """chmod 0600 the authorized_keys file and ensure passwd has root entry."""
    self._write("etc/passwd", "root:x:0:0:root:/root:/bin/sh\n", append=True)
    authorized_keys_file = self._get_build_path(self["copies"]["dropbear_authorized_keys"]["destination"])
    authorized_keys_file.chmod(0o600)


def dropbear_init(self):
    """Custom init: runs init_main.sh on console in background, starts dropbear
    for SSH unlock, waits for LUKS to be unlocked by either path, then continues boot."""

    names = list(self["cryptsetup"].keys())
    luks_check = " && ".join(
        [f"cryptsetup status {n} > /dev/null 2>&1" for n in names]
    )

    custom_init_contents = [
        self["shebang"],
        f'einfo "Starting dropbear module v{__version__}"',
        "print_banner",
        *self.generate_init_main(),
    ]

    run_init = [
        "print_banner",
        '# Start console unlock in background',
        f"setsid {self['_custom_init_file']} </dev/tty0 >/dev/tty0 2>/dev/tty0 &",
        'CONSOLE_PID=$!',
        '# Start dropbear for SSH unlock',
        'ip_addr=$(ip addr show | awk \'/inet / {print $2}\' | grep -v "127.0.0.1")',
        'if [ -n "$ip_addr" ]; then',
        '    einfo "Network is UP: $ip_addr - SSH unlock available on port 22"',
        'else',
        '    ewarn "Network does not appear to be ready"',
        'fi',
        f"dropbear -R -E -j -k -s -c /{UNLOCK_SCRIPT} -P /run/dropbear.pid || ewarn 'Failed to start dropbear'",
        '# Wait for LUKS to be unlocked by either path',
        'einfo "Waiting for LUKS unlock (console or SSH)..."',
        'while true; do',
        f'    if {luks_check}; then',
        '        break',
        '    fi',
        '    sleep 1',
        'done',
        'einfo "LUKS unlocked, continuing boot"',
        '# Kill console process and dropbear',
        'kill "$CONSOLE_PID" 2>/dev/null || true',
        'if [ -f /run/dropbear.pid ]; then',
        '    kill -9 $(cat /run/dropbear.pid) 2>/dev/null || true',
        '    rm -f /run/dropbear.pid',
        'fi',
    ]

    return run_init, custom_init_contents
