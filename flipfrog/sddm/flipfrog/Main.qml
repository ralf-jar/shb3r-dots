// Tema de SDDM de shb3r-dots: fondo del tema activo, píldora de fecha/hora
// arriba (como el reloj de la barra), tarjeta central desenfocada (el blur
// de los popups) y píldoras de sesión y energía abajo. Colores, redondeo,
// fuente y textos salen de theme.conf / theme.conf.user (sync_sddm.py).
import QtQuick
import QtQuick.Effects

Rectangle {
    id: root
    width: 1920
    height: 1080
    color: config.backgroundColor

    readonly property color cPanel: config.panelColor
    readonly property color cHover: config.hoverColor
    readonly property color cFg: config.foreground
    readonly property color cAccent: config.accent
    readonly property color cAccent2: config.accent2
    readonly property int r: parseInt(config.radius) || 4
    readonly property string font: config.font
    readonly property string iconFont: config.iconFont

    property int userIndex: userModel.lastIndex >= 0 ? userModel.lastIndex : 0
    property int sessionIndex: sessionModel.lastIndex >= 0 ? sessionModel.lastIndex : 0

    function userName(i) {
        var name = userModel.data(userModel.index(i, 0), Qt.UserRole + 1)
        return name ? name.toString() : (sddm.lastUser || "")
    }
    function userRealName(i) {
        var real = userModel.data(userModel.index(i, 0), Qt.UserRole + 2)
        return real && real.toString().length > 0 ? real.toString() : userName(i)
    }
    function sessionName(i) {
        var name = sessionModel.data(sessionModel.index(i, 0), Qt.UserRole + 4)
        return name ? name.toString() : "Hyprland"
    }
    function login() {
        error.visible = false
        sddm.login(userName(userIndex), password.text, sessionIndex)
    }

    Connections {
        target: sddm
        function onLoginFailed() {
            password.text = ""
            error.visible = true
            shake.start()
        }
    }

    Image {
        id: wallpaper
        anchors.fill: parent
        source: config.background
        fillMode: Image.PreserveAspectCrop
        asynchronous: false
        smooth: true
    }

    // Píldora con el fondo desenfocado detrás, recortada con el redondeo.
    component Panel: Item {
        id: panel
        default property alias content: inner.data
        property color tint: root.cPanel

        ShaderEffectSource {
            id: behind
            anchors.fill: parent
            sourceItem: wallpaper
            sourceRect: Qt.rect(panel.mapToItem(root, 0, 0).x, panel.mapToItem(root, 0, 0).y, panel.width, panel.height)
            visible: false
        }
        Rectangle {
            id: mask
            anchors.fill: parent
            radius: root.r
            visible: false
            layer.enabled: true
        }
        MultiEffect {
            anchors.fill: parent
            source: behind
            blurEnabled: true
            blur: 1.0
            blurMax: 48
            maskEnabled: true
            maskSource: mask
        }
        Rectangle {
            anchors.fill: parent
            radius: root.r
            color: panel.tint
        }
        Item {
            id: inner
            anchors.fill: parent
        }
    }

    // Fecha y hora, mismo formato que el reloj extendido de la barra.
    Panel {
        id: clockPill
        anchors.top: parent.top
        anchors.topMargin: 24
        anchors.horizontalCenter: parent.horizontalCenter
        width: clock.implicitWidth + 40
        height: 44

        Text {
            id: clock
            anchors.centerIn: parent
            color: root.cAccent
            font.family: root.font
            font.pixelSize: 18
            font.weight: Font.Bold
            function refresh() {
                var now = new Date()
                var date = now.toLocaleDateString(Qt.locale(config.locale), config.dateFormat)
                text = date.charAt(0).toUpperCase() + date.slice(1)
                     + "   " + now.toLocaleTimeString(Qt.locale("en_US"), "h:mm AP")
            }
            Component.onCompleted: refresh()
            Timer {
                interval: 1000
                running: true
                repeat: true
                onTriggered: clock.refresh()
            }
        }
    }

    Panel {
        id: card
        anchors.centerIn: parent
        width: 380
        height: column.implicitHeight + 64

        Column {
            id: column
            anchors.centerIn: parent
            width: parent.width - 64
            spacing: 18

            // Inicial del usuario en un chip de acento.
            Rectangle {
                anchors.horizontalCenter: parent.horizontalCenter
                width: 84
                height: 84
                radius: root.r
                color: root.cAccent
                Text {
                    anchors.centerIn: parent
                    text: root.userRealName(root.userIndex).charAt(0).toUpperCase()
                    color: root.cPanel.a > 0.5 ? root.cPanel : "#101414"
                    font.family: root.font
                    font.pixelSize: 40
                    font.weight: Font.Black
                }
            }

            // Nombre; con varios usuarios, clic pasa al siguiente.
            Text {
                id: userLabel
                anchors.horizontalCenter: parent.horizontalCenter
                text: root.userRealName(root.userIndex) + (userModel.count > 1 ? "  ▾" : "")
                color: userArea.containsMouse ? root.cAccent : root.cFg
                font.family: root.font
                font.pixelSize: 20
                font.weight: Font.DemiBold
                MouseArea {
                    id: userArea
                    anchors.fill: parent
                    hoverEnabled: true
                    enabled: userModel.count > 1
                    cursorShape: Qt.PointingHandCursor
                    onClicked: {
                        root.userIndex = (root.userIndex + 1) % userModel.count
                        password.forceActiveFocus()
                    }
                }
            }

            Rectangle {
                id: field
                width: parent.width
                height: 46
                radius: root.r
                color: Qt.rgba(root.cAccent.r, root.cAccent.g, root.cAccent.b, 0.12)
                border.width: 1
                border.color: password.activeFocus ? root.cAccent : Qt.rgba(root.cAccent.r, root.cAccent.g, root.cAccent.b, 0.35)

                SequentialAnimation {
                    id: shake
                    loops: 2
                    NumberAnimation { target: field; property: "x"; to: -10; duration: 50 }
                    NumberAnimation { target: field; property: "x"; to: 10; duration: 100 }
                    NumberAnimation { target: field; property: "x"; to: 0; duration: 50 }
                }

                TextInput {
                    id: password
                    anchors.fill: parent
                    anchors.leftMargin: 16
                    anchors.rightMargin: 48
                    verticalAlignment: TextInput.AlignVCenter
                    echoMode: TextInput.Password
                    passwordCharacter: "•"
                    color: root.cFg
                    selectionColor: root.cAccent
                    font.family: root.font
                    font.pixelSize: 16
                    focus: true
                    clip: true
                    Keys.onReturnPressed: root.login()
                    Keys.onEnterPressed: root.login()

                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        visible: password.text.length === 0
                        text: config.textPassword
                        color: root.cFg
                        opacity: 0.5
                        font: password.font
                    }
                }

                // Flecha para entrar (también con Enter).
                Text {
                    anchors.right: parent.right
                    anchors.rightMargin: 16
                    anchors.verticalCenter: parent.verticalCenter
                    text: ""
                    color: goArea.containsMouse ? root.cAccent : root.cFg
                    font.family: root.iconFont
                    font.pixelSize: 18
                    MouseArea {
                        id: goArea
                        anchors.fill: parent
                        anchors.margins: -8
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: root.login()
                    }
                }
            }

            Text {
                id: error
                anchors.horizontalCenter: parent.horizontalCenter
                visible: false
                text: config.textLoginFailed
                color: config.border
                font.family: root.font
                font.pixelSize: 14
            }

            Text {
                anchors.horizontalCenter: parent.horizontalCenter
                visible: keyboard.capsLock
                text: config.textCapsLock
                color: root.cAccent2
                font.family: root.font
                font.pixelSize: 13
            }
        }
    }

    // Sesión: clic pasa a la siguiente.
    Panel {
        anchors.left: parent.left
        anchors.bottom: parent.bottom
        anchors.margins: 24
        width: sessionRow.implicitWidth + 36
        height: 44

        Row {
            id: sessionRow
            anchors.centerIn: parent
            spacing: 10
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: ""
                color: sessionArea.containsMouse ? root.cAccent : root.cFg
                font.family: root.iconFont
                font.pixelSize: 15
            }
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: root.sessionName(root.sessionIndex) + (sessionModel.count > 1 ? "  ▾" : "")
                color: sessionArea.containsMouse ? root.cAccent : root.cFg
                font.family: root.font
                font.pixelSize: 15
            }
        }
        MouseArea {
            id: sessionArea
            anchors.fill: parent
            hoverEnabled: true
            enabled: sessionModel.count > 1
            cursorShape: Qt.PointingHandCursor
            onClicked: root.sessionIndex = (root.sessionIndex + 1) % sessionModel.count
        }
    }

    // Energía: el nombre aparece al pasar el mouse (línea de diseño:
    // explicaciones en hover, no texto fijo).
    Panel {
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.margins: 24
        width: powerRow.implicitWidth + 24
        height: 44
        visible: sddm.canSuspend || sddm.canReboot || sddm.canPowerOff

        Row {
            id: powerRow
            anchors.centerIn: parent
            spacing: 4

            Repeater {
                model: [
                    { glyph: "", label: config.textSuspend, show: sddm.canSuspend, action: function () { sddm.suspend() } },
                    { glyph: "", label: config.textReboot, show: sddm.canReboot, action: function () { sddm.reboot() } },
                    { glyph: "", label: config.textPowerOff, show: sddm.canPowerOff, action: function () { sddm.powerOff() } }
                ]
                delegate: Rectangle {
                    visible: modelData.show
                    width: btn.containsMouse ? btnText.implicitWidth + 24 : 36
                    height: 36
                    radius: root.r
                    color: btn.containsMouse ? root.cHover : "transparent"
                    Behavior on width { NumberAnimation { duration: 120 } }

                    Text {
                        id: btnText
                        anchors.centerIn: parent
                        text: btn.containsMouse ? modelData.glyph + "  " + modelData.label : modelData.glyph
                        color: btn.containsMouse ? root.cAccent : root.cFg
                        font.family: root.iconFont
                        font.pixelSize: 16
                    }
                    MouseArea {
                        id: btn
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: modelData.action()
                    }
                }
            }
        }
    }

    Component.onCompleted: password.forceActiveFocus()
}
