import QtQuick

// Assistente "Configurar a TV" (protótipo aprovado em 01/11): achar a TV, código, instalar, conectar, testar, capas e
// legendas. O trabalho fica no tvsetup.py (objeto "setup"); aqui só a tela. Carregado por um Loader no Overlay.qml.
Item {
    id: wiz
    signal close()
    property string font: "Segoe UI Variable Text"
    readonly property string mono: "Consolas"

    readonly property var st: setup.st
    readonly property int step: st.step || 0
    readonly property var steps: ["Início", "Achar a TV", "Código", "Instalar", "Conectar", "Teste", "Capas", "Legendas", "Pronto"]
    property int maxStep: 0
    onStepChanged: maxStep = Math.max(maxStep, step)

    readonly property color bg: "#0e0e10"
    readonly property color panel: "#161619"
    readonly property color panel2: "#1e1e22"
    readonly property color line: "#2a2a30"
    readonly property color fg: "#f4f2ee"
    readonly property color mute: "#a3a09a"
    readonly property color dim: "#6d6b67"
    readonly property color orange: "#ff8a1f"
    readonly property color green: "#3ecf6e"
    readonly property color warn: "#ffc24a"
    readonly property color bad: "#ff5a4e"

    function handleKey(e) {
        if (e.key === Qt.Key_Escape) { wiz.close(); return true }
        return false
    }

    Rectangle { anchors.fill: parent; color: wiz.bg }
    // arrastar a janela pela faixa de cima
    MouseArea {
        anchors { left: parent.left; right: parent.right; top: parent.top }
        height: 52
        onPressed: player.startMove()
        onDoubleClicked: player.toggleMaximize()
    }

    // ---------- componentes ----------
    component Txt: Text {
        color: wiz.mute; font.family: wiz.font; font.pixelSize: 15; wrapMode: Text.Wrap; lineHeight: 1.15
        textFormat: Text.StyledText; linkColor: wiz.fg
    }
    component Title: Text {
        color: wiz.fg; font.family: wiz.font; font.pixelSize: 26; font.weight: Font.Bold; wrapMode: Text.Wrap
    }
    component Button: Rectangle {
        id: b
        property string label: ""
        property bool primary: false
        property bool link: false
        signal clicked()
        implicitWidth: link ? lt.implicitWidth + 8 : lt.implicitWidth + 36
        implicitHeight: 40
        radius: 9
        opacity: enabled ? 1 : 0.4
        color: link ? "transparent" : primary ? (ma.containsMouse ? "#ffa04a" : wiz.orange) : (ma.containsMouse ? "#26262b" : wiz.panel2)
        border.color: link ? "transparent" : primary ? wiz.orange : wiz.line
        Text {
            id: lt
            anchors.centerIn: parent
            text: b.label
            color: b.primary ? "#141414" : b.link ? (ma.containsMouse ? wiz.fg : wiz.mute) : wiz.fg
            font.family: wiz.font; font.pixelSize: b.link ? 14 : 15; font.weight: b.link ? Font.Normal : Font.DemiBold
            font.underline: b.link
        }
        MouseArea { id: ma; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: b.clicked() }
    }
    component Spinner: Item {
        width: 16; height: 16
        Rectangle { anchors.fill: parent; radius: 8; color: "transparent"; border.width: 2; border.color: wiz.line }
        Item {
            anchors.fill: parent
            Rectangle { x: 6; y: 0; width: 4; height: 4; radius: 2; color: wiz.orange }
            RotationAnimation on rotation { from: 0; to: 360; duration: 800; loops: Animation.Infinite }
        }
    }
    component Waiting: Row {
        property string label: ""
        spacing: 10
        Spinner { anchors.verticalCenter: parent.verticalCenter }
        Txt { text: parent.label; font.pixelSize: 14; wrapMode: Text.NoWrap }
    }
    component Field: Rectangle {
        id: f
        property alias text: inp.text
        property alias input: inp
        property string label: ""
        property string placeholder: ""
        property bool password: false
        property bool big: false
        width: parent ? Math.min(parent.width, big ? 330 : 440) : 330
        height: big ? 64 : 42
        radius: 9; color: wiz.panel2
        border.color: inp.activeFocus ? wiz.orange : wiz.line
        TextInput {
            id: inp
            anchors { fill: parent; leftMargin: 14; rightMargin: 14 }
            verticalAlignment: TextInput.AlignVCenter
            color: wiz.fg; selectionColor: wiz.orange; selectedTextColor: "#141414"
            font.family: f.big ? wiz.mono : wiz.font; font.pixelSize: f.big ? 32 : 15; font.bold: f.big
            font.letterSpacing: f.big ? 10 : 0; font.capitalization: f.big ? Font.AllUppercase : Font.MixedCase
            echoMode: f.password ? TextInput.Password : TextInput.Normal
            clip: true; selectByMouse: true
        }
        Text {
            anchors { left: parent.left; leftMargin: 14; verticalCenter: parent.verticalCenter }
            visible: !inp.text && !inp.activeFocus
            text: f.placeholder; color: wiz.dim; font.family: wiz.font; font.pixelSize: 14
        }
        MouseArea { anchors.fill: parent; cursorShape: Qt.IBeamCursor; onPressed: function (m) { inp.forceActiveFocus(); m.accepted = false } }
    }
    component Box: Rectangle {
        // aviso: "ok" verde, "help" amarelo
        id: bx
        property string kind: "ok"
        property string text: ""
        width: parent ? parent.width : 400
        height: bt.implicitHeight + 24
        radius: 10
        color: kind === "ok" ? Qt.rgba(0.24, 0.81, 0.43, 0.13) : Qt.rgba(1, 0.76, 0.29, 0.13)
        Rectangle {
            id: dot
            visible: bx.kind === "ok"
            x: 14; anchors.verticalCenter: parent.verticalCenter
            width: 26; height: 26; radius: 13; color: wiz.green
            Text { anchors.centerIn: parent; text: "✓"; color: "#0b2414"; font.bold: true; font.pixelSize: 14 }
        }
        Txt {
            id: bt
            x: dot.visible ? 52 : 14; width: parent.width - x - 14
            anchors.verticalCenter: parent.verticalCenter
            text: bx.text; color: wiz.fg; font.pixelSize: 14
        }
    }
    component Task: Rectangle {
        // uma linha de passo: s = wait | now | ok | warn | bad
        id: tk
        property string s: "wait"
        property string title: ""
        property string body: ""
        property string value: ""
        default property alias extra: more.data
        width: parent ? parent.width : 400
        height: col.implicitHeight + 16
        radius: 10
        color: s === "now" ? wiz.panel2 : "transparent"
        border.color: s === "now" ? wiz.line : "transparent"
        opacity: s === "wait" ? 0.45 : 1
        Rectangle {
            x: 12; y: 9; width: 22; height: 22; radius: 11
            color: tk.s === "ok" ? wiz.green : tk.s === "warn" ? wiz.warn : tk.s === "bad" ? wiz.bad : "transparent"
            border.width: 2
            border.color: tk.s === "now" ? wiz.orange : tk.s === "wait" ? wiz.line : color
            Text {
                anchors.centerIn: parent
                text: tk.s === "ok" ? "✓" : tk.s === "warn" || tk.s === "bad" ? "!" : ""
                color: "#141414"; font.bold: true; font.pixelSize: 12
            }
        }
        Column {
            id: col
            x: 46; y: 8; width: parent.width - 58
            spacing: 2
            Row {
                spacing: 8
                Text { text: tk.title; color: wiz.fg; font.family: wiz.font; font.pixelSize: 15; font.weight: Font.DemiBold }
                Text { visible: tk.s === "now" && !tk.body; text: "testando…"; color: wiz.mute; font.family: wiz.mono; font.pixelSize: 13; anchors.baseline: parent.children[0].baseline }
            }
            Txt { visible: !!tk.value; width: parent.width; text: tk.value; font.family: wiz.mono; font.pixelSize: 13 }
            Txt { visible: !!tk.body; width: parent.width; text: tk.body; font.pixelSize: 14 }
            Column { id: more; width: parent.width; spacing: 8; topPadding: visibleChildren.length ? 6 : 0 }
        }
    }
    component Bar: Rectangle {
        property real p: 0
        width: parent ? parent.width : 300
        height: 8; radius: 4; color: wiz.panel2; border.color: wiz.line
        Rectangle { width: parent.width * Math.max(0, Math.min(1, parent.p / 100)); height: parent.height; radius: 4; color: wiz.orange }
    }
    component Mini: Rectangle {
        // card de exemplo do "antes e depois"
        id: mn
        property bool art: true
        property string head: ""
        property string sub: ""
        property string body: ""
        width: parent ? parent.width : 200
        height: 118; radius: 10; color: wiz.panel2; border.color: wiz.line
        Rectangle {
            id: pst
            x: 10; y: 12; width: 62; height: 92; radius: 6
            gradient: Gradient {
                GradientStop { position: 0; color: mn.art ? "#2a6a8c" : "#2a2a2f" }
                GradientStop { position: 1; color: mn.art ? "#0b1f2c" : "#2a2a2f" }
            }
            Text {
                anchors { left: parent.left; right: parent.right; bottom: parent.bottom; margins: 6 }
                text: mn.art ? "Maré Alta" : "Mare.Alta.2024.1080p.mkv"
                color: mn.art ? "white" : wiz.dim; wrapMode: mn.art ? Text.Wrap : Text.WrapAnywhere
                font.family: mn.art ? wiz.font : wiz.mono; font.pixelSize: mn.art ? 11 : 9; font.bold: mn.art
            }
        }
        Column {
            x: 84; y: 12; width: parent.width - 94; spacing: 3
            Text { text: mn.head; color: wiz.fg; font.family: mn.art ? wiz.font : wiz.mono; font.pixelSize: mn.art ? 14 : 11; font.bold: mn.art; width: parent.width; elide: Text.ElideRight }
            Text { text: mn.sub; color: mn.art ? wiz.mute : wiz.dim; font.family: wiz.font; font.pixelSize: 13; width: parent.width; wrapMode: Text.Wrap }
            Text { text: mn.body; color: wiz.mute; font.family: wiz.font; font.pixelSize: 12; width: parent.width; wrapMode: Text.Wrap; maximumLineCount: 3; elide: Text.ElideRight }
        }
    }
    component Caption: Text {
        color: wiz.dim; font.family: wiz.font; font.pixelSize: 12; font.bold: true; font.letterSpacing: 0.8; font.capitalization: Font.AllUppercase
    }

    // ---------- etapas no topo ----------
    Flow {
        id: chips
        anchors { top: parent.top; topMargin: 18; horizontalCenter: parent.horizontalCenter }
        width: Math.min(parent.width - 32, 900)
        spacing: 6
        Repeater {
            model: wiz.steps
            delegate: Rectangle {
                required property string modelData
                required property int index
                readonly property bool cur: index === wiz.step
                readonly property bool reach: index <= wiz.maxStep && index !== 2  // o código só vale na hora
                height: 26; width: ct.implicitWidth + 22; radius: 13
                color: cur ? Qt.rgba(1, 0.54, 0.12, 0.14) : "transparent"
                border.color: cur ? wiz.orange : wiz.line
                opacity: index > wiz.maxStep ? 0.45 : 1
                Text {
                    id: ct
                    anchors.centerIn: parent
                    text: (index < wiz.step ? "✓ " : (index + 1) + "  ") + modelData
                    color: parent.cur ? wiz.fg : index < wiz.step ? wiz.fg : wiz.mute
                    font.family: wiz.font; font.pixelSize: 13
                }
                MouseArea { anchors.fill: parent; enabled: parent.reach && !parent.cur; cursorShape: Qt.PointingHandCursor; onClicked: setup.go(index) }
            }
        }
    }
    // fechar
    Text {
        anchors { right: parent.right; top: parent.top; margins: 16 }
        text: "✕"; color: closeMa.containsMouse ? wiz.fg : wiz.mute; font.pixelSize: 18
        MouseArea { id: closeMa; anchors.fill: parent; anchors.margins: -8; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: wiz.close() }
    }

    // ---------- conteúdo da etapa ----------
    Flickable {
        id: flick
        anchors { top: chips.bottom; topMargin: 24; bottom: foot.top; bottomMargin: 12; horizontalCenter: parent.horizontalCenter }
        width: Math.min(parent.width - 48, 620)
        contentHeight: page.height
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        Column {
            id: page
            width: flick.width
            spacing: 16

            // 0. início
            Column {
                visible: wiz.step === 0; width: parent.width; spacing: 16
                Title { width: parent.width; text: "Vamos colocar o onVLC na sua TV" }
                Txt { width: parent.width; text: "Leva uns 3 minutos. Deixe a TV ligada e o controle remoto por perto: em alguns momentos você confirma alguma coisa na TV." }
                Repeater {
                    model: [["Achar a TV na sua rede", "Sem digitar endereço nenhum."], ["Instalar o onVLC na TV", "Direto deste PC, pela rede."],
                            ["Conectar e testar", "Um vídeo de 10 segundos confere imagem, som e rede."], ["Capas e legendas", "Opcional. Dá para fazer depois."]]
                    delegate: Row {
                        required property var modelData
                        required property int index
                        spacing: 12; width: page.width
                        Rectangle {
                            width: 26; height: 26; radius: 13; color: wiz.panel2; border.color: wiz.line
                            Text { anchors.centerIn: parent; text: index + 1; color: wiz.mute; font.family: wiz.font; font.pixelSize: 12; font.bold: true }
                        }
                        Column {
                            width: parent.width - 40
                            Text { text: modelData[0]; color: wiz.fg; font.family: wiz.font; font.pixelSize: 15 }
                            Txt { width: parent.width; text: modelData[1]; font.pixelSize: 14 }
                        }
                    }
                }
            }

            // 1. achar a TV
            Column {
                visible: wiz.step === 1; width: parent.width; spacing: 14
                Title { width: parent.width; text: "Qual é a sua TV?" }
                Txt { width: parent.width; text: "Estas são as TVs com Android que estão na mesma rede deste PC." }
                Repeater {
                    model: wiz.step === 1 ? (st.tvs || []) : []
                    delegate: Rectangle {
                        required property var modelData
                        width: page.width; height: 66; radius: 10
                        color: tvMa.containsMouse ? "#24242a" : wiz.panel2
                        border.color: tvMa.containsMouse ? wiz.orange : wiz.line
                        Rectangle {
                            x: 16; anchors.verticalCenter: parent.verticalCenter
                            width: 40; height: 26; radius: 4; color: "transparent"; border.width: 2; border.color: wiz.mute
                        }
                        Column {
                            x: 72; anchors.verticalCenter: parent.verticalCenter; width: parent.width - 180; spacing: 2
                            Text { text: modelData.name + (modelData.saved ? "  · já ligada a este PC" : ""); color: wiz.fg; font.family: wiz.font; font.pixelSize: 15; font.bold: true; elide: Text.ElideRight; width: parent.width }
                            Text {
                                text: [modelData.model, modelData.ip, modelData.mac].filter(function (x) { return !!x }).join(" · ")
                                color: wiz.mute; font.family: wiz.mono; font.pixelSize: 13; elide: Text.ElideRight; width: parent.width
                            }
                        }
                        Item {
                            anchors { right: parent.right; rightMargin: 16; verticalCenter: parent.verticalCenter }
                            width: 90; height: 20
                            Text { anchors.right: parent.right; visible: st.picking !== modelData.ip; text: "Escolher"; color: wiz.orange; font.family: wiz.font; font.pixelSize: 14; font.weight: Font.DemiBold }
                            Spinner { anchors.right: parent.right; visible: st.picking === modelData.ip }
                        }
                        MouseArea { id: tvMa; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; enabled: !st.picking; onClicked: setup.pick(modelData.ip) }
                    }
                }
                Waiting { label: st.scanned ? "Continuo procurando…" : "Procurando TVs na rede…" }
                Button { link: true; label: "Minha TV não aparece"; onClicked: notFound.visible = !notFound.visible }
                Box {
                    id: notFound; visible: false; kind: "help"
                    text: "Confira se a TV está ligada (não só em espera) e no mesmo Wi-Fi do PC. Algumas TVs desligam a rede em espera: aperte qualquer botão do controle e espere 10 segundos. Só aparecem TVs com Android TV ou Google TV."
                }
            }

            // 2. código
            Column {
                visible: wiz.step === 2; width: parent.width; spacing: 16
                Title { width: parent.width; text: "Digite o código que apareceu na TV" }
                Txt { width: parent.width; text: "É o mesmo pareamento do controle remoto do Google: depois dele, este PC consegue apertar botões e abrir o onVLC na TV por você." }
                Field {
                    id: codeField; big: true; placeholder: ""
                    input.maximumLength: 6
                    input.onAccepted: if (codeField.text.length === 6) setup.sendCode(codeField.text)
                    Connections { target: wiz; function onStepChanged() { if (wiz.step === 2) { codeField.text = ""; codeField.input.forceActiveFocus() } } }
                }
                Waiting { visible: !!(st.code && st.code.checking); label: "Conferindo…" }
                Box { visible: !!(st.code && st.code.error); kind: "help"; text: st.code ? (st.code.error || "") : "" }
            }

            // 3. instalar
            Column {
                id: inst
                visible: wiz.step === 3; width: parent.width; spacing: 14
                readonly property var i: st.install || ({})
                readonly property int at: ({ dev_settings: 0, dev_build: 1, dev_usb: 2, allow: 3, sending: 4, done: 5 })[i.phase] !== undefined
                                          ? ({ dev_settings: 0, dev_build: 1, dev_usb: 2, allow: 3, sending: 4, done: 5 })[i.phase] : -1
                function s(n) { return inst.at > n ? "ok" : inst.at === n ? "now" : "wait" }
                Title { width: parent.width; text: inst.i.phase === "done" ? "onVLC instalado" : "Instalar o onVLC na TV" }
                Txt {
                    width: parent.width; visible: inst.i.phase !== "done"
                    text: inst.at >= 0 && inst.at <= 2 ? "Esta TV ainda não deixa o PC instalar apps. A gente libera isso uma vez só, e eu aperto o que der por você."
                                                       : "O app vai pela rede, direto deste PC."
                }
                Waiting { visible: !inst.i.phase; label: "Conferindo a TV…" }
                Column {
                    visible: inst.at >= 0 && inst.i.phase !== "done"; width: parent.width; spacing: 6
                    Task {
                        s: inst.s(0); title: "Abrir as Configurações"
                        body: inst.at === 0 ? "Vou abrir as Configurações na TV. Lá, entre em <b>Sistema → Sobre</b> (em algumas TVs: <b>Preferências do dispositivo → Sobre</b>)." : ""
                        Button { visible: inst.at === 0; primary: true; label: "Abrir Configurações na TV"; onClicked: setup.devSettings() }
                        Button { visible: inst.at === 0; link: true; label: "A depuração já está ligada"; onClicked: setup.devSkip() }
                    }
                    Task {
                        s: inst.s(1); title: "Virar \"desenvolvedor\""
                        body: inst.at === 1 ? "Na TV, desça até <b>Build</b> (ou <b>Build do SO do Android TV</b>) e deixe selecionado. Eu aperto OK 7 vezes por você." : ""
                        Button {
                            visible: inst.at === 1; primary: true
                            enabled: !inst.i.press
                            label: inst.i.press ? "Apertando… " + inst.i.press + "/7" : "Apertar 7 vezes"
                            onClicked: setup.devPress()
                        }
                    }
                    Task {
                        s: inst.s(2); title: "Ligar \"Depuração USB\""
                        body: inst.at === 2 ? "Na TV, volte uma tela, abra <b>Opções do desenvolvedor</b> e ligue <b>Depuração USB</b>." : ""
                        Waiting { visible: inst.at === 2; label: "Esperando a TV liberar…" }
                    }
                    Task {
                        s: inst.s(3); title: "Permitir este PC"
                        body: inst.at === 3 ? "Na TV, marque <b>Sempre permitir deste computador</b> e aperte <b>OK</b>." : ""
                        Waiting { visible: inst.at === 3; label: inst.i.asked ? "Pedi de novo na TV. Esperando a permissão…" : "Esperando a permissão…" }
                    }
                    Task {
                        s: inst.s(4); title: "Instalar o onVLC"
                        body: inst.at === 4 ? "Enviando o app para a TV · " + (inst.i.p || 0) + "%" : ""
                        Bar { visible: inst.at === 4; p: inst.i.p || 0 }
                    }
                }
                Box { visible: inst.i.phase === "done"; text: inst.i.msg || "onVLC instalado na TV." }
                Box { visible: inst.i.phase === "error"; kind: "help"; text: inst.i.msg || "" }
                Button { visible: inst.i.phase === "error"; label: "Tentar de novo"; onClicked: setup.retryInstall() }
            }

            // 4. conectar
            Column {
                id: conn
                visible: wiz.step === 4; width: parent.width; spacing: 16
                readonly property var c: st.conn || ({})
                Title { width: parent.width; text: conn.c.phase === "ok" ? "TV e PC conectados" : "Abrindo o onVLC na TV…" }
                Txt {
                    width: parent.width
                    text: conn.c.phase === "ok" ? "A TV já sabe onde este PC está. Se o endereço mudar, ela acha de novo sozinha."
                                                : "O app abre na TV já com o endereço deste PC."
                }
                Waiting { visible: conn.c.phase !== "ok" && conn.c.phase !== "error"; label: "Conectando…" }
                Box { visible: conn.c.phase === "ok"; text: "<b>" + (st.tvName || "TV") + "</b>  ↔  <b>" + (st.pcName || "") + "</b>" }
                Box { visible: conn.c.phase === "error"; kind: "help"; text: conn.c.msg || "" }
                Button { visible: conn.c.phase === "error"; label: "Tentar de novo"; onClicked: setup.retryConnect() }
            }

            // 5. teste
            Column {
                id: tst
                visible: wiz.step === 5; width: parent.width; spacing: 14
                readonly property var t: st.test || ({})
                // terminou: rola até o fim, onde estão a pergunta e o "Testar de novo"
                readonly property bool done: t.phase === "done"
                onDoneChanged: if (done) Qt.callLater(function () { flick.contentY = Math.max(0, flick.contentHeight - flick.height) })
                Title { width: parent.width; text: "Teste rápido" }
                Txt { width: parent.width; text: "Vou mandar um vídeo de 10 segundos para a TV, com cores, movimento e som. A TV me conta o que conseguiu tocar." }
                Button { visible: !tst.t.phase; primary: true; label: "Começar o teste"; onClicked: setup.runTest() }
                Column {
                    visible: !!tst.t.phase; width: parent.width; spacing: 2
                    Repeater {
                        model: tst.t.rows || []
                        delegate: Task {
                            required property var modelData
                            required property int index
                            s: modelData.s; title: modelData.t; value: modelData.v
                            Button { visible: index === 5 && !!tst.t.wolFix; label: "Ligar para mim"; onClicked: setup.fixWol() }
                        }
                    }
                }
                Row {
                    visible: tst.t.phase === "done" && tst.t.seen !== true && tst.t.seen !== false; spacing: 10  // ainda não respondeu (o Python manda None = undefined)
                    Text { anchors.verticalCenter: parent.verticalCenter; text: "Você viu e ouviu o vídeo na TV?"; color: wiz.fg; font.family: wiz.font; font.pixelSize: 15; font.weight: Font.DemiBold }
                    Button { label: "Sim"; onClicked: setup.seen(true) }
                    Button { label: "Não"; onClicked: setup.seen(false) }
                }
                Box {
                    visible: tst.t.seen === false; kind: "help"
                    text: "Confira se a TV não está no mudo e se está na entrada certa (o HDMI onde a caixa da TV está ligada). Se a imagem apareceu sem som, o problema costuma ser o volume da TV."
                }
                // terminou (com ou sem problema): dá para repetir na hora, sem precisar voltar
                Button { visible: tst.t.phase === "done"; label: "Testar de novo"; onClicked: setup.runTest() }
            }

            // 6. capas
            Column {
                id: cap
                visible: wiz.step === 6; width: parent.width; spacing: 14
                readonly property var t: st.tmdb || ({})
                property bool change: false
                Title { width: parent.width; text: "Capas e sinopses dos filmes" }
                Txt { width: parent.width; text: "O onVLC busca capa, logo, sinopse e nota no TMDB, um catálogo gratuito de filmes e séries. Para isso ele precisa de um token seu." }
                Row {
                    width: parent.width; spacing: 12
                    Column {
                        width: (parent.width - 12) / 2; spacing: 6
                        Caption { text: "Sem o token" }
                        Mini { art: false; head: "Mare.Alta.2024.1080p.mkv"; sub: "Sem capa, sem sinopse, sem nota." }
                    }
                    Column {
                        width: (parent.width - 12) / 2; spacing: 6
                        Caption { text: "Com o token (exemplo)" }
                        Mini { head: "Maré Alta"; sub: "2024 · ★ 7,4 · Drama"; body: "Uma pescadora volta à vila onde cresceu e descobre que a família vendeu o barco do pai." }
                    }
                }
                Box { visible: cap.t.phase === "ok" || (cap.t.has && !cap.change && cap.t.phase !== "error"); text: cap.t.phase === "ok" ? "Token funcionando. A biblioteca já está buscando as capas." : "As capas já estão ligadas neste PC." }
                Button { visible: cap.t.has && !cap.change && cap.t.phase !== "ok" && cap.t.phase !== "error"; link: true; label: "Usar outro token"; onClicked: cap.change = true }
                Column {
                    visible: cap.t.phase !== "ok" && (!cap.t.has || cap.change || cap.t.phase === "error"); width: parent.width; spacing: 12
                    Txt {
                        width: parent.width; font.pixelSize: 14
                        text: "1. Crie uma conta grátis em <b>themoviedb.org</b><br>2. Em <b>Configurações → API</b>, peça uma chave para uso pessoal<br>3. Copie o <b>Token de leitura da API</b> (o texto longo) e cole aqui"
                    }
                    Field { id: tmdbField; placeholder: "Token de leitura da API"; width: parent.width; input.onAccepted: setup.tmdbCheck(tmdbField.text) }
                    Row {
                        spacing: 10
                        Button { label: cap.t.phase === "checking" ? "Conferindo…" : "Conectar"; enabled: cap.t.phase !== "checking" && tmdbField.text.length > 10; onClicked: setup.tmdbCheck(tmdbField.text) }
                    }
                    Box { visible: cap.t.phase === "error"; kind: "help"; text: cap.t.msg || "" }
                }
            }

            // 7. legendas
            Column {
                id: leg
                visible: wiz.step === 7; width: parent.width; spacing: 14
                readonly property var o: st.os || ({})
                Title { width: parent.width; text: "Legendas automáticas" }
                Txt { width: parent.width; text: "Quando um filme chega sem legenda, o onVLC procura uma em português no OpenSubtitles e deixa pronta no PC e na TV." }
                Row {
                    width: parent.width; spacing: 12
                    Column {
                        width: (parent.width - 12) / 2; spacing: 6
                        Caption { text: "Sem conta" }
                        Rectangle {
                            width: parent.width; height: 84; radius: 10; color: wiz.panel2; border.color: wiz.line
                            Column {
                                x: 12; y: 12; width: parent.width - 24; spacing: 3
                                Text { text: "Cidade de Vidro"; color: wiz.fg; font.family: wiz.font; font.pixelSize: 14; font.bold: true }
                                Text { text: "Legendas: nenhuma"; color: wiz.mute; font.family: wiz.font; font.pixelSize: 13 }
                            }
                        }
                    }
                    Column {
                        width: (parent.width - 12) / 2; spacing: 6
                        Caption { text: "Com conta (exemplo)" }
                        Rectangle {
                            width: parent.width; height: 84; radius: 10; color: wiz.panel2; border.color: wiz.line
                            Column {
                                x: 12; y: 12; width: parent.width - 24; spacing: 3
                                Text { text: "Cidade de Vidro"; color: wiz.fg; font.family: wiz.font; font.pixelSize: 14; font.bold: true }
                                Text { text: "Português (Brasil) · baixada sozinha"; color: wiz.orange; font.family: wiz.font; font.pixelSize: 13; width: parent.width; wrapMode: Text.Wrap }
                            }
                        }
                    }
                }
                Box { visible: !leg.o.available; kind: "help"; text: "As legendas automáticas ainda não estão disponíveis nesta versão do onVLC. Dá para pular: quando estiverem, aparecem em Configurações." }
                Box { visible: leg.o.available && (leg.o.phase === "ok" || (leg.o.has && leg.o.phase !== "error")); text: "Conectado como <b>" + (leg.o.user || "") + "</b>." }
                Column {
                    visible: leg.o.available && leg.o.phase !== "ok" && !leg.o.has; width: parent.width; spacing: 12
                    Txt { width: parent.width; font.pixelSize: 14; text: "Use sua conta grátis do <b>opensubtitles.com</b>. Sem conta, o site deixa baixar pouquíssimas legendas por dia." }
                    Field { id: osUser; placeholder: "Usuário"; text: leg.o.user || "" }
                    Field { id: osPass; placeholder: "Senha"; password: true; input.onAccepted: setup.osLogin(osUser.text, osPass.text) }
                    Button { label: leg.o.phase === "checking" ? "Entrando…" : "Entrar"; enabled: leg.o.phase !== "checking" && !!osUser.text && !!osPass.text; onClicked: setup.osLogin(osUser.text, osPass.text) }
                    Box { visible: leg.o.phase === "error"; kind: "help"; text: leg.o.msg || "" }
                }
            }

            // 8. pronto
            Column {
                visible: wiz.step === 8; width: parent.width; spacing: 16
                Title { width: parent.width; text: "Tudo pronto" }
                Repeater {
                    model: [[st.hasTv, st.hasTv ? "onVLC instalado e conectado na <b>" + (st.tvName || "TV") + "</b>" : "Nenhuma TV ligada ainda"],
                            [st.test && st.test.seen === true, "Teste de imagem, som e rede"],
                            [st.tmdb && st.tmdb.has, st.tmdb && st.tmdb.has ? "Capas e sinopses (TMDB)" : "Capas e sinopses: dá para ligar depois no menu da TV"],
                            [st.os && st.os.has, st.os && st.os.has ? "Legendas automáticas (OpenSubtitles)" : "Legendas automáticas: dá para ligar depois no menu da TV"]]
                    delegate: Row {
                        required property var modelData
                        spacing: 10
                        Rectangle {
                            width: 20; height: 20; radius: 10
                            color: modelData[0] ? wiz.green : wiz.panel2; border.color: modelData[0] ? wiz.green : wiz.line
                            Text { anchors.centerIn: parent; text: modelData[0] ? "✓" : "–"; color: modelData[0] ? "#0b2414" : wiz.dim; font.bold: true; font.pixelSize: 11 }
                        }
                        Txt { text: modelData[1]; color: wiz.fg; wrapMode: Text.NoWrap }
                    }
                }
                Txt { width: parent.width; text: "Coloque filmes e séries em <b>Vídeos\\onVLC</b>. Eles aparecem na TV em segundos, e o filme continua de onde parou em qualquer tela." }
            }

            Box { visible: !!st.error; kind: "help"; text: st.error || "" }
        }
    }

    // ---------- rodapé: Voltar à esquerda, o botão principal à direita ----------
    Item {
        id: foot
        anchors { bottom: parent.bottom; bottomMargin: 22; horizontalCenter: parent.horizontalCenter }
        width: flick.width; height: 40
        Button { visible: wiz.step > 0; label: "← Voltar"; onClicked: setup.go(wiz.step === 3 && st.code && st.code.done ? 1 : wiz.step - 1) }
        Row {
            anchors.right: parent.right
            spacing: 10
            Button { visible: wiz.step === 0; link: true; label: "Agora não"; onClicked: { setup.dismiss(); wiz.close() } }
            Button { visible: wiz.step === 5 && !(st.test && st.test.phase === "done"); link: true; label: "Pular o teste"; onClicked: setup.go(6) }
            Button { visible: wiz.step === 8; link: true; label: "Abrir a pasta"; onClicked: setup.openFolder() }
            Button {
                visible: wiz.step !== 1  // na lista de TVs, escolher a TV já é o próximo passo
                primary: true
                label: ["Começar", "", "Conectar", "Continuar", "Testar", "Continuar",
                        st.tmdb && st.tmdb.has ? "Continuar" : "Pular por agora",
                        st.os && st.os.has ? "Continuar" : "Pular por agora", "Concluir"][wiz.step]
                enabled: wiz.step === 2 ? codeField.text.length === 6 && !(st.code && st.code.checking)
                       : wiz.step === 3 ? !!(st.install && st.install.phase === "done")
                       : wiz.step === 4 ? !!(st.conn && st.conn.phase === "ok")
                       : wiz.step === 5 ? !!(st.test && st.test.phase === "done")  // o teste informa; não prende ninguém
                       : true
                onClicked: {
                    if (wiz.step === 2) setup.sendCode(codeField.text)
                    else if (wiz.step === 8) { setup.finish(); wiz.close() }
                    else setup.go(wiz.step + 1)
                }
            }
        }
    }
}
