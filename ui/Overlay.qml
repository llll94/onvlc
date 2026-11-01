import QtQuick

// Camada transparente por cima do vídeo: barra de baixo, faixa de cima, menu, quadros e avisos.
Item {
    id: root
    focus: true

    property var s: player.state
    // a biblioteca (o mesmo desenho da tela da TV) por cima do player: abre sozinha quando não há vídeo
    property bool libraryOpen: !player.state.hasMedia
    // voltou para a biblioteca com um filme aberto: o filme fecha (os controles do player não ficam por trás)
    onLibraryOpenChanged: if (libraryOpen && s.hasMedia) player.closeVideo()

    // cores e fontes
    readonly property color orange: "#ff8a1f"
    readonly property color orangeSoft: Qt.rgba(1, 0.54, 0.12, 0.14)
    readonly property color orangeLine: Qt.rgba(1, 0.54, 0.12, 0.5)
    readonly property color btnColor: Qt.rgba(1, 1, 1, 0.72)
    readonly property color panelBg: "#1e1f24"
    readonly property color panelDark: "#17181c"
    readonly property color panel2: "#2a2c33"
    readonly property color line: Qt.rgba(1, 1, 1, 0.08)
    readonly property color mutedText: "#9a9ca5"
    readonly property color green: "#3ecf6e"
    readonly property string uiFont: "Segoe UI Variable Text"
    readonly property string monoFont: "Consolas"

    property bool showTop: false
    property bool showBottom: false
    property bool idle: false
    property bool menuOpen: false
    property string cat: "midia"
    property string popName: ""
    property Item popAnchor: null
    property bool popHovered: false
    property string trackView: "main"
    property real hoverP: -1

    readonly property real dur: s.dur || 0
    readonly property bool watchOn: !!(s.watch && s.watch.stage)

    readonly property var cats: [
        { id: "midia", nome: "Mídia", ic: "film", desc: "Abrir e organizar o que vai tocar." },
        { id: "reproducao", nome: "Reprodução", ic: "playc", desc: "Como o vídeo anda." },
        { id: "audio", nome: "Áudio", ic: "vol", desc: "Faixa, volume e saída." },
        { id: "video", nome: "Vídeo", ic: "screen", desc: "Imagem, tamanho e janela." },
        { id: "legenda", nome: "Legenda", ic: "cc", desc: "Faixas, arquivos e sincronia." },
        { id: "ferramentas", nome: "Ferramentas", ic: "tools", desc: "Informações e utilidades." },
        { id: "exibir", nome: "Exibir", ic: "eye", desc: "O que aparece na tela." },
        { id: "ajuda", nome: "Ajuda", ic: "help", desc: "" }
    ]

    // ---------- utilidades ----------
    function fmt(sec) {
        sec = Math.max(0, Math.floor(sec || 0))
        var h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60), x = sec % 60
        return (h ? h + ":" + (m < 10 ? "0" : "") + m : m) + ":" + (x < 10 ? "0" : "") + x
    }
    function rateTxt(r) { return String(Math.round((r || 1) * 100) / 100).replace(".", ",") + "×" }
    function trackName(list, id) {
        list = list || []
        for (var i = 0; i < list.length; i++) if (list[i].id === id) return id === -1 ? "Desligada" : list[i].name
        return id === -1 ? "Desligada" : "—"
    }
    function subLabel(t) { return t.id === -1 ? "Desligadas" : t.name }
    function catInfo(id) {
        for (var i = 0; i < cats.length; i++) if (cats[i].id === id) return cats[i]
        return { nome: "Pasta onVLC", desc: "Os vídeos que a TV mostra e toca direto do PC." }
    }

    function moved(y) {
        idle = false
        showBottom = s.hasMedia && (y > height - 230 || popName !== "")
        showTop = y < 110 || menuOpen
        idleTimer.restart()
    }
    Timer {
        id: idleTimer
        interval: 2500
        onTriggered: {
            // quadro aberto sem o mouse em cima dele nem do botão: fecha
            if (root.popName !== "" && !root.popPinned() && !root.popHovered && !(root.popAnchor && root.popAnchor.hot)) root.popName = ""
            if (root.popName !== "" || root.menuOpen) return
            if (!bottomHover.hovered) root.showBottom = false
            if (!topHover.hovered) root.showTop = false
            root.idle = !bottomHover.hovered && !topHover.hovered
        }
    }

    function openPop(name, btn) {
        popClose.stop()
        if (popName !== name && name === "tracks") trackView = "main"
        popAnchor = btn
        popName = name
    }
    // nas telas de busca o quadro não fecha sozinho quando o mouse sai (dá para digitar e ler com calma);
    // o balão da TV fica aberto como um aviso até o mouse passar por cima dele e sair
    function popPinned() { return (popName === "tracks" && trackView !== "main") || (popName === "tv" && watchPinned) }
    function leavePop() { if (!popPinned()) popClose.restart() }
    onPopNameChanged: if (popName === "") { accountForm = false; watchPinned = false; root.forceActiveFocus() }
    property bool watchPinned: false
    property bool watchArmed: false
    readonly property string watchStage: s.watch ? (s.watch.stage || "") : ""
    function showWatch() {
        popClose.stop()
        showBottom = true
        popAnchor = tvBtn
        popName = "tv"
        watchPinned = true
        watchArmed = false
    }
    // etapas por onde a conexão já passou ("acordar" e "ligar a TV" só quando acontecem de verdade)
    property var seenStages: []
    // abre o balão sozinho no começo e quando algo acontece (TV desligada, outra janela, erro)
    onWatchStageChanged: {
        if (watchStage === "connect") seenStages = ["connect"]
        else if (["library", "wake", "tv", "open"].indexOf(watchStage) >= 0 && seenStages.indexOf(watchStage) < 0)
            seenStages = seenStages.concat([watchStage])
        if (watchStage === "connect" || watchStage === "ended") showWatch()
    }
    property bool accountForm: false
    function openWeb() {
        trackView = "web"
        if (s.webStatus === "idle" || s.webStatus === "error") player.webSearch()
    }
    Timer { id: popClose; interval: 300; onTriggered: root.popName = "" }

    function showToast(m) { toastText.text = m; toastBox.opacity = 1; toastTimer.restart() }
    function osd(icon, text, frac) {
        osdBox.icon = icon; osdBox.text = text; osdBox.frac = frac === undefined ? -1 : frac
        osdBox.opacity = 1; osdTimer.restart()
    }
    function playToggle() {
        bigplay.icon = s.playing ? "pause" : "play"
        player.togglePlay()
        if (s.hasMedia) bigplay.go()
    }
    function wheelVolume(dy) {
        if (menuOpen || !s.hasMedia) return
        var v = Math.max(0, Math.min(s.volMax, (s.muted ? 0 : s.vol) + (dy > 0 ? 5 : -5)))
        player.setVolume(v)
        osd(v === 0 ? "mute" : "vol", v + "%", v / s.volMax)
    }
    function cycleTrack(kind) {
        var list = kind === "audio" ? (s.audio || []) : (s.subs || [])
        var cur = kind === "audio" ? s.audioId : s.subId
        if (!list.length) return
        var i = 0
        for (var k = 0; k < list.length; k++) if (list[k].id === cur) i = k
        var nxt = list[(i + 1) % list.length].id
        if (kind === "audio") player.setAudioTrack(nxt); else player.setSubTrack(nxt)
    }

    // a lista do menu aberto, montada uma vez a cada mudança (e não uma vez por item)
    readonly property var menuList: menuOpen ? menuItems(cat) : []
    function menuItems(c) {
        var found = (s.found || []).length
        switch (c) {
        case "midia":
            var a = [{ t: "Biblioteca", k: "Ctrl+B", act: "library" }, { t: "Abrir arquivo…", k: "Ctrl+O", act: "open" },
                     { t: "Abrir pasta…", act: "openFolder" }]
            var rec = s.recent || []
            if (rec.length) {
                a.push({ head: "Recentes" })
                for (var i = 0; i < rec.length; i++) a.push({ t: rec[i].name, act: "recent:" + rec[i].path })
            }
            a.push({ head: " " })
            a.push({ t: "Sair", k: "Ctrl+Q", act: "quit" })
            return a
        case "reproducao":
            return [{ t: "Velocidade", v: rateTxt(s.rate), act: "rateNext" },
                    { t: "Voltar 10 s", k: "←", act: "back" }, { t: "Avançar 10 s", k: "→", act: "fwd" },
                    { t: "Vídeo anterior", act: s.prevName ? "prev" : "" }, { t: "Próximo vídeo", act: s.nextName ? "next" : "" },
                    { t: "Repetir", sw: !!s.repeat, act: "repeat" }, { t: "Continuar de onde parou", sw: !!s.resume, act: "resume" }]
        case "audio":
            return [{ t: "Faixa de áudio", v: trackName(s.audio, s.audioId), act: "audioNext" },
                    { t: "Sem som", k: "M", sw: !!s.muted, act: "mute" },
                    { t: "Volume até 200%", sw: s.volMax === 200, act: "boost" },
                    { t: "Equalizador", soon: true }]
        case "video":
            return [{ t: "Proporção", v: s.aspect, act: "aspect" }, { t: "Capturar imagem", k: "S", act: "snap" },
                    { t: "Tela cheia", k: "F", sw: !!s.fullscreen, act: "fs" }, { t: "Sempre no topo", sw: !!s.onTop, act: "ontop" }]
        case "legenda":
            return [{ t: "Faixa de legenda", v: trackName(s.subs, s.subId), act: "subNext" },
                    { t: "Procurar na pasta do vídeo", v: found + (found === 1 ? " encontrada" : " encontradas"), act: "findLocal" },
                    { t: "Adicionar arquivo de legenda…", act: "addSub" },
                    { t: "Atraso da legenda", v: (s.subDelay >= 0 ? "+" : "") + (s.subDelay || 0).toFixed(1).replace(".", ",") + " s" },
                    { t: "Atrasar 0,5 s", k: "H", act: "subLater" }, { t: "Adiantar 0,5 s", k: "G", act: "subEarlier" },
                    { t: "Buscar na internet", v: "OpenSubtitles", act: "findWeb" }]
        case "ferramentas":
            return [{ t: "Informações da mídia", act: "info" }, { t: "Abrir no VLC original", act: "vlc" },
                    { t: "Abrir vídeos sempre no onVLC", v: "Definir ›", act: "defaultApp" },
                    { t: "Efeitos e filtros", soon: true }, { t: "Converter / Salvar", soon: true }]
        case "exibir":
            return [{ t: "Tela cheia", k: "F", sw: !!s.fullscreen, act: "fs" }, { t: "Sempre no topo", sw: !!s.onTop, act: "ontop" },
                    { t: "Playlist", soon: true }]
        case "ajuda":
            return [{ head: "Atalhos" }, { t: "Play / pausa", k: "Espaço ou clique" }, { t: "Voltar / avançar 10 s", k: "← →" },
                    { t: "Velocidade", k: "↑ ↓" }, { t: "Volume", k: "Rodinha" }, { t: "Sem som", k: "M" },
                    { t: "Tela cheia", k: "F ou duplo clique" }, { t: "Capturar imagem", k: "S" },
                    { t: "Atrasar / adiantar legenda", k: "H G" }, { t: "Abrir e fechar o menu", k: "Tab" },
                    { t: "Abrir arquivo", k: "Ctrl+O" }, { head: " " }, { t: "Versão", v: "Deploy 3" }]
        case "tv":
            return [{ head: "Mandar para a TV" },
                    { t: "Mandar o vídeo atual", act: s.hasMedia ? "tvSend" : "" },
                    { t: "Escolher vídeos…", act: "tvAdd" },
                    { t: "Abrir pasta no Windows", act: "tvOpen" },
                    { note: "Mandar põe o vídeo na pasta onVLC sem ocupar espaço a mais e sem tirar o original de onde está (de outro disco, ele é copiado). Legendas com o mesmo nome vão junto. Também dá para jogar arquivos direto na pasta pelo Explorer. Apagar daqui manda para a Lixeira do PC. Dois cliques num vídeo da lista tocam ele aqui." }]
        }
        return []
    }

    function act(a) {
        if (a.indexOf("recent:") === 0) { menuOpen = false; player.openPath(a.substring(7)); return }
        switch (a) {
        case "open": menuOpen = false; player.openFile(); break
        case "library": menuOpen = false; libraryOpen = true; break
        case "openFolder": menuOpen = false; player.openFolder(); break
        case "quit": player.close(); break
        case "rateNext": player.stepRate(s.rate >= 2 ? -5 : 1); break
        case "back": player.skip(-10); ripL.go(); break
        case "fwd": player.skip(10); ripR.go(); break
        case "prev": player.next(-1); break
        case "next": player.next(1); break
        case "repeat": player.toggleRepeat(); break
        case "resume": player.toggleResume(); break
        case "audioNext": cycleTrack("audio"); break
        case "subNext": cycleTrack("subs"); break
        case "mute": player.toggleMute(); break
        case "boost": player.toggleBoost(); break
        case "aspect": player.cycleAspect(); break
        case "snap": player.snapshot(); break
        case "fs": player.toggleFullscreen(); break
        case "ontop": player.toggleOnTop(); break
        case "findLocal":
            menuOpen = false
            if (!s.hasMedia) return
            showBottom = true
            openPop("tracks", ccBtn)
            trackView = "found"
            break
        case "findWeb":
            menuOpen = false
            if (!s.hasMedia) return
            showBottom = true
            openPop("tracks", ccBtn)
            openWeb()
            break
        case "addSub": player.addSub(); break
        case "subLater": player.shiftSubDelay(0.5); break
        case "subEarlier": player.shiftSubDelay(-0.5); break
        case "info": player.mediaInfo(); break
        case "vlc": player.openInVlc(); break
        case "defaultApp": player.setDefaultApp(); break
        case "tvSend": player.tvSendCurrent(); break
        case "tvAdd": player.tvAddFiles(); break
        case "tvOpen": player.tvOpenFolder(); break
        }
    }

    Keys.onPressed: function (e) {
        var ctrl = e.modifiers & Qt.ControlModifier
        // digitando num campo de texto: as teclas são do campo, menos o Esc
        var fi = Window.activeFocusItem
        if (fi && fi !== root && fi.hasOwnProperty("echoMode")) {
            if (e.key === Qt.Key_Escape) { root.forceActiveFocus(); e.accepted = true }
            return
        }
        e.accepted = true
        if (libraryOpen && !menuOpen && popName === "" && libLoader.item && libLoader.item.handleKey(e)) return
        if (e.key === Qt.Key_Tab) { menuOpen = !menuOpen; if (menuOpen) showTop = true; return }
        if (e.key === Qt.Key_Escape) {
            if (menuOpen) menuOpen = false
            else if (popName !== "") popName = ""
            else if (s.fullscreen) player.toggleFullscreen()
            return
        }
        if (ctrl && e.key === Qt.Key_O) { player.openFile(); return }
        if (ctrl && e.key === Qt.Key_B) { libraryOpen = true; return }
        if (ctrl && e.key === Qt.Key_Q) { player.close(); return }
        if (menuOpen) return
        switch (e.key) {
        case Qt.Key_Space: playToggle(); break
        case Qt.Key_Left: act("back"); break
        case Qt.Key_Right: act("fwd"); break
        case Qt.Key_Up: player.stepRate(1); osd("speed", rateTxt(s.rate)); break
        case Qt.Key_Down: player.stepRate(-1); osd("speed", rateTxt(s.rate)); break
        case Qt.Key_M:
            player.toggleMute()
            osd(s.muted ? "mute" : "vol", s.muted ? "Sem som" : s.vol + "%", s.muted ? 0 : s.vol / s.volMax)
            break
        case Qt.Key_F: player.toggleFullscreen(); break
        case Qt.Key_S: player.snapshot(); break
        case Qt.Key_H: player.shiftSubDelay(0.5); break
        case Qt.Key_G: player.shiftSubDelay(-0.5); break
        default: e.accepted = false
        }
    }

    Connections {
        target: player
        function onToast(m) { root.showToast(m) }
        function onLibraryShow(on) { root.libraryOpen = on }
    }

    // ---------- componentes ----------
    component Btn: Item {
        id: b
        property string icon
        property real iconSize: 28
        property bool on: false
        property bool active: true
        property bool hoverBg: false
        property bool grow: true
        property bool badge: false
        property string pop: ""
        property color hoverColor: root.orange
        property color hoverFill: root.orangeSoft
        readonly property bool hot: ma.containsMouse && active
        signal clicked()
        width: 48
        height: 48
        Rectangle {
            anchors.fill: parent
            radius: 10
            color: b.hoverBg && b.hot ? b.hoverFill : "transparent"
            Behavior on color { ColorAnimation { duration: 120 } }
        }
        Icon {
            anchors.centerIn: parent
            name: b.icon
            size: b.iconSize * (b.grow && b.hot ? 1.16 : 1)
            color: !b.active ? Qt.rgba(1, 1, 1, 0.28)
                 : (b.hot || b.on || (b.pop !== "" && root.popName === b.pop)) ? b.hoverColor : root.btnColor
            Behavior on size { NumberAnimation { duration: 120 } }
            Behavior on color { ColorAnimation { duration: 120 } }
        }
        Rectangle {
            visible: b.badge
            width: 10; height: 10; radius: 5
            x: parent.width - 16; y: 8
            color: root.orange
            border.color: "black"; border.width: 2
        }
        MouseArea {
            id: ma
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: b.active ? Qt.PointingHandCursor : Qt.ArrowCursor
            onClicked: if (b.active) b.clicked()
            onContainsMouseChanged: {
                if (b.pop === "") return
                if (containsMouse) root.openPop(b.pop, b); else root.leavePop()
            }
        }
    }

    component Tag: Rectangle {
        property string label: "EM BREVE"
        width: tagText.implicitWidth + 12
        height: 18
        radius: 4
        color: root.orangeSoft
        Text {
            id: tagText
            anchors.centerIn: parent
            text: parent.label
            color: root.orange
            font.family: root.uiFont; font.pixelSize: 10; font.bold: true; font.letterSpacing: 0.6
        }
    }

    component Pop: Rectangle {
        id: pp
        property string name
        property bool center: false
        readonly property bool shown: root.popName === name && root.showBottom
        color: Qt.rgba(24 / 255, 25 / 255, 30 / 255, 0.97)
        radius: 12
        border.color: root.line
        z: 30
        opacity: shown ? 1 : 0
        visible: opacity > 0.01
        Behavior on opacity { NumberAnimation { duration: 140 } }
        function place() {
            if (!root.popAnchor) return
            var p = root.popAnchor.mapToItem(root, 0, 0)
            var v = center ? p.x + root.popAnchor.width / 2 - width / 2 : p.x + root.popAnchor.width - width
            x = Math.max(12, Math.min(root.width - width - 12, v))
            y = p.y - height - 6
        }
        onShownChanged: if (shown) place()
        onHeightChanged: if (shown) place()
        MouseArea { anchors.fill: parent; onWheel: function (w) { w.accepted = true } }
        HoverHandler {
            onHoveredChanged: {
                root.popHovered = hovered
                // passou por cima e saiu: recolhe (a saída logo depois do clique em "Assistir na TV" não conta)
                if (pp.name === "tv") {
                    if (hovered) root.watchArmed = true
                    else if (root.watchArmed) root.watchPinned = false
                }
                if (hovered) popClose.stop(); else root.leavePop()
            }
        }
    }

    component Opt: Rectangle {
        id: op
        property string label
        property bool sel: false
        signal pick()
        width: parent ? parent.width : 100
        height: 34
        radius: 7
        color: oma.containsMouse ? root.orangeSoft : "transparent"
        Icon { x: 8; anchors.verticalCenter: parent.verticalCenter; name: "check"; size: 16; color: root.orange; opacity: op.sel ? 1 : 0 }
        Text {
            x: 32; width: parent.width - 40
            anchors.verticalCenter: parent.verticalCenter
            text: op.label
            elide: Text.ElideRight
            color: oma.containsMouse ? root.orange : (op.sel ? "white" : Qt.rgba(1, 1, 1, 0.65))
            font.family: root.uiFont; font.pixelSize: 14
        }
        MouseArea { id: oma; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: op.pick() }
    }

    component Chip: Rectangle {
        id: ch
        property string icon
        property string label
        property bool soon: false
        signal clicked()
        height: 36
        radius: 8
        readonly property bool hot: cma.containsMouse && !soon
        color: hot ? root.orangeSoft : Qt.rgba(1, 1, 1, 0.04)
        border.color: hot ? root.orangeLine : root.line
        Row {
            anchors.centerIn: parent
            spacing: 8
            Icon { anchors.verticalCenter: parent.verticalCenter; name: ch.icon; size: 16; color: ch.soon ? Qt.rgba(1, 1, 1, 0.35) : (ch.hot ? root.orange : Qt.rgba(1, 1, 1, 0.8)) }
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: ch.label
                color: ch.soon ? Qt.rgba(1, 1, 1, 0.35) : (ch.hot ? root.orange : Qt.rgba(1, 1, 1, 0.8))
                font.family: root.uiFont; font.pixelSize: 13; font.weight: Font.DemiBold
            }
            Tag { visible: ch.soon; anchors.verticalCenter: parent.verticalCenter }
        }
        MouseArea { id: cma; anchors.fill: parent; hoverEnabled: true; cursorShape: ch.soon ? Qt.ArrowCursor : Qt.PointingHandCursor; onClicked: if (!ch.soon) ch.clicked() }
    }

    component Field: Rectangle {
        property alias text: ti.text
        property alias echo: ti.echoMode
        property string placeholder
        width: parent ? parent.width : 200
        height: 34
        radius: 7
        color: Qt.rgba(1, 1, 1, 0.06)
        border.color: ti.activeFocus ? root.orange : root.line
        TextInput {
            id: ti
            anchors.fill: parent
            anchors.leftMargin: 10; anchors.rightMargin: 10
            verticalAlignment: TextInput.AlignVCenter
            clip: true
            selectByMouse: true
            selectionColor: root.orange
            color: "white"
            font.family: root.uiFont; font.pixelSize: 13
        }
        Text {
            visible: !ti.text
            x: 10; anchors.verticalCenter: parent.verticalCenter
            text: parent.placeholder
            color: root.mutedText; font.family: root.uiFont; font.pixelSize: 13
        }
    }

    component Link: Text {
        id: lk
        property string label
        signal clicked()
        text: label
        color: lma.containsMouse ? root.orange : root.mutedText
        font.family: root.uiFont; font.pixelSize: 13; font.weight: Font.DemiBold
        MouseArea { id: lma; anchors.fill: parent; anchors.margins: -6; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: lk.clicked() }
    }

    component Ripple: Rectangle {
        id: rp
        property string label
        property string icon
        width: 120; height: 120; radius: 60
        color: Qt.rgba(0, 0, 0, 0.42)
        opacity: 0
        Text { visible: !rp.icon; anchors.centerIn: parent; text: rp.label; color: "white"; font.family: root.uiFont; font.pixelSize: 16; font.bold: true }
        Icon { visible: !!rp.icon; anchors.centerIn: parent; name: rp.icon; size: 44; color: "white" }
        function go() { anim.restart() }
        ParallelAnimation {
            id: anim
            SequentialAnimation {
                NumberAnimation { target: rp; property: "opacity"; from: 0; to: 1; duration: 110 }
                NumberAnimation { target: rp; property: "opacity"; to: 0; duration: 460 }
            }
            NumberAnimation { target: rp; property: "scale"; from: 0.7; to: 1.15; duration: 570; easing.type: Easing.OutCubic }
        }
    }

    component Edge: MouseArea {
        property int edges
        z: 60
        enabled: !s.maximized && !s.fullscreen
        hoverEnabled: true
        onPressed: player.startResize(edges)
    }

    // ---------- fundo: capta mouse, clique e rodinha em cima do vídeo ----------
    Rectangle { anchors.fill: parent; color: "#01000000" }
    MouseArea {
        property point pressAt
        property bool dragged: false
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: root.idle ? Qt.BlankCursor : Qt.ArrowCursor
        onPressed: function (m) { pressAt = Qt.point(m.x, m.y); dragged = false }
        onPositionChanged: function (m) {
            root.moved(m.y)
            // segurou e arrastou: move a janela, como pela faixa de cima
            if (pressed && !dragged && Math.abs(m.x - pressAt.x) + Math.abs(m.y - pressAt.y) > 6) {
                dragged = true
                player.startMove()
            }
        }
        onClicked: if (dragged) return; else if (root.popName !== "") root.popName = ""; else root.playToggle()
        onDoubleClicked: player.toggleFullscreen()
        onWheel: function (w) { root.wheelVolume(w.angleDelta.y) }
    }
    HoverHandler { onPointChanged: root.moved(point.position.y) }
    DropArea {
        anchors.fill: parent
        onDropped: function (d) { if (d.hasUrls) player.openUrl(d.urls[0].toString()) }
    }

    // tela vazia
    Column {
        visible: !s.hasMedia
        anchors.centerIn: parent
        spacing: 12
        Icon { anchors.horizontalCenter: parent.horizontalCenter; name: "film"; size: 56; color: Qt.rgba(1, 1, 1, 0.3) }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: "Arraste um vídeo para cá"
            color: "white"; font.family: root.uiFont; font.pixelSize: 22; font.weight: Font.DemiBold
        }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: "ou clique em qualquer lugar para abrir um arquivo"
            color: root.mutedText; font.family: root.uiFont; font.pixelSize: 14
        }
    }

    // ---------- biblioteca ----------
    // só existe enquanto está aberta: fechada, não ocupa memória nem fica pedindo a lista
    Loader {
        id: libLoader
        anchors.fill: parent
        active: root.libraryOpen
        source: "Library.qml"
        onLoaded: {
            item.font = root.uiFont
            item.canClose = Qt.binding(function () { return !!root.s.hasMedia })
        }
        onActiveChanged: if (!active) root.forceActiveFocus()
    }
    Connections {
        target: libLoader.item
        function onPlay(path, pos) { player.libraryOpenVideo(path, pos); root.libraryOpen = false }
        function onClose() { root.libraryOpen = false }
    }

    Ripple { id: ripL; label: "−10 s"; x: parent.width * 0.18; anchors.verticalCenter: parent.verticalCenter }
    Ripple { id: ripR; label: "+10 s"; x: parent.width * 0.82 - width; anchors.verticalCenter: parent.verticalCenter }
    Ripple { id: bigplay; icon: "pause"; width: 96; height: 96; radius: 48; anchors.centerIn: parent }

    // ---------- faixa de cima ----------
    Item {
        id: topBar
        anchors { left: parent.left; right: parent.right; top: parent.top }
        height: 100
        opacity: (root.showTop || !s.hasMedia || root.libraryOpen) ? 1 : 0
        visible: opacity > 0.01
        Behavior on opacity { NumberAnimation { duration: 200 } }
        HoverHandler { id: topHover }
        Rectangle {
            anchors.fill: parent
            gradient: Gradient {
                GradientStop { position: 0; color: Qt.rgba(0, 0, 0, 0.6) }
                GradientStop { position: 1; color: "transparent" }
            }
        }
        MouseArea {
            anchors { left: parent.left; right: parent.right; top: parent.top }
            height: 56
            enabled: !root.libraryOpen  // na biblioteca, as abas ficam aqui; arrastar é clicar, segurar e mover
            onPressed: player.startMove()
            onDoubleClicked: player.toggleMaximize()
        }
        Btn {
            x: 10; y: 6; width: 44; height: 44
            icon: "menu"; iconSize: 22; hoverBg: true; grow: false
            on: root.menuOpen
            onClicked: root.menuOpen = !root.menuOpen
        }
        Row {
            anchors.right: parent.right; anchors.rightMargin: 8
            y: 10
            spacing: 2
            Btn { width: 38; height: 32; icon: "min"; iconSize: 16; hoverBg: true; grow: false; onClicked: player.minimize() }
            Btn { width: 38; height: 32; icon: "max"; iconSize: 16; hoverBg: true; grow: false; onClicked: player.toggleMaximize() }
            Btn {
                width: 38; height: 32; icon: "close"; iconSize: 16; hoverBg: true; grow: false
                hoverColor: "white"; hoverFill: "#e5484d"
                onClicked: player.close()
            }
        }
    }

    // ---------- barra de baixo ----------
    Item {
        id: bottomBar
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
        height: 250
        opacity: root.showBottom ? 1 : 0
        visible: opacity > 0.01
        Behavior on opacity { NumberAnimation { duration: 250 } }
        transform: Translate { y: root.showBottom ? 0 : 14; Behavior on y { NumberAnimation { duration: 250 } } }
        HoverHandler { id: bottomHover }

        Rectangle {
            anchors.fill: parent
            gradient: Gradient {
                GradientStop { position: 0; color: "transparent" }
                GradientStop { position: 0.45; color: Qt.rgba(0, 0, 0, 0.55) }
                GradientStop { position: 1; color: Qt.rgba(0, 0, 0, 0.9) }
            }
        }

        Column {
            anchors { left: parent.left; right: parent.right; bottom: parent.bottom; leftMargin: 28; rightMargin: 28; bottomMargin: 14 }

            Text {
                width: parent.width
                text: s.title || ""
                elide: Text.ElideRight
                color: "white"
                font.family: root.uiFont; font.weight: Font.ExtraBold
                font.pixelSize: Math.max(22, Math.min(38, root.width * 0.03))
            }
            Item { width: 1; height: 12 }

            // linha do tempo
            Item {
                width: parent.width
                height: 22
                Item {
                    id: tl
                    anchors { left: parent.left; right: remain.left; rightMargin: 14; top: parent.top; bottom: parent.bottom }
                    readonly property bool hovering: tma.containsMouse || tma.pressed
                    Rectangle {
                        anchors.verticalCenter: parent.verticalCenter
                        width: parent.width
                        height: tl.hovering ? 6 : 4
                        Behavior on height { NumberAnimation { duration: 120 } }
                        radius: 2
                        clip: true
                        color: Qt.rgba(1, 1, 1, 0.22)
                        Rectangle {
                            height: parent.height
                            color: Qt.rgba(1, 1, 1, 0.35)
                            width: tl.hovering && root.hoverP >= 0 ? parent.width * root.hoverP : 0
                        }
                        Rectangle {
                            height: parent.height
                            color: root.orange
                            width: parent.width * (root.dur > 0 ? Math.max(0, Math.min(1, player.pos / root.dur)) : 0)
                        }
                    }
                    Rectangle {
                        width: 16; height: 16; radius: 8
                        anchors.verticalCenter: parent.verticalCenter
                        x: (root.dur > 0 ? player.pos / root.dur : 0) * tl.width - 8
                        color: root.orange
                        border.color: Qt.rgba(1, 0.54, 0.12, 0.3); border.width: 0
                        scale: tl.hovering ? 1 : 0.75
                        Behavior on scale { NumberAnimation { duration: 120 } }
                    }
                    // prévia ao passar o mouse: miniatura (quando já existe) + horário
                    Rectangle {
                        id: tip
                        visible: tl.hovering && root.hoverP >= 0 && root.dur > 0
                        readonly property string thumb: visible ? player.thumbAt(root.hoverP * root.dur) : ""
                        width: Math.max(thumb ? 248 : 0, tipRow.implicitWidth + 16)
                        height: (thumb ? thumbImg.height + 4 : 0) + 28
                        radius: 7
                        color: root.panelBg
                        border.color: root.line
                        y: -height - 8
                        x: Math.max(0, Math.min(tl.width - width, root.hoverP * tl.width - width / 2))
                        Image {
                            id: thumbImg
                            visible: tip.thumb !== ""
                            source: tip.thumb
                            anchors { top: parent.top; topMargin: 4; horizontalCenter: parent.horizontalCenter }
                            width: 240
                            height: status === Image.Ready ? Math.round(240 * implicitHeight / Math.max(1, implicitWidth)) : 135
                            fillMode: Image.PreserveAspectFit
                        }
                        Row {
                            id: tipRow
                            anchors { bottom: parent.bottom; horizontalCenter: parent.horizontalCenter }
                            height: 28
                            spacing: 8
                            Text { anchors.verticalCenter: parent.verticalCenter; text: root.fmt(root.hoverP * root.dur); color: "white"; font.family: root.monoFont; font.pixelSize: 12 }
                        }
                    }
                    MouseArea {
                        id: tma
                        anchors.fill: parent
                        anchors.topMargin: -6; anchors.bottomMargin: -6
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        function at(x) { return Math.max(0, Math.min(1, x / tl.width)) }
                        onPositionChanged: function (m) {
                            root.hoverP = at(m.x)
                            if (pressed) player.seek(root.hoverP * root.dur)
                        }
                        onPressed: function (m) { root.hoverP = at(m.x); player.seek(root.hoverP * root.dur) }
                        onExited: root.hoverP = -1
                    }
                }
                Text {
                    id: remain
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    width: 72
                    horizontalAlignment: Text.AlignRight
                    text: "-" + root.fmt(root.dur - player.pos)
                    color: Qt.rgba(1, 1, 1, 0.85)
                    font.family: root.monoFont; font.pixelSize: 14
                }
            }

            Item { width: 1; height: 8 }

            // botões
            Item {
                width: parent.width
                height: 52
                Row {
                    anchors.left: parent.left; anchors.leftMargin: -10
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 6
                    Btn { icon: s.playing ? "pause" : "play"; iconSize: 34; onClicked: root.playToggle() }
                    Btn { icon: "back10"; onClicked: root.act("back") }
                    Btn { icon: "fwd10"; onClicked: root.act("fwd") }
                    Btn { id: volBtn; icon: s.muted || s.vol === 0 ? "mute" : "vol"; on: !!s.muted; pop: "vol"; onClicked: player.toggleMute() }
                }
                Row {
                    anchors.right: parent.right; anchors.rightMargin: -10
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 6
                    Btn { icon: "prevep"; iconSize: 24; active: !!s.prevName; onClicked: player.next(-1) }
                    Btn { id: nextBtn; icon: "nextep"; active: !!s.nextName; pop: s.nextName ? "next" : ""; onClicked: player.next(1) }
                    Btn { id: ccBtn; icon: "cc"; pop: "tracks"; badge: !!s.badge }
                    Btn { id: speedBtn; icon: "speed"; pop: "speed"; on: Math.abs((s.rate || 1) - 1) > 0.01 }
                    Btn { id: tvBtn; objectName: "tvBtn"; icon: "tv"; pop: "tv"; on: root.watchOn && root.watchStage !== "ended" }
                    Btn { icon: s.fullscreen ? "fsexit" : "fs"; onClicked: player.toggleFullscreen() }
                }
            }
        }
    }

    // ---------- quadros que sobem dos botões ----------
    Pop {
        name: "vol"
        center: true
        width: 56; height: 170
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            y: 10
            text: (s.muted ? 0 : s.vol) + "%"
            color: "white"; font.family: root.monoFont; font.pixelSize: 12
        }
        Item {
            id: vtrack
            anchors.horizontalCenter: parent.horizontalCenter
            y: 34; width: 24; height: 120
            readonly property real frac: s.muted ? 0 : (s.vol || 0) / (s.volMax || 100)
            Rectangle {
                anchors.horizontalCenter: parent.horizontalCenter
                width: 4; height: parent.height; radius: 2
                color: Qt.rgba(1, 1, 1, 0.2)
                Rectangle { anchors.bottom: parent.bottom; width: parent.width; radius: 2; height: parent.height * vtrack.frac; color: root.orange }
            }
            Rectangle {
                width: 14; height: 14; radius: 7
                anchors.horizontalCenter: parent.horizontalCenter
                y: (1 - vtrack.frac) * parent.height - 7
                color: "white"
            }
            MouseArea {
                anchors.fill: parent
                anchors.margins: -8
                cursorShape: Qt.PointingHandCursor
                function setFrom(y) { player.setVolume(Math.round(Math.max(0, Math.min(1, 1 - (y - 8) / vtrack.height)) * s.volMax)) }
                onPressed: function (m) { setFrom(m.y) }
                onPositionChanged: function (m) { if (pressed) setFrom(m.y) }
            }
        }
    }

    Pop {
        name: "next"
        width: 300; height: nextCol.implicitHeight + 28
        Column {
            id: nextCol
            x: 16; y: 14; width: parent.width - 32
            spacing: 4
            Text { text: "A SEGUIR"; color: root.orange; font.family: root.uiFont; font.pixelSize: 11; font.bold: true; font.letterSpacing: 1 }
            Text { width: parent.width; text: s.nextName || ""; wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight; color: "white"; font.family: root.uiFont; font.pixelSize: 16; font.weight: Font.DemiBold }
            Text { text: "Mesma pasta · clique para tocar"; color: root.mutedText; font.family: root.uiFont; font.pixelSize: 13 }
        }
    }

    Pop {
        id: trkPop
        name: "tracks"
        width: Math.min(440, root.width - 24)
        // nunca maior que o espaço acima do botão, dentro da janela; o que não couber rola
        readonly property real contentH: (root.trackView === "main" ? trkMain.implicitHeight : root.trackView === "found" ? trkFound.implicitHeight : trkWeb.implicitHeight) + 26
        // pela posição final da barra de baixo (ela sobe 14 px numa animação ao aparecer), com folga no topo
        readonly property real maxH: Math.max(160, (root.popAnchor ? bottomBar.y + root.popAnchor.mapToItem(bottomBar, 0, 0).y : root.height - 120) - 32)
        height: Math.min(contentH, maxH)
        Connections { target: root; function onTrackViewChanged() { trkFlick.contentY = 0 } }
        Flickable {
            id: trkFlick
            anchors.fill: parent
            clip: true
            contentWidth: width
            contentHeight: trkPop.contentH
            boundsBehavior: Flickable.StopAtBounds
            flickableDirection: Flickable.VerticalFlick
        Column {
            id: trkMain
            visible: root.trackView === "main"
            x: 8; y: 14; width: parent.width - 16
            Row {
                width: parent.width
                Column {
                    width: parent.width / 2 - 1
                    leftPadding: 4; rightPadding: 4
                    spacing: 2
                    Text { leftPadding: 8; bottomPadding: 6; text: "Áudio"; color: "white"; font.family: root.uiFont; font.pixelSize: 13; font.bold: true }
                    Repeater {
                        model: (s.audio || []).length
                        delegate: Opt {
                            required property int index
                            width: parent.width - 8
                            label: s.audio[index] ? s.audio[index].name : ""
                            sel: !!s.audio[index] && s.audio[index].id === s.audioId
                            onPick: player.setAudioTrack(s.audio[index].id)
                        }
                    }
                    Text { visible: !(s.audio || []).length; leftPadding: 8; text: "Nenhuma faixa"; color: root.mutedText; font.family: root.uiFont; font.pixelSize: 13 }
                }
                Rectangle { width: 1; height: parent.height; color: root.line }
                Column {
                    width: parent.width / 2
                    leftPadding: 4; rightPadding: 4
                    spacing: 2
                    Text { leftPadding: 8; bottomPadding: 6; text: "Legendas"; color: "white"; font.family: root.uiFont; font.pixelSize: 13; font.bold: true }
                    Repeater {
                        model: (s.subs || []).length
                        delegate: Opt {
                            required property int index
                            width: parent.width - 8
                            label: s.subs[index] ? root.subLabel(s.subs[index]) : ""
                            sel: !!s.subs[index] && s.subs[index].id === s.subId
                            onPick: player.setSubTrack(s.subs[index].id)
                        }
                    }
                    Text { visible: !(s.subs || []).length; leftPadding: 8; text: "Nenhuma legenda"; color: root.mutedText; font.family: root.uiFont; font.pixelSize: 13 }
                }
            }
            Item { width: 1; height: 10 }
            Rectangle { width: parent.width; height: 1; color: root.line }
            Item { width: 1; height: 12 }
            Row {
                x: 6
                width: parent.width - 12
                spacing: 8
                Chip {
                    width: (parent.width - 8) / 2
                    icon: "folder"
                    label: "Procurar na pasta" + ((s.found || []).length ? " (" + s.found.length + ")" : "")
                    onClicked: root.trackView = "found"
                }
                Chip { width: (parent.width - 8) / 2; icon: "search"; label: "Buscar na internet"; onClicked: root.openWeb() }
            }
        }
        Column {
            id: trkFound
            visible: root.trackView === "found"
            x: 14; y: 14; width: parent.width - 28
            spacing: 2
            Text { bottomPadding: 6; text: "Legendas na pasta do vídeo"; color: "white"; font.family: root.uiFont; font.pixelSize: 13; font.bold: true }
            Repeater {
                model: (s.found || []).length
                delegate: Rectangle {
                    required property int index
                    readonly property var f: s.found[index] || ({})
                    width: trkFound.width; height: 38; radius: 8
                    color: rma.containsMouse ? root.orangeSoft : "transparent"
                    Text {
                        x: 10; width: parent.width - 130
                        anchors.verticalCenter: parent.verticalCenter
                        text: (f.name || "") + "  <font color='#9a9ca5'>" + (f.where || "") + "</font>"
                        textFormat: Text.StyledText
                        elide: Text.ElideRight
                        color: rma.containsMouse ? root.orange : Qt.rgba(1, 1, 1, 0.85)
                        font.family: root.uiFont; font.pixelSize: 13
                    }
                    Text {
                        anchors.right: parent.right; anchors.rightMargin: 10
                        anchors.verticalCenter: parent.verticalCenter
                        text: f.score ? f.score + "% parecido" : ""  // da biblioteca vem o idioma no nome, sem nota
                        color: f.score >= 80 ? root.green : root.mutedText
                        font.family: root.monoFont; font.pixelSize: 12
                    }
                    MouseArea {
                        id: rma
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: { player.loadSub(f.path); root.trackView = "main" }
                    }
                }
            }
            Text {
                visible: !(s.found || []).length
                width: parent.width
                wrapMode: Text.Wrap
                text: "Nenhuma legenda na pasta do vídeo nem nas subpastas Subs ou Legendas."
                color: root.mutedText; font.family: root.uiFont; font.pixelSize: 13
            }
            Item { width: 1; height: 6 }
            Row {
                spacing: 18
                Text {
                    text: "← Voltar"
                    color: bma.containsMouse ? root.orange : root.mutedText
                    font.family: root.uiFont; font.pixelSize: 13; font.weight: Font.DemiBold
                    MouseArea { id: bma; anchors.fill: parent; anchors.margins: -6; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: root.trackView = "main" }
                }
                Text {
                    text: "Escolher arquivo…"
                    color: ama.containsMouse ? root.orange : root.mutedText
                    font.family: root.uiFont; font.pixelSize: 13; font.weight: Font.DemiBold
                    MouseArea { id: ama; anchors.fill: parent; anchors.margins: -6; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: player.addSub() }
                }
            }
        }
        Column {
            id: trkWeb
            visible: root.trackView === "web"
            x: 14; y: 14; width: parent.width - 28
            spacing: 4
            readonly property bool showForm: s.webStatus === "setup" || root.accountForm
            Item {
                width: parent.width; height: 26
                Text { anchors.verticalCenter: parent.verticalCenter; text: "OpenSubtitles"; color: "white"; font.family: root.uiFont; font.pixelSize: 13; font.bold: true }
                Link {
                    anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                    visible: !!s.osConfigured && !trkWeb.showForm
                    label: "Conta"
                    onClicked: root.accountForm = true
                }
            }

            // conta do OpenSubtitles
            Column {
                visible: trkWeb.showForm
                width: parent.width
                spacing: 8
                Text {
                    width: parent.width
                    wrapMode: Text.Wrap
                    text: "Crie uma conta grátis em opensubtitles.com e gere uma chave de API em opensubtitles.com/consumers. A chave e a senha ficam guardadas no Gerenciador de Credenciais do Windows."
                    color: root.mutedText; font.family: root.uiFont; font.pixelSize: 12; lineHeight: 1.3
                }
                Field { id: fKey; placeholder: s.osConfigured ? "Chave de API (já salva)" : "Chave de API" }
                Field { id: fUser; placeholder: "Usuário"; Component.onCompleted: text = s.osUser || "" }
                Field { id: fPass; placeholder: s.osConfigured ? "Senha (já salva)" : "Senha"; echo: TextInput.Password }
                Chip {
                    width: parent.width
                    icon: "search"
                    label: "Salvar e buscar"
                    onClicked: {
                        player.saveAccount(fKey.text, fUser.text, fPass.text)
                        fKey.text = ""; fPass.text = ""
                        root.accountForm = false
                        root.forceActiveFocus()
                    }
                }
            }

            Text {
                visible: !trkWeb.showForm && (s.webStatus === "searching" || s.webStatus === "downloading")
                topPadding: 6; bottomPadding: 6
                text: s.webStatus === "searching" ? "Procurando legendas…" : "Baixando legenda…"
                color: root.mutedText; font.family: root.uiFont; font.pixelSize: 13
            }
            Text {
                visible: !trkWeb.showForm && !!s.webMsg && (s.webStatus === "error" || s.webStatus === "done")
                width: parent.width
                wrapMode: Text.Wrap
                topPadding: 6; bottomPadding: 6
                text: s.webMsg || ""
                color: s.webStatus === "error" ? "#ff8a80" : root.mutedText
                font.family: root.uiFont; font.pixelSize: 13
            }
            Repeater {
                model: trkWeb.showForm ? 0 : (s.web || []).length
                delegate: Rectangle {
                    required property int index
                    readonly property var w: s.web[index] || ({})
                    width: trkWeb.width; height: 40; radius: 8
                    color: wma.containsMouse ? root.orangeSoft : "transparent"
                    Text {
                        x: 10; width: parent.width - 170
                        anchors.verticalCenter: parent.verticalCenter
                        text: w.name || ""
                        elide: Text.ElideRight
                        color: wma.containsMouse ? root.orange : Qt.rgba(1, 1, 1, 0.85)
                        font.family: root.uiFont; font.pixelSize: 13
                    }
                    Row {
                        anchors.right: parent.right; anchors.rightMargin: 10
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 8
                        Text {
                            anchors.verticalCenter: parent.verticalCenter
                            text: w.exact ? "encaixe exato" : (w.downloads || 0) + " downloads"
                            color: w.exact ? root.green : root.mutedText
                            font.family: root.monoFont; font.pixelSize: 11
                        }
                        Tag { anchors.verticalCenter: parent.verticalCenter; label: (w.lang || "?").toUpperCase() }
                    }
                    MouseArea {
                        id: wma
                        anchors.fill: parent
                        hoverEnabled: true
                        enabled: s.webStatus !== "downloading"
                        cursorShape: Qt.PointingHandCursor
                        onClicked: player.webDownload(w.fileId, w.lang || "")
                    }
                }
            }
            Item { width: 1; height: 6 }
            Row {
                spacing: 18
                Link { label: "← Voltar"; onClicked: { root.accountForm = false; root.trackView = "main" } }
                Link {
                    visible: !!s.osConfigured && !trkWeb.showForm && s.webStatus !== "searching"
                    label: "Buscar de novo"
                    onClicked: player.webSearch()
                }
            }
        }
        }
        // barrinha que mostra onde a lista está (só quando não cabe tudo)
        Rectangle {
            visible: trkFlick.contentHeight > trkFlick.height + 1
            x: parent.width - 6; width: 3; radius: 1.5
            y: 8 + (parent.height - 16 - height) * (trkFlick.contentY / Math.max(1, trkFlick.contentHeight - trkFlick.height))
            height: Math.max(28, (parent.height - 16) * trkFlick.height / Math.max(1, trkFlick.contentHeight))
            color: Qt.rgba(1, 1, 1, 0.35)
        }
    }

    Pop {
        name: "speed"
        width: 340; height: 100
        readonly property var rates: [0.5, 0.75, 1, 1.25, 1.5, 2]
        Text { x: 20; y: 14; text: "Velocidade de reprodução"; color: "white"; font.family: root.uiFont; font.pixelSize: 13; font.bold: true }
        Item {
            id: speedRow
            x: 20; y: 46; width: parent.width - 40; height: 44
            Rectangle { x: 8; y: 7; width: parent.width - 16; height: 2; color: Qt.rgba(1, 1, 1, 0.2) }
            Repeater {
                model: 6
                delegate: Item {
                    required property int index
                    readonly property real r: [0.5, 0.75, 1, 1.25, 1.5, 2][index]
                    readonly property bool sel: Math.abs((s.rate || 1) - r) < 0.01
                    width: 40; height: 44
                    x: index * (speedRow.width - 16) / 5 + 8 - 20
                    Rectangle {
                        anchors.horizontalCenter: parent.horizontalCenter
                        width: 16; height: 16; radius: 8
                        color: parent.sel ? root.orange : "#3a3c44"
                        border.color: Qt.rgba(24 / 255, 25 / 255, 30 / 255, 1); border.width: 2
                    }
                    Text {
                        anchors.horizontalCenter: parent.horizontalCenter
                        y: 24
                        text: root.rateTxt(parent.r)
                        color: sma.containsMouse ? root.orange : (parent.sel ? "white" : Qt.rgba(1, 1, 1, 0.6))
                        font.family: root.monoFont; font.pixelSize: 12
                    }
                    MouseArea { id: sma; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: player.setRate(parent.r) }
                }
            }
        }
    }

    Pop {
        name: "tv"
        width: 330; height: (root.watchOn ? watchCol : tvCol).implicitHeight + 28

        // assistindo na TV: as etapas até o app onVLC da TV abrir, e depois o botão de voltar para cá
        Column {
            id: watchCol
            readonly property var w: s.watch || ({})
            readonly property bool playing: w.stage === "playing"
            readonly property bool ended: w.stage === "ended"
            visible: root.watchOn
            x: 14; y: 14; width: parent.width - 28
            spacing: 8
            Text {
                text: watchCol.playing ? "Tocando na TV · " + (watchCol.w.tv || "TV") : "Assistir na TV"
                color: "white"; font.family: root.uiFont; font.pixelSize: 13; font.bold: true
            }
            // etapas da conexão, uma embaixo da outra, com ✓ nas que já passaram
            Repeater {
                model: watchCol.playing || watchCol.ended || watchCol.w.stage === "stopping" ? [] : root.seenStages
                delegate: Item {
                    required property var modelData
                    required property int index
                    readonly property bool current: index === root.seenStages.length - 1
                    width: watchCol.width; height: 20
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: ({ connect: "Conectando na TV", library: "Colocando na biblioteca", wake: "Acordando a TV",
                                 tv: "Ligando a TV", open: "Abrindo no onVLC da TV" })[parent.modelData]
                              + (parent.modelData === "library" && parent.current && watchCol.w.p ? " " + watchCol.w.p + "%" : "")
                              + (parent.current ? "…" : "")
                        color: parent.current ? "white" : root.mutedText
                        font.family: root.uiFont; font.pixelSize: 13
                    }
                    Text {
                        anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                        text: parent.current ? "" : "✓"
                        color: root.green; font.pixelSize: 13
                    }
                }
            }
            Text {
                visible: watchCol.w.stage === "stopping"
                text: "Parando na TV…"
                color: root.mutedText; font.family: root.uiFont; font.pixelSize: 13
            }
            // aviso: TV desligada, outra janela assumiu, erro
            Text {
                visible: watchCol.ended
                width: parent.width
                wrapMode: Text.Wrap
                text: watchCol.w.msg || ""
                color: "white"; font.family: root.uiFont; font.pixelSize: 14
            }
            Chip {
                visible: watchCol.ended && watchCol.w.pos !== undefined && watchCol.w.pos !== null
                width: parent.width
                icon: "playc"
                label: "Continuar aqui no PC"
                onClicked: { player.tvResumeHere(); root.popName = "" }
            }
            Chip {
                visible: watchCol.ended
                width: parent.width
                icon: "tv"
                label: "Ligar de novo e continuar na TV"
                onClicked: player.tvRetry()
            }
            Link { visible: watchCol.ended; label: "Fechar"; onClicked: { player.tvResumeHere(); root.popName = "" } }
            Text {
                visible: watchCol.playing
                width: parent.width
                elide: Text.ElideRight
                text: watchCol.w.title || ""
                color: "white"; font.family: root.uiFont; font.pixelSize: 14
            }
            Text {
                visible: watchCol.playing
                width: parent.width
                wrapMode: Text.Wrap
                text: "Use o controle da TV para pausar e pular. A posição fica salva na biblioteca."
                color: root.mutedText; font.family: root.uiFont; font.pixelSize: 12
            }
            Link { visible: watchCol.playing; label: "Parar na TV e continuar aqui"; onClicked: player.tvWatchStop() }
        }

        Column {
            id: tvCol
            visible: !root.watchOn
            x: 14; y: 14; width: parent.width - 28
            spacing: 8
            Text { text: "Enviar para TV"; color: "white"; font.family: root.uiFont; font.pixelSize: 13; font.bold: true }
            Repeater {
                // [ícone, título, explicação, ação]; sem ação = ainda não existe
                model: [["tv", "Assistir na TV", "Abre no onVLC da TV, deste ponto.", "watch"],
                        ["folder", "Pasta onVLC", "Deixa na biblioteca da TV para ver quando quiser.", "folder"]]
                delegate: Rectangle {
                    id: dst
                    required property var modelData
                    readonly property bool ready: modelData[3] !== ""
                    readonly property bool hot: dma.containsMouse && ready
                    width: tvCol.width; height: 58; radius: 10
                    color: hot ? root.orangeSoft : Qt.rgba(1, 1, 1, 0.03)
                    border.color: hot ? root.orangeLine : root.line
                    Icon { x: 12; anchors.verticalCenter: parent.verticalCenter; name: dst.modelData[0]; size: 22; color: dst.ready ? root.orange : Qt.rgba(1, 0.54, 0.12, 0.5) }
                    Column {
                        x: 46; anchors.verticalCenter: parent.verticalCenter
                        width: parent.width - (dst.ready ? 58 : 120)
                        spacing: 2
                        Text { text: dst.modelData[1]; color: dst.ready ? "white" : Qt.rgba(1, 1, 1, 0.6); font.family: root.uiFont; font.pixelSize: 14; font.bold: true }
                        Text { width: parent.width; text: dst.modelData[2]; elide: Text.ElideRight; color: root.mutedText; font.family: root.uiFont; font.pixelSize: 12 }
                    }
                    Tag { visible: !dst.ready; anchors.right: parent.right; anchors.rightMargin: 10; anchors.verticalCenter: parent.verticalCenter }
                    MouseArea {
                        id: dma
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: dst.ready ? Qt.PointingHandCursor : Qt.ArrowCursor
                        onClicked: {
                            if (dst.modelData[3] === "watch") player.tvWatch()
                            else if (dst.ready) { player.tvSendCurrent(); root.popName = "" }
                        }
                    }
                }
            }
            Link { label: "Abrir pasta onVLC"; onClicked: { root.popName = ""; root.cat = "tv"; root.menuOpen = true; root.showTop = true } }
        }
    }

    // ---------- menu lateral ----------
    Rectangle {
        anchors.fill: parent
        color: Qt.rgba(0, 0, 0, 0.35)
        opacity: root.menuOpen ? 1 : 0
        visible: opacity > 0.01
        Behavior on opacity { NumberAnimation { duration: 200 } }
        z: 40
        MouseArea { anchors.fill: parent; onClicked: root.menuOpen = false; onWheel: function (w) { w.accepted = true } }
    }
    Rectangle {
        id: panel
        z: 41
        x: 12 + (root.menuOpen ? 0 : -16)
        y: 62
        width: Math.min(600, root.width - 24)
        height: root.height - 62 - 24
        radius: 14
        color: root.panelBg
        border.color: root.line
        opacity: root.menuOpen ? 1 : 0
        visible: opacity > 0.01
        Behavior on opacity { NumberAnimation { duration: 200 } }
        Behavior on x { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }
        MouseArea { anchors.fill: parent }

        // coluna 1: categorias
        Rectangle {
            id: catCol
            width: root.width < 560 ? 64 : 200
            height: parent.height
            radius: 14
            color: root.panelDark
            Rectangle { anchors.right: parent.right; width: 14; height: parent.height; color: root.panelDark }
            Column {
                x: 8; y: 10; width: parent.width - 16
                spacing: 2
                Repeater {
                    model: root.cats.length + 1
                    delegate: Column {
                        required property int index
                        readonly property bool isTv: index === root.cats.length
                        readonly property var c: isTv ? { id: "tv", nome: "Pasta onVLC", ic: "tv" } : root.cats[index]
                        width: catCol.width - 16
                        Rectangle { visible: parent.isTv; width: parent.width - 12; x: 6; height: 1; color: root.line }
                        Item { visible: parent.isTv; width: 1; height: 8 }
                        Rectangle {
                            width: parent.width; height: 38; radius: 8
                            readonly property bool active: root.cat === parent.c.id
                            color: active ? root.panel2 : (cma2.containsMouse ? root.orangeSoft : "transparent")
                            Icon {
                                x: catCol.width < 100 ? (parent.width - 18) / 2 : 10
                                anchors.verticalCenter: parent.verticalCenter
                                name: parent.parent.c.ic; size: 18
                                color: parent.active ? root.orange : (cma2.containsMouse ? root.orange : root.btnColor)
                            }
                            Text {
                                visible: catCol.width >= 100
                                x: 38; anchors.verticalCenter: parent.verticalCenter
                                text: parent.parent.c.nome
                                color: parent.active ? "white" : (cma2.containsMouse ? root.orange : root.btnColor)
                                font.family: root.uiFont; font.pixelSize: 14; font.weight: Font.Medium
                            }
                            MouseArea { id: cma2; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: root.cat = parent.parent.c.id }
                        }
                    }
                }
            }
        }

        // coluna 2: opções da categoria
        Flickable {
            x: catCol.width
            width: parent.width - catCol.width
            height: parent.height
            clip: true
            contentHeight: subCol.implicitHeight + 36
            boundsBehavior: Flickable.StopAtBounds
            Column {
                id: subCol
                x: 14; y: 18; width: parent.width - 28
                spacing: 2
                Text {
                    leftPadding: 8
                    text: root.catInfo(root.cat).nome.toUpperCase()
                    color: root.mutedText; font.family: root.uiFont; font.pixelSize: 12; font.bold: true; font.letterSpacing: 1.2
                }
                Text {
                    visible: !!root.catInfo(root.cat).desc
                    leftPadding: 8; bottomPadding: 10; topPadding: 2
                    text: root.catInfo(root.cat).desc
                    color: root.mutedText; font.family: root.uiFont; font.pixelSize: 13
                }

                // pasta onVLC: estado da biblioteca da TV e os vídeos (com o nome que a TV mostra)
                Column {
                    id: tvBox
                    readonly property var tv: s.tv || ({})
                    readonly property var files: tv.files || []
                    visible: root.cat === "tv"
                    width: parent.width
                    spacing: 2
                    Rectangle {
                        width: parent.width
                        height: shareCol.implicitHeight + 24
                        radius: 10
                        color: root.panelDark
                        border.color: root.line
                        Column {
                            id: shareCol
                            x: 12; y: 12; width: parent.width - 24
                            spacing: 6
                            Row {
                                spacing: 8
                                Rectangle {
                                    anchors.verticalCenter: parent.verticalCenter
                                    width: 8; height: 8; radius: 4
                                    color: tvBox.tv.running ? root.green : root.mutedText
                                }
                                Text {
                                    text: tvBox.tv.running ? "Biblioteca da TV ligada" + (tvBox.tv.summary ? " · " + tvBox.tv.summary : "")
                                        : "Biblioteca da TV desligada: ela liga junto com o onVLC"
                                    color: "white"; font.family: root.uiFont; font.pixelSize: 14; font.bold: true
                                }
                            }
                            Text { text: tvBox.tv.path || ""; color: root.mutedText; font.family: root.monoFont; font.pixelSize: 12 }
                            Text {
                                width: parent.width
                                wrapMode: Text.Wrap
                                text: (tvBox.tv.totalTxt || "0 MB") + " na pasta. A TV toca direto daqui; o PC precisa estar ligado."
                                color: root.mutedText; font.family: root.uiFont; font.pixelSize: 13
                            }
                        }
                    }
                    Item { width: 1; height: 8 }
                    Text {
                        visible: !tvBox.files.length
                        leftPadding: 8; topPadding: 4; bottomPadding: 8
                        text: "Nenhum vídeo na pasta onVLC."
                        color: root.mutedText; font.family: root.uiFont; font.pixelSize: 13
                    }
                    Repeater {
                        model: tvBox.files.length
                        delegate: Rectangle {
                            id: tf
                            required property int index
                            readonly property var f: tvBox.files[index] || ({})
                            // copy = ainda sendo copiado de outro disco
                            readonly property bool job: f.state === "copy"
                            width: tvBox.width; height: 40; radius: 8
                            color: fma.containsMouse ? root.panel2 : "transparent"
                            MouseArea {
                                id: fma
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: tf.job ? Qt.ArrowCursor : Qt.PointingHandCursor
                                onDoubleClicked: if (!tf.job) { root.menuOpen = false; player.tvPlay(tf.f.path) }
                            }
                            Icon { x: 10; anchors.verticalCenter: parent.verticalCenter; name: "file"; size: 18; color: root.orange }
                            Text {
                                x: 38; width: parent.width - 38 - stateRow.width - 12
                                anchors.verticalCenter: parent.verticalCenter
                                text: tf.f.title || tf.f.name || ""
                                elide: Text.ElideRight
                                color: Qt.rgba(1, 1, 1, 0.85); font.family: root.uiFont; font.pixelSize: 14
                            }
                            Row {
                                id: stateRow
                                anchors.right: parent.right; anchors.rightMargin: 4
                                anchors.verticalCenter: parent.verticalCenter
                                spacing: 10
                                Text { anchors.verticalCenter: parent.verticalCenter; text: [tf.f.quality, tf.f.size].filter(function (x) { return !!x }).join(" · "); color: root.mutedText; font.family: root.monoFont; font.pixelSize: 12 }
                                Text {
                                    anchors.verticalCenter: parent.verticalCenter
                                    readonly property string st: tf.f.state || ""
                                    text: st === "ok" ? "Na TV ✓" : st === "copy" ? "Copiando " + tf.f.p + "%" : st === "read" ? "Lendo…"
                                        : st === "bad" ? "Com problema" : "Biblioteca desligada"
                                    color: st === "ok" ? root.green : st === "bad" ? "#ff8a80" : (st === "pc" ? root.mutedText : root.orange)
                                    font.family: root.uiFont; font.pixelSize: 12; font.bold: true
                                }
                                Btn {
                                    visible: !tf.job
                                    width: 30; height: 30
                                    icon: "trash"; iconSize: 16; grow: false; hoverBg: true
                                    onClicked: player.tvDelete(tf.f.path)
                                }
                            }
                        }
                    }
                    Item { width: 1; height: 10 }
                    Timer {
                        // enquanto a página está aberta, atualiza o status a cada 3 s
                        interval: 3000; repeat: true; triggeredOnStart: true
                        running: root.menuOpen && root.cat === "tv"
                        onTriggered: player.tvRefresh()
                    }
                }

                Repeater {
                    model: root.menuList.length
                    delegate: Rectangle {
                        id: mi
                        required property int index
                        readonly property var it: root.menuList[index] || ({})
                        readonly property bool clickable: !!it.act && !it.soon
                        width: subCol.width
                        height: it.note ? noteText.implicitHeight + 16 : (it.head ? 30 : 40)
                        radius: 8
                        color: mma.containsMouse && clickable ? root.panel2 : "transparent"
                        Text {
                            id: noteText
                            visible: !!mi.it.note
                            x: 10; y: 4; width: parent.width - 20
                            wrapMode: Text.Wrap
                            text: mi.it.note || ""
                            color: Qt.rgba(1, 1, 1, 0.75); font.family: root.uiFont; font.pixelSize: 13; lineHeight: 1.3
                        }
                        Text {
                            visible: !!mi.it.head
                            x: 10; anchors.bottom: parent.bottom; anchors.bottomMargin: 6
                            text: (mi.it.head || "").toUpperCase()
                            color: root.mutedText; font.family: root.uiFont; font.pixelSize: 11; font.bold: true; font.letterSpacing: 1
                        }
                        Text {
                            visible: !!mi.it.t
                            x: 10; width: parent.width - 170
                            anchors.verticalCenter: parent.verticalCenter
                            text: mi.it.t || ""
                            elide: Text.ElideRight
                            color: mi.it.soon || (!mi.it.act && mi.it.k === undefined && mi.it.v === undefined) ? Qt.rgba(1, 1, 1, 0.4)
                                 : (mma.containsMouse && mi.clickable ? root.orange : Qt.rgba(1, 1, 1, 0.82))
                            font.family: root.uiFont; font.pixelSize: 14; font.weight: Font.Medium
                        }
                        Row {
                            anchors.right: parent.right; anchors.rightMargin: 10
                            anchors.verticalCenter: parent.verticalCenter
                            spacing: 8
                            Text { visible: mi.it.k !== undefined && !mi.it.soon; text: mi.it.k || ""; color: root.mutedText; font.family: root.monoFont; font.pixelSize: 12 }
                            Text { visible: mi.it.v !== undefined && !mi.it.soon; text: mi.it.v || ""; color: root.mutedText; font.family: root.uiFont; font.pixelSize: 13 }
                            Rectangle {
                                visible: mi.it.sw !== undefined
                                width: 34; height: 20; radius: 10
                                color: mi.it.sw ? root.orange : "#44464f"
                                Behavior on color { ColorAnimation { duration: 120 } }
                                Rectangle {
                                    y: 3; width: 14; height: 14; radius: 7; color: "white"
                                    x: mi.it.sw ? 17 : 3
                                    Behavior on x { NumberAnimation { duration: 120 } }
                                }
                            }
                            Tag { visible: !!mi.it.soon }
                        }
                        MouseArea {
                            id: mma
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: mi.clickable ? Qt.PointingHandCursor : Qt.ArrowCursor
                            onClicked: if (mi.clickable) root.act(mi.it.act)
                        }
                    }
                }
            }
        }
    }

    // ---------- bordas para redimensionar a janela ----------
    Edge { edges: 1; anchors { left: parent.left; right: parent.right; top: parent.top; leftMargin: 10; rightMargin: 10 } height: 5; cursorShape: Qt.SizeVerCursor }
    Edge { edges: 8; anchors { left: parent.left; right: parent.right; bottom: parent.bottom; leftMargin: 10; rightMargin: 10 } height: 5; cursorShape: Qt.SizeVerCursor }
    Edge { edges: 2; anchors { left: parent.left; top: parent.top; bottom: parent.bottom; topMargin: 10; bottomMargin: 10 } width: 5; cursorShape: Qt.SizeHorCursor }
    Edge { edges: 4; anchors { right: parent.right; top: parent.top; bottom: parent.bottom; topMargin: 10; bottomMargin: 10 } width: 5; cursorShape: Qt.SizeHorCursor }
    Edge { edges: 3; anchors { left: parent.left; top: parent.top } width: 10; height: 10; cursorShape: Qt.SizeFDiagCursor }
    Edge { edges: 5; anchors { right: parent.right; top: parent.top } width: 10; height: 10; cursorShape: Qt.SizeBDiagCursor }
    Edge { edges: 10; anchors { left: parent.left; bottom: parent.bottom } width: 10; height: 10; cursorShape: Qt.SizeBDiagCursor }
    Edge { edges: 12; anchors { right: parent.right; bottom: parent.bottom } width: 10; height: 10; cursorShape: Qt.SizeFDiagCursor }

    // ---------- avisos ----------
    Rectangle {
        id: osdBox
        property string icon: "vol"
        property string text: ""
        property real frac: -1
        z: 50
        anchors.centerIn: parent
        width: 160; height: osdCol.implicitHeight + 32
        radius: 14
        color: Qt.rgba(20 / 255, 21 / 255, 26 / 255, 0.85)
        opacity: 0
        Behavior on opacity { NumberAnimation { duration: 180 } }
        Column {
            id: osdCol
            anchors.centerIn: parent
            spacing: 10
            Icon { anchors.horizontalCenter: parent.horizontalCenter; name: osdBox.icon; size: 30; color: root.orange }
            Text { anchors.horizontalCenter: parent.horizontalCenter; text: osdBox.text; color: "white"; font.family: root.monoFont; font.pixelSize: 20; font.bold: true }
            Rectangle {
                visible: osdBox.frac >= 0
                width: 120; height: 4; radius: 2
                color: Qt.rgba(1, 1, 1, 0.2)
                Rectangle { width: parent.width * Math.max(0, osdBox.frac); height: parent.height; radius: 2; color: root.orange }
            }
        }
        Timer { id: osdTimer; interval: 1000; onTriggered: osdBox.opacity = 0 }
    }

    Rectangle {
        id: toastBox
        z: 51
        anchors.horizontalCenter: parent.horizontalCenter
        y: 70
        width: Math.min(toastText.implicitWidth + 28, root.width - 32)
        height: 38
        radius: 10
        color: Qt.rgba(30 / 255, 31 / 255, 36 / 255, 0.94)
        border.color: root.line
        opacity: 0
        Behavior on opacity { NumberAnimation { duration: 250 } }
        Text {
            id: toastText
            anchors.centerIn: parent
            width: Math.min(implicitWidth, root.width - 60)
            elide: Text.ElideRight
            color: Qt.rgba(1, 1, 1, 0.9); font.family: root.uiFont; font.pixelSize: 13
        }
        Timer { id: toastTimer; interval: 2200; onTriggered: toastBox.opacity = 0 }
    }

    Timer {
        interval: 5000
        running: true
        onTriggered: { root.showTop = false; root.showBottom = false }
    }

    Component.onCompleted: {
        root.forceActiveFocus()
        showTop = true
    }
    onSChanged: if (s.hasMedia && !initDone) { initDone = true; showBottom = true; idleTimer.restart() }
    property bool initDone: false
}
