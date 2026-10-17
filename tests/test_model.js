// Unit tests for Model.js. Run from the repo root: node tests/test_model.js
"use strict"

const assert = require("node:assert/strict")
const fs = require("node:fs")
const path = require("node:path")
const test = require("node:test")
const vm = require("node:vm")

// Model.js is a QML `.pragma library` script, not a module: drop the pragma
// and evaluate it in a sandbox to reach its functions.
const source = fs.readFileSync(path.join(__dirname, "..", "Model.js"), "utf8")
  .replace(/^\.pragma library\s*$/m, "")
const Model = {}
vm.createContext(Model)
vm.runInContext(source, Model)

test("toBytes parses docker units", () => {
  assert.equal(Model.toBytes("693.9MiB"), 693.9 * 1048576)
  assert.ok(Math.abs(Model.toBytes("8.19kB") - 8190) < 1e-6)
  assert.equal(Model.toBytes("0B"), 0)
  assert.equal(Model.toBytes("garbage"), 0)
  assert.equal(Model.toBytes(undefined), 0)
})

test("formatBytes picks a binary unit", () => {
  assert.equal(Model.formatBytes(0), "0 B")
  assert.equal(Model.formatBytes(1536), "1.5 KiB")
  assert.equal(Model.formatBytes(150 * 1048576), "150 MiB")
})

test("container text helpers", () => {
  const c = { cpu: 2.345, memUsage: "10MiB / 1GiB", disk: "8.19kB (virtual 782MB)" }
  assert.equal(Model.cpuText(c), "2.3%")
  assert.equal(Model.memText(c), "10.0 MiB")
  assert.equal(Model.diskText(c), "8.0 KiB")
  assert.equal(Model.diskVirtualText(c), "746 MiB")
  assert.equal(Model.memText({}), "—")
  assert.equal(Model.diskText({}), "—")
  assert.equal(Model.diskVirtualText({ disk: "1kB" }), "")
})

test("versionText and containerTooltip", () => {
  const c = { service: "web", image: "nginx:latest", version: " 1.27.3 ", disk: "1kB (virtual 2MB)" }
  assert.equal(Model.versionText(c), "1.27.3")
  assert.equal(Model.versionText({}), "")
  assert.equal(Model.containerTooltip(c),
    "Service web\nImage nginx:latest\nImage size 1.9 MiB · Disk = container writable layer")
  assert.equal(Model.containerTooltip({ service: "db" }), "Service db")
  assert.equal(Model.containerTooltip({ service: "db", version: "14.19", versionFull: "14-vectorchord0.4.3" }),
    "Service db\nVersion 14-vectorchord0.4.3")
  assert.equal(Model.containerTooltip(null), "")
})

test("projectSummary", () => {
  assert.equal(Model.projectSummary({ error: "boom", containers: [] }), "boom")
  assert.equal(Model.projectSummary({ containers: [] }), "No containers — stack is down")
  assert.equal(Model.projectSummary({ containers: [
    { state: "running", cpu: 1, memUsage: "1MiB / 1GiB" },
    { state: "exited", cpu: 0, memUsage: "" },
  ] }), "1/2 running · CPU 1.0% · RAM 1.0 MiB")
})

test("update counts", () => {
  const projects = [{ check: { updates: 2 } }, { check: null }, {}, { check: { updates: "1" } }]
  assert.equal(Model.updatesFor(projects[0]), 2)
  assert.equal(Model.updatesFor(projects[1]), 0)
  assert.equal(Model.totalUpdates(projects), 3)
  assert.equal(Model.totalUpdates(undefined), 0)
})

test("ago", () => {
  const now = Math.floor(Date.now() / 1000)
  assert.equal(Model.ago(now), "just now")
  assert.equal(Model.ago(now - 120), "2m ago")
  assert.equal(Model.ago(now - 7200), "2h ago")
  assert.equal(Model.ago(now - 172800), "2d ago")
})

test("checkSummary and checkDetail", () => {
  const now = Math.floor(Date.now() / 1000)
  const withUpdates = { check: { checkedAt: now, errors: 0, images: [
    { service: "web", status: "update", detail: "New image available" },
    { service: "db", status: "current", detail: "" },
  ] } }
  assert.equal(Model.checkSummary(withUpdates), "Updates: web · checked just now")
  assert.equal(Model.checkDetail(withUpdates), "web: update — New image available\ndb: current")
  assert.equal(Model.checkSummary({ check: { checkedAt: now, errors: 1, images: [] } }),
    "1 image(s) could not be checked · checked just now")
  assert.equal(Model.checkSummary({ check: { checkedAt: now, errors: 0, images: [] } }),
    "All images up to date · checked just now")
  assert.equal(Model.checkSummary({}), "")
})

test("parseJson", () => {
  assert.equal(Model.parseJson('{"ok": true}').ok, true)
  assert.equal(Model.parseJson("").ok, undefined)
  const bad = Model.parseJson("not json")
  assert.equal(bad.ok, false)
  assert.equal(bad.error, "Helper returned invalid output")
})

test("stateGlyph distinguishes states", () => {
  const glyphs = new Set([
    Model.stateGlyph({ state: "running" }),
    Model.stateGlyph({ state: "running", health: "unhealthy" }),
    Model.stateGlyph({ state: "restarting" }),
    Model.stateGlyph({ state: "exited" }),
  ])
  assert.equal(glyphs.size, 4)
  assert.equal(Model.stateGlyph(null), Model.stateGlyph({ state: "exited" }))
})
