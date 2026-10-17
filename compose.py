#!/usr/bin/env python3
"""Docker Compose helper for the ikpvk.oma-do-wa plugin.

Every subcommand prints one JSON document on stdout so the QML side only ever
has to JSON.parse a single blob. Watched compose files and the last update
check per file live in $XDG_STATE_HOME/ikpvk.oma-do-wa/state.json.

  status            projects with their containers and CPU / RAM / disk usage
  pick              open the desktop file chooser and watch the chosen files
  add <file>...     watch compose files
  remove <file>     stop watching a compose file
  check <file>      compare local image digests with the registry
  update <file>     pull new images and recreate changed containers
"""

import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

STATE_HOME = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
STATE_DIR = os.path.join(STATE_HOME, "ikpvk.oma-do-wa")
STATE_FILE = os.path.join(STATE_DIR, "state.json")
# Where the plugin kept its state under its old ID, nausica.compose-monitor.
LEGACY_STATE_FILE = os.path.join(STATE_HOME, "nausica.compose-monitor", "state.json")
SIZES_FILE = os.path.join(STATE_DIR, "sizes.json")
REGISTRY_TIMEOUT_SEC = 30
# `docker ps --size` walks every container's writable layer, so its result is
# reused for this long instead of being recomputed on every refresh.
SIZES_MAX_AGE_SEC = 60


def run(args, timeout=60):
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    return proc.returncode, proc.stdout, proc.stderr.strip()


def emit(value):
    json.dump(value, sys.stdout)
    sys.stdout.write("\n")


def fail(message):
    emit({"ok": False, "error": message})
    sys.exit(1)


def load_state():
    state = {}
    for path in (STATE_FILE, LEGACY_STATE_FILE):
        try:
            with open(path) as f:
                state = json.load(f)
            break
        except (OSError, ValueError):
            continue
    state.setdefault("files", [])
    state.setdefault("checks", {})
    return state


def write_json(path, value):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(value, f, indent=2)
    os.replace(tmp, path)


def save_state(state):
    write_json(STATE_FILE, state)


def json_lines(text):
    """Docker prints either a JSON array or one object per line."""
    text = text.strip()
    if not text:
        return []
    if text.startswith("["):
        return json.loads(text)
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def compose_files(path):
    """The watched file plus its override file, if one sits next to it.

    An explicit `-f` stops docker compose from loading `*.override.yml` on its
    own, so it is passed explicitly to match a plain `docker compose` run.
    """
    stem, ext = os.path.splitext(path)
    files = [path]
    for candidate_ext in dict.fromkeys((ext, ".yaml", ".yml")):
        override = stem + ".override" + candidate_ext
        if os.path.isfile(override):
            files.append(override)
            break
    return files


def compose(path, *args, timeout=60):
    flags = [arg for f in compose_files(path) for arg in ("-f", f)]
    return run(["docker", "compose", *flags, *args], timeout=timeout)


def compose_config(path):
    code, out, err = compose(path, "config", "--format", "json")
    if code != 0:
        raise RuntimeError(err or "docker compose config failed")
    return json.loads(out)


# --- status ------------------------------------------------------------------


def percent(text):
    try:
        return float(str(text).rstrip("%"))
    except ValueError:
        return 0.0


def container_stats(ids):
    """`docker stats` for running containers, keyed by short ID.

    docker stats fails outright if any ID has gone away since `compose ps`, so
    on failure retry once with only the IDs that are still running.
    """
    if not ids:
        return {}
    code, out, _ = run(["docker", "stats", "--no-stream", "--format", "json", *ids])
    if code != 0:
        code, out, _ = run(["docker", "ps", "-q", "--no-trunc"])
        alive = {cid[:12] for cid in out.split()} if code == 0 else set()
        ids = [cid for cid in ids if cid[:12] in alive]
        if not ids:
            return {}
        code, out, _ = run(["docker", "stats", "--no-stream", "--format", "json", *ids])
        if code != 0:
            return {}
    return {row.get("ID", "")[:12]: row for row in json_lines(out)}


def image_tag(ref):
    """"ghcr.io/a/b:v3@sha256:..." → "v3"; "" when the reference has no tag."""
    name = ref.split("@", 1)[0]
    last = name.rsplit("/", 1)[-1]
    return last.split(":", 1)[1] if ":" in last else ""


VERSION_NUMBER = re.compile(r"v?\d+(?:\.\d+)*")


def short_version(text):
    """Leading version number: "14-vectorchord0.4.3" → "14", "1.27.3-alpine"
    → "1.27.3". Text that doesn't start with one ("latest") is kept whole."""
    m = VERSION_NUMBER.match(text)
    return m.group(0) if m else text


def refines(precise, rough):
    """True if `precise` is a more exact form of `rough` (14.19 of 14)."""
    return precise.lstrip("v").startswith(rough.lstrip("v") + ".")


