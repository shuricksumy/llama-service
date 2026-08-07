"""llama-tool.py init -- one-time host setup after cloning this repo.

Installs the systemd units (the single-instance llama-server.service, the
llama-server@.service template for running several presets at once, and the
llama-server-restart[@].timer/.service pairs for periodic memory-clearing
restarts) and logrotate config (from deploy/) via sudo, and enables
llama-server.service to start on boot. Does NOT start any service (there's
no engine/model installed yet on a fresh clone -- run `engine install` and
`models download` first), does NOT enable the restart timers (opt-in
per-host, see README.md's "Periodic restart" section), and does NOT touch
user/group membership (see README.md's "Vulkan / render group access"
section for that, which is host/GPU-specific enough that it isn't safe to
automate here).
"""
import subprocess

from ._common import REPO_ROOT, die

DEPLOY_DIR = REPO_ROOT / "deploy"
SERVICE_SRC = DEPLOY_DIR / "llama-server.service"
TEMPLATE_SRC = DEPLOY_DIR / "llama-server@.service"
LOGROTATE_SRC = DEPLOY_DIR / "llama-server.logrotate"
RESTART_SERVICE_SRC = DEPLOY_DIR / "llama-server-restart.service"
RESTART_TIMER_SRC = DEPLOY_DIR / "llama-server-restart.timer"
RESTART_TEMPLATE_SERVICE_SRC = DEPLOY_DIR / "llama-server-restart@.service"
RESTART_TEMPLATE_TIMER_SRC = DEPLOY_DIR / "llama-server-restart@.timer"
SERVICE_DEST = "/etc/systemd/system/llama-server.service"
TEMPLATE_DEST = "/etc/systemd/system/llama-server@.service"
LOGROTATE_DEST = "/etc/logrotate.d/llama-server"
RESTART_SERVICE_DEST = "/etc/systemd/system/llama-server-restart.service"
RESTART_TIMER_DEST = "/etc/systemd/system/llama-server-restart.timer"
RESTART_TEMPLATE_SERVICE_DEST = "/etc/systemd/system/llama-server-restart@.service"
RESTART_TEMPLATE_TIMER_DEST = "/etc/systemd/system/llama-server-restart@.timer"


def _run(cmd, dry_run):
    print("+ " + " ".join(cmd))
    if dry_run:
        return
    result = subprocess.run(cmd)
    if result.returncode != 0:
        die(f"command failed: {' '.join(cmd)}")


def run(args):
    required = [
        SERVICE_SRC, TEMPLATE_SRC, LOGROTATE_SRC,
        RESTART_SERVICE_SRC, RESTART_TIMER_SRC,
        RESTART_TEMPLATE_SERVICE_SRC, RESTART_TEMPLATE_TIMER_SRC,
    ]
    for src in required:
        if not src.is_file():
            die(f"missing {src.relative_to(REPO_ROOT)}")

    installs = [
        (SERVICE_SRC, SERVICE_DEST),
        (TEMPLATE_SRC, TEMPLATE_DEST),
        (LOGROTATE_SRC, LOGROTATE_DEST),
        (RESTART_SERVICE_SRC, RESTART_SERVICE_DEST),
        (RESTART_TIMER_SRC, RESTART_TIMER_DEST),
        (RESTART_TEMPLATE_SERVICE_SRC, RESTART_TEMPLATE_SERVICE_DEST),
        (RESTART_TEMPLATE_TIMER_SRC, RESTART_TEMPLATE_TIMER_DEST),
    ]

    print("This will use sudo to:")
    for src, dest in installs:
        print(f"  copy {src.relative_to(REPO_ROOT)} -> {dest}")
    print("  systemctl daemon-reload")
    if not args.no_enable:
        print("  systemctl enable llama-server")
    print()
    print("It will NOT start any service, and will NOT touch user/group")
    print("membership (see README.md for Vulkan/render group setup). The")
    print("periodic-restart timers are installed but left disabled -- opt")
    print("in per-host (see 'Next' below).")
    print()

    if not args.dry_run and not args.yes:
        reply = input("Proceed? [y/N] ")
        if reply.strip().lower() != "y":
            print("Aborted.")
            return

    for src, dest in installs:
        _run(["sudo", "cp", str(src), dest], args.dry_run)
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
    print()
    print("To restart a service daily at 03:00 (clears accumulated memory),")
    print("opt in per service -- not enabled automatically:")
    print("  sudo systemctl enable --now llama-server-restart.timer")
    print("  sudo systemctl enable --now llama-server-restart@<preset>.timer")


def add_arguments(parser):
    parser.add_argument("-y", "--yes", action="store_true", help="Skip the confirmation prompt")
    parser.add_argument("-n", "--dry-run", action="store_true", help="Print what would be done without doing it")
    parser.add_argument("--no-enable", action="store_true", help="Install files but don't enable the service on boot")
