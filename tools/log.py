"""llama-tool.py log -- tail the llama-server log.

Thin wrapper around the system `tail -F` (defaults to LLAMA_LOG_FILE from
llama.env if no path is given). `-F` rather than `-f` so it keeps following
correctly across logrotate's copytruncate rotation (see
deploy/llama-server.logrotate). Not reimplemented in Python -- every target
host already has `tail`, and it handles this better than a hand-rolled
follow loop would.
"""
import os

from ._common import die
from ._env import resolve_log_file


def add_arguments(parser):
    parser.add_argument(
        "file", nargs="?", default=None,
        help="Log file to tail (default: LLAMA_LOG_FILE from llama.env)",
    )
    parser.add_argument(
        "-n", "--lines", default="50",
        help="Number of trailing lines to show initially (default: 50)",
    )
    parser.add_argument(
        "--no-follow", action="store_true",
        help="Print the trailing lines once and exit, instead of following",
    )


def run(args):
    path = args.file
    if not path:
        path = resolve_log_file()
        if not path:
            die("no log file given and LLAMA_LOG_FILE is not set in llama.env")

    if not os.path.isfile(path):
        die(f"log file not found: {path}")

    cmd = ["tail", "-n", str(args.lines)]
    if not args.no_follow:
        cmd.append("-F")
    cmd.append(path)

    os.execvp("tail", cmd)
