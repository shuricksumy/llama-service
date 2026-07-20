"""llama-tool.py init -- one-time host setup after cloning this repo.

Installs the systemd units (the single-instance llama-server.service, plus
the llama-server@.service template for running several presets at once)
and logrotate config (from deploy/) via sudo, and enables llama-server.service
to start on boot. Does NOT start any service (there's no engine/model
installed yet on a fresh clone -- run `engine install` and `models download`
first) and does NOT touch user/group membership (see README.md's "Vulkan /
render group access" section for that, which is host/GPU-specific enough
that it isn't safe to automate here).
"""
import subprocess

from ._common import REPO_ROOT, die

DEPLOY_DIR = REPO_ROOT / "deploy"
SERVICE_SRC = DEPLOY_DIR / "llama-server.service"
TEMPLATE_SRC = DEPLOY_DIR / "llama-server@.service"
LOGROTATE_SRC = DEPLOY_DIR / "llama-server.logrotate"
SERVICE_DEST = "/etc/systemd/system/llama-server.service"
TEMPLATE_DEST = "/etc/systemd/system/llama-server@.service"
LOGROTATE_DEST = "/etc/logrotate.d/llama-server"


def _run(cmd, dry_run):
    print("+ " + " ".join(cmd))
    if dry_run:
        return
    result = subprocess.run(cmd)
    if result.returncode != 0:
        die(f"command failed: {' '.join(cmd)}")


def run(args):
    if not SERVICE_SRC.is_file():
        die(f"missing {SERVICE_SRC.relative_to(REPO_ROOT)}")
    if not TEMPLATE_SRC.is_file():
        die(f"missing {TEMPLATE_SRC.relative_to(REPO_ROOT)}")
    if not LOGROTATE_SRC.is_file():
        die(f"missing {LOGROTATE_SRC.relative_to(REPO_ROOT)}")

    print("This will use sudo to:")
    print(f"  copy {SERVICE_SRC.relative_to(REPO_ROOT)} -> {SERVICE_DEST}")
    print(f"  copy {TEMPLATE_SRC.relative_to(REPO_ROOT)} -> {TEMPLATE_DEST}")
    print(f"  copy {LOGROTATE_SRC.relative_to(REPO_ROOT)} -> {LOGROTATE_DEST}")
    print("  systemctl daemon-reload")
    if not args.no_enable:
        print("  systemctl enable llama-server")
    print()
    print("It will NOT start any service, and will NOT touch user/group")
    print("membership (see README.md for Vulkan/render group setup).")
    print()

    if not args.dry_run and not args.yes:
        reply = input("Proceed? [y/N] ")
        if reply.strip().lower() != "y":
            print("Aborted.")
            return

    _run(["sudo", "cp", str(SERVICE_SRC), SERVICE_DEST], args.dry_run)
    _run(["sudo", "cp", str(TEMPLATE_SRC), TEMPLATE_DEST], args.dry_run)
    _run(["sudo", "cp", str(LOGROTATE_SRC), LOGROTATE_DEST], args.dry_run)
    _run(["sudo", "systemctl", "daemon-reload"], args.dry_run)
    if not args.no_enable:
        _run(["sudo", "systemctl", "enable", "llama-server"], args.dry_run)

    print()
    print("Dry-run complete, nothing changed." if args.dry_run else "Done.")
    print()
    print("Next:")
    print("  ./llama-tool.py engine install")
    print("  ./llama-tool.py models download <org>/<repo> <file.gguf>")
    print("  sudo systemctl start llama-server")
    print()
    print("To run additional presets alongside it as separate services, use")
    print("the llama-server@.service template instead (see README.md's")
    print("'Running several models at once'), e.g.:")
    print("  sudo systemctl enable --now llama-server@<preset>")


def add_arguments(parser):
    parser.add_argument("-y", "--yes", action="store_true", help="Skip the confirmation prompt")
    parser.add_argument("-n", "--dry-run", action="store_true", help="Print what would be done without doing it")
    parser.add_argument("--no-enable", action="store_true", help="Install files but don't enable the service on boot")
