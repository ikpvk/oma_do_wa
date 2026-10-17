#!/usr/bin/env python3
"""Docker Compose helper for the nausica.compose-monitor plugin.

Every subcommand prints one JSON document on stdout so the QML side only ever
has to JSON.parse a single blob. Watched compose files and the last update
check per file live in $XDG_STATE_HOME/nausica.compose-monitor/state.json.

  status            projects with their containers and CPU / RAM / disk usage
  pick              open the desktop file chooser and watch the chosen files
  add <file>...     watch compose files
  remove <file>     stop watching a compose file
  check <file>      compare local image digests with the registry
  update <file>     pull new images and recreate changed containers
"""

import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

STATE_DIR = os.path.join(
    os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"),
    "nausica.compose-monitor",
)
STATE_FILE = os.path.join(STATE_DIR, "state.json")
REGISTRY_TIMEOUT_SEC = 30


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
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}
    state.setdefault("files", [])
    state.setdefault("checks", {})
    return state


def save_state(state):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_FILE)


def json_lines(text):
    """Docker prints either a JSON array or one object per line."""
    text = text.strip()
    if not text:
        return []
    if text.startswith("["):
        return json.loads(text)
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def compose(path, *args, timeout=60):
    return run(["docker", "compose", "-f", path, *args], timeout=timeout)


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


def project_containers(path):
    code, out, err = compose(path, "ps", "-a", "--format", "json")
    if code != 0:
        raise RuntimeError(err or "docker compose ps failed")
    return json_lines(out)


def status():
    state = load_state()
    projects = []
    ids = []

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
            })
            if row.get("ID"):
                ids.append(row["ID"])

    stats, sizes = {}, {}
    if ids:
        # docker stats --no-stream samples for ~2s; run it alongside the size
        # query instead of after it.
        with ThreadPoolExecutor(max_workers=2) as pool:
            stats_job = pool.submit(run, ["docker", "stats", "--no-stream", "--format", "json", *ids])
            filters = [arg for cid in ids for arg in ("--filter", "id=" + cid)]
            sizes_job = pool.submit(run, ["docker", "ps", "-a", "--size", "--no-trunc", "--format", "json", *filters])
            code, out, _ = stats_job.result()
            if code == 0:
                for row in json_lines(out):
                    stats[row.get("ID", "")[:12]] = row
            code, out, _ = sizes_job.result()
            if code == 0:
                for row in json_lines(out):
                    sizes[row.get("ID", "")[:12]] = row.get("Size", "")

    for project in projects:
        for c in project["containers"]:
            short = c["id"][:12]
            s = stats.get(short)
            if s and c["state"] == "running":
                c["cpu"] = percent(s.get("CPUPerc"))
                c["memUsage"] = s.get("MemUsage", "")
                c["memPercent"] = percent(s.get("MemPerc"))
            c["disk"] = sizes.get(short, "")

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
    # `up -d` only recreates containers whose image or config changed.
    code, out, err = compose(path, "up", "-d", "--remove-orphans", timeout=900)
    if code != 0:
        fail(err.splitlines()[-1] if err else "docker compose up failed")
    state = load_state()
    state["checks"].pop(path, None)
    save_state(state)
    emit({"ok": True, "file": path})


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
