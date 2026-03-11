__version__ = "0.3.0"

from pathlib import Path
from typing import Union

from zenlib.util import contains


UNLOCK_SCRIPT = "unlock.sh"


def drop_the_bear_background(self) -> list:
    """Start dropbear in the background as a remote unlock path.
    The forced command is /unlock.sh which only prompts for the LUKS passphrase."""
    return [
        'ip_addr=$(ip addr show | awk \'/inet / {print $2}\' | grep -v "127.0.0.1")',
        'if [ -n "$ip_addr" ]; then',
        '    einfo "Network is UP: $ip_addr"',
        'else',
        '    ewarn "Network does not appear to be ready"',
        'fi',
        'einfo "Starting dropbear for remote unlock on port 22"',
        f"dropbear -R -E -j -k -s -c /{UNLOCK_SCRIPT} -P /run/dropbear.pid && einfo 'Dropbear started, SSH available' || ewarn 'Failed to start dropbear'",
    ]


def stop_dropbear(self) -> list:
    """Kill dropbear after root is mounted."""
    return [
        'if [ -f /run/dropbear.pid ]; then',
        '    einfo "Remote unlock complete, killing dropbear."',
        '    kill -9 $(cat /run/dropbear.pid) 2>/dev/null',
        '    rm -f /run/dropbear.pid',
        'fi',
    ]


def crypt_poll(self) -> list:
    """Start a background poller that detects remote unlock via signal file
    and sends a newline to unblock the console cryptsetup passphrase prompt."""
    return [
        '# Background poller: unblocks console prompt when remote unlock completes',
        '(',
        '    while true; do',
        '        if [ -f /run/ugrd/remote_unlocked ]; then',
        '            einfo "Remote unlock detected, unblocking console"',
        '            printf "\\n" > /dev/tty0 2>/dev/null || true',
        '            break',
        '        fi',
        '        sleep 1',
        '    done',
        ') &',
        'CRYPT_POLLER_PID=$!',
    ]


def crypt_poll_cleanup(self) -> list:
    """Kill the background poller after crypt_init completes."""
    return [
        'kill "$CRYPT_POLLER_PID" 2>/dev/null || true',
        'rm -f /run/ugrd/remote_unlocked',
    ]


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
    """Generates and deploys the unlock helper script used as dropbear forced command.
    The script prompts for the LUKS passphrase and exits — no shell access."""
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
        'touch /run/ugrd/remote_unlocked',
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
