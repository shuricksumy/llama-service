"""llama-tool.py cache -- verify/analyze prompt-cache reuse.

`check` is ported from check-cache-reuse.sh (live HTTP check against a
running llama-server or llama-slot-proxy agent path). `stats` is ported
from llama_cache_stats.py (parses llama-server log output for per-request/
per-slot cache reuse across real traffic).
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

from ._common import die
from ._env import resolve_log_file

# --- check: live HTTP verification -----------------------------------------

SYSTEM_PROMPT = (
    "You are a terse test assistant used only to verify KV-cache reuse. "
    "Reply with only the number you are given, nothing else."
)


def cmd_check(args):
    url = args.url.rstrip("/")
    api_key = os.environ.get("LLAMA_API_KEY", "")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    def call(user_msg):
        body = json.dumps({
            "model": "x",
            "cache_prompt": True,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
        }).encode()
        req = urllib.request.Request(
            f"{url}/v1/chat/completions", data=body, headers=headers, method="POST",
        )
        try:
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read())
        except (urllib.error.URLError, json.JSONDecodeError) as e:
            return {"error": str(e)}

    def show(resp):
        if "error" in resp:
            print(f"  error: {resp['error']}")
            return
        usage = resp.get("usage", {})
        cached = usage.get("prompt_tokens_details", {}).get("cached_tokens", "n/a")
        print(f"  prompt_tokens={usage.get('prompt_tokens', 'n/a')}  cached_tokens={cached}")

    print(f"Target: {url}/v1/chat/completions")
    print()
    print("1st call (cold slot — expect cached_tokens ~ 0):")
    show(call("1"))
    print()
    print("2nd call, identical system prompt (expect cached_tokens > 0 once warm):")
    show(call("2"))
    print()
    print("If cached_tokens stayed 0 on the 2nd call: check that cache_prompt is")
    print("enabled server-side (LLAMA_CACHE_PROMPT=true), that repeated calls are")
    print("landing on the same slot (a fixed id_slot via llama-slot-proxy, or")
    print("--parallel 1), and that --ctx-size is large enough per slot. See")
    print("llama-tool.py cache stats to analyze reuse across real traffic from logs.")


# --- stats: log-based analysis ----------------------------------------------

# Slot ids are right-padded with spaces in current llama.cpp builds
# ("id  2 | task ..."), so allow one-or-more spaces rather than exactly one.
RE_NEW_PROMPT = re.compile(
    r"id\s+(\d+) \| task (\d+) \|.*new prompt.*task\.n_tokens\s*=\s*(\d+)"
)
RE_MEMORY_SEQ_RM = re.compile(
    r"id\s+(\d+) \| task (\d+) \|.*n_tokens\s*=\s*(\d+),\s*memory_seq_rm"
)
# Matches either the current "stop processing" release line or the older
# "prompt done" line, depending on llama.cpp build.
RE_PROMPT_DONE = re.compile(
    r"id\s+(\d+) \| task (\d+) \|.*(?:prompt done|stop processing)"
)


class CacheStatsTracker:
    def __init__(self, live=False, every=1):
        self.live = live
        self.every = every
        self.total_tok = {}
        self.cached_tok = {}
        self.slot_total = defaultdict(int)
        self.slot_cached = defaultdict(int)
        self.grand_total = 0
        self.grand_cached = 0
        self.n_requests = 0
        self.per_request = []

    def process_line(self, line):
        m = RE_NEW_PROMPT.search(line)
        if m:
            slot, task, total = m.group(1), m.group(2), int(m.group(3))
            self.total_tok[(slot, task)] = total
            return

        m = RE_MEMORY_SEQ_RM.search(line)
        if m:
            slot, task, cached = m.group(1), m.group(2), int(m.group(3))
            self.cached_tok[(slot, task)] = cached
            return

        m = RE_PROMPT_DONE.search(line)
        if m:
            slot, task = m.group(1), m.group(2)
            self._flush_task(slot, task)

    def _flush_task(self, slot, task):
        key = (slot, task)
        if key not in self.total_tok:
            return

        total = self.total_tok.pop(key)
        cached = self.cached_tok.pop(key, 0)
        new_tokens = total - cached
        pct = (cached / total * 100.0) if total > 0 else 0.0

        print(
            f"slot {slot:<3} task {task:<8} "
            f"total={total:<6} cached={cached:<6} new={new_tokens:<6} "
            f"reuse={pct:5.1f}%"
        )

        self.grand_total += total
        self.grand_cached += cached
        self.slot_total[slot] += total
        self.slot_cached[slot] += cached
        self.n_requests += 1

        self.per_request.append({
            "slot": slot,
            "task": task,
            "total_tokens": total,
            "cached_tokens": cached,
            "new_tokens": new_tokens,
            "reuse_pct": round(pct, 1),
        })

        if self.live and self.n_requests % self.every == 0:
            overall = (
                self.grand_cached / self.grand_total * 100.0
                if self.grand_total > 0 else 0.0
            )
            print(
                f"  -- running: {self.n_requests} requests, "
                f"overall reuse = {overall:.1f}% "
                f"(cached {self.grand_cached} / total {self.grand_total} tokens)"
            )

    def print_summary(self, as_json=False):
        print("-" * 66)
        if self.n_requests == 0:
            print('No completed requests found in log (no matching "prompt done"/"stop processing" lines).')
            print("Make sure --verbosity is at least 4 on the server.")
            return

        overall = (
            self.grand_cached / self.grand_total * 100.0
            if self.grand_total > 0 else 0.0
        )
        print(
            f"TOTAL: {self.n_requests} requests | {self.grand_total} tokens total | "
            f"{self.grand_cached} cached | overall reuse = {overall:.1f}%"
        )
        print()
        print("Per-slot breakdown:")
        slot_summary = {}
        for slot in sorted(self.slot_total, key=lambda s: int(s)):
            s_total = self.slot_total[slot]
            s_cached = self.slot_cached[slot]
            s_pct = (s_cached / s_total * 100.0) if s_total > 0 else 0.0
            print(f"  slot {slot:<3} : {s_total:6d} tokens total, {s_cached:6d} cached, reuse = {s_pct:5.1f}%")
            slot_summary[slot] = {
                "total_tokens": s_total,
                "cached_tokens": s_cached,
                "reuse_pct": round(s_pct, 1),
            }

        if as_json:
            print()
            print(json.dumps({
                "n_requests": self.n_requests,
                "grand_total_tokens": self.grand_total,
                "grand_cached_tokens": self.grand_cached,
                "overall_reuse_pct": round(overall, 1),
                "per_slot": slot_summary,
                "per_request": self.per_request,
            }, indent=2))


def cmd_stats(args):
    if args.follow and args.source is None:
        args.source = "-"
    if args.source is None:
        args.source = resolve_log_file()
        if not args.source:
            die("a log file, '-', or -f is required (or set LLAMA_LOG_FILE in llama.env)")
        print(f"No log file given, using LLAMA_LOG_FILE: {args.source}")

    tracker = CacheStatsTracker(live=args.follow, every=args.every)

    if args.source == "-":
        for line in sys.stdin:
            tracker.process_line(line)
    else:
        path = Path(args.source)
        if not path.is_file():
            die(f"log file not found: {args.source}")
        with open(path, errors="replace") as f:
            for line in f:
                tracker.process_line(line)

    tracker.print_summary(as_json=args.json)


def add_arguments(parser):
    sub = parser.add_subparsers(dest="cache_command", required=True)

    p_check = sub.add_parser(
        "check",
        help="Send a repeated system prompt to a running server and report cached_tokens",
    )
    p_check.add_argument(
        "url", nargs="?", default="http://127.0.0.1:18080",
        help="llama-server URL, or a llama-slot-proxy agent path (default: http://127.0.0.1:18080)",
    )

    p_stats = sub.add_parser("stats", help="Parse llama-server log output for prompt-cache reuse stats")
    p_stats.add_argument(
        "source", nargs="?", default=None,
        help="Log file path, or '-' for stdin (default: LLAMA_LOG_FILE from llama.env)",
    )
    p_stats.add_argument("-f", "--follow", action="store_true", dest="follow",
                          help="Live mode: running summary as requests complete (reads stdin)")
    p_stats.add_argument("-n", type=int, default=1, dest="every",
                          help="In live mode, print running summary every N requests (default: 1)")
    p_stats.add_argument("--json", action="store_true", help="Also print a machine-readable JSON summary")


def run(args):
    if args.cache_command == "check":
        cmd_check(args)
    elif args.cache_command == "stats":
        cmd_stats(args)
