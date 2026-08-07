# llama-service

Systemd-managed `llama-server` (llama.cpp, Vulkan backend) with a
locally-vendored, self-updating engine, a preset-based model switcher, and
prompt-cache tuning aimed at running behind
[llama-slot-proxy](https://github.com/shuricksumy/llama-slot-proxy).

All tooling lives behind one entrypoint, `llama-tool.py` — a stdlib-only
Python CLI (no pip install, no venv):

```bash
./llama-tool.py --help          # lists all commands
./llama-tool.py <command> --help
```

## First-time setup

After cloning this repo onto a host:

```bash
cp deploy/llama-server.env.example deploy/llama-server.env
$EDITOR deploy/llama-server.env             # set LLAMA_API_KEY (gitignored, never committed)
$EDITOR deploy/llama-server.service         # set User=/Group=/WorkingDirectory= for your host (see below)
$EDITOR deploy/llama-server@.service        # same edits, for the multi-instance template (see below)
./llama-tool.py init                        # installs the systemd units + logrotate config, enables on boot
./llama-tool.py engine install              # fetches llama-server into vendor/llama.cpp/
./llama-tool.py models download <org>/<repo> <file.gguf>   # fetches a model, generates a preset
sudo systemctl start llama-server
```

`deploy/llama-server.service` ships with generic defaults — `User=1000`/`Group=1000`
(the first regular user on most Debian/Ubuntu hosts; systemd accepts a
numeric id here) and `WorkingDirectory=/opt/llama-service`. Edit both to
match your actual user and wherever you cloned this repo *before* running
`init`, since it copies the file as-is into `/etc/systemd/system/`.
`deploy/llama-server@.service` (see "Running several models at once") and
`deploy/llama-server.logrotate` have the same working-directory path and
need the same edit if you didn't clone to `/opt/llama-service`.

`init` copies `deploy/llama-server.service`, `deploy/llama-server@.service`,
`deploy/llama-server.logrotate`, and the `deploy/llama-server-restart[@].service`/
`.timer` pairs (see "Periodic restart" below) into `/etc/` via `sudo` and runs
`systemctl daemon-reload` + `enable` (for `llama-server.service` only —
see "Installing the systemd service" below for exactly what it does and how
to do it by hand instead). It shows every command before running it and asks
for confirmation (`-y` to skip, `--dry-run` to preview, `--no-enable` to
skip the boot-enable step). It does not start any service, does not enable
the restart timers (opt in per-host, see "Periodic restart"), and does not
touch user/group membership — see "Vulkan / render group access" for that.

### Secrets

`llama.env` is committed to git, so `LLAMA_API_KEY` is deliberately left
empty there rather than hardcoded. Real values live in
`deploy/llama-server.env` (copied from the `.example` file above, gitignored)
and are loaded via the systemd unit's `EnvironmentFile=` — an already-set
env var always wins over `llama.env`'s `: "${VAR:=default}"` fallback. For
manual/CLI use outside systemd, `export LLAMA_API_KEY="..."` in your shell
instead. If `LLAMA_API_KEY` is left empty, the server runs without an API
key — fine on `127.0.0.1`, not recommended with `LLAMA_HOST=0.0.0.0`.

## The llama.cpp engine

The `llama-server` binary is vendored locally under `vendor/llama.cpp/`
instead of depending on LM Studio's bundled backend:

```bash
./llama-tool.py engine install                 # install latest release, activate it
./llama-tool.py engine check                    # see if a newer release exists, no download
./llama-tool.py engine list                     # list installed versions (* = active)
./llama-tool.py engine install b9900            # install a specific version, activate it
./llama-tool.py engine use b9900                # switch to an already-installed version (instant)
./llama-tool.py engine install --no-activate    # install without switching `current`
```

Downloads the official prebuilt Vulkan Linux x64 build from
[ggml-org/llama.cpp releases](https://github.com/ggml-org/llama.cpp/releases)
into `vendor/llama.cpp/<tag>/`, and keeps a `vendor/llama.cpp/current`
symlink pointing at the active one (`vendor/` is gitignored — these are
per-host binary downloads, not source). `llama.env` resolves `LLAMA_BINARY`
from `LLAMA_CPP_VERSION` (default `current`); set `LLAMA_CPP_VERSION` to a
specific tag there, or override it per-run, to pin a preset to an exact
engine build regardless of what's newest:

```bash
LLAMA_CPP_VERSION=b9900 ./llama-tool.py run qwen3.5-9b --dry-run
```

Set `GITHUB_TOKEN` to raise GitHub's 60/hr unauthenticated API rate limit if
you're checking for updates often (e.g. from a cron job). Set
`LLAMA_CPP_ASSET` to change which release asset is fetched (default
`ubuntu-vulkan-x64`; e.g. `ubuntu-x64` for CPU-only, `ubuntu-rocm-7.2-x64`
for ROCm) if your hardware needs a different backend.

## Models

Models are downloaded from Hugging Face into `models/<org>/<repo>/`
(gitignored, like `vendor/`) instead of depending on LM Studio's model
folder. Downloading a model also generates a matching preset; deleting a
preset removes its model file(s) too, unless another preset still uses them.

```bash
./llama-tool.py models list-files unsloth/Qwen3.5-9B-GGUF          # see available quants + sizes first
./llama-tool.py models download unsloth/Qwen3.5-9B-GGUF Qwen3.5-9B-Q4_K_M.gguf
./llama-tool.py models download unsloth/Qwen3.5-9B-GGUF Qwen3.5-9B-Q4_K_M.gguf mmproj-F32.gguf --preset my-qwen --port 18085
./llama-tool.py models list                                        # what's downloaded, with sizes on disk
./llama-tool.py models check                                       # flag models Hugging Face has since changed
./llama-tool.py models delete my-qwen                               # removes the preset and (if unshared) the model file(s)
```

Downloads go straight from Hugging Face's CDN (no `huggingface_hub`
dependency), resume on interruption, and are checksum-verified against
Hugging Face's recorded sha256 before being kept. The preset `models
download` writes records its source as `# hf-source: ...` / `# hf-sha256:
...` comment lines — that's the only bookkeeping involved, and it's what
`check`/`delete`/`list` read back. A preset without those tags (e.g. one
pointing at LM Studio's models, or a hand-written one) is left alone by
`delete` and `check`.

Set `HF_TOKEN` to access gated/private Hugging Face repos.

## Running a model

```bash
./llama-tool.py run --list              # see available presets
./llama-tool.py run qwen3.5-9b          # run a specific preset
./llama-tool.py run                     # run $ACTIVE_PRESET from llama.env
./llama-tool.py run qwen3.5-9b --dry-run   # print the resolved command, don't launch
```

Any `LLAMA_*` variable can be overridden for a single run without editing
files:

```bash
LLAMA_CTX_SIZE=16384 LLAMA_PORT=18081 ./llama-tool.py run qwen3.5-9b --dry-run
```

### Adding or switching models

Each model lives in its own `presets/<name>.env` file (still plain bash —
`llama-tool.py` resolves them by sourcing, so the `${VAR}` expansion and
`: "${VAR:=default}"` per-invocation override idiom keep working exactly as
before). To add a model, copy an existing preset and change `LLAMA_MODEL` /
`LLAMA_MMPROJ` — nothing in `llama-tool.py` needs to change, and it shows up
automatically in `run --list`. The first `#` comment line in the file is
used as its description.

Several presets for the same base model (different quant/source) are kept
side by side on different ports (e.g. `qwen3.5-9b` vs `qwen3.5-9b-lmstudio`)
so you can run two at once and compare them directly instead of
editing-and-restarting to A/B test.

`run` validates the binary and model paths before launching and fails fast
with a clear error if something's missing (`--dry-run` downgrades these to
warnings so you can preview a command from a machine that doesn't have the
model files, e.g. before pushing config to the actual host).

