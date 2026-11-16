"""One animation authority, multiple viewports. Transfer state, never screen pixels."""
import json
import uuid

from PySide6.QtCore import QObject, QTimer, QUrlQuery, QEvent


class CharacterStateManager(QObject):
    def __init__(self, pet, viewport):
        super().__init__(pet)
        self.pet, self.viewport = pet, viewport
        self.source = None
        self.source_url = ''
        self.channel = ''
        self.active = False
        self.closed = False
        self.pulse_pending = False
        self.pulse = QTimer(self, interval=42)
        self.pulse.timeout.connect(self.advance_hidden)
        pet.renderer.ready.connect(self.source_ready)
        pet.installEventFilter(self)

    def eventFilter(self, watched, event):
        if watched is self.pet and event.type() in (QEvent.Type.Hide, QEvent.Type.Show):
            # Qt propagates widget hiding to the page after the event handler.
            QTimer.singleShot(0, self.refresh)
        return super().eventFilter(watched, event)

    def source_ready(self, _state=None):
        if self.closed:
            return
        source = self.pet.renderer.view
        if source is None:
            return
        if source is not self.source:
            self.stop_publishing()
            self.source = source
            self.source_url = source.url().toString()
            self.channel = 'tt-character-' + uuid.uuid4().hex
        self.refresh()

    def stop_publishing(self):
        import shiboken6
        if self.source is not None and shiboken6.isValid(self.source):
            self.source.page().runJavaScript('window.sdkProbe && sdkProbe.setCharacterPublisher(null)')

    def refresh(self):
        if self.closed:
            return
        self.active = self.viewport.presentation_visible()
        if self.active and not self.pet.isVisible():
            self.pulse.start()
        else:
            self.pulse.stop()
        if self.source is None:
            return
        if not self.active:
            self.stop_publishing()
            self.viewport.suspend()
            return
        url = self.source.url()
        query = QUrlQuery(url)
        query.addQueryItem('portrait', '1')
        query.addQueryItem('stateChannel', self.channel)
        query.removeAllQueryItems('verify')
        url.setQuery(query)
        self.viewport.load_source(url, self.pet.renderer.profile)
        self.source.page().setVisible(True)
        self.source.page().runJavaScript('window.sdkProbe && sdkProbe.setCharacterPublisher(' + json.dumps(self.channel) + ')')
        self.viewport.resume()

    def advance_hidden(self):
        # Chromium suspends rAF on a hidden native surface, even with an active page.
        # Continue the same authority clock at 24 Hz only while the chat needs it.
        if self.closed or not self.source or self.pulse_pending:
            return
        self.pulse_pending = True
        def done(_):
            self.pulse_pending = False
        self.source.page().runJavaScript('window.sdkProbe && sdkProbe.advanceHidden()', done)

    def close(self):
        if self.closed:
            return
        self.stop_publishing()
        self.pulse.stop()
        self.closed = True

