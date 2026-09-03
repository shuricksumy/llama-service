"""llama-tool.py services -- single place to see and manage every preset's
systemd lifecycle: `llama-server@<preset>.service` for each preset under
`presets/*.env` (whether or not it's currently running -- every preset runs
as its own named service, there's no anonymous default instance), the
paired `llama-server-restart@` periodic-restart timers, and
`llama-mem-report.service`.

`list`/`status` need no privileges; every other subcommand shells out to
`sudo systemctl <verb>`. `start`/`stop`/`enable`/`disable`/`status` take
target(s) -- a number from the last `list`, a preset name (-> `llama-server@
<preset>.service`), `llama-mem-report`, or a full unit name -- and (except
`status`) work even if that unit has never been loaded yet, which is how a
brand new preset gets turned into a running service for the first time.

`enable`/`disable` additionally bring the preset's `llama-server-restart@`
*timer* along for the ride (enabling a preset also arms its daily restart;
disabling it disarms that restart too) -- pass `--no-restart-timer` to
manage the timer separately instead. `start`/`stop` are deliberately not
paired this way: they're one-off actions, not a change to what should
persist across reboots.

`restart` works off the same catalog `list` prints (by number, unit name,
or preset name); with no selector it restarts every *currently running*
llama-server@* instance (not timers or mem-report -- select those
explicitly by number/name if that's what you want restarted).
"""
import os
import subprocess
import sys

from ._common import die, warn
from ._env import list_presets

_RED = "\033[31m"
_YELLOW = "\033[33m"
_GREEN = "\033[32m"
_RESET = "\033[0m"


def _color_enabled():
    """Disabled when not a real terminal (piped/redirected) or when NO_COLOR
    is set (https://no-color.org) -- never inject escape codes into logs."""
    return sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def _state_color(active, sub, loaded):
    """None = don't color it (a normal/expected state, not a problem)."""
    if not loaded:
        return None  # never started -- that's just "off", not broken
    if active == "failed" or sub == "failed":
        return _RED
    if active == "activating" and "auto-restart" in sub:
        return _RED  # crash-looping
    if active in ("activating", "deactivating"):
        return _YELLOW  # mid-transition -- not necessarily broken, worth a look
    if active == "active":
        return _GREEN
    return None

CORE_PATTERNS = ["llama-server@*.service"]
TIMER_PATTERNS = ["llama-server-restart@*.service", "llama-server-restart@*.timer"]
REPORT_PATTERNS = ["llama-mem-report.service"]
ALL_PATTERNS = CORE_PATTERNS + TIMER_PATTERNS + REPORT_PATTERNS

SPECIAL_UNITS = {
    "llama-mem-report": "llama-mem-report.service",
}


def _systemctl_loaded(patterns):
    """{unit_name: {active, sub, desc}} for every unit matching `patterns`
    that systemd currently knows about (started/enabled at least once)."""
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

    loaded = {}
    for line in result.stdout.splitlines():
        parts = line.split(None, 4)
        if len(parts) < 4:
            continue
        unit, _load, active, sub = parts[:4]
        desc = parts[4] if len(parts) > 4 else ""
        loaded[unit] = {"active": active, "sub": sub, "desc": desc}
    return loaded


def _paired_timer(unit):
    """llama-server@<preset>.service -> llama-server-restart@<preset>.timer;
    anything else (mem-report, a timer itself, ...) -> None.
    """
    prefix, suffix = "llama-server@", ".service"
    if unit.startswith(prefix) and unit.endswith(suffix):
        preset = unit[len(prefix):-len(suffix)]
        return f"llama-server-restart@{preset}.timer"
    return None


def _build_catalog():
    """Ordered list of every unit this repo cares about, whether or not
    it's currently loaded: one row per preset in presets/*.env, then loaded
    restart timers, then a loaded mem-report. Each entry: unit, label,
    category (0=llama-server, 1=timer, 2=report), loaded, active, sub,
    timer_note (category 0 only).
    """
    loaded = _systemctl_loaded(ALL_PATTERNS)
    catalog = []

    def add(unit, label, category):
        lu = loaded.get(unit)
        entry = {
            "unit": unit, "label": label, "category": category,
            "loaded": lu is not None,
            "active": lu["active"] if lu else "",
            "sub": lu["sub"] if lu else "",
            "timer_note": "",
        }
        if category == 0:
            paired = _paired_timer(unit)
            tu = loaded.get(paired) if paired else None
            entry["timer_note"] = "restart timer: armed" if (tu and tu["active"] == "active") else "restart timer: off"
        catalog.append(entry)

    for name, _desc in list_presets():
        add(f"llama-server@{name}.service", name, 0)

    for unit in sorted(u for u in loaded if u.startswith("llama-server-restart")):
        add(unit, unit, 1)

    for unit in sorted(u for u in loaded if u.startswith("llama-mem-report")):
        add(unit, unit, 2)

    return catalog


def _print_catalog(catalog):
    width = max((len(e["label"]) for e in catalog), default=0)
    sections = [
        ("llama-server", [e for e in catalog if e["category"] == 0]),
        ("Periodic restart timers", [e for e in catalog if e["category"] == 1]),
        ("Memory / GPU report", [e for e in catalog if e["category"] == 2]),
    ]
    colorize = _color_enabled()
    i = 1
    first = True
    for title, entries in sections:
        if not entries:
            continue
        if not first:
            print()
        first = False
        print(f"{title}:")
        for e in entries:
            state = f"{e['active']} {e['sub']}" if e["loaded"] else "not running"
            padded = f"{state:<15}"
            if colorize:
                color = _state_color(e["active"], e["sub"], e["loaded"])
                if color:
                    padded = f"{color}{padded}{_RESET}"
            extra = f"  [{e['timer_note']}]" if e["timer_note"] else ""
            print(f"  {i}) {e['label']:<{width}}  {padded}{extra}")
            i += 1
    if not any(e["loaded"] for e in catalog):
        print()
        print("Nothing above is running yet -- `services start <preset>` or")
        print("`services enable <preset> --now` to bring one up.")


def _resolve_target(name):
    """Bare preset name -> llama-server@<name>.service; 'llama-mem-report'
    -> its exact unit; anything already ending in .service/.timer (a full
    unit name, e.g. llama-server-restart@foo.timer) is passed through as-is.
    Checking the suffix rather than "contains a dot" matters because preset
    names themselves often have dots in them (e.g. qwen3.5-9b,
    qwen3-embedding-0.6b).
    """
    if name in SPECIAL_UNITS:
        return SPECIAL_UNITS[name]
    if not name:
        die("no preset/unit name given")
    if name.endswith(".service") or name.endswith(".timer"):
        return name
    return f"llama-server@{name}.service"


def _resolve_single(catalog, selector):
    if selector.isdigit():
        idx = int(selector)
        if 1 <= idx <= len(catalog):
            return catalog[idx - 1]["unit"]
        die(f"no unit at position {idx} -- run `llama-tool.py services list` to see valid numbers")
    for e in catalog:
        if e["unit"] == selector:
            return e["unit"]
    return _resolve_target(selector)


def _resolve_multi(catalog, selectors):
    if not selectors:
        return [e["unit"] for e in catalog]
    units, seen = [], set()
    for sel in selectors:
        u = _resolve_single(catalog, sel)
        if u not in seen:
            units.append(u)
            seen.add(u)
    return units


def _run(cmd, dry_run):
    print("+ " + " ".join(cmd))
    if dry_run:
        return True
    result = subprocess.run(cmd)
    if result.returncode != 0:
        die(f"command failed: {' '.join(cmd)}")
    return True


def _run_soft(cmd, dry_run):
    """Like _run but reports failure instead of dying -- used for the
    paired restart-timer step so a timer hiccup doesn't undo the primary
    (already-succeeded) enable/disable of the preset itself."""
    print("+ " + " ".join(cmd))
    if dry_run:
        return True
    result = subprocess.run(cmd)
    return result.returncode == 0


def cmd_list(args):
    _print_catalog(_build_catalog())


def cmd_status(args):
    units = _resolve_multi(_build_catalog(), args.selector)
    cmd = ["systemctl", "status", *units]
    if args.lines is not None:
        cmd += ["-n", str(args.lines)]
    try:
        result = subprocess.run(cmd)
    except FileNotFoundError:
        die("systemctl not found -- this command only works on a systemd host")
    # systemctl status exits non-zero for a stopped/failed unit by design --
    # that's informative output, not a tool failure, so just propagate its
    # exit code instead of treating it as an error via die().
    raise SystemExit(result.returncode)


def cmd_restart(args):
    catalog = _build_catalog()
    if args.selector:
        targets = _resolve_multi(catalog, args.selector)
    else:
        targets = [e["unit"] for e in catalog if e["category"] == 0 and e["loaded"] and e["active"] == "active"]
        if not targets:
            die(
                "no llama-server@* instances are currently running -- nothing to restart.\n"
                "Pass a number/name from `services list` to restart a specific unit (e.g. a timer or "
                "llama-mem-report), or `services start <preset>` to bring up a new one."
            )

    print("This will use sudo to restart:")
    for u in targets:
        print(f"  {u}")
    print()

    if not args.dry_run and not args.yes:
        reply = input("Proceed? [y/N] ")
        if reply.strip().lower() != "y":
            print("Aborted.")
            return

    _run(["sudo", "systemctl", "restart", *targets], args.dry_run)
    print("Dry-run complete, nothing changed." if args.dry_run else "Done.")


def cmd_start(args):
    unit = _resolve_single(_build_catalog(), args.target)
    print("This will use sudo to start:")
    print(f"  {unit}")
    print()
    if not args.dry_run and not args.yes:
        reply = input("Proceed? [y/N] ")
        if reply.strip().lower() != "y":
            print("Aborted.")
            return
    _run(["sudo", "systemctl", "start", unit], args.dry_run)
    print("Dry-run complete, nothing changed." if args.dry_run else "Done.")


