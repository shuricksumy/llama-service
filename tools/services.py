"""llama-tool.py services -- list/start/stop/enable/disable/restart the
systemd units this repo installs: `llama-server.service` (the ACTIVE_PRESET
instance), any `llama-server@<preset>.service` instances, the
`llama-server-restart[@]` periodic-restart timer/oneshot pairs, and
`llama-mem-report.service`.

`list` needs no privileges; every other subcommand shells out to
`sudo systemctl <verb>`. `start`/`stop`/`enable`/`disable` take a single
target (preset name, `llama-server`/`llama-mem-report`, or a full unit name)
and work even if that unit has never been loaded yet -- that's how you bring
up a *new* preset as a service. `restart` instead works off the numbered
`list` output (by number, unit name, or preset name) since it only makes
sense for units that are already loaded; with no selector it restarts every
loaded `llama-server`/`llama-server@*` instance (not the timers or
mem-report, which restarting doesn't meaningfully do anything for as a
group default).
"""
import subprocess

from ._common import die

CORE_PATTERNS = ["llama-server.service", "llama-server@*.service"]
TIMER_PATTERNS = [
    "llama-server-restart.service", "llama-server-restart.timer",
    "llama-server-restart@*.service", "llama-server-restart@*.timer",
]
REPORT_PATTERNS = ["llama-mem-report.service"]
ALL_PATTERNS = CORE_PATTERNS + TIMER_PATTERNS + REPORT_PATTERNS

SPECIAL_UNITS = {
    "llama-server": "llama-server.service",
    "llama-mem-report": "llama-mem-report.service",
}


def _category(unit):
    if unit.startswith("llama-server-restart"):
        return 1
    if unit.startswith("llama-mem-report"):
        return 2
    return 0  # llama-server.service / llama-server@*.service


