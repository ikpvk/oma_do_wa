.pragma library

var UNITS = { "B": 1, "kB": 1e3, "KB": 1e3, "MB": 1e6, "GB": 1e9, "TB": 1e12,
              "KiB": 1024, "MiB": 1048576, "GiB": 1073741824, "TiB": 1099511627776 }

function parseJson(raw) {
  try {
    var parsed = JSON.parse(String(raw || "").trim() || "{}")
    if (parsed && typeof parsed === "object") return parsed
  } catch (e) {}
  return { ok: false, error: "Helper returned invalid output" }
}

// "693.9MiB" → bytes
function toBytes(text) {
  var m = String(text || "").trim().match(/^([\d.]+)\s*([A-Za-z]+)$/)
  if (!m) return 0
  return parseFloat(m[1]) * (UNITS[m[2]] || 1)
}

function formatBytes(bytes) {
  var steps = ["B", "KiB", "MiB", "GiB", "TiB"]
  var value = Number(bytes) || 0
  var i = 0
  while (value >= 1024 && i < steps.length - 1) { value /= 1024; i++ }
  return (i === 0 || value >= 100 ? Math.round(value) : value.toFixed(1)) + " " + steps[i]
}

// "693.9MiB / 15.27GiB" → bytes used
function memBytes(container) {
  return toBytes(String(container && container.memUsage || "").split("/")[0])
}

function cpuText(container) {
  return (Number(container && container.cpu) || 0).toFixed(1) + "%"
}

function memText(container) {
  if (!container || !container.memUsage) return "—"
  return formatBytes(memBytes(container))
}

// "8.19kB (virtual 782MB)" → "8.2 KiB" (writable layer); image size in tooltip.
function diskText(container) {
  var raw = String(container && container.disk || "")
  if (!raw) return "—"
  return formatBytes(toBytes(raw.split("(")[0]))
}

function diskVirtualText(container) {
  var m = String(container && container.disk || "").match(/virtual\s+([^)]+)\)/)
  return m ? formatBytes(toBytes(m[1])) : ""
}

function versionText(container) {
  return String(container && container.version || "").trim()
}

// Row hover text: service, full image reference, image size.
function containerTooltip(container) {
  if (!container) return ""
  var lines = ["Service " + (container.service || "?")]
  if (container.image) lines.push("Image " + container.image)
  var full = String(container.versionFull || "").trim()
  if (full && full !== versionText(container)) lines.push("Version " + full)
  var size = diskVirtualText(container)
  if (size) lines.push("Image size " + size + " · Disk = container writable layer")
  return lines.join("\n")
}

function running(container) {
  return container && container.state === "running"
}

function projectSummary(project) {
  var list = project && project.containers || []
  if (project && project.error) return project.error
  if (list.length === 0) return "No containers — stack is down"
  var up = 0, cpu = 0, mem = 0
  for (var i = 0; i < list.length; i++) {
    if (running(list[i])) up++
    cpu += Number(list[i].cpu) || 0
    mem += memBytes(list[i])
  }
  return up + "/" + list.length + " running · CPU " + cpu.toFixed(1) + "% · RAM " + formatBytes(mem)
}

function updatesFor(project) {
  return project && project.check ? Number(project.check.updates) || 0 : 0
}

function totalUpdates(projects) {
  var n = 0
  for (var i = 0; i < (projects || []).length; i++) n += updatesFor(projects[i])
  return n
}

function ago(epochSec) {
  var diff = Math.max(0, Math.floor(Date.now() / 1000) - Number(epochSec || 0))
  if (diff < 60) return "just now"
  if (diff < 3600) return Math.floor(diff / 60) + "m ago"
  if (diff < 86400) return Math.floor(diff / 3600) + "h ago"
  return Math.floor(diff / 86400) + "d ago"
}

function checkSummary(project) {
  var check = project && project.check
  if (!check) return ""
  var images = check.images || []
  var names = []
  for (var i = 0; i < images.length; i++) {
    if (images[i].status === "update") names.push(images[i].service)
  }
  var when = " · checked " + ago(check.checkedAt)
  if (names.length > 0) return "Updates: " + names.join(", ") + when
  if (check.errors > 0) return check.errors + " image(s) could not be checked" + when
  return "All images up to date" + when
}

function checkDetail(project) {
  var images = project && project.check ? project.check.images || [] : []
  var lines = []
  for (var i = 0; i < images.length; i++) {
    var img = images[i]
    lines.push(img.service + ": " + img.status + (img.detail ? " — " + img.detail : ""))
  }
  return lines.join("\n")
}

function stateGlyph(container) {
  if (!container) return "󰝦"
  if (container.state === "running") return container.health === "unhealthy" ? "󰀦" : "󰄬"
  if (container.state === "restarting") return "󰑓"
  return "󰝦"
}