### Running several models at once

Any preset can already be run standalone (`./llama-tool.py run <preset> &`
in a second shell, or two separate terminals) as long as its `LLAMA_PORT`
doesn't collide with another running instance's. To have systemd manage
several presets as independent, auto-restarting services instead of one
`ACTIVE_PRESET`, use the `llama-server@.service` template installed by
`init` (see "Installing the systemd service"):

```bash
sudo systemctl enable --now llama-server@qwen3.5-9b
sudo systemctl enable --now llama-server@qwen3-embedding-0.6b
systemctl status 'llama-server@*'
```

or the equivalent, shorter `llama-tool.py services` form (see "Debugging /
status" below) — same effect, just the bare preset name instead of the
full unit name:

```bash
./llama-tool.py services enable qwen3.5-9b --now
./llama-tool.py services enable qwen3-embedding-0.6b --now
./llama-tool.py services list
```

Each instance gets its own log at `llama-server-<preset>.log`; pass that
path explicitly to `llama-tool.py log`/`cache stats` since `LLAMA_LOG_FILE`
only covers the plain `llama-server.service` / `ACTIVE_PRESET` path.

### Embedding models

`models download --embedding` generates a preset for an embedding-only
model instead of a chat model: it adds `--embedding`, `--pooling last`
(the pooling most decoder-only embedding models, including Qwen3-Embedding,
expect), and raises `LLAMA_BATCH_SIZE`/`LLAMA_UBATCH_SIZE` to 8192 so a
whole document is embedded in one pass instead of being split.

```bash
./llama-tool.py models list-files Qwen/Qwen3-Embedding-0.6B-GGUF
./llama-tool.py models download Qwen/Qwen3-Embedding-0.6B-GGUF Qwen3-Embedding-0.6B-Q8_0.gguf \
    --embedding --port 18085 --preset qwen3-embedding-0.6b
./llama-tool.py run qwen3-embedding-0.6b --dry-run
```

Query it at `POST /v1/embeddings` (OpenAI-compatible) or `POST /embedding`.
Run it alongside a chat preset with the multi-instance systemd template above,
or manually in a second shell.

#### Full example: rolling a new preset out to the server

Once a preset like the one above exists in git (model weights themselves are
gitignored — only `presets/*.env` and any `deploy/` changes are committed),
here's the end-to-end sequence to bring it up on the actual host as its own
service, alongside whatever's already running:

```bash
# 1. Pull the new preset (and any deploy/ changes) onto the server
cd /opt/llama-service
git pull

# 2. Re-run init if deploy/llama-server.service or deploy/llama-server@.service
#    changed (e.g. a new systemd directive) -- safe/idempotent to re-run
#    even if they didn't
./llama-tool.py init

# 3. Download the model's weights (not in git) -- re-running the same
#    `download` command is idempotent, it also rewrites the preset to match
./llama-tool.py models download Qwen/Qwen3-Embedding-0.6B-GGUF Qwen3-Embedding-0.6B-Q8_0.gguf \
    --embedding --port 18085 --preset qwen3-embedding-0.6b

# 4. Sanity-check the resolved command before starting anything
./llama-tool.py run qwen3-embedding-0.6b --dry-run

# 5. Start it as its own auto-restarting service
sudo systemctl enable --now llama-server@qwen3-embedding-0.6b
systemctl status llama-server@qwen3-embedding-0.6b

# 6. Verify it's actually serving
curl -s http://localhost:18085/v1/embeddings \
    -H "Content-Type: application/json" \
    -d '{"input": "hello world"}' | head -c 300

./llama-tool.py log llama-server-qwen3-embedding-0.6b.log --no-follow -n 50
```

If `deploy/llama-server@.service` picked up a new directive that needs
cgroup v2 (e.g. `MemorySwapMax=0`), confirm the host has it before step 2:
`cat /sys/fs/cgroup/cgroup.controllers` should exist and list controllers —
if it doesn't, the unit will fail to start and that directive needs removing.

## Prompt caching & llama-slot-proxy

This server is tuned to sit behind
[llama-slot-proxy](https://github.com/shuricksumy/llama-slot-proxy), a
companion repo (same account) that pins each caller (e.g. an n8n agent) to
a fixed `id_slot` so its system prompt stays warm in that slot's KV-cache
instead of being reprocessed on every call. This repo provides the
`llama-server` side of that pairing — the proxy repo's README documents the
`--parallel`/`--ctx-size`/`--cache-reuse` flags it expects in return.

For that to work, `llama.env` sets:

- `LLAMA_PARALLEL` — number of slots. **Must be >= the number of agents** in
  the proxy's `config.yaml`, since each agent gets its own `id_slot`.
- `LLAMA_CTX_SIZE` (per preset) — total context, split evenly across
  `LLAMA_PARALLEL` slots. Size it so every agent's system prompt + history
  fits with room to spare, or the cache gets evicted and you lose the point
  of pinning slots at all.
- `LLAMA_CACHE_PROMPT` — enables `--cache-prompt` so a slot's cache is reused
  instead of reprocessed. The proxy also sets `cache_prompt: true` per
  request as a belt-and-suspenders default.
- `LLAMA_CACHE_REUSE` — min chunk size (tokens) reused when the new prompt
  diverges partway through the cached one (e.g. same system prompt, new
  trailing turn), instead of discarding the whole slot.
- `LLAMA_CACHE_RAM` — RAM (MB) reserved for prompt-cache storage.
- `LLAMA_SLOT_SAVE_PATH` — enables `/slots` save/restore/erase so a slot's
  cache can survive a server restart. Empty = disabled.
- `LLAMA_DEFRAG_THOLD` — KV-cache defrag threshold; matters more the longer
  several slots stay warm concurrently.

### Verifying cache reuse is actually working

```bash
./llama-tool.py cache check                                  # direct to llama-server, 127.0.0.1:18080
./llama-tool.py cache check http://host:18080                 # direct, remote
./llama-tool.py cache check http://host:8090/router            # through llama-slot-proxy, agent "router"
```

Sends the same system prompt twice and prints
`usage.prompt_tokens_details.cached_tokens` for each call — it should be ~0
on the first call and roughly the shared prefix's token count on the second.

For cache behavior across real traffic (not just a synthetic check), parse
the server log instead:

```bash
./llama-tool.py cache stats                                    # defaults to LLAMA_LOG_FILE (llama.env)
./llama-tool.py cache stats /opt/llama-service/llama-server.log
docker logs -f llama-server 2>&1 | ./llama-tool.py cache stats -f   # live-follow
```

## Watching the log

```bash
./llama-tool.py log                 # tail -F on LLAMA_LOG_FILE (llama.env), follows across logrotate rotation
./llama-tool.py log -n 200          # show more trailing lines before following
./llama-tool.py log --no-follow     # print trailing lines once and exit
./llama-tool.py log /path/to/other.log
```

`LLAMA_LOG_FILE` defaults to `llama-server.log` in the repo root, matching
`deploy/llama-server.service`'s `StandardOutput=`/`StandardError=` — if you
change one, change the other (systemd directives can't reference
`llama.env`, so they aren't linked automatically).

## Installing the systemd service

`./llama-tool.py init` does this for you (see "First-time setup" above).
By hand:

```bash
cp ./deploy/llama-server.service  /etc/systemd/system/llama-server.service
cp ./deploy/llama-server@.service /etc/systemd/system/llama-server@.service
sudo systemctl daemon-reload
sudo systemctl enable llama-server
sudo systemctl start llama-server
sudo systemctl status llama-server
```

The unit's `EnvironmentFile=-/opt/llama-service/llama-server.env` loads
secrets (see "Secrets" above) — that file isn't created by `init`, copy it
from `deploy/llama-server.env.example` yourself first.

`llama-server.service` always runs `llama-tool.py run` with no arguments, so
it uses `ACTIVE_PRESET` from `llama.env` — set that to whichever preset
should run on boot/restart. `llama-server@.service` is a template instead:
`systemctl enable --now llama-server@<preset>` runs that specific preset as
its own service (`llama-tool.py run <preset>`), so several can run side by
side — see "Running several models at once" above. It isn't enabled by
`init` for any specific preset since that's a per-instance choice.

## logrotate

Also handled by `./llama-tool.py init`. By hand:

```bash
cp ./deploy/llama-server.logrotate /etc/logrotate.d/llama-server
sudo logrotate -vf /etc/logrotate.d/llama-server
```

Covers both `llama-server.log` (the single-instance/`ACTIVE_PRESET` service)
and `llama-server-*.log` (per-preset logs from the `llama-server@.service`
template).

## Periodic restart

`llama-server` (or any long-running inference server) can accumulate
fragmented/leaked memory over days of uptime. To bound that, `init` also
installs (but does not enable) a pair of systemd timers that restart the
service daily at 03:00, ±5 min randomized delay so it doesn't land on the
same instant every host:

```bash
sudo systemctl enable --now llama-server-restart.timer               # for llama-server.service
sudo systemctl enable --now llama-server-restart@<preset>.timer      # for llama-server@<preset>.service
systemctl list-timers 'llama-server-restart*'                        # confirm schedule
```

Each timer triggers a oneshot `systemctl try-restart` — a no-op if that
service isn't currently running, so it's safe to leave enabled even while
a service is stopped for maintenance. `Restart=always` on the underlying
service (see `deploy/llama-server.service`) already brings it back up.
To change the time, edit `deploy/llama-server-restart.timer`'s (and/or
`deploy/llama-server-restart@.timer`'s) `OnCalendar=` line, re-run `init`,
then `sudo systemctl daemon-reload`.

By hand, without `init`:

```bash
sudo cp ./deploy/llama-server-restart.service  /etc/systemd/system/llama-server-restart.service
sudo cp ./deploy/llama-server-restart.timer    /etc/systemd/system/llama-server-restart.timer
sudo cp ./deploy/llama-server-restart@.service /etc/systemd/system/llama-server-restart@.service
sudo cp ./deploy/llama-server-restart@.timer   /etc/systemd/system/llama-server-restart@.timer
sudo systemctl daemon-reload
sudo systemctl enable --now llama-server-restart.timer
```

## Memory / GPU report

`scripts/llama_mem_report_server.py` serves a live, auto-refreshing HTML
page — RAM/swap breakdown, AMD GPU VRAM/GTT usage, top processes by RSS and
by swap, and recent OOM-killer history — useful for keeping an eye on the
memory growth that the periodic-restart timers above are there to bound.
Stdlib only, no pip install:

```bash
python3 ./scripts/llama_mem_report_server.py 9909    # port defaults to 8899 if omitted
```

Then open `http://<host>:9909/` in a browser (auto-refreshes every 60s), or
`curl http://<host>:9909/`. It binds `0.0.0.0` with no authentication, so
only run it on a trusted network or behind a firewall/reverse proxy — same
caution as `LLAMA_API_KEY` above, but this one has no key at all.

### Running it as a service

`deploy/llama-mem-report.service` runs it on boot via systemd (port `9909`,
same as above). Not installed by `llama-tool.py init` — it's an optional
diagnostics tool, not part of the llama-server lifecycle — so set it up by
hand:

```bash
$EDITOR deploy/llama-mem-report.service   # set User=/Group=/WorkingDirectory= for your host, and the port if not 9909
sudo cp ./deploy/llama-mem-report.service /etc/systemd/system/llama-mem-report.service
sudo systemctl daemon-reload
sudo systemctl enable --now llama-mem-report
systemctl status llama-mem-report
```

## Debugging / status

`llama-tool.py services` is the easiest way to see what's running, restart
it, or bring a preset up/down as a service, without having to remember unit
names or glob patterns. It covers every systemd unit this repo installs —
`llama-server[@]`, the `llama-server-restart[@]` timers, and
`llama-mem-report` — grouped under one numbered list:

```bash
./llama-tool.py services list
# llama-server:
#   1) llama-server.service                       active running   Llama.cpp Vulkan Server
#   2) llama-server@qwen3-embedding-0.6b.service   active running   Llama.cpp Vulkan Server (qwen3-embedding-0.6b)
#
# Periodic restart timers:
#   3) llama-server-restart.timer                  active waiting   Daily restart timer for llama-server
#
# Memory / GPU report:
#   4) llama-mem-report.service                    active running   Memory / GPU / Swap Report HTTP Server
```

Restart one, several, or (default) every currently-running `llama-server`
instance:

```bash
./llama-tool.py services restart              # restart every loaded llama-server/llama-server@* instance
./llama-tool.py services restart 2            # restart just #2 from the list above
./llama-tool.py services restart 2 4          # restart #2 and #4 (works on timers/mem-report too, by number)
./llama-tool.py services restart qwen3.5-9b   # or by preset/unit name instead of number
./llama-tool.py services restart -y           # skip the confirmation prompt
./llama-tool.py services restart --dry-run    # preview the systemctl command without running it
```

Bring a preset up or down as a service — `start`/`stop` are one-off (not
persisted across reboot), `enable`/`disable` (with `--now` to also
act immediately) control whether it comes back on the next boot:

```bash
./llama-tool.py services start qwen3.5-9b-sushi-coder            # run it now, this boot only
./llama-tool.py services enable qwen3.5-9b-sushi-coder --now     # run it now AND on every future boot
./llama-tool.py services stop qwen3.5-9b-sushi-coder              # stop it now, leaves boot-enable state alone
./llama-tool.py services disable qwen3.5-9b-sushi-coder --now     # stop it now and remove from boot
```

Unlike `list`/`restart`, these work even for a preset that's never been
started before — that's how you turn a new `presets/<name>.env` into a
running `llama-server@<name>.service` for the first time (no need to
remember the `llama-server@<preset>.service` unit name; the bare preset
name resolves automatically). `llama-server`/`llama-mem-report` are
recognized directly too (`services enable llama-mem-report --now`).

`restart`'s numbers are positional, re-derived fresh from
`systemctl list-units` on every invocation — safe to `list` and then
`restart <n>` as two separate commands as long as nothing else
starts/stops a unit in between. With no selector, `restart` only touches
`llama-server`/`llama-server@*` (not the timers or mem-report, which
restarting doesn't meaningfully do as a group default) — target those
explicitly by number or name if you want to restart just one of them.

For the raw `systemctl`/`journalctl` commands `services` wraps (useful if
you want more detail than the numbered list gives you):

```bash
systemctl list-units 'llama-server*' --all       # every llama-server unit + state, running or not
systemctl status 'llama-server@*'                # just the active @-instances, with preset name in each block
systemctl status llama-server                    # the single ACTIVE_PRESET instance
systemctl status llama-mem-report                 # the memory/GPU report server, if installed

systemctl list-timers 'llama-server-restart*'    # next/last fire time per preset's restart timer
journalctl -u llama-server-restart.service --since -7d               # did the ACTIVE_PRESET restart actually run?
journalctl -u llama-server-restart@<preset>.service --since -7d      # same, per-instance

./llama-tool.py log --no-follow -n 100                                # tail LLAMA_LOG_FILE (ACTIVE_PRESET)
./llama-tool.py log llama-server-<preset>.log --no-follow -n 100      # tail a specific @-instance's log
journalctl -u llama-server -f                                          # or via journald, if StandardOutput=journal
```

A `llama-server-restart[@].service` showing `loaded inactive dead` in
`list-units` is expected, not a problem — it's a `Type=oneshot` unit that
only runs momentarily when its `.timer` fires, then goes back to `dead`
until the next scheduled restart. The `.timer` itself should show `active
waiting`; that's what confirms the schedule is armed.

For live memory/GPU/swap numbers instead of systemd state, see "Memory /
GPU report" above.

## Vulkan / render group access

```bash
sudo usermod -aG render <your-username>   # the User= from deploy/llama-server.service
getent group render

groups
ls -l /dev/dri/render*
vulkaninfo --summary
```
