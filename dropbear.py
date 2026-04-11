__version__ = "0.3.0"

from pathlib import Path
from shutil import copy2
from subprocess import run as run_proc
from typing import Union

from zenlib.util import contains

DROPBEAR_KEY_TYPES = {
    "dropbear_ecdsa_host_key": "ecdsa",
    "dropbear_ed25519_host_key": "ed25519",
    "dropbear_rsa_host_key": "rsa",
}


def drop_the_bear(self) -> str:
    """Returns shell lines to kill the dropbear server if the switch_root_target is mounted"""
    return """
    if [ -n "$(awk -v target="$(readvar SWITCH_ROOT_TARGET)" '$2 == target {print $2}' /proc/mounts)" ]; then
        einfo "Switch root target mounted, killing dropbear."
        kill -9 $(cat /run/dropbear.pid)
        return
    fi
    eerror "Switch root target not mounted after dropbear init, ending session"
    rd_fail
    """


def _process_dropbear_authorized_keys(self, authorized_key_path: Union[str, Path]):
    """Sets the dropbear_authorized_keys to the path of the authorized_keys file"""
    authorized_key_path = Path(authorized_key_path)
    if not authorized_key_path.exists():
        raise FileNotFoundError(f"[dropbear] Authorized_keys file not found at: {authorized_key_path}")
    self.data["dropbear_authorized_keys"] = authorized_key_path


@contains("dropbear_authorized_keys", raise_exception=True)
def add_dropbear_keys(self):
    """Adds public keys to the dropbear authorized_keys file"""
    self["copies"] = {
        "dropbear_authorized_keys": {
            "source": self["dropbear_authorized_keys"],
            "destination": "/root/.ssh/authorized_keys",
        }
    }


def dropbear_wait(self):
    """ Returns a shell script to sleep while dropbear is running """
    return """
    while [ "$(cat /proc/$(cat /run/dropbear.pid)/comm)" == "dropbear" ]; do
        edebug "Dropbear is still running, sleeping"
        sleep 1
    done
    einfo "Dropbear has stopped, continuing"
    """

def dropbear_finalize(self):
    """ Create a passwd entry for root if it doesn't exist, chmod 0600 the authorized_keys file.
    Generate dropbear host keys in /etc/dropbear if they do not already exist. """
    self._write("etc/passwd", "root:x:0:0:root:/root:/bin/sh\n", append=True)
    authorized_keys_file = self._get_build_path(self["copies"]["dropbear_authorized_keys"]["destination"])
    authorized_keys_file.chmod(0o600)

    key_dir = Path("/etc/dropbear")
    key_dir.mkdir(parents=True, exist_ok=True)
    for key_file, key_type in DROPBEAR_KEY_TYPES.items():
        key_path = key_dir / key_file
        if not key_path.exists():
            self.logger.info("[dropbear] Generating %s host key: %s" % (key_type, key_path))
            run_proc(["dropbearkey", "-t", key_type, "-f", str(key_path)], check=True)
        dest = self._get_build_path(f"/etc/dropbear/{key_file}")
        self.logger.info("[dropbear] Copying host key to initramfs: %s" % key_file)
        copy2(key_path, dest)
        dest.chmod(0o600)

def dropbear_init(self):
    """Returns a shell script to start init_main using dropbear"""

    custom_init_contents = [
        self["shebang"],
        f'einfo "Starting dropbear module v{__version__}"',
        "print_banner",
        *self.generate_init_main(),
        "drop_the_bear",
    ]

    run_init = [  # Run dropbear as a daemon, poll for SSH unlock or keypress for local unlock
        "einfo Starting dropbear",
        f"dropbear -E -j -k -s -c /{self['_custom_init_file']} -P /run/dropbear.pid || rd_fail",
        f'einfo "Unlock remotely via SSH, or press any key to unlock locally"',
        f'while [ "$(cat /proc/$(cat /run/dropbear.pid)/comm 2>/dev/null)" = "dropbear" ]; do',
        f'    if read -t 1 -n 1 -r _key < /dev/console 2>/dev/null; then',
        f'        einfo "Local unlock selected"',
        f'        . /{self["_custom_init_file"]}',
        f'        break',
        f'    fi',
        f'done',
    ]

    return run_init, custom_init_contents
