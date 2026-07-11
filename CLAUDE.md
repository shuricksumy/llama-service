# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

Deployment/ops configuration for running `llama-server` (llama.cpp's Vulkan-backed server binary) as a systemd service on a Linux host, tuned to sit behind [llama-slot-proxy](https://github.com/shuricksumy/llama-slot-proxy) (a separate project) so multiple callers each keep their own warm prompt cache on a fixed `id_slot`. There is no application source code to build or test here — this repo is tooling and config files that wrap a pre-built binary and downloaded model files, both vendored locally rather than sourced from LM Studio. All tooling is a single stdlib-only Python CLI (`llama-tool.py` + `tools/`) — no pip install, no venv.

## Files

- `llama-tool.py` — the entrypoint. `argparse` dispatcher over six subcommand groups implemented in `tools/`: `init`, `run`, `engine`, `models`, `cache`, `log`. `llama-tool.py --help` / `llama-tool.py <command> --help` is self-documenting; don't duplicate that help text elsewhere.
- `tools/init.py` — `llama-tool.py init`. One-time host setup after cloning: `sudo cp`s `deploy/llama-server.service` and `deploy/llama-server.logrotate` into `/etc/`, runs `systemctl daemon-reload` + `enable`. Prints every command before running it and asks for confirmation (`-y`/`--dry-run`/`--no-enable` available). Deliberately does not start the service (nothing's installed yet on a fresh clone) or touch user/group membership (too host/GPU-specific to automate safely — stays a documented manual step).
- `tools/_common.py` — shared helpers: `REPO_ROOT`/`PRESETS_DIR`/`MODELS_DIR`/`VENDOR_DIR` constants, `die()`/`warn()`, `sha256_of()`, `human_size()`, and `urlopen()` (a `urllib.request.urlopen` wrapper that converts network/TLS failures into a clean one-line error instead of a raw traceback — see "SSL/network errors" below).
- `tools/_env.py` — resolves `llama.env`/`presets/*.env` by shelling out to `bash -c 'set -a; source llama.env; source presets/<name>.env; set +a; env'` and parsing the output. This is deliberate: config stays in its existing bash format (`${VAR}` expansion, the `: "${VAR:=default}"` override idiom) instead of being reimplemented in Python. `set -a` is required — without it, `env` only reflects *exported* variables, and these files never `export`.
- `tools/run.py` — `llama-tool.py run [preset] [--dry-run] [--list]`. Resolves config via `_env.py`, validates the binary/model/mmproj paths (warns instead of failing under `--dry-run`), builds the `llama-server` argv via the `ARG_SPEC` table (which must stay in the exact order run-llama.sh's bash ARGS builder used — see "ARG_SPEC ordering" below), and `os.execv`s it. Adding a model never requires editing this file — drop a new `presets/*.env`, by hand or via `models download`.
- `tools/engine.py` — `llama-tool.py engine {install,check,list,use}`. Fetches prebuilt `llama.cpp` releases (Vulkan Linux x64, by default) from `ggml-org/llama.cpp` on GitHub into `vendor/llama.cpp/<tag>/` (gitignored), and maintains a `vendor/llama.cpp/current` symlink to the active version.
- `tools/models.py` — `llama-tool.py models {download,delete,list,list-files,check}`. Downloads GGUF models from Hugging Face into `models/<org>/<repo>/` (gitignored), verifying each download against Hugging Face's recorded sha256 before keeping it. `download` also writes a matching `presets/<name>.env`; `delete <preset>` removes the preset and its model file(s) together (skipping a file still referenced by another preset). The only bookkeeping is `# hf-source: <org>/<repo>/<file>` / `# hf-sha256: ...` comment lines embedded in the generated preset (read via `read_tags()`) — `list`/`check`/`delete` all just read those back out, so there's no separate manifest/database to fall out of sync.
- `tools/cache.py` — `llama-tool.py cache {check,stats}`. `check` sends the same system prompt twice to a running `llama-server` (or through a `llama-slot-proxy` agent path) and compares `usage.prompt_tokens_details.cached_tokens` to confirm slot caching is actually working. `stats` (ported from the old standalone `llama_cache_stats.py`) parses `llama-server` log output for prompt-cache reuse per request/slot; its regexes tolerate the slot-id padding (`id  2`) and completion-line wording (`stop processing` vs. older `prompt done`) that vary between llama.cpp builds. `stats` with no file argument falls back to `LLAMA_LOG_FILE` (`llama.env`) via `_env.py`'s `resolve_log_file()`.
- `tools/log.py` — `llama-tool.py log [file] [-n LINES] [--no-follow]`. Thin `os.execvp("tail", ...)` wrapper (uses `-F`, not `-f`, so it keeps following across logrotate's `copytruncate` rotation); defaults to `LLAMA_LOG_FILE` if no file is given. Not reimplemented in Python on purpose — `tail` already handles this correctly.
- `presets/*.env` — one file per model, still plain bash (see `_env.py` above for why). Each uses `: "${VAR:=default}"` (not plain `VAR=`) so any value can be overridden per-invocation via the environment without editing the file, e.g. `LLAMA_CTX_SIZE=16384 ./llama-tool.py run qwen3.5-9b`. The first `#` comment line in each file is shown as its description in `run --list`. Several presets are alternate quant/source builds of the same base model kept on distinct ports specifically so they can run side by side for A/B comparison. Presets generated by `models download` additionally carry `# hf-source:`/`# hf-sha256:` (and `# hf-mmproj:`/`# hf-mmproj-sha256:` if applicable) comment lines — don't strip these, they're what `models check`/`delete`/`list` key off of.
- `llama.env` — shared config sourced before the preset: base paths, the vendored-engine resolution (`LLAMA_CPP_VERSION` → `LLAMA_BINARY`), local model storage (`LLAMA_MODELS_DIR`, used by generated presets), `LLAMA_LOG_FILE` (default log path for `cache stats`/`log`, must be kept in sync by hand with `deploy/llama-server.service`'s `StandardOutput=`/`StandardError=` since systemd directives can't reference it), `ACTIVE_PRESET` (used by systemd, which always invokes `llama-tool.py run` with no args), network/API settings, batching, and the prompt-cache/slot section. Comments above each variable document its valid range and, for the cache/slot vars, how it relates to llama-slot-proxy — keep those in sync when changing a value.
- `deploy/llama-server.service` — systemd unit; ships with generic defaults (`User=1000`/`Group=1000` — systemd accepts a numeric id, and 1000 is the first regular user on most Debian/Ubuntu hosts — and `WorkingDirectory=/opt/llama-service`) that whoever deploys edits to match their actual user and clone path before running `init` (see README's "First-time setup"). Runs `llama-tool.py run`, logs appended to `llama-server.log`, `LimitMEMLOCK=infinity` for `--mlock` support. Relies on the `#!/usr/bin/env python3` shebang, so `llama-tool.py` must stay executable (`chmod +x`). Installed to `/etc/systemd/system/` by `llama-tool.py init`. Loads `deploy/llama-server.env` (gitignored, not installed by `init`) via `EnvironmentFile=` for secrets — see `llama.env`'s `LLAMA_API_KEY` note below.
- `deploy/llama-server.logrotate` — logrotate config for `llama-server.log` (weekly, keep 4, compressed). Installed to `/etc/logrotate.d/llama-server` by `llama-tool.py init`.
- `deploy/llama-server.env.example` — template for `deploy/llama-server.env` (gitignored). Copy it and fill in real values; never commit the copy.
- `scripts/ram.sh` — ad hoc snippets (not a script meant to be executed as-is) for checking swap activity and which processes are using swap.
- `vendor/llama.cpp/` — gitignored; populated by `llama-tool.py engine`, not checked in.
- `models/` — gitignored; populated by `llama-tool.py models`, not checked in.

## Common commands

First-time host setup (installs systemd unit + logrotate config from `deploy/`):
```bash
./llama-tool.py init                 # prompts before touching /etc; --dry-run to preview, -y to skip prompt
```

Manage the vendored llama.cpp engine:
```bash
./llama-tool.py engine install                 # install latest release, activate it
./llama-tool.py engine check                    # is a newer release available? (no download)
./llama-tool.py engine list                     # installed versions (* = active)
./llama-tool.py engine use b9900                # switch to an installed version, instantly
```

Manage downloaded models:
```bash
./llama-tool.py models list-files <org>/<repo>           # see available quants + sizes before downloading
./llama-tool.py models download <org>/<repo> <file.gguf> [mmproj.gguf]   # fetch + generate a matching preset
./llama-tool.py models list                              # what's downloaded, with sizes
./llama-tool.py models check                             # flag models Hugging Face has since changed
./llama-tool.py models delete <preset>                    # remove a preset + its model file(s) (with confirmation)
```

List and run presets:
```bash
./llama-tool.py run --list                          # available presets + descriptions
./llama-tool.py run qwen3.5-9b                       # run a specific preset
./llama-tool.py run                                  # run $ACTIVE_PRESET from llama.env
./llama-tool.py run qwen3.5-9b --dry-run             # print the resolved command, don't launch
LLAMA_CTX_SIZE=16384 ./llama-tool.py run qwen3.5-9b  # one-off override of any LLAMA_* var
```

Verify prompt-cache reuse:
```bash
./llama-tool.py cache check                              # live HTTP check, direct, 127.0.0.1:18080
./llama-tool.py cache check http://host:8090/router       # through llama-slot-proxy, agent "router"
./llama-tool.py cache stats                                           # defaults to LLAMA_LOG_FILE
./llama-tool.py cache stats /opt/llama-service/llama-server.log      # log-based, real traffic
docker logs -f llama-server 2>&1 | ./llama-tool.py cache stats -f     # live-follow
```

Watch the log:
```bash
./llama-tool.py log                # tail -F on LLAMA_LOG_FILE
./llama-tool.py log --no-follow -n 200
```

Starting the service itself is left to systemd directly (`init` doesn't do this — see README.md): `sudo systemctl start llama-server`.

## Editing conventions

- When adding a new preset: copy an existing `presets/*.env` file, keep the `: "${VAR:=...}"` idiom (plain `VAR=` would block per-invocation overrides), and pick a port that doesn't collide with other presets you might run concurrently. No changes to `llama-tool.py`/`tools/*.py` are needed.
- **ARG_SPEC ordering** (`tools/run.py`): the list is a single ordered sequence of `(kind, env_var, flag)` tuples, not two separate value/bool lists — that matters because run-llama.sh's original bash builder interleaved boolean flags (`--jinja`, `--kv-offload`, etc.) between the value flags and the prompt-cache/slot section. Splitting them into separate lists changes the resulting argv order (harmless to `llama-server` itself, but breaks byte-for-byte diffing against what the old bash version produced, which is how this port was verified). Some flags are deliberately absent (`--mmproj`, `--n-cpu-moe`, `--tensor-split`, `--mlock`) matching lines run-llama.sh had commented out — `LLAMA_MMPROJ` is still validated for existence even though it's never passed as a flag.
- **`tools/_env.py`'s `set -a` trick**: don't "simplify" this to a plain `source ...; env` — non-exported bash variables (which is everything in `llama.env`/`presets/*.env`, since they never say `export`) don't show up in `env`'s output without `set -a` first. Also don't split `source llama.env` and `source presets/<name>.env` into two separate `subprocess.run` calls — they must run in one bash invocation so a preset's `: "${VAR:=...}"` sees llama.env's plain `VAR=` assignments as already-set (matching the original single-bash-process sourcing order), and so a CLI env override (`LLAMA_CTX_SIZE=16384 ./llama-tool.py run ...`) is visible to both.
- **SSL/network errors**: always route `urllib.request.urlopen()` calls through `tools/_common.py`'s `urlopen()` (or catch `urllib.error.URLError` broadly and call `describe_url_error()`), not raw `urlopen()` with only `HTTPError` caught — a plain `except HTTPError` misses connection-level failures (DNS, timeout, TLS), and this repo has actually hit an unconfigured-CA-bundle `SSLCertVerificationError` from a non-system `python3` in the wild; `describe_url_error()` adds a hint for exactly that case instead of leaking a raw traceback.
- `tools/models.py` resolves download URLs as `https://huggingface.co/<org>/<repo>/resolve/main/<file>` and file metadata (size, sha256) via `https://huggingface.co/api/models/<org>/<repo>?blobs=true` — the `?blobs=true` query param is required to get per-file size/sha256; without it `siblings` only lists filenames. Downloads go to a `.part` file first and are only renamed into place after sha256 verification succeeds, so an interrupted or corrupted download never masquerades as a good one.
- `tools/engine.py` downloads `llama-<tag>-bin-${LLAMA_CPP_ASSET}.tar.gz` from `ggml-org/llama.cpp` releases; the default asset (`ubuntu-vulkan-x64`) is a flat directory (after stripping the tarball's single top-level dir) with runtime CPU dispatch (multiple `libggml-cpu-*.so` variants), so no separate AVX2 build is needed. Extraction goes through a temp dir + `shutil.move` rather than manually rewriting `tarfile` member names, so symlinks/permissions inside the archive are preserved correctly.
- `llama.env`'s paths (`LLAMA_MODELS_DIR`, `LLAMA_BINARY`, `LLAMA_LOG_FILE`) are all derived from `LLAMA_ENV_DIR` (computed at source time as the directory containing `llama.env`), so they're portable across hosts/usernames without editing. `deploy/llama-server.service`/`deploy/llama-server.logrotate` are the exception — their paths are hardcoded (systemd units can't reference `llama.env`), so those two files' `User=`/`Group=`/working-directory paths need manual, per-host editing (see the `deploy/llama-server.service` bullet above).
- `LLAMA_PARALLEL` (in `llama.env`) is the slot count and must be >= the number of agents configured in llama-slot-proxy's `config.yaml`; `LLAMA_CTX_SIZE` (per preset) is split evenly across slots, so bumping `LLAMA_PARALLEL` without also reviewing per-preset context sizes can starve individual agents' caches.
- **Never hardcode `LLAMA_API_KEY` (or any secret) into `llama.env`** — that file is committed to git. This repo previously had a live key hardcoded there from its first commit onward, which required a `git filter-repo` history rewrite + key rotation to fix once the repo was made public; don't reintroduce that. `llama.env` uses `: "${LLAMA_API_KEY:=}"` (empty default); real values go in `deploy/llama-server.env` (gitignored, loaded via systemd's `EnvironmentFile=`) or an exported shell var for manual use.
- This was ported from an all-bash version (git history has it, e.g. `run-llama.sh`/`update-llama-cpp.sh`/`manage-models.sh`/`check-cache-reuse.sh`/`llama_cache_stats.py`) specifically to move off bash's rougher edges (`set -e`/`set -u`/`pipefail` interactions that caused real bugs during that version's development) in favor of stdlib Python — don't reintroduce a bash rewrite of `tools/*.py` without a strong reason.
