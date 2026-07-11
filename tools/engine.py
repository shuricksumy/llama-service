"""llama-tool.py engine -- vendors llama.cpp releases locally.

Ported from update-llama-cpp.sh. Downloads the official prebuilt release
(Vulkan Linux x64 by default) from ggml-org/llama.cpp on GitHub into
vendor/llama.cpp/<tag>/, and maintains a vendor/llama.cpp/current symlink
to the active version.
"""
import json
import os
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from ._common import REPO_ROOT, VENDOR_DIR, die, urlopen

REPO = "ggml-org/llama.cpp"
DEFAULT_ASSET = "ubuntu-vulkan-x64"


def _gh_headers():
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def latest_tag() -> str:
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/releases/latest", headers=_gh_headers(),
    )
    with urlopen(req, description="GitHub releases API") as resp:
        data = json.loads(resp.read())
    return data["tag_name"]


def active_version():
    current = VENDOR_DIR / "current"
    if current.is_symlink():
        return os.readlink(current)
    return None


def installed_versions():
    if not VENDOR_DIR.is_dir():
        return []
    return sorted(p.name for p in VENDOR_DIR.iterdir() if p.is_dir())


def _download_with_progress(url: str, dest: Path):
    req = urllib.request.Request(url)
    with urlopen(req, description=url) as resp, open(dest, "wb") as out:
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


def install(tag, activate: bool, force: bool):
    VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    if tag is None:
        print("Checking latest release ...")
        tag = latest_tag()

    dest = VENDOR_DIR / tag
    binary = dest / "llama-server"

    if binary.is_file() and os.access(binary, os.X_OK) and not force:
        print(f"{tag} already installed at {dest.relative_to(REPO_ROOT)} (use --force to re-download)")
    else:
        asset_pattern = os.environ.get("LLAMA_CPP_ASSET", DEFAULT_ASSET)
        asset = f"llama-{tag}-bin-{asset_pattern}.tar.gz"
        url = f"https://github.com/{REPO}/releases/download/{tag}/{asset}"
        print(f"Downloading {url}")

        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / asset
            _download_with_progress(url, archive)

            extract_dir = Path(tmp) / "extract"
            extract_dir.mkdir()
            with tarfile.open(archive) as tf:
                try:
                    tf.extractall(extract_dir, filter="data")
                except TypeError:
                    tf.extractall(extract_dir)

            subdirs = list(extract_dir.iterdir())
            if len(subdirs) != 1 or not subdirs[0].is_dir():
                die("unexpected archive layout (expected a single top-level directory)")

            if dest.exists():
                shutil.rmtree(dest)
            shutil.move(str(subdirs[0]), str(dest))

        if not binary.is_file():
            shutil.rmtree(dest, ignore_errors=True)
            die("extracted archive has no llama-server binary")
        binary.chmod(binary.stat().st_mode | 0o111)
        print(f"Installed {tag} -> {dest.relative_to(REPO_ROOT)}")

    if activate:
        use_version(tag)


def use_version(tag: str):
    binary = VENDOR_DIR / tag / "llama-server"
    if not (binary.is_file() and os.access(binary, os.X_OK)):
        die(f"{tag} is not installed (no {binary.relative_to(REPO_ROOT)}). Run: llama-tool.py engine install {tag}")
    current = VENDOR_DIR / "current"
    if current.exists() or current.is_symlink():
        current.unlink()
    current.symlink_to(tag)
    print(f"Active version: {tag}")


def list_versions():
    active = active_version()
    print(f"Installed versions (in {VENDOR_DIR.relative_to(REPO_ROOT)}):")
    versions = [v for v in installed_versions() if v != "current"]
    if not versions:
        print("  (none — run: llama-tool.py engine install)")
        return
    for v in versions:
        marker = "* " if v == active else "  "
        suffix = " (active)" if v == active else ""
        print(f"  {marker}{v}{suffix}")


def check():
    latest = latest_tag()
    active = active_version()
    print(f"Latest upstream release: {latest}")
    print(f"Currently active:        {active or 'none'}")
    if active == latest:
        print("Up to date.")
    else:
        print("Update available: llama-tool.py engine install")


def add_arguments(parser):
    sub = parser.add_subparsers(dest="engine_command", required=True)

    p_install = sub.add_parser("install", help="Install latest (or a specific) release and activate it")
    p_install.add_argument("tag", nargs="?", default=None, help="Specific release tag (default: latest)")
    p_install.add_argument("--no-activate", action="store_true", help="Install without switching `current`")
    p_install.add_argument("--force", action="store_true", help="Re-download even if already installed")

    sub.add_parser("check", help="Show latest vs. active version, no download")
    sub.add_parser("list", help="List installed versions (* = active)")

    p_use = sub.add_parser("use", help="Switch active version (must already be installed)")
    p_use.add_argument("tag")


def run(args):
    if args.engine_command == "install":
        install(args.tag, activate=not args.no_activate, force=args.force)
    elif args.engine_command == "check":
        check()
    elif args.engine_command == "list":
        list_versions()
    elif args.engine_command == "use":
        use_version(args.tag)