def image_version(ref, labels, env):
    """(short, full) version of the image a container runs.

    Tags like "latest" or "v3" rarely say much, so the full version is the
    OCI version label, else a <IMAGE>_VERSION env var (e.g. VALKEY_VERSION
    for valkey; PG_VERSION for postgres images), else the tag. The short one
    is its leading version number, made more exact by a later source that
    agrees with it: immich's postgres label "14-vectorchord0.4.3-..." plus
    PG_VERSION=14.19-1.pgdg12+1 gives "14.19".
    """
    base = ref.split("@", 1)[0].rsplit("/", 1)[-1].split(":", 1)[0]
    keys = [base.upper().replace("-", "_").replace(".", "_") + "_VERSION="]
    if "postgres" in base or "postgis" in base:
        keys.append("PG_VERSION=")
    candidates = [(labels or {}).get("org.opencontainers.image.version", "")]
    for key in keys:
        candidates += [var[len(key):] for var in env or [] if var.startswith(key)]
    candidates.append(image_tag(ref))
    candidates = [c.strip() for c in candidates if c and c.strip()]
    if not candidates:
        return "", ""
    full = candidates[0]
    short = short_version(full)
    for candidate in candidates[1:]:
        if refines(short_version(candidate), short):
            short = short_version(candidate)
    return short, full


def container_images(ids):
    """Image reference and version per short container ID."""
    if not ids:
        return {}
    _, out, _ = run(["docker", "inspect", *ids])
    # docker inspect exits non-zero if any ID has gone away but still prints
    # the ones it found.
    try:
        rows = json.loads(out or "[]")
    except ValueError:
        return {}
    images = {}
    for row in rows:
        config = row.get("Config") or {}
        ref = config.get("Image", "")
        short, full = image_version(ref, config.get("Labels"), config.get("Env"))
        images[row.get("Id", "")[:12]] = {"image": ref, "version": short, "versionFull": full}
    return images


