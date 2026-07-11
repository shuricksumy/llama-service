"""llama-tool.py run -- resolves config and execs llama-server.

Ported from run-llama.sh. Flag tables below mirror that script's ARGS
builder exactly, including flags it deliberately leaves commented out
(--mmproj, --n-cpu-moe, --tensor-split, --mlock) -- LLAMA_MMPROJ is still
validated for existence even though it's never passed as a flag, matching
the original's behavior.
"""
import os
from pathlib import Path

from ._common import die, warn
from ._env import list_presets, resolve_env

# (kind, env var, flag) in the exact order run-llama.sh built ARGS in --
# value-flags, then boolean flags, then chat-template-kwargs, then the
# prompt-cache/slot section (which itself interleaves value and bool flags).
ARG_SPEC = [
    ("value", "LLAMA_MODEL", "--model"),
    ("value", "LLAMA_HOST", "--host"),
    ("value", "LLAMA_PORT", "--port"),
    ("value", "LLAMA_API_KEY", "--api-key"),
    ("value", "LLAMA_VERBOSITY", "--verbosity"),
    ("value", "LLAMA_CTX_SIZE", "--ctx-size"),
    ("value", "LLAMA_BATCH_SIZE", "--batch-size"),
    ("value", "LLAMA_UBATCH_SIZE", "--ubatch-size"),
    ("value", "LLAMA_N_GPU_LAYERS", "--n-gpu-layers"),
    ("value", "LLAMA_MAIN_GPU", "--main-gpu"),
    ("value", "LLAMA_THREADS", "--threads"),
    ("value", "LLAMA_PARALLEL", "--parallel"),
    ("value", "LLAMA_SPLIT_MODE", "--split-mode"),
    ("value", "LLAMA_CACHE_TYPE_K", "--cache-type-k"),
    ("value", "LLAMA_CACHE_TYPE_V", "--cache-type-v"),
    ("value", "LLAMA_FLASH_ATTN", "--flash-attn"),
    ("value", "LLAMA_OVERRIDE_KV", "--override-kv"),
    ("value", "LLAMA_REASONING", "--reasoning"),
    ("bool", "LLAMA_NO_WEBUI", "--no-webui"),
    ("bool", "LLAMA_JINJA", "--jinja"),
    ("bool", "LLAMA_LOG_DISABLE", "--log-disable"),
    ("bool", "LLAMA_KV_OFFLOAD", "--kv-offload"),
    ("bool", "LLAMA_KV_UNIFIED", "--kv-unified"),
    ("bool", "LLAMA_NO_DIRECT_IO", "--no-direct-io"),
    ("bool", "LLAMA_METRICS", "--metrics"),
    ("value", "LLAMA_CHAT_TEMPLATE_KWARGS", "--chat-template-kwargs"),
    ("value", "LLAMA_CACHE_REUSE", "--cache-reuse"),
    ("value", "LLAMA_CACHE_RAM", "--cache-ram"),
    ("bool", "LLAMA_CACHE_PROMPT", "--cache-prompt"),
    ("value", "LLAMA_SLOT_SAVE_PATH", "--slot-save-path"),
    ("value", "LLAMA_DEFRAG_THOLD", "--defrag-thold"),
]


def add_arguments(parser):
    parser.add_argument(
        "preset", nargs="?", default=None,
        help="Preset name (default: $ACTIVE_PRESET from llama.env)",
    )
    parser.add_argument(
        "-n", "--dry-run", action="store_true",
        help="Print the resolved command instead of running it",
    )
    parser.add_argument(
        "-l", "--list", action="store_true",
        help="List available presets and exit",
    )


def _print_presets():
    print("Available presets (presets/<name>.env):")
    for name, desc in list_presets():
        print(f"  {name:<32} {desc}")


def run(args):
    if args.list:
        _print_presets()
        return

    base_env = resolve_env(None)
    preset = args.preset or base_env.get("ACTIVE_PRESET")
    if not preset:
        die("no preset given and ACTIVE_PRESET is not set in llama.env")

    print(f"Starting llama-server with preset: {preset}", flush=True)
    env = resolve_env(preset)

    def fail_or_warn(message):
        if args.dry_run:
            warn(message)
        else:
            die(message)

    binary = env.get("LLAMA_BINARY", "")
    model = env.get("LLAMA_MODEL", "")
    mmproj = env.get("LLAMA_MMPROJ", "")

    if not (binary and os.access(binary, os.X_OK)):
        hint = " (run: llama-tool.py engine install)" if "/vendor/llama.cpp/" in binary else ""
        fail_or_warn(f"llama-server binary not found or not executable: {binary}{hint}")
    if not (model and Path(model).is_file()):
        fail_or_warn(f"model file not found: {model}")
    if mmproj and not Path(mmproj).is_file():
        fail_or_warn(f"mmproj file not found: {mmproj}")

    argv = []
    for kind, var, flag in ARG_SPEC:
        if kind == "value":
            val = env.get(var, "")
            if val:
                argv += [flag, val]
        else:
            if env.get(var, "") == "true":
                argv.append(flag)

    print("----------------------------")
    print(binary + " " + " ".join(argv))
    print("----------------------------")

    if args.dry_run:
        return

    os.execv(binary, [binary] + argv)
