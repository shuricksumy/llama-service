"""Resolves llama.env / presets/*.env by shelling out to bash.

Config stays in its existing bash format (${VAR} expansion, the
`: "${VAR:=default}"` per-invocation override idiom) instead of being
reimplemented in Python -- a real bash subshell sources the files with
`set -a` so every variable they set becomes part of that subprocess's
environment and shows up in `env`'s output, including values inherited
from our own environment (so `LLAMA_CTX_SIZE=16384 ./llama-tool.py run ...`
still overrides a preset's default, exactly as it does for run-llama.sh).
"""
import subprocess
from pathlib import Path
from typing import Optional

from ._common import REPO_ROOT, PRESETS_DIR, die


def preset_path(name: str) -> Path:
    return PRESETS_DIR / f"{name}.env"


def list_presets():
    """Returns [(name, description), ...] sorted by name."""
    results = []
    if not PRESETS_DIR.is_dir():
        return results
    for f in sorted(PRESETS_DIR.glob("*.env")):
        desc = ""
        for line in f.read_text().splitlines():
            line = line.strip()
            if line.startswith("#"):
                desc = line.lstrip("#").strip()
                break
        results.append((f.stem, desc))
    return results


def resolve_env(preset: Optional[str] = None) -> dict:
    commands = ["source llama.env"]
    if preset is not None:
        pfile = preset_path(preset)
        if not pfile.is_file():
            die(f"unknown preset '{preset}' (no {pfile.relative_to(REPO_ROOT)})")
        commands.append(f"source presets/{preset}.env")
    script = "set -a\n" + "\n".join(commands) + "\nset +a\nenv"

    proc = subprocess.run(
        ["bash", "-c", script], cwd=REPO_ROOT, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        die(f"failed to resolve config:\n{proc.stderr}")

    env = {}
    for line in proc.stdout.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            env[key] = value
    return env


def resolve_log_file() -> str:
    """Returns LLAMA_LOG_FILE from llama.env (empty string if somehow unset)."""
    return resolve_env(None).get("LLAMA_LOG_FILE", "")
