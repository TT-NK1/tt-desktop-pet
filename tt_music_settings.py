"""Music platform controls in TT's settings tab."""
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFileDialog, QFormLayout, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QMenu, QPushButton, QPlainTextEdit, QScrollArea, QVBoxLayout, QWidget,
)

from tt_music_services import NETEASE_LOCAL_SOURCE, PLATFORMS, WAITING_CLIENTS
from tt_theme import LIGHT


COMMANDS = {'play':'播放','pause':'暂停','toggle':'播放/暂停切换','next':'下一首','previous':'上一首','seek':'进度调整','volume':'应用音量'}


class MusicPlatformsPage(QScrollArea):
    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        page = QWidget()
        self.setWidget(page)
        body = QVBoxLayout(page)
        body.setContentsMargins(16,14,16,16)
        body.setSpacing(9)
        bar = QHBoxLayout()
        self.current = QLabel()
        self.current.setObjectName('musicPlatformCurrent')
        bar.addWidget(self.current,1)
        self.local = self.button(bar,'使用本地音频',lambda: service.switch_to('local'))
        body.addLayout(bar)
        self.feedback = QLabel('在官方客户端播放歌曲后，可通过「诊断」检测媒体会话。')
        self.feedback.setTextFormat(Qt.TextFormat.PlainText)
        self.feedback.setWordWrap(True)
        self.feedback.setObjectName('musicPlatformFeedback')
        body.addWidget(self.feedback)
        pause = QCheckBox('切换时尽可能暂停原播放来源')
        pause.setChecked(service.pause_previous)
        pause.toggled.connect(service.set_pause_previous)
        body.addWidget(pause)
        self.cards = {}
        for key, platform in PLATFORMS.items():
            card = QFrame()
            card.setObjectName('musicPlatformCard')
            column = QVBoxLayout(card)
            column.setContentsMargins(11,8,11,8)
            column.setSpacing(4)
            heading = QHBoxLayout()
            mark = QLabel(platform.mark)
            mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
            mark.setFixedSize(32,25)
            icon = QIcon(str(Path(__file__).with_name('tt_music_icons')/f'{key}.ico'))
            if not icon.isNull():
                mark.setPixmap(icon.pixmap(24,24))
            else:
                mark.setStyleSheet(f'background:{platform.color};color:white;border-radius:6px;font-weight:700;')
            heading.addWidget(mark)
            name = QLabel(platform.name)
            name.setObjectName('musicPlatformName')
            heading.addWidget(name,1)
            self.button(heading,'官方下载',lambda _,url=platform.website:QDesktopServices.openUrl(QUrl(url)))
            diagnostics = QPushButton('诊断')
            diagnostics.setAccessibleName(platform.name+' 诊断')
            diagnostics.setStyleSheet('padding:4px 8px; border-radius:7px;')
            menu = QMenu(diagnostics)
            test = menu.addAction('检测媒体会话',lambda _checked=False,k=key: service.attach(k,test=True))
            control = menu.addAction('测试播放/暂停',lambda _checked=False,k=key: service.test_control(k))
            menu.addAction('查看诊断报告',lambda _checked=False,k=key: self.show_report(k))
            diagnostics.setMenu(menu)
            heading.addWidget(diagnostics)
            column.addLayout(heading)
            status = QLabel()
            status.setWordWrap(True)
            status.setTextFormat(Qt.TextFormat.PlainText)
            column.addWidget(status)
            row = QHBoxLayout()
            connect = self.button(row,'打开官方客户端',lambda _,k=key: service.launch_client(k))
            select = self.button(row,'设为当前',lambda _,k=key: service.attach(k,activate=True))
            disconnect = self.button(row,'断开绑定',lambda _,k=key: service.disconnect(k))
            row.addStretch()
            column.addLayout(row)
            self.cards[key]=(status,connect,test,select,disconnect,control)
            if key=='apple':
                self.apple_status = QLabel()
                self.apple_status.setWordWrap(True)
                self.apple_status.setTextFormat(Qt.TextFormat.PlainText)
                column.addWidget(self.apple_status)
                apple_row = QHBoxLayout()
                self.button(apple_row,'MusicKit 配置',self.set_token)
                self.apple_open = self.button(apple_row,'打开 MusicKit',lambda: service.apple.show(self.window()))
                self.apple_clear = self.button(apple_row,'清除令牌',self.clear_token)
                apple_row.addStretch()
                column.addLayout(apple_row)
                account = QHBoxLayout()
                self.apple_test = self.button(account,'验证授权',lambda: service.apple_command('test'))
                self.apple_select = self.button(account,'使用 MusicKit',lambda: service.switch_to('musickit'))
                self.apple_disconnect = self.button(account,'断开授权',lambda: service.apple_command('disconnect'))
                account.addStretch()
                column.addLayout(account)
            body.addWidget(card)
        body.addStretch()
        service.feedback.connect(self.feedback.setText)
        service.changed.connect(self.refresh)
        self.apply_theme(LIGHT)
        self.refresh()

    def apply_theme(self, palette):
        light = palette is LIGHT
        current, feedback, name, card, border = (
            ('#543965', '#624970', '#493452', '#fffcff', '#e3d4ed') if light
            else (palette.text, palette.muted, palette.text, palette.card, palette.border)
        )
        self.setStyleSheet(f'''
            QLabel#musicPlatformCurrent {{font-size:19px;font-weight:600;color:{current};}}
            QLabel#musicPlatformFeedback {{color:{feedback};padding:3px 0;}}
            QLabel#musicPlatformName {{font-size:15px;font-weight:600;color:{name};}}
            QFrame#musicPlatformCard {{background:{card};border:1px solid {border};border-radius:11px;}}
        ''')

    @staticmethod
    def button(row,text,callback):
        widget = QPushButton(text)
        widget.setStyleSheet('padding:4px 8px; border-radius:7px;')
        widget.clicked.connect(callback)
        row.addWidget(widget)
        return widget

    def set_token(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('Apple Music · 开发者配置')
        dialog.setMinimumWidth(520)
        body = QVBoxLayout(dialog)
        form = QFormLayout()
        team, key, path, token = (QLineEdit(dialog) for _ in range(4))
        token.setEchoMode(QLineEdit.EchoMode.Password)
        claims = self.service.apple.developer_details()
        team.setText(claims.get('team',''))
        key.setText(claims.get('key',''))
        picker = QHBoxLayout()
        picker.addWidget(path,1)
        def pick():
            file, _ = QFileDialog.getOpenFileName(dialog,'选择 Apple Media Services 私钥','','Apple 私钥 (*.p8)')
            if file:
                path.setText(file)
        self.button(picker,'选择文件',pick)
        for label, field in (('Team ID',team),('Key ID',key),('MusicKit 私钥',picker),('Developer Token',token)):
            form.addRow(label,field)
        body.addLayout(form)
        status = QLabel()
        status.setTextFormat(Qt.TextFormat.PlainText)
        status.setWordWrap(True)
        body.addWidget(status)
        apple = self.service.apple
        def refresh_status():
            status.setText(apple.developer_status())
        apple.changed.connect(refresh_status)
        dialog.finished.connect(lambda _: apple.changed.disconnect(refresh_status))
        refresh_status()
        buttons = QHBoxLayout()
        def save_and_verify(signed=False):
            value = token.text().strip()
            if signed:
                value, error = apple.sign_developer(team.text().strip(),key.text().strip(),path.text().strip())
                if error:
                    status.setText(error)
                    return
            if value:
                problem = apple.save_developer(value)
                if problem:
                    status.setText(problem)
                    return
                token.clear()
                path.clear()
            apple.verify_developer()
        self.button(buttons,'本地签名并验证',lambda:save_and_verify(True))
        self.button(buttons,'验证 Developer Token',lambda:save_and_verify())
        self.button(buttons,'关闭',dialog.accept)
        body.addLayout(buttons)
        dialog.exec()

    def show_report(self, key):
        dialog = QDialog(self)
        dialog.setWindowTitle(PLATFORMS[key].name+' · 会话诊断')
        dialog.resize(620,450)
        body = QVBoxLayout(dialog)
        details = QPlainTextEdit(dialog)
        details.setReadOnly(True)
        body.addWidget(details)
        def update():
            report = self.service.diagnostics(key)
            if key=='apple':
                report += '\n\nMusicKit Web：'+self.service.apple.developer_status()+'\n'+self.apple_status.text()
            details.setPlainText(report)
        self.service.changed.connect(update)
        dialog.finished.connect(lambda _:self.service.changed.disconnect(update))
        update()
        if key=='apple':
            self.service.poll(force_inventory=True,artwork=('apple',),completed=lambda:None)
        row = QHBoxLayout()
        self.button(row,'复制诊断报告',lambda:QApplication.clipboard().setText(details.toPlainText()))
        self.button(row,'关闭',dialog.accept)
        body.addLayout(row)
        dialog.exec()

    def clear_token(self):
        result = self.service.apple.clear_credentials()
        self.feedback.setText(result or '已从 Windows 凭据管理器清除 Developer Token。')

    def refresh(self):
        service = self.service
        active = service.active
        name = PLATFORMS[active].name if active in PLATFORMS else 'Apple Music · MusicKit' if active=='musickit' else '本地音频'
        self.current.setText('当前来源 · '+name)
        self.local.setEnabled((active!='local' or service.auto_select_apple) and not service.pending)
        for key,(label,connect,test,select,disconnect,control) in self.cards.items():
            state = service.state(key)
            available = '、'.join(COMMANDS[c] for c in COMMANDS if c in state.capabilities)
            relation = service.session_status(key)
            capabilities = '已检测到 '+available if available and state.online else '可用操作以会话检测为准'
            connection = '本地播放记录' if state.online and state.source == NETEASE_LOCAL_SOURCE else 'Windows SMTC'
            label.setText(f'{relation}  ·  {connection}\n账号授权由官方客户端管理  ·  {capabilities}')
            connect.setEnabled(not service.pending)
            test.setEnabled(not service.pending)
            select.setVisible(key!=active)
            select.setEnabled(not service.pending and key!=active
                              and (key in WAITING_CLIENTS or state.online and bool(state.capabilities)))
            disconnect.setEnabled(key in service.bindings and not service.pending)
            action = 'pause' if state.status=='Playing' else 'play'
            control.setEnabled(not service.pending and state.online and bool(state.title)
                               and (action in state.capabilities or 'toggle' in state.capabilities))
        apple=service.apple
        status = ('账户已授权' if apple.authorized else '已保存授权 · 待验证' if apple.user else '待用户授权' if apple.developer_ready else '缺少有效的 Developer Token')
        if apple.error:
            status += ' · '+apple.error
        if apple.sdk_ready:
            status += ' · JS 已加载'
        if apple.configured:
            status += ' · SDK 已初始化'
        if apple.authorized:
            status += ' · '+('SDK 确认订阅' if apple.subscribable is True else
                              '未检测到订阅' if apple.subscribable is False else '订阅待播放验证')
        self.apple_status.setText('MusicKit Web  ·  '+status)
        self.apple_clear.setEnabled(bool(apple.developer) and not (apple.authorized or apple.user))
        self.apple_test.setEnabled(apple.configured)
        self.apple_select.setEnabled(apple.authorized and active!='musickit')
        self.apple_disconnect.setEnabled(apple.authorized or bool(apple.user))
