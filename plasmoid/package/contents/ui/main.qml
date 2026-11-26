pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.components as PC
import org.kde.plasma.extras as PlasmaExtras
import org.kde.plasma.plasma5support as P5Support
import org.kde.kirigami as Kirigami

// Read-only cheat sheet. All decoding happens in ../code/sheet_json.py (copied in by install.sh).
PlasmoidItem {
    id: root

    property var sheet: null
    property string message: ""
    property bool busy: false
    property int profileIndex: 0
    property string hovered: ""
    readonly property var profile: sheet && sheet.profiles.length ? sheet.profiles[Math.min(profileIndex, sheet.profiles.length - 1)] : null
    readonly property var rows: profile ? profile.rows : []
    readonly property string helper: "/usr/bin/python '" + decodeURIComponent(Qt.resolvedUrl("../code/sheet_json.py").toString().replace(/^file:\/\//, "")) + "'"

    // Number markers on the mouse, in the drawing units of mouseview.py (mouse seen from above).
    readonly property var marks: ({
        lmb: [445, 95], rmb: [535, 95], wheel: [490, 128], tilt_l: [464, 162], tilt_r: [516, 162], top: [490, 224],
        dpi_up: [420, 149], dpi_dn: [418, 197], sniper: [391, 262], thumb_fwd: [395, 313], thumb_back: [397, 367]
    })

    Plasmoid.icon: Qt.resolvedUrl("../icons/g502.svg")
    Plasmoid.status: PlasmaCore.Types.ActiveStatus
    toolTipMainText: "G502 Button Map"
    toolTipSubText: sheet ? sheet.name + (profile ? " · Profile " + profile.id : "") : "Show button mapping"

    // Own monochrome icon, tinted with the panel text colour like the other tray icons.
    compactRepresentation: MouseArea {
        hoverEnabled: true
        onClicked: root.expanded = !root.expanded
        Kirigami.Icon {
            anchors.fill: parent
            source: Qt.resolvedUrl("../icons/g502.svg")
            isMask: true
            color: Kirigami.Theme.textColor
            active: parent.containsMouse
        }
    }

    function run(args) {
        busy = true
        exec.connectSource(helper + args)
    }

    onExpandedChanged: if (expanded && !busy) run("")
    Component.onCompleted: run("")

    P5Support.DataSource {
        id: exec
        engine: "executable"
        onNewData: (source, data) => {
            disconnectSource(source)
            if (source.indexOf("sheet_json.py") < 0)
                return
            root.busy = false
            let result = null
            try {
                result = JSON.parse(data.stdout)
            } catch (e) {
                root.message = "The helper returned no valid answer."
                return
            }
            if (!result.ok) {
                root.message = result.error
                return
            }
            const keep = root.sheet && root.profile ? root.profile.id : -1
            root.sheet = result
            const again = result.profiles.findIndex(p => p.id === keep)
            root.profileIndex = again >= 0 ? again : result.selected
            root.message = result.name + " · as of " + result.time
        }
    }

    fullRepresentation: PlasmaExtras.Representation {
        Layout.minimumWidth: Kirigami.Units.gridUnit * 22
        Layout.minimumHeight: Kirigami.Units.gridUnit * 22
        Layout.preferredWidth: Kirigami.Units.gridUnit * 28
        Layout.preferredHeight: Kirigami.Units.gridUnit * 28
        collapseMarginsHint: true

        header: PlasmaExtras.PlasmoidHeading {
            contentItem: RowLayout {
                spacing: Kirigami.Units.smallSpacing
                PC.ComboBox {
                    visible: count > 1
                    model: root.sheet ? root.sheet.profiles.map(p => "Profile " + p.id + (p.running ? " · running" : "")) : []
                    currentIndex: root.profileIndex
                    onActivated: index => root.profileIndex = index
                }
                PC.Label {
                    Layout.fillWidth: true
                    text: root.busy ? "Reading …" : root.message
                    font: Kirigami.Theme.smallFont
                    opacity: 0.7
                    elide: Text.ElideRight
                }
                PC.ToolButton {
                    icon.name: "view-refresh"
                    text: "Refresh from mouse (read-only)"
                    display: PC.AbstractButton.IconOnly
                    enabled: !root.busy
                    onClicked: root.run(" --refresh")
                    PC.ToolTip.text: text
                    PC.ToolTip.visible: hovered
                    PC.ToolTip.delay: Kirigami.Units.toolTipDelay
                }
                PC.ToolButton {
                    icon.name: "document-edit"
                    text: "Open editor"
                    display: PC.AbstractButton.IconOnly
                    onClicked: {
                        exec.connectSource("gtk-launch io.github.compact161.G502OnboardEditor")
                        root.expanded = false
                    }
                    PC.ToolTip.text: text
                    PC.ToolTip.visible: hovered
                    PC.ToolTip.delay: Kirigami.Units.toolTipDelay
                }
            }
        }

        contentItem: RowLayout {
            spacing: Kirigami.Units.smallSpacing

            ColumnLayout {
                Layout.fillHeight: true
                Layout.preferredWidth: Math.min(height * 0.5, parent.width * 0.36)
                Layout.maximumWidth: Layout.preferredWidth
                Layout.leftMargin: Kirigami.Units.smallSpacing
                spacing: Kirigami.Units.smallSpacing

                Item {
                    id: mouse
                    // Visible part of the drawing: x 360…620, y 50…550.
                    readonly property real s: Math.min(width / 260, height / 500)
                    readonly property real ox: (width - 260 * s) / 2 - 360 * s
                    readonly property real oy: (height - 500 * s) / 2 - 50 * s
                    Layout.fillWidth: true
                    Layout.fillHeight: true

                    Canvas {
                        id: canvas
                        readonly property color fg: Kirigami.Theme.textColor
                        anchors.fill: parent
                        onFgChanged: requestPaint()
                        onPaint: {
                            const c = getContext("2d")
                            c.reset()
                            c.translate(mouse.ox, mouse.oy)
                            c.scale(mouse.s, mouse.s)
                            const ink = a => Qt.rgba(fg.r, fg.g, fg.b, a)
                            const stroke = (a, w) => { c.strokeStyle = ink(a); c.lineWidth = w; c.stroke() }
                            const part = (x, y, w, h, r) => {
                                c.beginPath(); c.roundedRect(x, y, w, h, r, r)
                                c.fillStyle = ink(0.22); c.fill(); stroke(0.75, 2)
                            }
                            // Body
                            c.beginPath()
                            c.moveTo(490, 62)
                            c.bezierCurveTo(545, 62, 580, 85, 584, 150)
                            c.bezierCurveTo(590, 250, 600, 330, 588, 420)
                            c.bezierCurveTo(578, 500, 540, 540, 490, 540)
                            c.bezierCurveTo(440, 540, 404, 500, 396, 420)
                            c.bezierCurveTo(388, 350, 402, 300, 408, 240)
                            c.bezierCurveTo(410, 200, 404, 160, 408, 130)
                            c.bezierCurveTo(414, 85, 440, 62, 490, 62)
                            c.closePath()
                            c.fillStyle = ink(0.06); c.fill(); stroke(0.75, 2)
                            // Split between the main buttons and their rear edge
                            c.beginPath(); c.moveTo(490, 62); c.lineTo(490, 250); stroke(0.45, 1.5)
                            c.beginPath(); c.moveTo(410, 250); c.bezierCurveTo(450, 268, 530, 268, 589, 250); stroke(0.45, 1.5)
                            // Wheel, button behind it
                            part(480, 110, 20, 78, 9)
                            part(480, 210, 20, 28, 5)
                            // Two buttons at the edge of the left main button
                            part(412, 128, 16, 42, 5)
                            part(410, 176, 16, 42, 5)
                            // Thumb side: sniper, forward, back
                            part(380, 244, 22, 36, 7)
                            part(386, 290, 18, 46, 6)
                            part(388, 342, 18, 50, 6)
                        }
                    }

                    Repeater {
                        model: root.rows
                        delegate: Rectangle {
                            id: mark
                            required property var modelData
                            required property int index
                            readonly property bool hot: root.hovered === modelData.slot
                            width: Math.max(Kirigami.Units.gridUnit * 0.9, 24 * mouse.s)
                            height: width
                            radius: width / 2
                            x: mouse.ox + root.marks[modelData.slot][0] * mouse.s - width / 2
                            y: mouse.oy + root.marks[modelData.slot][1] * mouse.s - height / 2
                            z: hot ? 1 : 0
                            scale: hot ? 1.35 : 1
                            color: hot ? Kirigami.Theme.highlightColor : Kirigami.Theme.backgroundColor
                            border.color: Kirigami.Theme.highlightColor
                            border.width: 1
                            Text {
                                anchors.centerIn: parent
                                text: mark.index + 1
                                color: mark.hot ? Kirigami.Theme.highlightedTextColor : Kirigami.Theme.textColor
                                font.pixelSize: mark.width * 0.6
                                font.bold: true
                            }
                            HoverHandler {
                                onHoveredChanged: root.hovered = hovered ? mark.modelData.slot : (root.hovered === mark.modelData.slot ? "" : root.hovered)
                            }
                        }
                    }
                }
                PC.Label {
                    Layout.fillWidth: true
                    visible: root.profile !== null
                    text: root.profile ? "DPI " + root.profile.dpi.split(" · ").join("\n") : ""
                    font: Kirigami.Theme.smallFont
                    opacity: 0.7
                    wrapMode: Text.WordWrap
                }
            }

            PC.ScrollView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                contentItem: ListView {
                    id: list
                    clip: true
                    model: root.rows
                    delegate: Rectangle {
                        id: row
                        required property var modelData
                        required property int index
                        readonly property bool hot: root.hovered === modelData.slot
                        width: ListView.view.width
                        height: content.implicitHeight + Kirigami.Units.smallSpacing
                        radius: Kirigami.Units.cornerRadius
                        color: hot ? Qt.alpha(Kirigami.Theme.highlightColor, 0.18) : "transparent"
                        HoverHandler {
                            onHoveredChanged: root.hovered = hovered ? row.modelData.slot : (root.hovered === row.modelData.slot ? "" : root.hovered)
                        }
                        RowLayout {
                            id: content
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            anchors.leftMargin: Kirigami.Units.smallSpacing
                            anchors.rightMargin: Kirigami.Units.smallSpacing
                            spacing: Kirigami.Units.smallSpacing
                            PC.Label {
                                Layout.preferredWidth: Kirigami.Units.gridUnit
                                horizontalAlignment: Text.AlignHCenter
                                text: row.index + 1
                                font.bold: true
                                color: Kirigami.Theme.highlightColor
                            }
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 0
                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: Kirigami.Units.smallSpacing
                                    PC.Label {
                                        text: row.modelData.normal
                                        font.bold: true
                                        elide: Text.ElideRight
                                        Layout.maximumWidth: content.width * 0.6
                                    }
                                    PC.Label {
                                        Layout.fillWidth: true
                                        text: row.modelData.title
                                        font: Kirigami.Theme.smallFont
                                        opacity: 0.65
                                        elide: Text.ElideRight
                                        horizontalAlignment: Text.AlignRight
                                    }
                                }
                                PC.Label {
                                    Layout.fillWidth: true
                                    text: "G-Shift: " + row.modelData.shift
                                    color: Kirigami.Theme.highlightColor
                                    elide: Text.ElideRight
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