def cmd_stop(args):
    unit = _resolve_single(_build_catalog(), args.target)
    print("This will use sudo to stop:")
    print(f"  {unit}")
    print()
    if not args.dry_run and not args.yes:
        reply = input("Proceed? [y/N] ")
        if reply.strip().lower() != "y":
            print("Aborted.")
            return
    _run(["sudo", "systemctl", "stop", unit], args.dry_run)
    print("Dry-run complete, nothing changed." if args.dry_run else "Done.")


def _enable_or_disable(args, verb):
    unit = _resolve_single(_build_catalog(), args.target)
    paired = None if args.no_restart_timer else _paired_timer(unit)
    now_suffix = " --now" if args.now else ""

    print(f"This will use sudo to {verb}{now_suffix}:")
    print(f"  {unit}")
    if paired:
        print(f"  {paired}  (paired restart timer -- pass --no-restart-timer to skip this)")
    print()

    if not args.dry_run and not args.yes:
        reply = input("Proceed? [y/N] ")
        if reply.strip().lower() != "y":
            print("Aborted.")
            return

    now_flag = ["--now"] if args.now else []
    _run(["sudo", "systemctl", verb, *now_flag, unit], args.dry_run)

    if paired:
        ok = _run_soft(["sudo", "systemctl", verb, *now_flag, paired], args.dry_run)
        if not ok:
            warn(
                f"{unit} was {verb}d, but {paired} failed to {verb} -- its periodic restart isn't armed. "
                f"If you haven't already, run `./llama-tool.py init` to install the restart-timer templates, "
                f"then retry, or pass --no-restart-timer to manage it separately."
            )

    print("Dry-run complete, nothing changed." if args.dry_run else "Done.")


def cmd_enable(args):
    _enable_or_disable(args, "enable")


def cmd_disable(args):
    _enable_or_disable(args, "disable")


def run(args):
    {
        "list": cmd_list,
        "status": cmd_status,
        "restart": cmd_restart,
        "start": cmd_start,
        "stop": cmd_stop,
        "enable": cmd_enable,
        "disable": cmd_disable,
    }[args.services_command](args)


def _add_confirm_flags(parser):
    parser.add_argument("-y", "--yes", action="store_true", help="Skip the confirmation prompt")
    parser.add_argument("-n", "--dry-run", action="store_true", help="Print the systemctl command(s) without running them")


def add_arguments(parser):
    sub = parser.add_subparsers(dest="services_command", required=True)

    sub.add_parser(
        "list",
        help="List every preset + its restart timer + mem-report, numbered, running or not",
    )

    p_status = sub.add_parser(
        "status", help="Show `systemctl status` for one or more units from `list` (by number/name) -- did it start OK?",
    )
    p_status.add_argument(
        "selector", nargs="+",
        help="Number(s)/name(s) from `services list` (e.g. 1, or qwen3.5-9b, or 1 3 to check several at once).",
    )
    p_status.add_argument("-n", "--lines", type=int, default=None, help="How many recent log lines to show (systemctl's default if omitted)")

    p_restart = sub.add_parser(
        "restart",
        help="Restart running preset instance(s) (default: all of them), or any unit from `list` by number/name",
    )
    p_restart.add_argument(
        "selector", nargs="*",
        help="Number(s)/name(s) from `services list` (e.g. 1 3, qwen3.5-9b, llama-mem-report). "
             "Omit to restart every currently-running llama-server@* instance.",
    )
    _add_confirm_flags(p_restart)

    def _target_arg(p):
        p.add_argument(
            "target",
            help="Number from `services list`, preset name (-> llama-server@<preset>.service), or a full "
                 "unit name (llama-mem-report, llama-server-restart@<preset>.timer, ...).",
        )

    p_start = sub.add_parser("start", help="Start a unit now (not persisted across reboot -- see 'enable' for that)")
    _target_arg(p_start)
    _add_confirm_flags(p_start)

    p_stop = sub.add_parser("stop", help="Stop a running unit now (leaves its enabled/disabled state untouched)")
    _target_arg(p_stop)
    _add_confirm_flags(p_stop)

    p_enable = sub.add_parser(
        "enable",
        help="Enable a preset/unit to start on boot (also arms its daily restart timer, unless --no-restart-timer)",
    )
    _target_arg(p_enable)
    p_enable.add_argument("--now", action="store_true", help="Also start it immediately")
    p_enable.add_argument(
        "--no-restart-timer", action="store_true",
        help="Don't also enable the paired llama-server-restart[@] timer",
    )
    _add_confirm_flags(p_enable)

    p_disable = sub.add_parser(
        "disable",
        help="Disable a preset/unit from starting on boot (also disarms its daily restart timer, unless --no-restart-timer)",
    )
    _target_arg(p_disable)
    p_disable.add_argument("--now", action="store_true", help="Also stop it immediately")
    p_disable.add_argument(
        "--no-restart-timer", action="store_true",
        help="Don't also disable the paired llama-server-restart[@] timer",
    )
    _add_confirm_flags(p_disable)
