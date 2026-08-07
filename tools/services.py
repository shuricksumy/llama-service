"""llama-tool.py services -- list/restart the llama-server systemd unit(s).

Covers `llama-server.service` (the ACTIVE_PRESET instance) and any loaded
`llama-server@<preset>.service` instances -- not the `llama-server-restart[@]`
timer/oneshot pairs (those restart the units below on their own schedule,
they aren't something you'd restart directly) and not `llama-mem-report.service`
(a separate diagnostics tool, not an inference server). `list` needs no
privileges; `restart` shells out to `sudo systemctl restart`.
"""
import subprocess

from ._common import die

UNIT_PATTERNS = ["llama-server.service", "llama-server@*.service"]


def _list_units():
    cmd = [
        "systemctl", "list-units", "--all", "--type=service",
        "--no-legend", "--plain", "--no-pager", *UNIT_PATTERNS,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except FileNotFoundError:
        die("systemctl not found -- this command only works on a systemd host")

    if result.returncode != 0 and not result.stdout.strip():
        die(f"systemctl list-units failed: {result.stderr.strip()}")

    units = []
    for line in result.stdout.splitlines():
        parts = line.split(None, 4)
        if len(parts) < 4:
            continue
        unit, load, active, sub = parts[:4]
        desc = parts[4] if len(parts) > 4 else ""
        units.append({"unit": unit, "load": load, "active": active, "sub": sub, "desc": desc})
    units.sort(key=lambda u: u["unit"])
    return units


def _print_units(units):
    if not units:
        print("No llama-server units are loaded on this host.")
        print("(A unit only shows up here once it's been started/enabled at least once this boot.)")
        return
    width = max(len(u["unit"]) for u in units)
    for i, u in enumerate(units, 1):
        state = f"{u['active']} {u['sub']}"
        print(f"{i}) {u['unit']:<{width}}  {state:<16} {u['desc']}")


def _preset_alias(unit):
    """`llama-server@qwen3.5-9b.service` -> `qwen3.5-9b`, else None."""
    prefix, suffix = "llama-server@", ".service"
    if unit.startswith(prefix) and unit.endswith(suffix):
        return unit[len(prefix):-len(suffix)]
    return None


def _resolve_selection(units, selectors):
    if not selectors:
        return units

    by_index = {str(i): u for i, u in enumerate(units, 1)}
    by_name = {u["unit"]: u for u in units}
    for u in units:
        alias = _preset_alias(u["unit"])
        if alias is not None:
            by_name.setdefault(alias, u)

    chosen, seen = [], set()
    for sel in selectors:
        u = by_index.get(sel) or by_name.get(sel)
        if u is None:
            die(f"no unit matches '{sel}' -- run `llama-tool.py services list` to see valid numbers/names")
        if u["unit"] not in seen:
            chosen.append(u)
            seen.add(u["unit"])
    return chosen


def _run(cmd, dry_run):
    print("+ " + " ".join(cmd))
    if dry_run:
        return
    result = subprocess.run(cmd)
    if result.returncode != 0:
        die(f"command failed: {' '.join(cmd)}")


def cmd_list(args):
    _print_units(_list_units())


def cmd_restart(args):
    units = _list_units()
    if not units:
        die("no llama-server units are loaded on this host -- nothing to restart")

    targets = _resolve_selection(units, args.selector)

    print("This will use sudo to restart:")
    for u in targets:
        print(f"  {u['unit']}")
    print()

    if not args.dry_run and not args.yes:
        reply = input("Proceed? [y/N] ")
        if reply.strip().lower() != "y":
            print("Aborted.")
            return

    _run(["sudo", "systemctl", "restart", *(u["unit"] for u in targets)], args.dry_run)
    print("Dry-run complete, nothing changed." if args.dry_run else "Done.")


def run(args):
    if args.services_command == "list":
        cmd_list(args)
    elif args.services_command == "restart":
        cmd_restart(args)


def add_arguments(parser):
    sub = parser.add_subparsers(dest="services_command", required=True)

    sub.add_parser("list", help="List llama-server systemd units on this host, numbered")

    p_restart = sub.add_parser("restart", help="Restart one, several, or (default) all llama-server units")
    p_restart.add_argument(
        "selector", nargs="*",
        help="Number(s) from `services list`, unit name(s), or preset name(s) (e.g. 1 3, or "
             "llama-server@qwen3.5-9b, or qwen3.5-9b). Omit to restart every listed unit.",
    )
    p_restart.add_argument("-y", "--yes", action="store_true", help="Skip the confirmation prompt")
    p_restart.add_argument("-n", "--dry-run", action="store_true", help="Print what would be restarted without doing it")
