import QtQuick
import qs.Commons
import "Reader.js" as Reader

// Every key Reader answers to, in two columns. Shown over whatever view is
// open; any key or click puts it away.
Item {
  id: sheet

  property color fg: Color.foreground
  property color dim: Qt.darker(fg, 1.5)
  property color accent: Color.accent
  property string fontFamily: Style.font.family

  readonly property var sections: Reader.keySheet()
  // The two columns, balanced by how many lines each section takes.
  readonly property var columns: {
    var left = []
    var right = []
    var total = 0
    var i
    for (i = 0; i < sections.length; i++) total += sections[i].keys.length + 2
    var used = 0
    for (i = 0; i < sections.length; i++) {
      if (used < total / 2) left.push(sections[i])
      else right.push(sections[i])
      used += sections[i].keys.length + 2
    }
    return [left, right]
  }

  signal dismissed()

  MouseArea {
    anchors.fill: parent
    acceptedButtons: Qt.AllButtons
    onClicked: sheet.dismissed()
    onWheel: function(wheel) { wheel.accepted = true }
  }

  Row {
    id: body
    anchors.top: parent.top
    anchors.horizontalCenter: parent.horizontalCenter
    spacing: Style.space(36)

    Repeater {
      model: sheet.columns

      Column {
        id: column

        required property var modelData

        width: Math.floor((sheet.width - body.spacing) / 2)
        spacing: Style.space(18)

        Repeater {
          model: column.modelData

          Column {
            id: section

            required property var modelData

            width: column.width
            spacing: Style.space(7)

            Text {
              textFormat: Text.PlainText
              text: section.modelData.title
              color: sheet.dim
              font.family: sheet.fontFamily
              font.pixelSize: Style.font.caption
              font.bold: true
              bottomPadding: Style.space(2)
            }

            Repeater {
              model: section.modelData.keys

              Row {
                id: line

                required property var modelData

                width: section.width
                spacing: Style.space(10)

                // One cap per key; "← →" is two keys, "Double-click" one.
                Row {
                  id: caps
                  width: Style.space(96)
                  spacing: Style.space(4)
                  layoutDirection: Qt.RightToLeft

                  Repeater {
                    model: String(line.modelData[0]).split(" ").reverse()

                    Rectangle {
                      required property string modelData

                      width: Math.max(height, cap.implicitWidth + Style.space(10))
                      height: cap.implicitHeight + Style.space(6)
                      radius: Math.min(Style.cornerRadius, Style.space(4))
                      color: Util.alpha(sheet.fg, 0.06)
                      border.width: 1
                      border.color: Util.alpha(sheet.fg, 0.22)

                      Text {
                        id: cap
                        anchors.centerIn: parent
                        textFormat: Text.PlainText
                        text: parent.modelData
                        color: sheet.fg
                        font.family: sheet.fontFamily
                        font.pixelSize: Style.font.caption
                      }
                    }
                  }
                }

                Text {
                  width: parent.width - caps.width - parent.spacing
                  anchors.verticalCenter: parent.verticalCenter
                  textFormat: Text.PlainText
                  text: line.modelData[1]
                  color: sheet.fg
                  font.family: sheet.fontFamily
                  font.pixelSize: Style.font.bodySmall
                  wrapMode: Text.Wrap
                }
              }
            }
          }
        }
      }
    }
  }

  Text {
    anchors.bottom: parent.bottom
    anchors.horizontalCenter: parent.horizontalCenter
    textFormat: Text.PlainText
    text: "h j k l work as the arrows  ·  letters work in capitals too"
    color: sheet.dim
    font.family: sheet.fontFamily
    font.pixelSize: Style.font.caption
  }
}
