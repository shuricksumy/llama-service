"""llama-tool.py memwatch -- per-preset memory watchdog.

`check` is what deploy/llama-memwatch.timer runs every minute: for every
active llama-server@<preset>.service whose preset sets a limit
(LLAMA_MEMWATCH_SOFT_MB / LLAMA_MEMWATCH_HARD_MB, see llama.env), it reads
the instance's system-RAM footprint -- RAM = cgroup anon + GPU GTT (see
below) -- and restarts it if it's over:

  hard  restart immediately.
  soft  restart only once every slot is idle (GET /slots, is_processing),
        so an in-flight request isn't cut off -- or after
        LLAMA_MEMWATCH_GRACE_MIN minutes of staying busy, whichever's first.

RAM is the cgroup's `anon` (memory.stat) plus the process's resident GTT
from amdgpu's per-client DRM fdinfo (/proc/<pid>/fdinfo, deduplicated by
drm-client-id). anon, not memory.current: the latter includes the
page-cached model file, which isn't growth and would trip the limit on
load. GTT because GPU buffers in GTT are system RAM but aren't charged to
the cgroup -- on an iGPU/APU that's where the weights, KV cache and compute
buffers live, i.e. most of the instance's footprint. VRAM isn't counted
(dedicated GPU memory, not system RAM); drivers without DRM fdinfo (e.g.
NVIDIA's proprietary one) just report no GTT. Limits are per preset only -- no host-wide total,
since a per-instance breach always says which instance to restart (and the
sum of every preset's limit bounds the host).

"Restart" is a SIGTERM to the instance's main PID, not `systemctl
restart`: llama-server shuts down cleanly and the unit's Restart=always
brings it back, so this runs as the same unprivileged user as llama-server
(no root/sudo). `status` is a read-only table of the same numbers.
"""
import json
import os
import signal
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from ._common import die, human_size, warn
from ._env import preset_path, resolve_env
from .services import CORE_PATTERNS, _systemctl_loaded

# Don't act on an instance that started less than this long ago -- if a
# limit is set below an instance's baseline, this turns a restart-every-
# minute loop into one restart per MIN_UPTIME_SEC, and the log says why.
MIN_UPTIME_SEC = 300

TIMER_UNIT = "llama-memwatch.timer"

# RuntimeDirectory= (deploy/llama-memwatch.service) sets RUNTIME_DIRECTORY;
# the fallback is only for running `check` by hand.
STATE_DIR = Path(os.environ.get("RUNTIME_DIRECTORY", "/run/llama-memwatch"))


def _log(preset, message):
    print(f"memwatch: {preset}: {message}")


def _parse_mb(env, var, preset):
    raw = env.get(var, "")
    if not raw:
        return None
    try:
        return int(raw) * 1024 * 1024
    except ValueError:
        warn(f"{preset}: {var}={raw!r} is not an integer (MB) -- ignoring it")
        return None


def _unit_props(unit):
    """{MainPID, ControlGroup, ActiveEnterTimestampMonotonic} via systemctl show."""
    result = subprocess.run(
        ["systemctl", "show", "-p", "MainPID,ControlGroup,ActiveEnterTimestampMonotonic", unit],
        capture_output=True, text=True, timeout=10,
    )
    props = {}
    for line in result.stdout.splitlines():
        key, _, value = line.partition("=")
        props[key] = value
    return props


def _cgroup_memory(control_group):
    """(anon, file) bytes from cgroup v2 memory.stat, or None if unreadable
    (cgroup v1 host, or the unit stopped between list-units and here)."""
    try:
        text = Path(f"/sys/fs/cgroup{control_group}/memory.stat").read_text()
    except OSError:
        return None
    stat = {}
    for line in text.splitlines():
        key, _, value = line.partition(" ")
        stat[key] = value
    try:
        return int(stat["anon"]), int(stat.get("file", 0))
    except (KeyError, ValueError):
        return None


def _gpu_memory(pid):
    """(gtt, vram) resident bytes across the process's DRM clients, from
    /proc/<pid>/fdinfo. Every fd onto the same client repeats the same
    numbers, so dedupe by (drm-pdev, drm-client-id). (0, 0) if the process
    has no DRM fds or the driver doesn't report per-client memory."""
    clients = {}
    try:
        fds = list(Path(f"/proc/{pid}/fdinfo").iterdir())
    except OSError:
        return 0, 0
    for fd in fds:
        try:
            text = fd.read_text()
        except OSError:
            continue
        if "drm-driver:" not in text:
            continue
        info = {}
        for line in text.splitlines():
            key, _, value = line.partition(":")
            info[key.strip()] = value.strip()
        key = (info.get("drm-pdev"), info.get("drm-client-id"))
        if key[1] is None or key in clients:
            continue

        def kib(*names):
            # drm-resident-<region> is the current name; drm-memory-<region>
            # the older (pre-6.x kernels) one for the same number.
            for name in names:
                value = info.get(name, "")
                if value.endswith(" KiB"):
                    try:
                        return int(value[:-4]) * 1024
                    except ValueError:
                        pass
            return 0

        clients[key] = (
            kib("drm-resident-gtt", "drm-memory-gtt"),
            kib("drm-resident-vram", "drm-memory-vram"),
        )
    return sum(c[0] for c in clients.values()), sum(c[1] for c in clients.values())


def _measure(props):
    """{ram, anon, file, gtt, vram} bytes, or None if the cgroup's
    memory.stat can't be read."""
    mem = _cgroup_memory(props.get("ControlGroup", ""))
    if mem is None:
        return None
    anon, file = mem
    gtt, vram = _gpu_memory(int(props.get("MainPID", "0") or 0))
    return {"ram": anon + gtt, "anon": anon, "file": file, "gtt": gtt, "vram": vram}


def _slots_busy(env):
    """True/False = some/no slot is processing; None = couldn't tell
    (endpoint disabled via --no-slots, auth failure, not listening yet)."""
    host = env.get("LLAMA_HOST", "")
    if host in ("", "0.0.0.0", "::"):
        host = "127.0.0.1"
    port = env.get("LLAMA_PORT", "")
    if not port:
        return None
    req = urllib.request.Request(f"http://{host}:{port}/slots")
    api_key = env.get("LLAMA_API_KEY", "")
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            slots = json.loads(resp.read())
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return None
    if not isinstance(slots, list):
        return None
    return any(s.get("is_processing") for s in slots if isinstance(s, dict))


def _instances():
    """[(preset, unit, env, props)] for every active llama-server@ instance
    whose preset still exists, sorted by preset name."""
    loaded = _systemctl_loaded(CORE_PATTERNS)
    prefix, suffix = "llama-server@", ".service"
    out = []
    for unit in sorted(loaded):
        if loaded[unit]["active"] != "active":
            continue
        preset = unit[len(prefix):-len(suffix)]
        if not preset_path(preset).is_file():
            warn(f"{unit} is running but presets/{preset}.env no longer exists -- skipping it")
            continue
        out.append((preset, unit, resolve_env(preset), _unit_props(unit)))
    return out


def _uptime_sec(props):
    try:
        entered_us = int(props.get("ActiveEnterTimestampMonotonic", "0"))
    except ValueError:
        return None
    if not entered_us:
        return None
    return time.clock_gettime(time.CLOCK_MONOTONIC) - entered_us / 1e6


def _read_pending(preset):
    try:
        return float((STATE_DIR / preset).read_text())
    except (OSError, ValueError):
        return None


def _write_pending(preset, since, dry_run):
    if dry_run:
        return
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        (STATE_DIR / preset).write_text(str(since))
    except OSError as e:
        warn(f"{preset}: can't record pending soft-limit state in {STATE_DIR}: {e}")


def _clear_pending(preset, dry_run):
    if dry_run:
        return
    try:
        (STATE_DIR / preset).unlink()
    except FileNotFoundError:
        pass
    except OSError as e:
        warn(f"{preset}: can't clear pending state in {STATE_DIR}: {e}")


def _restart(preset, props, reason, dry_run):
    pid = int(props.get("MainPID", "0") or 0)
    _log(preset, f"{reason} -> restarting" + (" (dry run, not restarted)" if dry_run else ""))
    if dry_run:
        return
    if not pid:
        warn(f"{preset}: no MainPID -- can't restart")
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except PermissionError:
        warn(
            f"{preset}: not allowed to signal PID {pid} -- llama-memwatch.service must run as "
            f"the same User= as llama-server@.service"
        )
        return
    except ProcessLookupError:
        pass
    _clear_pending(preset, dry_run)


def cmd_check(args):
    now = time.time()
    for preset, unit, env, props in _instances():
        soft = _parse_mb(env, "LLAMA_MEMWATCH_SOFT_MB", preset)
        hard = _parse_mb(env, "LLAMA_MEMWATCH_HARD_MB", preset)
        if soft is None and hard is None:
            continue

        m = _measure(props)
        if m is None:
            warn(f"{preset}: can't read memory.stat for {unit} (needs cgroup v2) -- skipping")
            continue
        ram = m["ram"]
        what = f"ram {human_size(ram)} (anon {human_size(m['anon'])} + gtt {human_size(m['gtt'])})"

        over_hard = hard is not None and ram >= hard
        over_soft = soft is not None and ram >= soft
        if not (over_hard or over_soft):
            _clear_pending(preset, args.dry_run)
            continue

        uptime = _uptime_sec(props)
        if uptime is not None and uptime < MIN_UPTIME_SEC:
            limit = hard if over_hard else soft
            _log(
                preset,
                f"{what} >= {'hard' if over_hard else 'soft'} {human_size(limit)}, "
                f"but it started only {uptime:.0f}s ago -- skipping (is the limit below its baseline? "
                f"see `llama-tool.py memwatch status`)",
            )
            continue

        if over_hard:
            _restart(preset, props, f"{what} >= hard {human_size(hard)}", args.dry_run)
            continue

        grace_min = env.get("LLAMA_MEMWATCH_GRACE_MIN", "") or "0"
        try:
            grace_sec = float(grace_min) * 60
        except ValueError:
            warn(f"{preset}: LLAMA_MEMWATCH_GRACE_MIN={grace_min!r} is not a number -- using 0")
            grace_sec = 0

        busy = _slots_busy(env)
        base = f"{what} >= soft {human_size(soft)}"
        if busy is False:
            _restart(preset, props, f"{base}, all slots idle", args.dry_run)
            continue

        since = _read_pending(preset)
        if since is None:
            since = now
            _write_pending(preset, since, args.dry_run)
        waited = now - since
        why = "slots busy" if busy else "slot state unknown (/slots unreachable)"
        if waited >= grace_sec:
            _restart(preset, props, f"{base}, {why} for {waited / 60:.0f}min (grace {grace_min}min)", args.dry_run)
        else:
            _log(preset, f"{base}, {why} -- waiting for idle ({waited / 60:.0f}/{grace_min}min)")


def cmd_status(args):
    instances = _instances()
    if not instances:
        print("No llama-server@* instances are running.")
        return

    rows = [("PRESET", "UPTIME", "RAM", "ANON", "GTT", "VRAM", "FILE", "SOFT", "HARD", "SLOTS", "PENDING")]
    for preset, unit, env, props in instances:
        soft = _parse_mb(env, "LLAMA_MEMWATCH_SOFT_MB", preset)
        hard = _parse_mb(env, "LLAMA_MEMWATCH_HARD_MB", preset)
        m = _measure(props)
        uptime = _uptime_sec(props)
        busy = _slots_busy(env)
        since = _read_pending(preset)
        rows.append((
            preset,
            f"{uptime / 60:.0f}min" if uptime is not None else "?",
            *((human_size(m[k]) for k in ("ram", "anon", "gtt", "vram", "file")) if m else ("?",) * 5),
            human_size(soft) if soft is not None else "-",
            human_size(hard) if hard is not None else "-",
            {True: "busy", False: "idle", None: "?"}[busy],
            f"{(time.time() - since) / 60:.0f}min" if since is not None else "-",
        ))

    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    for r in rows:
        print("  ".join(cell.ljust(w) for cell, w in zip(r, widths)).rstrip())

    print()
    print("RAM = ANON + GTT is what the limits compare against. GTT is GPU memory in system RAM")
    print("(on an iGPU: weights + KV + compute buffers); VRAM (dedicated GPU memory) and FILE")
    print("(page cache, mostly the model file) aren't counted.")
    active = subprocess.run(
        ["systemctl", "is-active", TIMER_UNIT], capture_output=True, text=True,
    ).stdout.strip()
    if active != "active":
        print(f"Note: {TIMER_UNIT} is not active, so limits aren't being enforced --")
        print("`llama-tool.py services enable llama-memwatch --now` to arm it.")


def run(args):
    {
        "check": cmd_check,
        "status": cmd_status,
    }[args.memwatch_command](args)


def add_arguments(parser):
    sub = parser.add_subparsers(dest="memwatch_command", required=True)

    p_check = sub.add_parser(
        "check",
        help="One pass over running instances, restarting any over its limit (what llama-memwatch.timer runs)",
    )
    p_check.add_argument(
        "-n", "--dry-run", action="store_true",
        help="Report what would be restarted without restarting anything",
    )

    sub.add_parser(
        "status",
        help="Show each running instance's memory vs. its limits, slot state, and pending soft-limit wait",
    )
