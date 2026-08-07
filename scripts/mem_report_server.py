#!/usr/bin/env python3
"""
mem_report_server.py — serves the RAM / GPU / Swap report as an HTML page.
No pip installs needed — stdlib only.

Usage:
    python3 mem_report_server.py [port]        # default port 8899

Then open in a browser (or curl):
    http://<server-ip>:8899/

Auto-refreshes every 30s. Ctrl+C to stop.
"""

import glob
import html
import os
import re
import subprocess
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8899


def run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
    except Exception as e:
        return f"(error running {cmd}: {e})"


def get_mem_swap():
    out = run(["free", "-m"])
    data = {}
    for line in out.splitlines():
        parts = line.split()
        if line.startswith("Mem:"):
            data["mem_total"], data["mem_used"], data["mem_free"] = int(parts[1]), int(parts[2]), int(parts[3])
            data["mem_shared"], data["mem_buffcache"], data["mem_avail"] = int(parts[4]), int(parts[5]), int(parts[6])
        elif line.startswith("Swap:"):
            data["swap_total"], data["swap_used"], data["swap_free"] = int(parts[1]), int(parts[2]), int(parts[3])
    return data


def readnum(path):
    try:
        with open(path) as f:
            return int(f.read().strip())
    except Exception:
        return 0


def get_gpu_info():
    cards = []
    for dev in sorted(glob.glob("/sys/class/drm/card*/device")):
        if not os.path.exists(os.path.join(dev, "mem_info_vram_total")):
            continue
        cards.append({
            "device": dev,
            "vram_total_gb": readnum(os.path.join(dev, "mem_info_vram_total")) / 1024**3,
            "vram_used_gb": readnum(os.path.join(dev, "mem_info_vram_used")) / 1024**3,
            "gtt_total_gb": readnum(os.path.join(dev, "mem_info_gtt_total")) / 1024**3,
            "gtt_used_gb": readnum(os.path.join(dev, "mem_info_gtt_used")) / 1024**3,
        })
    return cards


def get_swap_activity():
    out = run(["vmstat", "-S", "M", "1", "2"])
    lines = [l for l in out.splitlines() if l.strip()]
    if len(lines) < 3:
        return None
    last = lines[-1].split()
    try:
        si, so = int(last[6]), int(last[7])
    except (IndexError, ValueError):
        return None
    return {"si": si, "so": so, "active": not (si == 0 and so == 0)}


def read_cmdline(pid):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            raw = f.read()
        cmd = raw.replace(b"\x00", b" ").decode(errors="replace").strip()
        if cmd:
            return cmd[:80]
    except Exception:
        pass
    try:
        with open(f"/proc/{pid}/comm") as f:
            return f.read().strip()
    except Exception:
        return "?"


def get_top_processes(n=30):
    rss_list, swap_list = [], []
    for pid_str in os.listdir("/proc"):
        if not pid_str.isdigit():
            continue
        pid = int(pid_str)
        try:
            with open(f"/proc/{pid}/status") as f:
                status = f.read()
        except Exception:
            continue
        rss_m = re.search(r"VmRSS:\s+(\d+)", status)
        swap_m = re.search(r"VmSwap:\s+(\d+)", status)
        rss_kb = int(rss_m.group(1)) if rss_m else 0
        swap_kb = int(swap_m.group(1)) if swap_m else 0
        cmd = read_cmdline(pid)
        if rss_kb > 0:
            rss_list.append((pid, rss_kb, cmd))
        if swap_kb > 0:
            swap_list.append((pid, swap_kb, cmd))
    rss_list.sort(key=lambda x: -x[1])
    swap_list.sort(key=lambda x: -x[1])
    return rss_list[:n], swap_list[:n]


def get_oom_history():
    out = run(["dmesg"])
    lines = [l for l in out.splitlines() if re.search(r"out of memory|oom-killer|killed process", l, re.I)]
    return lines[-10:]


