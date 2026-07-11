"""llama-tool.py models -- downloads GGUF models from Hugging Face.

Ported from manage-models.sh. `download` fetches a model (+ optional
mmproj) via direct HTTPS and writes a matching presets/<name>.env; `delete`
removes a preset and its model file(s) together. The only bookkeeping is
a handful of "# hf-source: ..." / "# hf-sha256: ..." comment lines embedded
in the generated preset -- list/check/delete all just read those back out,
so there's no separate manifest to fall out of sync.
"""
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

from ._common import (
    REPO_ROOT, MODELS_DIR, PRESETS_DIR, die, describe_url_error, sha256_of, human_size, urlopen,
)

HF_API = "https://huggingface.co/api/models"
TAG_RE = re.compile(r"^# ([a-z0-9-]+): (.*)$")


def _hf_headers():
    token = os.environ.get("HF_TOKEN")
    return {"Authorization": f"Bearer {token}"} if token else {}


def repo_blobs(repo: str) -> dict:
    req = urllib.request.Request(f"{HF_API}/{repo}?blobs=true", headers=_hf_headers())
    with urlopen(req, description=f"Hugging Face repo {repo}") as resp:
        return json.loads(resp.read())


def file_meta(blobs: dict, filename: str):
    """Returns (size, sha256) for a file in repo blobs, or None if absent."""
    for sib in blobs.get("siblings", []):
        if sib.get("rfilename") == filename:
            size = sib.get("size", 0)
            sha = (sib.get("lfs") or {}).get("sha256", "")
            return size, sha
    return None


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower())
    return s.strip("-")


def read_tags(preset_file: Path) -> dict:
    tags = {}
    for line in preset_file.read_text().splitlines():
        m = TAG_RE.match(line)
        if m and m.group(1) not in tags:
            tags[m.group(1)] = m.group(2)
    return tags