def container_sizes(ids):
    """`docker ps --size` output keyed by short ID, cached for SIZES_MAX_AGE_SEC."""
    if not ids:
        return {}
    wanted = {cid[:12] for cid in ids}
    try:
        with open(SIZES_FILE) as f:
            cache = json.load(f)
        if (time.time() - cache["at"] < SIZES_MAX_AGE_SEC
                and wanted <= set(cache["sizes"])):
            return cache["sizes"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    filters = [arg for cid in ids for arg in ("--filter", "id=" + cid)]
    code, out, _ = run(["docker", "ps", "-a", "--size", "--no-trunc", "--format", "json", *filters])
    if code != 0:
        return {}
    sizes = {row.get("ID", "")[:12]: row.get("Size", "") for row in json_lines(out)}
    try:
        write_json(SIZES_FILE, {"at": time.time(), "sizes": sizes})
    except OSError:
        pass
    return sizes


def project_containers(path):
    code, out, err = compose(path, "ps", "-a", "--format", "json")
    if code != 0:
        raise RuntimeError(err or "docker compose ps failed")
    return json_lines(out)


def status():
    state = load_state()
    projects = []
    ids, running_ids = [], []

    for path in state["files"]:
        project = {
            "file": path,
            "name": os.path.basename(os.path.dirname(path)) or path,
            "containers": [],
            "error": "",
            "check": state["checks"].get(path),
        }
        projects.append(project)
        if not os.path.isfile(path):
            project["error"] = "Compose file not found"
            continue
        try:
            rows = project_containers(path)
        except (RuntimeError, ValueError, subprocess.TimeoutExpired) as e:
            project["error"] = str(e)
            continue
        for row in rows:
            if row.get("Project"):
                project["name"] = row["Project"]
            project["containers"].append({
                "id": row.get("ID", ""),
                "name": row.get("Name", ""),
                "service": row.get("Service", ""),
                "state": row.get("State", ""),
                "health": row.get("Health", ""),
                "status": row.get("Status", ""),
                "cpu": 0.0,
                "memUsage": "",
                "memPercent": 0.0,
                "disk": "",
                "image": row.get("Image", ""),
                "version": "",
                "versionFull": "",
            })
            if row.get("ID"):
                ids.append(row["ID"])
                if row.get("State") == "running":
                    running_ids.append(row["ID"])

    # docker stats --no-stream samples for ~2s; run it alongside the size
    # query instead of after it.
    with ThreadPoolExecutor(max_workers=3) as pool:
        stats_job = pool.submit(container_stats, running_ids)
        sizes_job = pool.submit(container_sizes, ids)
        images_job = pool.submit(container_images, ids)
        stats, sizes, images = stats_job.result(), sizes_job.result(), images_job.result()

    for project in projects:
        for c in project["containers"]:
            short = c["id"][:12]
            s = stats.get(short)
            if s and c["state"] == "running":
                c["cpu"] = percent(s.get("CPUPerc"))
                c["memUsage"] = s.get("MemUsage", "")
                c["memPercent"] = percent(s.get("MemPerc"))
            c["disk"] = sizes.get(short, "")
            image = images.get(short)
            if image:
                c["image"] = image["image"] or c["image"]
                c["version"] = image["version"]
                c["versionFull"] = image["versionFull"]

    emit({"ok": True, "projects": projects})


# --- watch list ----------------------------------------------------------------


def add(paths):
    state = load_state()
    added = []
    for p in paths:
        p = os.path.realpath(os.path.expanduser(p))
        if not os.path.isfile(p):
            fail("Not a file: " + p)
        if p not in state["files"]:
            state["files"].append(p)
            added.append(p)
    save_state(state)
    emit({"ok": True, "added": added})


def pick():
    code, out, err = run(
        ["omarchy-file-select", "--title", "Pick a docker compose file",
         "--multiple", "--extensions", "yml yaml"],
        timeout=700,
    )
    if code == 1:
        emit({"ok": True, "added": []})
        return
    if code != 0:
        fail(err or "File chooser failed")
    add([line for line in out.splitlines() if line.strip()])


def remove(path):
    state = load_state()
    state["files"] = [p for p in state["files"] if p != path]
    state["checks"].pop(path, None)
    save_state(state)
    emit({"ok": True})


# --- update check ----------------------------------------------------------------


def remote_digest(image):
    code, out, err = run(
        ["docker", "buildx", "imagetools", "inspect", image, "--format", "{{.Manifest.Digest}}"],
        timeout=REGISTRY_TIMEOUT_SEC,
    )
    if code != 0:
        raise RuntimeError(err.splitlines()[-1] if err else "registry lookup failed")
    return out.strip()


def local_digests(image):
    code, out, _ = run(["docker", "image", "inspect", image, "--format", "{{json .RepoDigests}}"])
    if code != 0:
        return None
    return [d.split("@", 1)[-1] for d in json.loads(out.strip() or "[]")]


def check_image(service, image):
    entry = {"service": service, "image": image, "status": "current", "detail": ""}
    if "@sha256:" in image:
        entry.update(status="pinned", detail="Pinned to a digest")
        return entry
    local = local_digests(image)
    if local is None:
        entry.update(status="update", detail="Not pulled yet")
        return entry
    try:
        remote = remote_digest(image)
    except (RuntimeError, subprocess.TimeoutExpired) as e:
        entry.update(status="error", detail=str(e))
        return entry
    if remote not in local:
        entry.update(status="update", detail="New image available")
    return entry


def check(path):
    try:
        config = compose_config(path)
    except (RuntimeError, ValueError, subprocess.TimeoutExpired) as e:
        fail(str(e))

    images, futures = [], []
    with ThreadPoolExecutor(max_workers=6) as pool:
        for name, service in sorted(config.get("services", {}).items()):
            image = service.get("image") or ""
            if service.get("build"):
                images.append({"service": name, "image": image, "status": "local", "detail": "Built locally"})
            elif image:
                futures.append(pool.submit(check_image, name, image))
        images += [f.result() for f in futures]

    result = {
        "checkedAt": int(time.time()),
        "updates": sum(1 for i in images if i["status"] == "update"),
        "errors": sum(1 for i in images if i["status"] == "error"),
        "images": images,
    }
    state = load_state()
    state["checks"][path] = result
    save_state(state)
    emit({"ok": True, "file": path, "check": result})


# --- update ----------------------------------------------------------------------


def update(path):
    # Only services whose image is pulled from a registry; built ones would make
    # `pull` fail on registries they were never pushed to.
    code, out, err = compose(path, "pull", "--ignore-buildable", timeout=1800)
    if code != 0:
        fail(err.splitlines()[-1] if err else "docker compose pull failed")
    # Recreate only services that are running now, so stopped services stay
    # stopped; `up -d` leaves them alone unless their image or config changed.
    # No --remove-orphans: another compose file may share this project name.
    code, out, err = compose(path, "ps", "--services", "--filter", "status=running")
    if code != 0:
        fail(err.splitlines()[-1] if err else "docker compose ps failed")
    services = [s for s in out.split() if s]
    if services:
        code, out, err = compose(path, "up", "-d", *services, timeout=900)
        if code != 0:
            fail(err.splitlines()[-1] if err else "docker compose up failed")
    state = load_state()
    state["checks"].pop(path, None)
    save_state(state)
    emit({"ok": True, "file": path, "restarted": services})


def main(argv):
    if not argv:
        fail("usage: compose.py status|pick|add|remove|check|update")
    cmd, args = argv[0], argv[1:]
    if cmd == "status":
        status()
    elif cmd == "pick":
        pick()
    elif cmd == "add" and args:
        add(args)
    elif cmd in ("remove", "check", "update") and len(args) == 1:
        {"remove": remove, "check": check, "update": update}[cmd](args[0])
    else:
        fail("unknown command: " + " ".join(argv))


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except FileNotFoundError as e:
        fail("Missing command: " + str(e.filename))
