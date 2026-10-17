"""Unit tests for compose.py with docker mocked out.

Run from the repo root: python3 -m unittest discover -s tests
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import compose  # noqa: E402


class FakeDocker:
    """Answers `run` calls from a list of (predicate, (code, stdout, stderr))."""

    def __init__(self, rules):
        self.rules = rules
        self.calls = []

    def __call__(self, args, timeout=60):
        self.calls.append(args)
        for match, result in self.rules:
            if match(args):
                return result() if callable(result) else result
        raise AssertionError("unexpected command: " + " ".join(args))


def has(*words):
    return lambda args: all(w in args for w in words)


class StateTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        state_dir = os.path.join(self.tmp.name, "state")
        for name, value in (
            ("STATE_DIR", state_dir),
            ("STATE_FILE", os.path.join(state_dir, "state.json")),
            ("SIZES_FILE", os.path.join(state_dir, "sizes.json")),
        ):
            patcher = mock.patch.object(compose, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def touch(self, name, text="services: {}\n"):
        path = os.path.join(self.tmp.name, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
        return path

    def capture(self, fn, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            try:
                fn(*args)
            except SystemExit:
                pass
        return json.loads(out.getvalue())


class ComposeFilesTest(StateTestCase):
    def test_no_override(self):
        base = self.touch("app/compose.yaml")
        self.assertEqual(compose.compose_files(base), [base])

    def test_matching_override(self):
        base = self.touch("app/docker-compose.yml")
        override = self.touch("app/docker-compose.override.yml")
        self.assertEqual(compose.compose_files(base), [base, override])

    def test_override_with_other_extension(self):
        base = self.touch("app/compose.yml")
        override = self.touch("app/compose.override.yaml")
        self.assertEqual(compose.compose_files(base), [base, override])

    def test_compose_passes_every_file(self):
        base = self.touch("app/compose.yaml")
        override = self.touch("app/compose.override.yaml")
        fake = FakeDocker([(has("ps"), (0, "", ""))])
        with mock.patch.object(compose, "run", fake):
            compose.compose(base, "ps")
        self.assertEqual(fake.calls[0], ["docker", "compose", "-f", base, "-f", override, "ps"])


class JsonLinesTest(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(compose.json_lines(""), [])
        self.assertEqual(compose.json_lines('[{"a": 1}]'), [{"a": 1}])
        self.assertEqual(compose.json_lines('{"a": 1}\n\n{"a": 2}\n'), [{"a": 1}, {"a": 2}])


class StatsTest(StateTestCase):
    def test_retries_without_vanished_containers(self):
        ok = (0, '{"ID": "aaaaaaaaaaaa", "CPUPerc": "1.5%"}', "")
        fake = FakeDocker([
            (lambda a: "stats" in a and "bbbbbbbbbbbbXX" in a, (1, "", "No such container")),
            (has("stats"), ok),
            (has("ps", "-q"), (0, "aaaaaaaaaaaaXX\n", "")),
        ])
        with mock.patch.object(compose, "run", fake):
            stats = compose.container_stats(["aaaaaaaaaaaaXX", "bbbbbbbbbbbbXX"])
        self.assertEqual(list(stats), ["aaaaaaaaaaaa"])
        self.assertEqual(fake.calls[-1][-1:], ["aaaaaaaaaaaaXX"])

    def test_no_ids_runs_nothing(self):
        fake = FakeDocker([])
        with mock.patch.object(compose, "run", fake):
            self.assertEqual(compose.container_stats([]), {})
        self.assertEqual(fake.calls, [])


class SizesTest(StateTestCase):
    def sizes_docker(self):
        return FakeDocker([(has("--size"), (0, '{"ID": "aaaaaaaaaaaaXX", "Size": "1kB (virtual 2MB)"}', ""))])

    def test_cache_is_reused_while_fresh(self):
        fake = self.sizes_docker()
        with mock.patch.object(compose, "run", fake):
            first = compose.container_sizes(["aaaaaaaaaaaaXX"])
            second = compose.container_sizes(["aaaaaaaaaaaaXX"])
        self.assertEqual(first, {"aaaaaaaaaaaa": "1kB (virtual 2MB)"})
        self.assertEqual(second, first)
        self.assertEqual(len(fake.calls), 1)

    def test_cache_refreshes_when_stale_or_incomplete(self):
        fake = self.sizes_docker()
        with mock.patch.object(compose, "run", fake):
            compose.container_sizes(["aaaaaaaaaaaaXX"])
            compose.container_sizes(["aaaaaaaaaaaaXX", "ccccccccccccXX"])
            with mock.patch.object(compose.time, "time", return_value=compose.time.time() + 3600):
                compose.container_sizes(["aaaaaaaaaaaaXX"])
        self.assertEqual(len(fake.calls), 3)


class StatusTest(StateTestCase):
    def test_status_merges_stats_and_sizes(self):
        path = self.touch("web/compose.yaml")
        compose.save_state({"files": [path], "checks": {}})
        ps = "\n".join(json.dumps(r) for r in (
            {"ID": "aaaaaaaaaaaa", "Name": "web-1", "Service": "web", "State": "running", "Project": "web"},
            {"ID": "bbbbbbbbbbbb", "Name": "db-1", "Service": "db", "State": "exited", "Project": "web"},
        ))
        fake = FakeDocker([
            (has("compose", "ps"), (0, ps, "")),
            (has("stats"), (0, '{"ID": "aaaaaaaaaaaa", "CPUPerc": "2.5%", "MemUsage": "10MiB / 1GiB", "MemPerc": "1%"}', "")),
            (has("--size"), (0, '{"ID": "aaaaaaaaaaaa", "Size": "1kB"}\n{"ID": "bbbbbbbbbbbb", "Size": "2kB"}', "")),
            (has("inspect"), (1, json.dumps([{"Id": "aaaaaaaaaaaaXX", "Config": {
                "Image": "nginx:latest", "Labels": {"org.opencontainers.image.version": "1.27.3"}}}]),
                "Error: No such object: bbbbbbbbbbbb")),
        ])
        with mock.patch.object(compose, "run", fake):
            result = self.capture(compose.status)
        web, db = result["projects"][0]["containers"]
        self.assertEqual((web["cpu"], web["memUsage"], web["disk"]), (2.5, "10MiB / 1GiB", "1kB"))
        self.assertEqual((db["cpu"], db["memUsage"], db["disk"]), (0.0, "", "2kB"))
        self.assertEqual((web["image"], web["version"]), ("nginx:latest", "1.27.3"))
        self.assertEqual(db["version"], "")
        stats_call = next(c for c in fake.calls if "stats" in c)
        self.assertNotIn("bbbbbbbbbbbb", stats_call)

    def test_missing_file(self):
        compose.save_state({"files": ["/nope/compose.yaml"], "checks": {}})
        with mock.patch.object(compose, "run", FakeDocker([])):
            result = self.capture(compose.status)
        self.assertEqual(result["projects"][0]["error"], "Compose file not found")


class ImageVersionTest(unittest.TestCase):
    def test_tag(self):
        self.assertEqual(compose.image_tag("ghcr.io/immich-app/immich-server:v3"), "v3")
        self.assertEqual(compose.image_tag("docker.io/valkey/valkey:9@sha256:abc"), "9")
        self.assertEqual(compose.image_tag("localhost:5000/app"), "")
        self.assertEqual(compose.image_tag("redis@sha256:abc"), "")

    def test_short_version(self):
        self.assertEqual(compose.short_version("14-vectorchord0.4.3-pgvectors0.2.0"), "14")
        self.assertEqual(compose.short_version("1.27.3-alpine"), "1.27.3")
        self.assertEqual(compose.short_version("v3.3.3"), "v3.3.3")
        self.assertEqual(compose.short_version("latest"), "latest")

    def test_prefers_oci_label(self):
        labels = {"org.opencontainers.image.version": "v3.3.3"}
        self.assertEqual(compose.image_version("ghcr.io/immich-app/immich-server:v3", labels, []),
                         ("v3.3.3", "v3.3.3"))

    def test_falls_back_to_image_env_var(self):
        env = ["PATH=/usr/bin", "GOSU_VERSION=1.17", "VALKEY_VERSION=9.1.1"]
        self.assertEqual(compose.image_version("docker.io/valkey/valkey:9@sha256:abc", {}, env),
                         ("9.1.1", "9.1.1"))
        env = ["NODE_RED_VERSION=4.0.2"]
        self.assertEqual(compose.image_version("nodered/node-red:latest", None, env), ("4.0.2", "4.0.2"))

    def test_falls_back_to_tag(self):
        self.assertEqual(compose.image_version("jellyfin/jellyfin:latest", {}, ["GOSU_VERSION=1.17"]),
                         ("latest", "latest"))
        self.assertEqual(compose.image_version("nginx:1.27.3-alpine", None, None), ("1.27.3", "1.27.3-alpine"))
        self.assertEqual(compose.image_version("redis@sha256:abc", None, None), ("", ""))

    def test_postgres_label_is_refined_by_pg_version(self):
        labels = {"org.opencontainers.image.version": "14-vectorchord0.4.3-pgvector0.8.1-pgvectors0.2.0"}
        env = ["GOSU_VERSION=1.17", "PG_MAJOR=14", "PG_VERSION=14.19-1.pgdg12+1"]
        self.assertEqual(
            compose.image_version("ghcr.io/immich-app/postgres:14-vectorchord0.4.3@sha256:abc", labels, env),
            ("14.19", "14-vectorchord0.4.3-pgvector0.8.1-pgvectors0.2.0"))

    def test_refinement_must_agree(self):
        labels = {"org.opencontainers.image.version": "2.1"}
        self.assertEqual(compose.image_version("app/app:3.0.4", labels, []), ("2.1", "2.1"))
        self.assertEqual(compose.image_version("app/app:2.1.7", labels, []), ("2.1.7", "2.1"))


class CheckTest(StateTestCase):
    def test_classifies_images(self):
        path = self.touch("app/compose.yaml")
        config = {"services": {
            "built": {"build": {"context": "."}, "image": "me/built"},
            "pinned": {"image": "redis@sha256:abc"},
            "fresh": {"image": "nginx:latest"},
            "stale": {"image": "postgres:16"},
            "missing": {"image": "alpine:3"},
        }}
        fake = FakeDocker([
            (has("config"), (0, json.dumps(config), "")),
            (has("image", "inspect", "nginx:latest"), (0, '["nginx@sha256:new"]', "")),
            (has("image", "inspect", "postgres:16"), (0, '["postgres@sha256:old"]', "")),
            (has("image", "inspect", "alpine:3"), (1, "", "No such image")),
            (has("imagetools"), (0, "sha256:new\n", "")),
        ])
        with mock.patch.object(compose, "run", fake):
            result = self.capture(compose.check, path)
        statuses = {i["service"]: i["status"] for i in result["check"]["images"]}
        self.assertEqual(statuses, {"built": "local", "pinned": "pinned", "fresh": "current",
                                    "stale": "update", "missing": "update"})
        self.assertEqual(result["check"]["updates"], 2)
        self.assertIn(path, compose.load_state()["checks"])


class UpdateTest(StateTestCase):
    def test_recreates_only_running_services(self):
        path = self.touch("app/compose.yaml")
        compose.save_state({"files": [path], "checks": {path: {"updates": 1}}})
        fake = FakeDocker([
            (has("pull"), (0, "", "")),
            (has("ps", "--services"), (0, "web\nworker\n", "")),
            (has("up"), (0, "", "")),
        ])
        with mock.patch.object(compose, "run", fake):
            result = self.capture(compose.update, path)
        self.assertEqual(result["restarted"], ["web", "worker"])
        up = next(c for c in fake.calls if "up" in c)
        self.assertEqual(up[-4:], ["up", "-d", "web", "worker"])
        self.assertNotIn("--remove-orphans", up)
        self.assertNotIn(path, compose.load_state()["checks"])

    def test_nothing_running_skips_up(self):
        path = self.touch("app/compose.yaml")
        fake = FakeDocker([(has("pull"), (0, "", "")), (has("ps", "--services"), (0, "", ""))])
        with mock.patch.object(compose, "run", fake):
            result = self.capture(compose.update, path)
        self.assertEqual(result, {"ok": True, "file": path, "restarted": []})
        self.assertFalse(any("up" in c for c in fake.calls))

    def test_pull_failure_reports_last_line(self):
        path = self.touch("app/compose.yaml")
        fake = FakeDocker([(has("pull"), (1, "", "pulling\nmanifest unknown"))])
        with mock.patch.object(compose, "run", fake):
            result = self.capture(compose.update, path)
        self.assertEqual(result, {"ok": False, "error": "manifest unknown"})


class WatchListTest(StateTestCase):
    def test_add_and_remove(self):
        path = self.touch("app/compose.yaml")
        self.capture(compose.add, [path, path])
        self.assertEqual(compose.load_state()["files"], [path])
        self.capture(compose.remove, path)
        self.assertEqual(compose.load_state()["files"], [])

    def test_add_rejects_missing_file(self):
        result = self.capture(compose.add, ["/nope.yml"])
        self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()