def download_file(repo: str, filename: str, dest: Path, expected_sha: str, force: bool):
    if dest.is_file() and not force:
        print(f"{filename} already downloaded -> {dest.relative_to(REPO_ROOT)}")
        return
    url = f"https://huggingface.co/{repo}/resolve/main/{filename}"
    partial = dest.parent / (dest.name + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {filename} ...")

    req = urllib.request.Request(url, headers=_hf_headers())
    try:
        with urllib.request.urlopen(req) as resp, open(partial, "wb") as out:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                downloaded += len(chunk)
                if total:
                    print(f"\r  {downloaded / total * 100:5.1f}%", end="", flush=True)
        print()
    except urllib.error.URLError as e:
        partial.unlink(missing_ok=True)
        die(f"download failed for {filename}: {describe_url_error(e)}")

    if expected_sha:
        print("Verifying checksum...")
        actual = sha256_of(partial)
        if actual != expected_sha:
            partial.unlink(missing_ok=True)
            die(f"checksum mismatch for {filename}\n  expected: {expected_sha}\n  got:      {actual}")

    partial.rename(dest)
    print(f"Saved -> {dest.relative_to(REPO_ROOT)}")


def write_preset(name, repo, file, file_sha, mmproj, mmproj_sha, port) -> Path:
    PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    preset_file = PRESETS_DIR / f"{name}.env"

    lines = [
        f"# {repo} ({file}) — downloaded via llama-tool.py models download",
        f"# hf-source: {repo}/{file}",
        f"# hf-sha256: {file_sha}",
    ]
    if mmproj:
        lines.append(f"# hf-mmproj: {repo}/{mmproj}")
        lines.append(f"# hf-mmproj-sha256: {mmproj_sha}")
    lines.append("")
    lines.append(f': "${{LLAMA_MODEL:=${{LLAMA_MODELS_DIR}}/{repo}/{file}}}"')
    if mmproj:
        lines.append(f': "${{LLAMA_MMPROJ:=${{LLAMA_MODELS_DIR}}/{repo}/{mmproj}}}"')
    else:
        lines.append(': "${LLAMA_MMPROJ:=}"')
    lines.append("")
    lines.append(f': "${{LLAMA_PORT:={port}}}"')
    lines.append(': "${LLAMA_CTX_SIZE:=32768}"')
    lines.append(': "${LLAMA_N_GPU_LAYERS:=999999}"')
    lines.append(': "${LLAMA_OVERRIDE_KV:=}"')
    lines.append(': "${LLAMA_REASONING:=off}"')

    preset_file.write_text("\n".join(lines) + "\n")
    print(f"Wrote {preset_file.relative_to(REPO_ROOT)}")
    return preset_file


def cmd_download(args):
    repo, file, mmproj = args.repo, args.file, args.mmproj or ""

    print(f"Fetching metadata for {repo} ...")
    blobs = repo_blobs(repo)

    meta = file_meta(blobs, file)
    if meta is None:
        die(f"'{file}' not found in {repo}. Try: llama-tool.py models list-files {repo}")
    _, model_sha = meta

    mmproj_sha = ""
    if mmproj:
        mmeta = file_meta(blobs, mmproj)
        if mmeta is None:
            die(f"mmproj '{mmproj}' not found in {repo}. Try: llama-tool.py models list-files {repo}")
        _, mmproj_sha = mmeta

    dest_dir = MODELS_DIR / repo
    download_file(repo, file, dest_dir / file, model_sha, args.force)
    if mmproj:
        download_file(repo, mmproj, dest_dir / mmproj, mmproj_sha, args.force)

    preset_name = args.preset or slugify(file[:-5] if file.endswith(".gguf") else file)
    write_preset(preset_name, repo, file, model_sha, mmproj, mmproj_sha, args.port)

    print()
    print(f"Done. Try:  ./llama-tool.py run {preset_name} --dry-run")


def cmd_list():
    print(f"Downloaded models (in {MODELS_DIR.relative_to(REPO_ROOT)}):")
    found = False
    for p in sorted(PRESETS_DIR.glob("*.env")):
        tags = read_tags(p)
        hf_source = tags.get("hf-source")
        if not hf_source:
            continue
        found = True
        path = MODELS_DIR / hf_source
        size = human_size(path.stat().st_size) if path.is_file() else "?"
        print(f"  {p.stem:<32} {size:<8} {hf_source}")
    if not found:
        print("  (none — run: llama-tool.py models download <org>/<repo> <file.gguf>)")


def cmd_list_files(args):
    repo = args.repo
    print(f"GGUF files in {repo}:")
    blobs = repo_blobs(repo)
    for sib in blobs.get("siblings", []):
        name = sib.get("rfilename", "")
        if not name.endswith(".gguf"):
            continue
        size = sib.get("size") or 0
        print(f"  {name:<45} {size / 1e9:6.2f} GB")


def cmd_check():
    any_found = False
    for p in sorted(PRESETS_DIR.glob("*.env")):
        tags = read_tags(p)
        hf_source = tags.get("hf-source")
        if not hf_source:
            continue
        any_found = True
        hf_sha = tags.get("hf-sha256", "")
        name = p.stem

        parts = hf_source.split("/", 2)
        if len(parts) < 3:
            print(f"{name}: malformed hf-source tag ({hf_source})")
            continue
        repo_id = f"{parts[0]}/{parts[1]}"
        filename = parts[2]

        blobs = repo_blobs(repo_id)
        meta = file_meta(blobs, filename)
        if meta is None:
            print(f"{name}: removed upstream? ({hf_source} not found in {repo_id})")
            continue
        _, remote_sha = meta
        if not hf_sha or remote_sha == hf_sha:
            print(f"{name}: up to date")
        else:
            print(f"{name}: UPDATE AVAILABLE — re-run: llama-tool.py models download {repo_id} {filename} --preset {name} --force")
    if not any_found:
        print("No downloaded-model presets found (nothing has a '# hf-source:' tag).")


def cmd_delete(args):
    name = args.preset
    preset_file = PRESETS_DIR / f"{name}.env"
    if not preset_file.is_file():
        die(f"no such preset: presets/{name}.env")

    tags = read_tags(preset_file)
    hf_source = tags.get("hf-source")
    hf_mmproj = tags.get("hf-mmproj")

    if not hf_source:
        die(
            f"presets/{name}.env wasn't downloaded by this tool (no '# hf-source:' tag). "
            f"Delete it manually with: rm presets/{name}.env"
        )

    print(f"Preset:  presets/{name}.env")
    print(f"Model:   {MODELS_DIR / hf_source}")
    if hf_mmproj:
        print(f"Mmproj:  {MODELS_DIR / hf_mmproj}")

    if not args.yes:
        reply = input("Delete this preset and its model file(s)? [y/N] ")
        if reply.strip().lower() != "y":
            print("Aborted.")
            return

    other_presets_text = None

    def referenced_elsewhere(ref: str) -> bool:
        nonlocal other_presets_text
        if other_presets_text is None:
            other_presets_text = [
                p.read_text() for p in PRESETS_DIR.glob("*.env") if p != preset_file
            ]
        return any(ref in text for text in other_presets_text)

    for ref in filter(None, [hf_source, hf_mmproj]):
        target = MODELS_DIR / ref
        if referenced_elsewhere(ref):
            print(f"Keeping {target} (still referenced by another preset)")
        else:
            target.unlink(missing_ok=True)
            print(f"Removed {target}")

    preset_file.unlink()
    print(f"Removed presets/{name}.env")

    d = (MODELS_DIR / hf_source).parent
    while d != REPO_ROOT and d.exists() and not any(d.iterdir()):
        d.rmdir()
        d = d.parent


def add_arguments(parser):
    sub = parser.add_subparsers(dest="models_command", required=True)

    p_dl = sub.add_parser("download", help="Download a model (+ optional mmproj) and create a matching preset")
    p_dl.add_argument("repo", help="Hugging Face repo, e.g. org/repo")
    p_dl.add_argument("file", help="GGUF filename in the repo")
    p_dl.add_argument("mmproj", nargs="?", default="", help="Optional mmproj GGUF filename")
    p_dl.add_argument("--preset", default="", help="Preset file name (default: derived from <file>)")
    p_dl.add_argument("--port", default="18080", help="LLAMA_PORT for the generated preset")
    p_dl.add_argument("--force", action="store_true", help="Re-download even if the file already exists")

    p_del = sub.add_parser("delete", help="Delete a preset and its model file(s)")
    p_del.add_argument("preset")
    p_del.add_argument("-y", "--yes", action="store_true", help="Skip the confirmation prompt")

    sub.add_parser("list", help="List downloaded models and their presets")

    p_lf = sub.add_parser("list-files", help="List downloadable .gguf files in a Hugging Face repo")
    p_lf.add_argument("repo")

    sub.add_parser("check", help="Compare downloaded models against their current Hugging Face source")


def run(args):
    if args.models_command == "download":
        cmd_download(args)
    elif args.models_command == "delete":
        cmd_delete(args)
    elif args.models_command == "list":
        cmd_list()
    elif args.models_command == "list-files":
        cmd_list_files(args)
    elif args.models_command == "check":
        cmd_check()
