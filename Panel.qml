import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "Model.js" as Model

Panel {
  id: root
  moduleName: "ikpvk.oma-do-wa"
  ipcTarget: "ikpvk.oma-do-wa"
  manageIpc: false

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property color accent: Color.accent
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  property var projects: []
  property bool loaded: false
  property string lastError: ""
  property string actionStatus: ""
  // A refresh asked for while one is in flight (e.g. right after a job) runs
  // once that one finishes, so the panel never keeps pre-job data.
  property bool refreshPending: false

  // One docker job (pick / remove / check / update) at a time; the row that
  // owns it shows its progress and every other action button is disabled.
  property string jobKind: ""
  property string jobFile: ""
  readonly property bool jobRunning: jobProcess.running
  readonly property int updateCount: Model.totalUpdates(projects)

  readonly property int refreshIntervalSec: intSetting("refreshIntervalSec", 5, 2, 600)
  readonly property string helperPath: {
    var s = String(Qt.resolvedUrl("compose.py"))
    if (s.indexOf("file://") === 0) s = s.slice("file://".length)
    return decodeURIComponent(s)
  }

  function intSetting(name, fallback, min, max) {
    var value = settings ? settings[name] : undefined
    var n = parseInt(String(value === undefined || value === null ? fallback : value), 10)
    if (!isFinite(n)) n = fallback
    return Math.max(min, Math.min(max, n))
  }

  function refresh() {
    if (statusProcess.running) {
      refreshPending = true
      return
    }
    refreshPending = false
    statusProcess.command = ["python3", helperPath, "status"]
    statusProcess.running = true
  }

  function runJob(kind, file) {
    if (jobProcess.running) return
    jobKind = kind
    jobFile = file || ""
    lastError = ""
    actionStatus = {
      pick: "Waiting for the file chooser…",
      remove: "Removing…",
      check: "Checking registries for new images…",
      update: "Pulling images and restarting containers…"
    }[kind] || ""
    jobProcess.command = file ? ["python3", helperPath, kind, file] : ["python3", helperPath, kind]
    jobProcess.running = true
  }

  function addCompose() { runJob("pick", "") }
  function removeProject(file) { runJob("remove", file) }
  function checkProject(file) { runJob("check", file) }
  function updateProject(file) { runJob("update", file) }

  function elide(text) {
    var value = String(text || "").replace(/\s+/g, " ").trim()
    return value.length > 160 ? value.substring(0, 157) + "…" : value
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  onOpenedChanged: if (opened) {
    refresh()
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  // Slow background poll so the bar badge reflects cached update checks; the
  // fast poll only runs while the panel is open, since `docker stats` samples
  // for about two seconds each time.
  Timer {
    interval: root.opened ? root.refreshIntervalSec * 1000 : 300000
    repeat: true
    running: true
    triggeredOnStart: true
    onTriggered: root.refresh()
  }

  Timer {
    id: actionStatusTimer
    interval: 4000
    onTriggered: root.actionStatus = ""
  }

  Process {
    id: statusProcess
    command: []
    stdout: StdioCollector { id: statusStdout; waitForEnd: true }
    stderr: StdioCollector { id: statusStderr; waitForEnd: true }
    onExited: function(exitCode) {
      var parsed = Model.parseJson(statusStdout.text)
      if (exitCode === 0 && parsed.ok) {
        root.projects = parsed.projects || []
        root.loaded = true
      } else if (!root.jobRunning) {
        root.lastError = root.elide(parsed.error || statusStderr.text || "Could not read docker status")
      }
      if (root.refreshPending) Qt.callLater(root.refresh)
    }
  }

  Process {
    id: jobProcess
    command: []
    stdout: StdioCollector { id: jobStdout; waitForEnd: true }
    stderr: StdioCollector { id: jobStderr; waitForEnd: true }
    onExited: function(exitCode) {
      var parsed = Model.parseJson(jobStdout.text)
      if (exitCode === 0 && parsed.ok) {
        root.lastError = ""
        if (root.jobKind === "update") root.actionStatus = (parsed.restarted || []).length > 0
          ? "Pulled and restarted " + parsed.restarted.join(", ") : "Pulled images · no running services to restart"
        else if (root.jobKind === "check") root.actionStatus = parsed.check && parsed.check.updates > 0
          ? parsed.check.updates + " update(s) available" : "Everything is up to date"
        else root.actionStatus = ""
        actionStatusTimer.restart()
      } else {
        root.actionStatus = ""
        root.lastError = root.elide(parsed.error || jobStderr.text || "Docker command failed")
      }
      root.jobKind = ""
      root.jobFile = ""
      root.refresh()
    }
  }

  IpcHandler {
    target: root.ipcTarget
    function open(): void { root.open() }
    function close(): void { root.close() }
    function toggle(): void { root.toggle() }
    function refresh(): string { root.refresh(); return "ok" }
    function updates(): string { return String(root.updateCount) }
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    tooltipText: root.updateCount > 0 ? "Compose: " + root.updateCount + " image update(s)" : "Compose stacks"
    iconComponent: Component {
      Item {
        Text {
          anchors.centerIn: parent
          text: "\uDB86\uDCD8" // nf-md-truck_cargo_container (U+F18D8)
          color: root.bar ? root.bar.foreground : root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.space(14)
        }
        Rectangle {
          visible: root.updateCount > 0
          width: Style.space(6); height: width; radius: width / 2
          color: root.accent
          anchors.right: parent.right
          anchors.top: parent.top
        }
      }
    }
    onPressed: function(buttonCode) {
      if (buttonCode === Qt.RightButton) root.refresh()
      else root.toggle()
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(460))
    contentHeight: panel.fittedContentHeight(column.implicitHeight, Style.space(640))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(t) {
        if (t === "r" || t === "R") root.refresh()
        else if (t === "a" || t === "A") root.addCompose()
      }

      Flickable {
        id: panelFlick
        anchors.fill: parent
        contentWidth: width
        contentHeight: column.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        interactive: contentHeight > height
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        Column {
          id: column
          width: panelFlick.width
          spacing: Style.space(12)

          PanelHero {
            id: hero
            width: parent.width
            title: "Oma-do-wa"
            meta: root.projects.length === 0
              ? "No stacks watched"
              : root.projects.length + " stack(s)" + (root.updateCount > 0 ? " · " + root.updateCount + " update(s)" : "")
            foreground: root.foreground
            fontFamily: root.fontFamily
            iconComponent: Component {
              Text {
                text: "\uDB86\uDCD8"
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.display
              }
            }
            trailingControl: Component {
              PanelActionButton {
                iconText: "󰐕"
                tooltipText: "Add a compose file (a)"
                foreground: root.foreground
                fontFamily: root.fontFamily
                enabled: !root.jobRunning
                onClicked: root.addCompose()
              }
            }
          }

          Text {
            textFormat: Text.PlainText
            visible: text !== ""
            width: parent.width
            text: root.lastError !== "" ? root.lastError : root.actionStatus
            color: root.lastError !== "" ? root.urgent : root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.WordWrap
          }

          Text {
            visible: root.loaded && root.projects.length === 0
            width: parent.width
            text: "Pick a docker-compose file with  󰐕  to start monitoring its containers."
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
            wrapMode: Text.WordWrap
            horizontalAlignment: Text.AlignHCenter
          }

          Repeater {
            model: root.projects
            ProjectBlock {
              required property var modelData
              width: column.width
              project: modelData
            }
          }
        }
      }
    }
  }

  component ProjectBlock: Column {
    id: block
    property var project: null
    readonly property string file: project ? String(project.file) : ""
    readonly property int updates: Model.updatesFor(project)
    readonly property bool busyHere: root.jobRunning && root.jobFile === file

    spacing: Style.space(6)

    PanelSeparator { foreground: root.foreground }

    RowLayout {
      width: parent.width
      spacing: Style.space(6)

      ColumnLayout {
        Layout.fillWidth: true
        spacing: Style.space(1)

        Text {
          textFormat: Text.PlainText
          Layout.fillWidth: true
          text: block.project ? block.project.name : ""
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
          font.bold: true
          elide: Text.ElideRight
        }
        Text {
          textFormat: Text.PlainText
          Layout.fillWidth: true
          text: Model.projectSummary(block.project)
          color: block.project && block.project.error ? root.urgent : root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          elide: Text.ElideRight
        }
      }

      PanelActionButton {
        iconText: block.busyHere && root.jobKind === "check" ? "󰔟" : "󰚰"
        tooltipText: "Check for image updates"
        foreground: root.foreground
        fontFamily: root.fontFamily
        enabled: !root.jobRunning
        onClicked: root.checkProject(block.file)
      }
      PanelActionButton {
        visible: block.updates > 0
        iconText: block.busyHere && root.jobKind === "update" ? "󰔟" : "󰇚"
        tooltipText: "Pull " + block.updates + " update(s) and restart"
        foreground: root.accent
        fontFamily: root.fontFamily
        enabled: !root.jobRunning
        onClicked: root.updateProject(block.file)
      }
      PanelActionButton {
        iconText: "󰆴"
        tooltipText: "Stop watching " + block.file
        foreground: root.foreground
        fontFamily: root.fontFamily
        enabled: !root.jobRunning
        onClicked: root.removeProject(block.file)
      }
    }

    Text {
      id: checkLine
      textFormat: Text.PlainText
      visible: text !== ""
      width: parent.width
      text: Model.checkSummary(block.project)
      color: block.updates > 0 ? root.accent : root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      elide: Text.ElideRight

      MouseArea { id: checkHover; anchors.fill: parent; hoverEnabled: true }
      PanelToolTip {
        visible: checkHover.containsMouse
        text: Model.checkDetail(block.project)
        fontFamily: root.fontFamily
      }
    }

    Repeater {
      model: block.project ? block.project.containers : []
      ContainerRow {
        required property var modelData
        width: block.width
        container: modelData
      }
    }
  }

  component ContainerRow: CursorSurface {
    id: row
    property var container: null
    readonly property bool up: Model.running(container)

    foreground: root.foreground
    hasCursor: rowHover.containsMouse
    implicitHeight: rowLayout.implicitHeight + Style.spacing.rowPaddingX

    MouseArea { id: rowHover; anchors.fill: parent; hoverEnabled: true }

    RowLayout {
      id: rowLayout
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.space(10)
      anchors.rightMargin: Style.space(10)
      spacing: Style.space(8)

      Text {
        text: Model.stateGlyph(row.container)
        color: row.up ? (row.container.health === "unhealthy" ? root.urgent : root.foreground) : root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.icon
        Layout.alignment: Qt.AlignVCenter
      }

      ColumnLayout {
        Layout.fillWidth: true
        spacing: Style.space(1)

        Text {
          textFormat: Text.PlainText
          Layout.fillWidth: true
          text: row.container ? row.container.name : ""
          color: row.up ? root.foreground : root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
          elide: Text.ElideRight
        }
        Text {
          textFormat: Text.PlainText
          Layout.fillWidth: true
          text: row.up
            ? "CPU " + Model.cpuText(row.container) + "  ·  RAM " + Model.memText(row.container) + "  ·  Disk " + Model.diskText(row.container)
            : (row.container ? row.container.status || row.container.state : "") + "  ·  Disk " + Model.diskText(row.container)
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          elide: Text.ElideRight
        }
      }
    }

    PanelToolTip {
      visible: rowHover.containsMouse && Model.diskVirtualText(row.container) !== ""
      text: "Service " + (row.container ? row.container.service : "") + " · image " + Model.diskVirtualText(row.container)
        + "\nDisk = container writable layer"
      fontFamily: root.fontFamily
    }
  }
}
