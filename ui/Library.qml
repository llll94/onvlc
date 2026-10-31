import QtQuick

// A biblioteca da TV dentro do onVLC do PC: o mesmo desenho da tela da TV (1920×1080, escalado para a janela),
// feito para o mouse. Lista e imagens vêm da biblioteca (onvlc-biblioteca.exe) em 127.0.0.1:8780.
// Só existe enquanto está aberta (carregada por um Loader no Overlay.qml).
Item {
    id: lib
    signal play(string path, real pos)
    signal close()
    property bool canClose: false  // tem vídeo aberto atrás: Esc volta para ele
    property string font: "Segoe UI Variable Text"

    readonly property string api: "http://127.0.0.1:8780"
    readonly property color orange: "#ff8a1f"
    readonly property color bad: "#ff5a4e"
    readonly property color bgColor: "#0b0b0c"

    property var lib: null  // a resposta de /api/library
    property bool offline: false
    property string etag: ""
    property var byId: ({})
    property var rows: []  // [{ title, wide, items, when }]
    property string screen: "home"  // home | movie | show | gone
    property var cur: null  // o item da página aberta
    property int season: 0
    property var hot: null  // o item em destaque (passou o mouse)
    property int rowIdx: 0
    property real wheelAt: 0

    // ---------- utilidades ----------
    function img(n) { return n ? api + "/img/" + encodeURIComponent(n) : "" }
    function clock(sec) {
        sec = Math.floor(sec)
        var h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60), s = sec % 60
        return h ? h + ":" + pad(m) + ":" + pad(s) : m + ":" + pad(s)
    }
    function pad(n) { return (n < 10 ? "0" : "") + n }
    function hm(min) { return min >= 60 ? Math.floor(min / 60) + "h" + pad(min % 60) : min + " min" }
    function plural(n, a, b) { return n + " " + (n === 1 ? a : b) }
    function gb(n) { return (n / 1e9).toFixed(1).replace(".", ",") + " GB" }
    readonly property var months: ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
    function ymd(x) { return x.getFullYear() + "-" + pad(x.getMonth() + 1) + "-" + pad(x.getDate()) }
    function dateLabel(d) {
        if (!d) return ""
        var p = d.split("-").map(Number), now = new Date()
        if (d === ymd(now)) return "hoje"
        if (d === ymd(new Date(now - 864e5))) return "ontem"
        return p[2] + " " + months[p[1] - 1] + (p[0] !== now.getFullYear() ? " " + p[0] : "")
    }
    function seenText(d) {
        var l = dateLabel(d)
        return "✓ Assistido " + (l === "hoje" || l === "ontem" ? l : "em " + l)
    }
    function nameOf(it) { return it.kind === "episode" ? it.show.title : it.title }
    function epLabel(e) { return "T" + e.season + ":E" + e.episode }
    function episodesOf(show) {
        var out = []
        show.seasons.forEach(function (se) { out = out.concat(se.episodes) })
        return out
    }
    function backdropOf(it) {
        if (!it) return ["", false]
        if (it.kind === "episode") return it.show.backdrop ? [it.show.backdrop, false] : [it.still || it.show.frame, true]
        return it.backdrop ? [it.backdrop, false] : [it.frame, true]
    }

    // ---------- dados ----------
    function load() {
        var x = new XMLHttpRequest()
        x.onreadystatechange = function () {
            if (x.readyState !== XMLHttpRequest.DONE) return
            if (x.status === 304) { offline = false; return }
            if (x.status !== 200) { offline = true; retry.start(); return }
            offline = false
            etag = x.getResponseHeader("ETag") || ""
            index(JSON.parse(x.responseText))
        }
        x.open("GET", api + "/api/library")
        if (etag) x.setRequestHeader("If-None-Match", etag)
        x.send()
    }
    Timer { id: retry; interval: 2000; onTriggered: lib.load() }
    Timer { interval: 30000; running: true; repeat: true; onTriggered: lib.load() }  // só roda com a biblioteca aberta

    function index(d) {
        var by = {}
        d.movies.forEach(function (m) { m.kind = "movie"; by[m.id] = m })
        d.shows.forEach(function (s) {
            s.kind = "show"
            by[s.id] = s
            s.seasons.forEach(function (se) { se.episodes.forEach(function (e) { e.kind = "episode"; e.show = s; by[e.id] = e }) })
        })
        // o que saiu da pasta continua nos assistidos, como item "indisponível" (com os dados guardados)
        function gone(id, h) {
            if (by[id]) return by[id]
            if (!by["h:" + id]) by["h:" + id] = {
                kind: "gone", id: "h:" + id, title: h.title, year: h.year, runtime: h.runtime || 0, genres: h.genres || [],
                vote: h.vote || 0, overview: h.overview || "", poster: h.poster || "", backdrop: h.backdrop || "",
                logo: h.logo || "", frame: h.frame || "", quality: h.quality || "", watched: h.date,
                ep: h.season ? "T" + h.season + ":E" + h.episode + " · " + (h.epTitle || "") : "" }
            return by["h:" + id]
        }
        var seen = {}, watched = []
        ;(d.history || []).forEach(function (h) {
            if (seen[h.id]) return
            seen[h.id] = true
            watched.push({ it: gone(h.id, h), when: [h.kind === "episode" ? "T" + h.season + ":E" + h.episode : "", dateLabel(h.date)]
                .filter(function (x) { return x }).join(" · ") })
        })
        var r = []
        var cont = d["continue"].map(function (id) { return by[id] }).filter(function (x) { return x })
        if (cont.length) r.push({ title: "Continuar assistindo", wide: true, items: cont, when: [] })
        var all = d.movies.concat(d.shows).sort(function (a, b) { return b.added - a.added || a.title.localeCompare(b.title) })
        if (all.length) r.push({ title: "Mídias", wide: false, items: all, when: [] })
        if (watched.length) r.push({ title: "Vocês já assistiram", wide: false, items: watched.map(function (w) { return w.it }),
                                     when: watched.map(function (w) { return w.when }) })
        byId = by
        lib.lib = d
        rows = r
        rowIdx = Math.min(rowIdx, Math.max(0, r.length - 1))
        if (cur && by[cur.id]) cur = by[cur.id]
        if (!hot || !by[hot.id]) hot = r.length ? r[0].items[0] : null
    }

    Component.onCompleted: load()

    // ---------- navegação ----------
    function open(it) {
        if (!it) return
        if (it.kind === "episode") return start(it, it.pos)
        cur = it
        season = 0
        screen = it.kind
        hot = it
    }
    function openId(id) { open(byId[id]) }
    function back() {
        if (screen !== "home") { screen = "home"; cur = null; return true }
        if (canClose) { close(); return true }
        return false
    }
    function start(it, pos) {
        if (it.broken) return
        play(it.path, pos || 0)
    }
    function handleKey(e) {
        if (e.key === Qt.Key_Escape || e.key === Qt.Key_Backspace || e.key === Qt.Key_Back) return back()
        if (screen === "home" && (e.key === Qt.Key_Down || e.key === Qt.Key_Up)) {
            moveRow(e.key === Qt.Key_Down ? 1 : -1)
            return true
        }
        // setas e espaço são da biblioteca enquanto ela está aberta (não pulam o vídeo de trás)
        return e.key === Qt.Key_Left || e.key === Qt.Key_Right || e.key === Qt.Key_Up || e.key === Qt.Key_Down
            || e.key === Qt.Key_Space
    }
    function moveRow(d) {
        var n = Math.max(0, Math.min(rows.length - 1, rowIdx + d))
        if (n === rowIdx) return
        rowIdx = n
        hot = rows[n].items[0]
    }

    // clicar, segurar e arrastar move a janela do onVLC (como em cima do vídeo); um clique simples abre
    component Drag: MouseArea {
        property point at
        property bool moved: false
        signal tap()
        acceptedButtons: Qt.LeftButton | Qt.BackButton
        hoverEnabled: true
        onPressed: function (m) {
            if (m.button === Qt.BackButton) { lib.back(); return }
            at = Qt.point(m.x, m.y); moved = false
        }
        onPositionChanged: function (m) {
            if (pressed && !moved && Math.abs(m.x - at.x) + Math.abs(m.y - at.y) > 6) { moved = true; player.startMove() }
        }
        onClicked: function (m) { if (!moved && m.button === Qt.LeftButton) tap() }
    }

    // ---------- tela ----------
    Rectangle { anchors.fill: parent; color: lib.bgColor }
    Drag {
        anchors.fill: parent
        onWheel: function (w) {
            if (lib.screen !== "home" || Date.now() - lib.wheelAt < 180 || w.angleDelta.y === 0) return
            lib.wheelAt = Date.now()
            lib.moveRow(w.angleDelta.y < 0 ? 1 : -1)
        }
    }

    // a tela da TV tem 1920×1080; aqui ela é escalada para caber na janela, encostada no alto
    Item {
        id: canvas
        width: 1920; height: 1080
        scale: Math.min(lib.width / 1920, lib.height / 1080)
        transformOrigin: Item.TopLeft
        x: (lib.width - width * scale) / 2

        // fundo do item em destaque (troca suave entre duas camadas)
        Item {
            id: bg
            anchors.fill: parent
            property var shown: lib.backdropOf(lib.screen === "home" ? lib.hot : lib.cur)
            property bool flip: false
            onShownChanged: {
                var src = shown[0] ? lib.img(shown[0]) : ""
                var on = flip ? bgB : bgA
                if (on.source.toString() === src) return
                flip = !flip
                var nxt = flip ? bgB : bgA, old = flip ? bgA : bgB
                nxt.source = src
                nxt.frame = shown[1]
                nxt.opacity = 1
                old.opacity = 0
            }
            component Layer: Image {
                property bool frame: false
                anchors.fill: parent
                fillMode: Image.PreserveAspectCrop
                verticalAlignment: Image.AlignTop
                sourceSize.width: 1280
                asynchronous: true
                opacity: 0
                Behavior on opacity { NumberAnimation { duration: 400 } }
                Rectangle { anchors.fill: parent; color: "black"; opacity: parent.frame ? 0.25 : 0 }  // quadro do filme: mais escuro
            }
            Layer { id: bgA }
            Layer { id: bgB }
        }
        Rectangle {
            anchors.fill: parent
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0; color: lib.bgColor }
                GradientStop { position: 0.26; color: Qt.rgba(11 / 255, 11 / 255, 12 / 255, 0.92) }
                GradientStop { position: 0.58; color: Qt.rgba(11 / 255, 11 / 255, 12 / 255, 0.35) }
                GradientStop { position: 1; color: Qt.rgba(11 / 255, 11 / 255, 12 / 255, 0.1) }
            }
        }
        Rectangle {
            anchors.fill: parent
            gradient: Gradient {
                GradientStop { position: 0.38; color: "transparent" }
                GradientStop { position: 0.66; color: Qt.rgba(11 / 255, 11 / 255, 12 / 255, 0.9) }
                GradientStop { position: 1; color: lib.bgColor }
            }
        }

        // barra: marca (o menu e os botões da janela são os do player, por cima)
        Row {
            x: 96; y: 44; height: 56
            spacing: 14
            Image { anchors.verticalCenter: parent.verticalCenter; source: "icon.png"; width: 44; height: 44; sourceSize: Qt.size(88, 88) }
            Text { anchors.verticalCenter: parent.verticalCenter; text: "onVLC"; color: "white"; font.family: lib.font; font.pixelSize: 30; font.weight: Font.ExtraBold }
        }
        Rectangle {
            visible: !!lib.lib && lib.lib.busy > 0 && !lib.offline
            anchors.right: parent.right; anchors.rightMargin: 96; y: 52
            height: 40; radius: 20; width: busyText.implicitWidth + 36
            color: Qt.rgba(0, 0, 0, 0.55)
            Text {
                id: busyText
                anchors.centerIn: parent
                text: lib.lib ? "Lendo " + lib.plural(lib.lib.busy, "vídeo novo", "vídeos novos") : ""
                color: "white"; font.family: lib.font; font.pixelSize: 22; font.weight: Font.DemiBold
            }
        }

        // ---------- pedaços ----------
        component TitleBlock: Item {
            property var it
            property int h: 170
            property int maxW: 620
            readonly property var t: it ? (it.kind === "episode" ? it.show : it) : null
            height: h
            Image {
                id: logo
                visible: !!(parent.t && parent.t.logo)
                anchors.left: parent.left; anchors.bottom: parent.bottom
                source: visible ? lib.img(parent.t.logo) : ""
                width: Math.min(parent.maxW, implicitWidth * parent.h / Math.max(1, implicitHeight))
                height: parent.h
                fillMode: Image.PreserveAspectFit
                horizontalAlignment: Image.AlignLeft; verticalAlignment: Image.AlignBottom
                sourceSize.height: parent.h * 2
                asynchronous: true
            }
            Text {
                visible: !logo.visible
                anchors.left: parent.left; anchors.bottom: parent.bottom
                width: parent.width
                text: parent.t ? parent.t.title : ""
                wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight
                color: "white"; font.family: lib.font; font.pixelSize: parent.h > 170 ? 76 : 70; font.weight: Font.ExtraBold
                lineHeight: 1.0; font.letterSpacing: -1
            }
        }

        component Meta: Flow {
            property var it
            spacing: 22
            property var parts: {
                var x = it, p = []
                if (!x) return p
                var t = x.kind === "episode" ? x.show : it
                if (t.year) p.push({ t: String(t.year) })
                if (x.kind === "gone" && x.ep) p.push({ t: x.ep })
                if (x.kind === "show") {
                    var n = lib.episodesOf(x).length
                    p.push({ t: x.seasons.length > 1 ? lib.plural(x.seasons.length, "temporada", "temporadas") : lib.plural(n, "episódio", "episódios") })
                } else if (x.runtime && !(x.kind === "gone" && x.ep)) p.push({ t: lib.hm(x.runtime) })
                if (t.genres && t.genres.length) p.push({ t: t.genres.join(", ") })
                if (t.vote) p.push({ t: String(t.vote).replace(".", ","), star: true })
                var q = x.kind === "show" ? (lib.episodesOf(x)[0] || {}).quality : x.quality
                if (q && x.kind !== "gone") p.push({ t: q, q: true })
                if (x.broken) p.push({ t: "Arquivo com problema", bad: true })
                if (x.kind === "gone") p.push({ t: "Indisponível", bad: true })
                if (x.watched) p.push({ t: lib.seenText(x.watched), seen: true })
                return p
            }
            Repeater {
                model: parent.parts
                delegate: Rectangle {
                    required property var modelData
                    width: mt.implicitWidth + (modelData.q ? 18 : 0) + (modelData.star ? 30 : 0)
                    height: 34
                    color: "transparent"
                    radius: 6
                    border.width: modelData.q ? 2 : 0
                    border.color: Qt.rgba(1, 1, 1, 0.5)
                    Text {
                        visible: !!modelData.star
                        anchors.verticalCenter: parent.verticalCenter
                        text: "★"; color: "#ffc24a"; font.pixelSize: 26
                    }
                    Text {
                        id: mt
                        anchors.verticalCenter: parent.verticalCenter
                        x: modelData.q ? 9 : (modelData.star ? 30 : 0)
                        text: modelData.t
                        color: modelData.bad ? lib.bad : modelData.seen ? lib.orange : Qt.rgba(1, 1, 1, 0.85)
                        font.family: lib.font
                        font.pixelSize: modelData.q ? 21 : 26
                        font.weight: modelData.q || modelData.bad || modelData.seen ? Font.Bold : Font.Medium
                        font.letterSpacing: modelData.q ? 1 : 0
                    }
                }
            }
        }

        component Synopsis: Text {
            property var it
            property int lines: 3
            readonly property string body: it ? (it.kind === "episode" ? (it.overview || it.show.overview) : it.overview) : ""
            width: 880
            text: body || (lib.lib && lib.lib.tmdb ? "Sem sinopse." : "Sem sinopse. Com a chave do TMDB no PC aparecem capa, fundo e sinopse.")
            wrapMode: Text.Wrap; maximumLineCount: lines; elide: Text.ElideRight
            color: body ? Qt.rgba(1, 1, 1, 0.8) : Qt.rgba(1, 1, 1, 0.45)
            font.family: lib.font; font.pixelSize: 28; lineHeight: 1.42
        }

        component Card: Item {
            id: card
            property var it
            property bool wide: false
            property string when: ""
            readonly property bool hot: dm.containsMouse
            width: wide ? 420 : 220
            height: wide ? 236 : 330
            z: hot ? 2 : 0
            scale: hot ? 1.07 : 1
            Behavior on scale { NumberAnimation { duration: 160 } }
            readonly property string src: {
                if (!it) return ""
                if (wide) return it.kind === "episode" ? (it.still || it.show.backdrop || it.show.frame) : (it.backdrop || it.frame)
                return it.kind === "episode" ? it.show.poster : it.poster
            }
            readonly property string fallback: it ? (it.kind === "episode" ? (it.still || it.show.frame) : it.frame) : ""
            Rectangle {
                id: box
                anchors.fill: parent
                radius: 14
                color: "#1c1c1f"
                clip: true
                Image {
                    anchors.fill: parent
                    source: lib.img(card.src || card.fallback)
                    fillMode: Image.PreserveAspectCrop
                    sourceSize: Qt.size(card.width * 1.5, card.height * 1.5)  // decodifica no tamanho que aparece
                    asynchronous: true
                    opacity: card.it && (card.it.broken || card.it.kind === "gone") ? 0.45 : 1
                }
                // sem capa (ou card largo): nome por cima, com a barra de onde parou
                Rectangle {
                    visible: card.wide || !card.src
                    anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
                    height: capCol.implicitHeight + 62
                    gradient: Gradient {
                        GradientStop { position: 0; color: "transparent" }
                        GradientStop { position: 1; color: Qt.rgba(0, 0, 0, 0.9) }
                    }
                    Column {
                        id: capCol
                        x: 16; width: parent.width - 32
                        anchors.bottom: parent.bottom; anchors.bottomMargin: 14
                        spacing: 4
                        Text {
                            width: parent.width
                            text: card.it ? lib.nameOf(card.it) : ""
                            wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight
                            color: "white"; font.family: lib.font; font.pixelSize: 22; font.weight: Font.Bold
                        }
                        Text {
                            visible: text !== ""
                            width: parent.width
                            elide: Text.ElideRight
                            text: !card.wide || !card.it ? "" : card.it.kind === "episode" ? lib.epLabel(card.it) + " · " + card.it.title
                                  : card.it.pos ? "Parou em " + lib.clock(card.it.pos) + " · faltam " + lib.hm(Math.max(1, Math.round((card.it.dur - card.it.pos) / 60))) : ""
                            color: Qt.rgba(1, 1, 1, 0.75); font.family: lib.font; font.pixelSize: 19; font.weight: Font.Medium
                        }
                        Rectangle {
                            visible: card.wide && !!card.it && card.it.pos > 0 && card.it.dur > 0
                            width: parent.width; height: 6; radius: 3
                            color: Qt.rgba(1, 1, 1, 0.3)
                            Rectangle { height: parent.height; radius: 3; color: lib.orange; width: parent.width * (card.it && card.it.dur ? card.it.pos / card.it.dur : 0) }
                        }
                    }
                }
                Rectangle {
                    visible: card.when !== "" || (!!card.it && card.it.kind === "gone" && !card.wide)
                    anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
                    height: 70
                    gradient: Gradient {
                        GradientStop { position: 0; color: "transparent" }
                        GradientStop { position: 1; color: Qt.rgba(0, 0, 0, 0.9) }
                    }
                    Text {
                        x: 14; anchors.bottom: parent.bottom; anchors.bottomMargin: 12
                        width: parent.width - 28; elide: Text.ElideRight
                        text: card.when + (card.it && card.it.kind === "gone" ? (card.when ? " · indisponível" : "Indisponível") : "")
                        color: "white"; font.family: lib.font; font.pixelSize: 20; font.weight: Font.Bold
                    }
                }
                Rectangle {
                    visible: !!card.it && !!card.it.broken
                    x: 12; y: 12; width: bt.implicitWidth + 20; height: 30; radius: 6
                    color: lib.bad
                    Text { id: bt; anchors.centerIn: parent; text: "Com problema"; color: "white"; font.family: lib.font; font.pixelSize: 17; font.weight: Font.Bold }
                }
                Rectangle {
                    visible: !!card.it && !!card.it.watched && card.when === "" && card.it.kind !== "gone"
                    anchors.right: parent.right; anchors.rightMargin: 10; y: 10
                    width: 36; height: 36; radius: 18
                    color: Qt.rgba(0, 0, 0, 0.7)
                    Text { anchors.centerIn: parent; text: "✓"; color: lib.orange; font.pixelSize: 20; font.weight: Font.ExtraBold }
                }
            }
            Rectangle {  // anel do foco
                anchors.fill: parent; anchors.margins: -5
                radius: 19
                color: "transparent"
                border.width: 5; border.color: lib.orange
                visible: card.hot
            }
            Drag {
                id: dm
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onContainsMouseChanged: if (containsMouse && card.it && card.it.kind !== "episode") lib.hot = card.it
                onTap: lib.open(card.it)
            }
        }

        component Arrow: Rectangle {
            property Item row
            property int dir
            visible: row.hovered && (dir < 0 ? row.first > 0 : row.first < row.maxFirst)
            y: 54 + (row.model.wide ? 236 : 330) / 2 - 36
            width: 56; height: 72; radius: 12
            color: am.containsMouse ? Qt.rgba(0, 0, 0, 0.85) : Qt.rgba(0, 0, 0, 0.6)
            Text { anchors.centerIn: parent; text: parent.dir < 0 ? "‹" : "›"; color: "white"; font.pixelSize: 54; font.weight: Font.Light }
            MouseArea {
                id: am
                anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                onClicked: row.first = Math.max(0, Math.min(row.maxFirst, row.first + parent.dir * Math.max(1, row.fits - 1)))
            }
        }
        component Row_: Item {
            id: row
            property var model
            width: 1920
            height: 36 + 18 + (model.wide ? 236 : 330)
            readonly property int step: (model.wide ? 420 : 220) + 28
            readonly property int fits: Math.floor((1920 - 192 + 28) / step)
            property int first: 0
            readonly property int maxFirst: Math.max(0, model.items.length - fits)
            readonly property bool hovered: rowHover.hovered
            HoverHandler { id: rowHover }
            Text {
                x: 96; height: 36
                text: row.model.title
                color: "white"; font.family: lib.font; font.pixelSize: 30; font.weight: Font.Bold
            }
            Item {
                y: 54; x: 96 - 30; width: 1920 - 96 - 66; height: row.model.wide ? 236 : 330
                clip: false
                Row {
                    x: 30 - row.first * row.step
                    Behavior on x { NumberAnimation { duration: 250; easing.type: Easing.OutCubic } }
                    spacing: 28
                    Repeater {
                        model: row.model.items
                        delegate: Card {
                            required property var modelData
                            required property int index
                            it: modelData
                            wide: row.model.wide
                            when: row.model.when[index] || ""
                        }
                    }
                }
            }
            Arrow { row: row; dir: -1; x: 24 }
            Arrow { row: row; dir: 1; x: 1920 - 80 }
            WheelHandler {  // rodinha de lado, ou Shift + rodinha: anda na fileira
                acceptedModifiers: Qt.ShiftModifier
                onWheel: function (w) {
                    var d = w.angleDelta.y || w.angleDelta.x
                    if (d) row.first = Math.max(0, Math.min(row.maxFirst, row.first + (d < 0 ? 1 : -1)))
                }
            }
        }

        // ---------- início ----------
        Item {
            anchors.fill: parent
            visible: lib.screen === "home"

            Column {  // destaque
                x: 96; y: 160; width: 940
                visible: !!lib.hot
                TitleBlock { width: 940; it: lib.hot }
                Item { width: 1; height: 26 }
                Meta { width: 940; it: lib.hot }
                Text {
                    visible: !!lib.hot && lib.hot.kind === "episode"
                    topPadding: 22
                    text: lib.hot && lib.hot.kind === "episode" ? lib.epLabel(lib.hot) + " · " + lib.hot.title : ""
                    color: Qt.rgba(1, 1, 1, 0.8); font.family: lib.font; font.pixelSize: 28
                }
                Item { width: 1; height: 22 }
                Synopsis { it: lib.hot }
            }

            Item {  // fileiras
                x: 0; y: 610; width: 1920; height: 470
                clip: true
                Column {
                    id: rowsCol
                    y: -rowsCol.offset(lib.rowIdx)
                    Behavior on y { NumberAnimation { duration: 280; easing.type: Easing.OutCubic } }
                    spacing: 44
                    function offset(i) {
                        var y = 0
                        for (var k = 0; k < i && k < lib.rows.length; k++) y += 36 + 18 + (lib.rows[k].wide ? 236 : 330) + 44
                        return y
                    }
                    Repeater {
                        model: lib.rows
                        delegate: Row_ { required property var modelData; model: modelData }
                    }
                }
            }

            Column {  // biblioteca vazia
                visible: !!lib.lib && !lib.rows.length
                x: 96; y: 300; width: 1100; spacing: 14
                Text { text: "A biblioteca está vazia"; color: "white"; font.family: lib.font; font.pixelSize: 64; font.weight: Font.ExtraBold }
                Text {
                    width: parent.width; wrapMode: Text.Wrap; textFormat: Text.StyledText
                    text: "Coloque filmes e séries na pasta <b>Vídeos › onVLC</b> do PC (" + (lib.lib ? lib.lib.folder : "") + "). Eles aparecem aqui sozinhos em poucos segundos. Séries são reconhecidas pelo \"S01E02\" no nome."
                    color: Qt.rgba(1, 1, 1, 0.75); font.family: lib.font; font.pixelSize: 30; lineHeight: 1.45
                }
            }
            Column {  // biblioteca ainda abrindo
                visible: !lib.lib
                x: 96; y: 300; spacing: 20
                Text { text: lib.offline ? "Procurando a biblioteca" : "Abrindo a biblioteca"; color: "white"; font.family: lib.font; font.pixelSize: 64; font.weight: Font.ExtraBold }
                Text { text: "Ela abre junto com o onVLC; leva só um instante."; color: Qt.rgba(1, 1, 1, 0.75); font.family: lib.font; font.pixelSize: 30 }
            }
        }

        // ---------- página do filme, da série ou de um vídeo que saiu da pasta ----------
        component Btn: Rectangle {
            id: b
            property string label
            property bool playIcon: false
            signal tap()
            height: 76
            width: br.implicitWidth + 68
            radius: 14
            color: bm.containsMouse ? "white" : Qt.rgba(1, 1, 1, 0.14)
            scale: bm.containsMouse ? 1.05 : 1
            Behavior on scale { NumberAnimation { duration: 140 } }
            Row {
                id: br
                anchors.centerIn: parent
                spacing: 12
                Icon { visible: b.playIcon; anchors.verticalCenter: parent.verticalCenter; name: "play"; size: 30; color: bm.containsMouse ? "#111" : "white" }
                Text { anchors.verticalCenter: parent.verticalCenter; text: b.label; color: bm.containsMouse ? "#111" : "white"; font.family: lib.font; font.pixelSize: 28; font.weight: Font.Medium }
            }
            MouseArea { id: bm; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: b.tap() }
        }

        Item {
            anchors.fill: parent
            visible: lib.screen !== "home" && !!lib.cur

            Text {  // voltar (no PC não tem o botão do controle da TV)
                x: 120; y: 112
                text: "‹  Voltar"
                color: vm.containsMouse ? lib.orange : Qt.rgba(1, 1, 1, 0.7)
                font.family: lib.font; font.pixelSize: 26; font.weight: Font.DemiBold
                MouseArea { id: vm; anchors.fill: parent; anchors.margins: -10; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: lib.back() }
            }

            Column {
                x: 120; y: lib.screen === "show" ? 150 : 170; width: 1100
                TitleBlock { width: 1100; it: lib.cur; h: lib.screen === "show" ? 170 : 200; maxW: 700 }
                Item { width: 1; height: 30 }
                Meta { width: 1100; it: lib.cur }
                Item { width: 1; height: 22 }
                Synopsis { it: lib.cur; lines: lib.screen === "show" ? 3 : 4; width: 1000 }
                Rectangle {  // avisos
                    visible: lib.screen === "gone" || (!!lib.cur && !!lib.cur.broken)
                    width: noteText.implicitWidth + 48; height: noteText.implicitHeight + 28
                    y: 24
                    radius: 14
                    color: lib.screen === "gone" ? Qt.rgba(1, 1, 1, 0.08) : Qt.rgba(1, 90 / 255, 78 / 255, 0.14)
                    border.width: 2
                    border.color: lib.screen === "gone" ? Qt.rgba(1, 1, 1, 0.25) : Qt.rgba(1, 90 / 255, 78 / 255, 0.5)
                    Text {
                        id: noteText
                        anchors.centerIn: parent
                        width: Math.min(implicitWidth, 1000); wrapMode: Text.Wrap
                        textFormat: Text.StyledText
                        text: lib.screen === "gone" ? "<b>Indisponível.</b> Este arquivo não está mais na pasta onVLC do PC. Coloque ele de volta lá para assistir."
                                                    : "O PC não consegue abrir este arquivo. Ele pode estar incompleto ou corrompido."
                        color: "white"; font.family: lib.font; font.pixelSize: 26; font.weight: Font.DemiBold
                    }
                }
                Item { width: 1; height: 40 }
                // botões: tocar
                Row {
                    spacing: 22
                    property var btns: {
                        var it = lib.cur, b = []
                        if (!it || it.kind === "gone") return b
                        if (it.kind === "movie" && !it.broken) {
                            if (it.pos) b.push({ t: "Continuar de " + lib.clock(it.pos), play: true, it: it, pos: it.pos }, { t: "Do começo", it: it, pos: 0 })
                            else b.push({ t: it.watched ? "Assistir de novo" : "Assistir", play: true, it: it, pos: 0 })
                        } else if (it.kind === "show") {
                            var started = null
                            lib.lib["continue"].forEach(function (id) { var e = lib.byId[id]; if (!started && e && e.kind === "episode" && e.show === it) started = e })
                            var first = lib.episodesOf(it).filter(function (e) { return !e.broken })[0]
                            if (started) b.push({ t: "Continuar " + lib.epLabel(started), play: true, it: started, pos: started.pos })
                            else if (first) b.push({ t: "Assistir " + lib.epLabel(first), play: true, it: first, pos: 0 })
                        }
                        return b
                    }
                    Repeater {
                        model: parent.btns
                        delegate: Btn {
                            required property var modelData
                            label: modelData.t; playIcon: !!modelData.play
                            onTap: lib.start(modelData.it, modelData.pos)
                        }
                    }
                }
                // temporadas
                Item { width: 1; height: lib.screen === "show" && lib.cur.seasons.length > 1 ? 34 : 0 }
                Row {
                    visible: lib.screen === "show" && lib.cur.seasons.length > 1
                    spacing: 14
                    Repeater {
                        model: lib.screen === "show" ? lib.cur.seasons : []
                        delegate: Rectangle {
                            required property var modelData
                            required property int index
                            width: st.implicitWidth + 48; height: 54; radius: 14
                            color: sm.containsMouse ? "white" : "transparent"
                            border.width: 2
                            border.color: sm.containsMouse || index === lib.season ? "white" : Qt.rgba(1, 1, 1, 0.25)
                            Text { id: st; anchors.centerIn: parent; text: "Temporada " + modelData.n; color: sm.containsMouse ? "#111" : "white"; font.family: lib.font; font.pixelSize: 24; font.weight: Font.Medium }
                            MouseArea { id: sm; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: lib.season = index }
                        }
                    }
                }
                // ficha do filme
                Item { width: 1; height: lib.screen === "movie" ? 44 : 0 }
                Column {
                    visible: lib.screen === "movie"
                    spacing: 12
                    Repeater {
                        model: lib.screen === "movie" && lib.cur ? [
                            ["Áudio", (lib.cur.audio || []).filter(function (x, i, a) { return a.indexOf(x) === i }).join(" · ") || "—"],
                            ["Legendas", (lib.cur.subs || []).join(" · ") || "nenhuma"],
                            ["Arquivo", lib.cur.file + " · " + lib.gb(lib.cur.size)]] : []
                        delegate: Item {
                            required property var modelData
                            width: 180 + 24 + 880; height: fv.implicitHeight
                            Text { width: 180; text: modelData[0]; color: Qt.rgba(1, 1, 1, 0.5); font.family: lib.font; font.pixelSize: 24 }
                            Text {
                                id: fv
                                x: 204; width: 880; wrapMode: Text.WrapAnywhere
                                text: modelData[1]
                                color: modelData[0] === "Arquivo" ? Qt.rgba(1, 1, 1, 0.6) : Qt.rgba(1, 1, 1, 0.88)
                                font.family: lib.font; font.pixelSize: modelData[0] === "Arquivo" ? 21 : 24
                            }
                        }
                    }
                }
            }

            // episódios da temporada
            Row_ {
                visible: lib.screen === "show"
                y: 760
                model: lib.screen === "show" && lib.cur ? { title: lib.cur.seasons.length > 1 ? "Temporada " + lib.cur.seasons[lib.season].n : "Episódios",
                                                             wide: true, items: lib.cur.seasons[lib.season].episodes, when: [] }
                                                         : { title: "", wide: true, items: [], when: [] }
            }
        }
    }
}
