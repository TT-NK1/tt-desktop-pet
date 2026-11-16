import sys

import desktop_pet_chat as base
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget
from tt_avatar import reply_delivery
from tt_live2d_view import TTLive2DAvatar


class CozyWindow(base.ChatWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("TT 的小窝")
        self.setObjectName("cozyWindow")
        self.resize(720, 690)

        self.avatar = TTLive2DAvatar(self)
        self.avatar_turn = None
        self.avatar_text = ""
        self.avatar_return = QTimer(self)
        self.avatar_return.setSingleShot(True)
        self.avatar_return.setInterval(9000)
        self.avatar_return.timeout.connect(self.avatar_idle_when_quiet)
        self.bubble_reply.connect(self.avatar_reply)
        header = QLabel("✦  TT 的小窝")
        header.setObjectName("cozyHeader")
        self.avatar_caption = QLabel("TT 在这里。想聊聊天吗？")
        self.avatar_caption.setWordWrap(True)
        card = QWidget()
        card.setObjectName("ttCard")
        row = QHBoxLayout(card)
        row.addWidget(self.avatar)
        words = QVBoxLayout()
        words.addStretch()
        words.addWidget(header)
        words.addWidget(self.avatar_caption)
        words.addStretch()
        row.addLayout(words, 1)
        self.layout().insertWidget(0, card)
        self.resize(720, 750)

        self.send_button.setText("递给 TT")
        self.send_button.setObjectName("cozySend")
        self.new_button.setText("开启新话题")
        self.input.setPlaceholderText("在这里写下想对 TT 说的话…")
        self.view.setPlaceholderText("这里会留下你和 TT 的对话。")

        self.setStyleSheet("""
            QWidget#cozyWindow { background: #f4edf9; }
            QWidget#ttCard { background: #fbf4ff; border-radius: 16px; }
            QLabel, QCheckBox { color: #493957; }
            QLabel#cozyHeader {
                color: #5b3b75; font-size: 24px; font-weight: bold;
                padding: 10px 4px 2px;
            }
            QPlainTextEdit, QComboBox {
                background: #fffaf7; color: #382d43;
                border: 1px solid #ded1e9; border-radius: 12px;
                padding: 8px; font-size: 14px;
                selection-background-color: #72568f;
                selection-color: white;
            }
            QPushButton {
                background: #e9dcf4; color: #483257;
                border: 1px solid #d9c6ea; border-radius: 10px;
                padding: 7px 9px;
            }
            QPushButton:hover { background: #dec9ee; }
            QPushButton#cozySend {
                background: #755493; color: white; font-weight: bold;
            }
            QPushButton#cozySend:hover { background: #664382; }
            QPushButton:disabled, QPushButton#cozySend:disabled {
                background: #ece6f0; color: #756c7e;
            }
        """)

    def start_worker(self, worker):
        if worker.mode == "chat":
            self.avatar_return.stop()
            self.avatar.set_resting(False)
            self.avatar_turn = worker
            self.avatar_text = ""
            worker.delta.connect(self.avatar_delta)
            self.avatar.set_mode("thinking")
            self.avatar_caption.setText("唔，让 TT 想想…")
        super().start_worker(worker)

    def avatar_idle(self):
        self.avatar_return.stop()
        self.avatar_turn = None
        self.avatar_text = ""
        self.avatar.set_mode("idle")
        self.avatar.set_resting(True)
        self.avatar_caption.setText("TT 在这里。想聊聊天吗？")

    def avatar_idle_when_quiet(self):
        if getattr(getattr(self, "pro_voice", None), "active", False):
            self.avatar_return.start(1500)
        else:
            self.avatar_idle()

    def resolve_reply_delivery(self, prompt, text):
        return reply_delivery(prompt, text)

    def avatar_speaking(self, expression):
        self.avatar.set_resting(False)
        self.avatar.set_mode(expression)
        self.avatar_return.start(9000)

    def avatar_delta(self, text):
        if self.avatar_turn is None or self.sender() is not self.worker or self.sender() is not self.avatar_turn:
            return
        self.avatar_text = (self.avatar_text + text)[:600]
        if not self.avatar_text.strip(" \n\t*#>，。！？"):
            return
        mode = self.resolve_reply_delivery(self.pending_prompt, self.avatar_text).expression
        if mode == "idle" and len(self.avatar_text) < 8 and not any(mark in self.avatar_text for mark in "。！？"):
            return
        if self.avatar.mode == "thinking" or mode != "idle":
            self.avatar.set_mode(mode)
        self.avatar_caption.setText("TT 正在回复…")

    def avatar_reply(self, text):
        prompt = self.pending_prompt if self.pending_id is not None else ""
        self.avatar.set_resting(False)
        self.avatar.set_mode(self.resolve_reply_delivery(prompt, text).expression)
        self.avatar_caption.setText("TT 在这里。想聊聊天吗？")
        self.avatar_turn = None
        self.avatar_return.start(9000)

    def finished(self):
        if not self.avatar_return.isActive():
            self.avatar_idle()
        super().finished()

    def cancel_reply_worker(self):
        super().cancel_reply_worker()
        if self.avatar_turn is not None:
            self.avatar_idle()

    def new_chat(self):
        self.avatar_idle()
        super().new_chat()


    def send(self):
        sent = super().send()
        if sent and self.worker is not None:
            self.status.setText("TT 正在想怎么回答你…")
        return sent



if __name__ == "__main__":
    from desktop_pet_search import main
    sys.exit(main())