def _list_units(patterns):
    cmd = [
        "systemctl", "list-units", "--all", "--type=service", "--type=timer",
        "--no-legend", "--plain", "--no-pager", *patterns,
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
    units.sort(key=lambda u: (_category(u["unit"]), u["unit"]))
    return units


def _print_units(units):
    if not units:
        print("No llama-server/llama-mem-report units are loaded on this host.")
        print("(A unit only shows up here once it's been started/enabled at least once this boot --")
        print(" use `services start <preset>` or `services enable <preset> --now` to bring one up.)")
        return

    width = max(len(u["unit"]) for u in units)
    sections = [
        ("llama-server", [u for u in units if _category(u["unit"]) == 0]),
        ("Periodic restart timers", [u for u in units if _category(u["unit"]) == 1]),
        ("Memory / GPU report", [u for u in units if _category(u["unit"]) == 2]),
    ]
    i = 1
    first = True
    for label, section_units in sections:
        if not section_units:
            continue
        if not first:
            print()
        first = False
        print(f"{label}:")
        for u in section_units:
            state = f"{u['active']} {u['sub']}"
            print(f"  {i}) {u['unit']:<{width}}  {state:<16} {u['desc']}")
            i += 1


def _resolve_target(name):
    """Bare preset name -> llama-server@<name>.service; 'llama-server' /
    'llama-mem-report' -> their exact unit; anything already ending in
    .service/.timer (a full unit name, e.g. llama-server-restart@foo.timer)
    is passed through as-is. Checking the suffix rather than "contains a
    dot" matters because preset names themselves often have dots in them
    (e.g. qwen3.5-9b, qwen3-embedding-0.6b).
    """
    if not name or name in SPECIAL_UNITS:
        return SPECIAL_UNITS.get(name, "llama-server.service")
    if name.endswith(".service") or name.endswith(".timer"):
        return name
    return f"llama-server@{name}.service"


def _resolve_selection(units, selectors):
    if not selectors:
        return units

    by_index = {str(i): u for i, u in enumerate(units, 1)}
    by_name = {u["unit"]: u for u in units}

    chosen, seen = [], set()
    for sel in selectors:
        u = by_index.get(sel) or by_name.get(sel) or by_name.get(_resolve_target(sel))
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


def _confirm_and_run(cmd, verb, unit, dry_run, yes):
    print(f"This will use sudo to {verb}:")
    print(f"  {unit}")
    print()
    if not dry_run and not yes:
        reply = input("Proceed? [y/N] ")
        if reply.strip().lower() != "y":
            print("Aborted.")
            return
    _run(cmd, dry_run)
    print("Dry-run complete, nothing changed." if dry_run else "Done.")


def cmd_list(args):
    _print_units(_list_units(ALL_PATTERNS))


def cmd_restart(args):
    units = _list_units(ALL_PATTERNS)
    if args.selector:
        if not units:
            die("no matching units are loaded on this host -- nothing to restart")
        targets = _resolve_selection(units, args.selector)
    else:
        targets = [u for u in units if _category(u["unit"]) == 0]
        if not targets:
            die(
                "no llama-server.service/llama-server@* units are loaded -- nothing to restart.\n"
                "Pass a number/name from `services list` to restart a specific unit (e.g. a timer or "
                "llama-mem-report), or `services start <preset>` to bring up a new one."
            )

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


def cmd_start(args):
    unit = _resolve_target(args.target)
    _confirm_and_run(["sudo", "systemctl", "start", unit], "start", unit, args.dry_run, args.yes)


def cmd_stop(args):
    unit = _resolve_target(args.target)
    _confirm_and_run(["sudo", "systemctl", "stop", unit], "stop", unit, args.dry_run, args.yes)


def cmd_enable(args):
    unit = _resolve_target(args.target)
    cmd = ["sudo", "systemctl", "enable"] + (["--now"] if args.now else []) + [unit]
    verb = "enable --now (start immediately + on boot)" if args.now else "enable (on boot only, not started now)"
    _confirm_and_run(cmd, verb, unit, args.dry_run, args.yes)


def cmd_disable(args):
    unit = _resolve_target(args.target)
    cmd = ["sudo", "systemctl", "disable"] + (["--now"] if args.now else []) + [unit]
    verb = "disable --now (stop immediately + on boot)" if args.now else "disable (stops starting on boot, doesn't stop it now)"
    _confirm_and_run(cmd, verb, unit, args.dry_run, args.yes)


def run(args):
    {
        "list": cmd_list,
        "restart": cmd_restart,
        "start": cmd_start,
        "stop": cmd_stop,
        "enable": cmd_enable,
        "disable": cmd_disable,
    }[args.services_command](args)


def _add_confirm_flags(parser):
    parser.add_argument("-y", "--yes", action="store_true", help="Skip the confirmation prompt")
    parser.add_argument("-n", "--dry-run", action="store_true", help="Print the systemctl command without running it")


def add_arguments(parser):
    sub = parser.add_subparsers(dest="services_command", required=True)

    sub.add_parser("list", help="List llama-server/restart-timer/mem-report systemd units, numbered")

    p_restart = sub.add_parser(
        "restart",
        help="Restart llama-server instance(s) (default: all loaded ones), or any unit from `list` by number/name",
    )
    p_restart.add_argument(
        "selector", nargs="*",
        help="Number(s)/name(s) from `services list` (e.g. 1 3, qwen3.5-9b, llama-mem-report). "
             "Omit to restart every loaded llama-server/llama-server@* instance.",
    )
    _add_confirm_flags(p_restart)

    def _target_arg(p, extra=""):
        p.add_argument(
            "target",
            help="Preset name (-> llama-server@<preset>.service), 'llama-server' for the ACTIVE_PRESET "
                 f"instance, or a full unit name (llama-mem-report, llama-server-restart.timer, ...).{extra}",
        )

    p_start = sub.add_parser("start", help="Start a unit now (not persisted across reboot -- see 'enable' for that)")
    _target_arg(p_start)
    _add_confirm_flags(p_start)

    p_stop = sub.add_parser("stop", help="Stop a running unit now (leaves its enabled/disabled state untouched)")
    _target_arg(p_stop)
    _add_confirm_flags(p_stop)

    p_enable = sub.add_parser("enable", help="Enable a unit to start on boot")
    _target_arg(p_enable)
    p_enable.add_argument("--now", action="store_true", help="Also start it immediately")
    _add_confirm_flags(p_enable)

    p_disable = sub.add_parser("disable", help="Disable a unit from starting on boot")
    _target_arg(p_disable)
    p_disable.add_argument("--now", action="store_true", help="Also stop it immediately")
    _add_confirm_flags(p_disable)
