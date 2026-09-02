"""llama-tool.py init -- one-time host setup after cloning this repo.

Installs the systemd units (the llama-server@.service template for running
presets as named services, and the llama-server-restart@.timer/.service
pair for periodic memory-clearing restarts) and logrotate config (from
deploy/) via sudo. Does NOT start or enable any service -- there's no
"default" service anymore (every preset runs as its own named
llama-server@<preset> instance; see `llama-tool.py services enable
<preset> --now`), and nothing's installed yet on a fresh clone anyway (run
`engine install` and `models download` first). Does NOT touch user/group
membership (see README.md's "Vulkan / render group access" section for
that, which is host/GPU-specific enough that it isn't safe to automate
here).
"""
import os
import subprocess

from ._common import REPO_ROOT, die

DEPLOY_DIR = REPO_ROOT / "deploy"
TEMPLATE_SRC = DEPLOY_DIR / "llama-server@.service"
LOGROTATE_SRC = DEPLOY_DIR / "llama-server.logrotate"
RESTART_TEMPLATE_SERVICE_SRC = DEPLOY_DIR / "llama-server-restart@.service"
RESTART_TEMPLATE_TIMER_SRC = DEPLOY_DIR / "llama-server-restart@.timer"
TEMPLATE_DEST = "/etc/systemd/system/llama-server@.service"
LOGROTATE_DEST = "/etc/logrotate.d/llama-server"
RESTART_TEMPLATE_SERVICE_DEST = "/etc/systemd/system/llama-server-restart@.service"
RESTART_TEMPLATE_TIMER_DEST = "/etc/systemd/system/llama-server-restart@.timer"

# Units an older clone of this repo may have installed that no longer exist
# here -- init offers to remove them (see "Migrating off llama-server.service"
# in README.md) so a stale unit doesn't linger in `systemctl list-units`.
RETIRED_UNIT_DESTS = [
    "/etc/systemd/system/llama-server.service",
    "/etc/systemd/system/llama-server-restart.service",
    "/etc/systemd/system/llama-server-restart.timer",
]


def _run(cmd, dry_run):
    print("+ " + " ".join(cmd))
    if dry_run:
        return
    result = subprocess.run(cmd)
    if result.returncode != 0:
        die(f"command failed: {' '.join(cmd)}")


def run(args):
    required = [TEMPLATE_SRC, LOGROTATE_SRC, RESTART_TEMPLATE_SERVICE_SRC, RESTART_TEMPLATE_TIMER_SRC]
    for src in required:
        if not src.is_file():
            die(f"missing {src.relative_to(REPO_ROOT)}")

    installs = [
        (TEMPLATE_SRC, TEMPLATE_DEST),
        (LOGROTATE_SRC, LOGROTATE_DEST),
        (RESTART_TEMPLATE_SERVICE_SRC, RESTART_TEMPLATE_SERVICE_DEST),
        (RESTART_TEMPLATE_TIMER_SRC, RESTART_TEMPLATE_TIMER_DEST),
    ]

    stale = [dest for dest in RETIRED_UNIT_DESTS if os.path.exists(dest)]

    print("This will use sudo to:")
    for src, dest in installs:
        print(f"  copy {src.relative_to(REPO_ROOT)} -> {dest}")
    print("  systemctl daemon-reload")
    if stale:
        print()
        print("It also found retired unit(s) from an older clone of this repo:")
        for dest in stale:
            print(f"  {dest}")
        print("These are no longer installed by this repo (see README.md's")
        print("'Migrating off llama-server.service') -- if a service by that")
        print("name is still running, stop/disable it first:")
        print("  sudo systemctl disable --now llama-server")
        print("Then remove the stale file(s) by hand:")
        for dest in stale:
            print(f"  sudo rm {dest}")
        print("(init does not remove them for you -- deleting a unit file")
        print("out from under a running service is not something to automate.)")
    print()
    print("It will NOT start or enable any service -- every preset runs as")
    print("its own named service now, so there's no single default to enable.")
    print("It will NOT touch user/group membership (see README.md for")
    print("Vulkan/render group setup). The periodic-restart timer is")
    print("installed but left disabled -- opt in per-preset (see 'Next' below).")
    print()

    if not args.dry_run and not args.yes:
        reply = input("Proceed? [y/N] ")
        if reply.strip().lower() != "y":
            print("Aborted.")
            return

    for src, dest in installs:
        _run(["sudo", "cp", str(src), dest], args.dry_run)
    _run(["sudo", "systemctl", "daemon-reload"], args.dry_run)

    print()
    print("Dry-run complete, nothing changed." if args.dry_run else "Done.")
    print()
    print("Next:")
    print("  ./llama-tool.py engine install")
    print("  ./llama-tool.py models download <org>/<repo> <file.gguf>")
    print("  ./llama-tool.py services enable <preset> --now")
    print()
    print("That last step also arms the preset's daily 03:00 restart timer")
    print("(clears accumulated memory) in the same call -- see")
    print("`llama-tool.py services --help` / README.md's 'Debugging / status'.")


def add_arguments(parser):
    parser.add_argument("-y", "--yes", action="store_true", help="Skip the confirmation prompt")
    parser.add_argument("-n", "--dry-run", action="store_true", help="Print what would be done without doing it")
