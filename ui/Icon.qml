import QtQuick
import QtQuick.Shapes
import "icons.js" as Icons

// Ícone vetorial que muda de cor (branco translúcido → laranja no hover)
Item {
    id: r
    property string name
    property color color: "white"
    property real size: 22
    readonly property var def: Icons.get(name)
    width: size
    height: size

    Item {
        width: 24
        height: 24
        scale: r.size / 24
        transformOrigin: Item.TopLeft

        Shape {
            anchors.fill: parent
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                strokeColor: r.def.s ? r.color : "transparent"
                strokeWidth: 1.8
                fillColor: "transparent"
                capStyle: ShapePath.RoundCap
                joinStyle: ShapePath.RoundJoin
                PathSvg { path: r.def.s || "M0 0" }
            }
            ShapePath {
                strokeColor: "transparent"
                fillColor: r.def.f ? r.color : "transparent"
                PathSvg { path: r.def.f || "M0 0" }
            }
        }

        Text {
            visible: !!r.def.t
            text: r.def.t ? r.def.t[0] : ""
            color: r.color
            font.pixelSize: 7
            font.bold: true
            width: 12
            horizontalAlignment: Text.AlignHCenter
            x: r.def.t ? r.def.t[1] - 6 : 0
            y: r.def.t ? r.def.t[2] - 7 : 0
        }
    }
}
