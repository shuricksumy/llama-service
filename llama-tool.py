#!/usr/bin/env python3
"""llama-tool.py -- unified CLI for this repo's ops tooling.

  init     One-time host setup: install systemd unit + logrotate config
  run      Resolve config and launch llama-server
  engine   Manage the vendored llama.cpp engine (install/check/list/use)
  models   Download/manage GGUF models from Hugging Face
  cache    Verify/analyze prompt-cache reuse (live check + log stats)
  log      Tail the llama-server log (defaults to LLAMA_LOG_FILE)

Run `llama-tool.py <command> --help` for a command's full options.
Stdlib only -- no pip install, no venv needed.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Line-buffer stdout even when piped/redirected (e.g. under systemd, or
# `| tee`), so progress/status lines interleave in the right order with
# stderr warnings/errors instead of sitting in a block buffer.
sys.stdout.reconfigure(line_buffering=True)

from tools import cache as cache_mod
from tools import engine as engine_mod
from tools import init as init_mod
from tools import log as log_mod
from tools import models as models_mod
from tools import run as run_mod


def main():
    parser = argparse.ArgumentParser(
        prog="llama-tool.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="One-time host setup: install systemd unit + logrotate config")
    init_mod.add_arguments(p_init)

    p_run = sub.add_parser("run", help="Resolve config and launch llama-server")
    run_mod.add_arguments(p_run)

    p_engine = sub.add_parser("engine", help="Manage the vendored llama.cpp engine")
    engine_mod.add_arguments(p_engine)

    p_models = sub.add_parser("models", help="Download/manage GGUF models from Hugging Face")
    models_mod.add_arguments(p_models)

    p_cache = sub.add_parser("cache", help="Verify/analyze prompt-cache reuse")
    cache_mod.add_arguments(p_cache)

    p_log = sub.add_parser("log", help="Tail the llama-server log")
    log_mod.add_arguments(p_log)

    args = parser.parse_args()

    if args.command == "init":
        init_mod.run(args)
    elif args.command == "run":
        run_mod.run(args)
    elif args.command == "engine":
        engine_mod.run(args)
    elif args.command == "models":
        models_mod.run(args)
    elif args.command == "cache":
        cache_mod.run(args)
    elif args.command == "log":
        log_mod.run(args)


if __name__ == "__main__":
    main()