def build_html():
    mem = get_mem_swap()
    gpu = get_gpu_info()
    swap_act = get_swap_activity()
    top_rss, top_swap = get_top_processes(30)
    oom = get_oom_history()

    total_rss_gb = sum(r[1] for r in top_rss) / 1024 / 1024
    total_swap_gb = sum(s[1] for s in top_swap) / 1024 / 1024

    def row(pid, kb, cmd):
        return f"<tr><td>{pid}</td><td>{kb/1024/1024:.2f} GB</td><td>{html.escape(cmd)}</td></tr>"

    rss_rows = "\n".join(row(*r) for r in top_rss)
    swap_rows = "\n".join(row(*s) for s in top_swap)

    if gpu:
        gpu_html = ""
        for c in gpu:
            gpu_html += (
                f"<p><b>Card:</b> {html.escape(c['device'])}<br>"
                f"VRAM used: {c['vram_used_gb']:.2f} / {c['vram_total_gb']:.2f} GB<br>"
                f"<span class='warn'>GTT used (RAM): {c['gtt_used_gb']:.2f} / {c['gtt_total_gb']:.2f} GB "
                f"— REAL RAM, invisible in the tables below!</span></p>"
            )
    else:
        gpu_html = "<p>No AMD GPU sysfs memory info found.</p>"

    if swap_act is None:
        swap_state_html = "<p>Could not read vmstat.</p>"
    else:
        cls = "bad" if swap_act["active"] else "ok"
        label = "ACTIVE — real-time paging happening right now!" if swap_act["active"] else "PARKED (old data, not actively swapping)"
        swap_state_html = f'<p class="{cls}">si={swap_act["si"]} MB/s  so={swap_act["so"]} MB/s  &rarr;  {label}</p>'

    oom_html = "<br>".join(html.escape(l) for l in oom) if oom else "(none logged since boot / dmesg buffer wrap)"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="60">
<title>Memory / GPU / Swap Report</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; background:#0f1115; color:#e6e6e6; margin:2rem; }}
  h1 {{ font-size:1.4rem; }}
  h2 {{ color:#5ec8f8; border-bottom:1px solid #333; padding-bottom:.3rem; margin-top:0;}}
  table {{ border-collapse:collapse; width:100%; margin-top:.5rem; }}
  td, th {{ padding:4px 10px; text-align:left; border-bottom:1px solid #222; font-size:.9rem;}}
  th {{ color:#9adcf7; }}
  .ok {{ color:#7CFC00; }}
  .bad {{ color:#ff5c5c; font-weight:bold; }}
  .warn {{ color:#f5c542; }}
  .card {{ background:#181b21; border-radius:8px; padding:1rem 1.5rem; margin-bottom:1.2rem; }}
  .total {{ color:#f5c542; font-weight:bold; }}
</style>
</head>
<body>
<h1>Memory / GPU / Swap Report — {now}</h1>

<div class="card">
<h2>Real Memory Breakdown</h2>
<p>
Total RAM: {mem.get('mem_total',0)/1024:.1f} GB<br>
Used: {mem.get('mem_used',0)/1024:.1f} GB<br>
Free: {mem.get('mem_free',0)/1024:.1f} GB<br>
Buff/Cache (reclaimable): {mem.get('mem_buffcache',0)/1024:.1f} GB<br>
Available: {mem.get('mem_avail',0)/1024:.1f} GB
</p>
<p>
Swap Total: {mem.get('swap_total',0)/1024:.1f} GB<br>
Swap Used: {mem.get('swap_used',0)/1024:.1f} GB<br>
Swap Free: {mem.get('swap_free',0)/1024:.1f} GB
</p>
{gpu_html}
{swap_state_html}
</div>

<div class="card">
<h2>Top 30 Processes by RAM (RSS) — <span class="total">Total: {total_rss_gb:.2f} GB</span></h2>
<table>
<tr><th>PID</th><th>RSS</th><th>Command</th></tr>
{rss_rows}
</table>
</div>

<div class="card">
<h2>Top 30 Processes by Swap — <span class="total">Total: {total_swap_gb:.2f} GB</span></h2>
<table>
<tr><th>PID</th><th>Swap</th><th>Command</th></tr>
{swap_rows}
</table>
</div>

<div class="card">
<h2>OOM-Killer History</h2>
<p>{oom_html}</p>
</div>

<p style="color:#666;font-size:.8rem;">Auto-refreshes every 60s. GPU GTT usage is real RAM but never appears in the process tables above.</p>
</body>
</html>
"""


class ReportHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ("/", "/index.html"):
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found. Try /")
            return
        try:
            body = build_html().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(f"error: {e}".encode())

    def log_message(self, format, *args):
        pass  # quiet, no request logging


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", PORT), ReportHandler)
    print(f"Serving memory report on http://0.0.0.0:{PORT}/  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass

