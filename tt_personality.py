"""Local editor for TT's user-defined persona prompt."""

import sqlite3

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QPlainTextEdit, QVBoxLayout,
)


PREFERENCE = 'persona_prompt'


def load_personality(db):
    row = db.execute('SELECT value FROM tt_preferences WHERE name=?', (PREFERENCE,)).fetchone()
    return row[0].strip() if row else ''


class PersonalityDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.db = parent.db
        self.setWindowTitle('自定义人格')
        self.resize(500, 350)
        layout = QVBoxLayout(self)

        note = QLabel('可填写 TT 的性格、说话方式、称呼或互动习惯。内容保存在本机，'
                      '聊天时会随请求发给当前模型。留空保存可恢复默认聊天风格。')
        note.setWordWrap(True)
        layout.addWidget(note)

        self.editor = QPlainTextEdit()
        self.editor.setAccessibleName('人格设定')
        self.editor.setPlaceholderText('在这里填写你想要的人格设定')
        self.editor.setPlainText(load_personality(self.db))
        layout.addWidget(self.editor, 1)
        self.editor.setFocus()
        self.error = QLabel()
        self.error.hide()
        layout.addWidget(self.error)

        standard = QDialogButtonBox.StandardButton
        buttons = QDialogButtonBox(standard.Save | standard.Cancel)
        buttons.button(standard.Save).setText('保存')
        buttons.button(standard.Cancel).setText('取消')
        buttons.button(standard.Save).clicked.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def save(self):
        value = self.editor.toPlainText().strip()
        try:
            with self.db:
                if value:
                    self.db.execute('INSERT OR REPLACE INTO tt_preferences VALUES (?, ?)',
                                    (PREFERENCE, value))
                else:
                    self.db.execute('DELETE FROM tt_preferences WHERE name=?', (PREFERENCE,))
        except sqlite3.Error:
            self.error.setText('人格设定保存失败，请重试。')
            self.error.show()
            return
        self.accept()
