"""Shared helpers for llama-tool.py subcommands."""
import hashlib
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PRESETS_DIR = REPO_ROOT / "presets"
MODELS_DIR = REPO_ROOT / "models"
VENDOR_DIR = REPO_ROOT / "vendor" / "llama.cpp"


def die(message: str, code: int = 1):
    print(f"Error: {message}", file=sys.stderr)
    sys.exit(code)


def describe_url_error(e) -> str:
    """Formats a urllib HTTPError/URLError into a one-line message, with a
    hint for the most common real-world trip-up: an SSL cert verification
    failure from a python3 interpreter whose CA bundle isn't configured
    (seen with some non-system Python installs) -- curl-based tooling
    doesn't hit this since it uses the OS trust store instead.
    """
    if isinstance(e, urllib.error.HTTPError):
        return f"HTTP {e.code}"
    reason = getattr(e, "reason", e)
    hint = ""
    if isinstance(reason, ssl.SSLCertVerificationError):
        hint = (
            "\nThis python3's SSL certificates aren't configured correctly. "
            "Try a different python3 (e.g. the system one), or "
            "`pip install certifi` and set SSL_CERT_FILE to its bundle."
        )
    return f"{reason}{hint}"


def urlopen(req, description=""):
    """urllib.request.urlopen wrapper that dies with a clean one-line
    message on any network/TLS failure instead of leaking a raw traceback.
    """
    what = description or getattr(req, "full_url", str(req))
    try:
        return urllib.request.urlopen(req)
    except urllib.error.URLError as e:
        die(f"request failed for {what}: {describe_url_error(e)}")


def warn(message: str):
    print(f"Warning: {message}", file=sys.stderr)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def human_size(num_bytes) -> str:
    try:
        n = float(num_bytes)
    except (TypeError, ValueError):
        return "?"
    for unit in ("B", "K", "M", "G", "T"):
        if n < 1024 or unit == "T":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}T"
