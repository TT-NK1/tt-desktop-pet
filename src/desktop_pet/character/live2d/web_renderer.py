"""Official Cubism Web SDK adapter. All served resources stay in the project."""
import functools
import json
from pathlib import Path
from urllib.parse import unquote, urlencode, urlsplit
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from PySide6.QtCore import QObject, QTimer, QUrl, Signal, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QColor
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineUrlRequestInterceptor, QWebEngineScript
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QStackedLayout, QWidget, QGraphicsOpacityEffect, QLabel

from .model_manager import inspect_model


class LocalOnly(QWebEngineUrlRequestInterceptor):
    def __init__(self, port, parent):
        super().__init__(parent)
        self.port = port

    def interceptRequest(self, info):
        url = info.requestUrl()
        if url.scheme() in ('data', 'blob', 'about'):
            return
        if not (url.scheme() == 'http' and url.host() == '127.0.0.1' and url.port() == self.port):
            info.block(True)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,models,**kwargs):
        self.models = models
        super().__init__(*args,**kwargs)

    def log_message(self, *args):
        pass

    def list_directory(self, path):
        self.send_error(403)
        return None

    def send_head(self):
        try:
            return super().send_head()
        except ValueError:
            self.send_error(404)
            return None

    def translate_path(self, url):
        # Keep the SDK's existing /Resources/<model>/ URLs while serving
        # selected models from their validated source directories.
        route = unquote(urlsplit(url).path,errors='strict').lstrip('/').split('/')
        if len(route)>=2 and route[0]=='Resources' and route[1] in self.models:
            folder = self.models[route[1]]
            parts = route[2:]
            if not parts or any(part in ('','.','..') or any(c in part for c in '\\:\x00')
                                for part in parts):
                raise ModelRouteError
            try:
                path = folder.joinpath(*parts).resolve()
            except (OSError,RuntimeError) as error:
                raise ModelRouteError from error
            if not path.is_relative_to(folder) or not path.is_file():
                raise ModelRouteError
            return str(path)
        return super().translate_path(url)


class ModelRouteError(ValueError):
    pass


class WebRenderer(QObject):
    ready = Signal(dict)
    failed = Signal(str)
    motion_finished = Signal()
    motion_failed = Signal(str, str)
    closed = Signal(bool)

    def __init__(self, root, parent=None, verify=False):
        super().__init__(parent)
        self.root = Path(root)
        self.runtime = self.root / 'runtime/live2d/desktop'
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.dist = self.root / 'vendor/CubismSdkForWeb-5-r.3/Samples/TypeScript/Demo/dist-desktop'
        if not (self.dist / 'index.html').is_file():
            raise RuntimeError('缺少桌面渲染构建，请先运行 tools/build_live2d_desktop.ps1')
        self.model_directories = {}
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(
            Handler,directory=str(self.dist),models=self.model_directories))
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.profile = QWebEngineProfile(self)
        self.profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
        self.interceptor = LocalOnly(self.server.server_port, self.profile)
        self.profile.setUrlRequestInterceptor(self.interceptor)
        self.widget = QWidget(parent)
        self.widget.setStyleSheet('background:transparent')
        self.layout = QStackedLayout(self.widget)
        # Keep a loading candidate visible behind the current model so its
        # requestAnimationFrame loop can initialize before the crossfade.
        self.layout.setStackingMode(QStackedLayout.StackingMode.StackAll)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.view = None
        self.candidate = None
        self.retiring_view = None
        self.snapshot = {}
        self.ranges = {}
        self.bound = set()
        self.verify = verify
        self.closing = False
        self.pending = False
        self.fps = 24
        self.last_finished = 0
        self.model_path = None
        self.candidate_path = None
        self.outfit_token = 0
        self.pending_variant = None
        self.last_action_error = None
        self.poll = QTimer(self)
        self.poll.setInterval(500)
        self.poll.timeout.connect(self._poll)
        self.poll.start()

    def select_variant(self, path, variant):
        if (self.view is None or self.closing or self.candidate is not None
                or self.model_path != path or path.name != 'street_neck_rebuild_v20.model3.json'):
            return False
        self.outfit_token += 1
        token = self.outfit_token
        self.pending_variant = token
        self.poll.setInterval(32)

        def sent(accepted):
            if self.pending_variant != token or self.closing:
                return
            if not accepted:
                self.finish_variant()
                self.failed.emit('此模型无法切换服装，已保留当前服装。')
            else:
                self._poll()

        self.command(f'sdkProbe.selectOutfit({json.dumps(variant)},{token})', sent)
        QTimer.singleShot(12000, lambda: self.variant_timeout(token))
        return True

    def finish_variant(self):
        self.pending_variant = None
        self.poll.setInterval(120)

    def variant_timeout(self, token):
        if self.closing or self.pending_variant != token:
            return
        self.command(f'sdkProbe.cancelOutfit({token})')
        self.finish_variant()
        self.failed.emit('服装加载超时，已保留当前服装。')

    def load_model(self, path, *, body_variant=None):
        if self.candidate or self.retiring_view or self.closing:
            raise RuntimeError('模型正在加载或关闭')
        path = Path(path).resolve()
        if not path.is_relative_to(self.root / 'assets/character'):
            raise ValueError('只允许加载角色目录内的模型')
        inspect_model(path)
        name = path.name.removesuffix('.model3.json')
        if not name.replace('_', '').isalnum():
            raise ValueError('模型名必须为字母、数字或下划线')
        self.model_directories[name] = path.parent
        view = QWebEngineView(self.widget)
        page = QWebEnginePage(self.profile, view)
        page.setBackgroundColor(QColor(0, 0, 0, 0))
        # Opacity does not suspend requestAnimationFrame. A new outfit can
        # initialize without leaking through the transparent old outfit.
        conceal = QWebEngineScript()
        conceal.setName('outfit-preload-conceal')
        conceal.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
        conceal.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        conceal.setSourceCode("document.documentElement.style.opacity='0';")
        page.scripts().insert(conceal)
        view.setPage(page)
        view.setStyleSheet('background:transparent')
        self.layout.addWidget(view)
        self.candidate = view
        self.candidate_path = path
        self.poll.setInterval(46)
        self.attempts = 0
        params = {'desktop': 1, 'personal': 1, 'model': name}
        if body_variant is not None:
            params['outfit'] = body_variant
        if self.verify:
            params['verify'] = 1
        query = '?' + urlencode(params)
        entry = f'index-{name}.html' if name in {'sweater', 'sports', 'shirt', 'winter'} else ''
        page.load(QUrl(f'http://127.0.0.1:{self.server.server_port}/{entry}{query}'))

    def _poll(self):
        if self.closing or self.pending:
            return
        target = self.candidate or self.view
        if target is None:
            return
        self.pending = True
        target.page().runJavaScript('JSON.stringify(window.sdkProbe ? sdkProbe.snapshot() : null)',
                                    lambda raw, target=target: self._received(target, raw))

    def _received(self, target, raw):
        self.pending = False
        if self.closing:
            return
        try:
            state = json.loads(raw or 'null') or {}
        except (ValueError, TypeError):
            state = {}
        if target is self.candidate:
            self.attempts += 1
            if state.get('ready') and state.get('drawableCount', 0) > 0 and not state.get('errors'):
                previous = self.view
                self.view = self.candidate
                self.candidate = None
                self.model_path = self.candidate_path
                self.poll.setInterval(120)
                self.layout.setCurrentWidget(self.view)
                duration = 460 if previous else 180
                reveal = "document.documentElement.style.transition='opacity %dms ease-in-out'; getComputedStyle(document.documentElement).opacity; document.documentElement.style.opacity='1';" % duration
                self.view.page().runJavaScript(reveal)
                if previous:
                    self.retiring_view = previous
                    previous.page().runJavaScript(
                        "document.documentElement.style.transition='opacity 460ms ease-in-out 100ms'; document.documentElement.style.opacity='0';")
                    QTimer.singleShot(650, lambda previous=previous: self._finish_outfit_transition(previous))
                self.ranges = {k: tuple(v[:3]) for k, v in state['parameters'].items()}
                self.bound = {x['id'] for x in state.get('bindingAudit', []) if x['bound']}
                self.last_finished = state.get('motionFinished', 0)
                self.snapshot = state
                self.set_frame_rate(self.fps, force=True)
                self.ready.emit(state)
            elif self.attempts > 500 or state.get('errors'):
                self.candidate = None
                self._delete_view(target)
                self.failed.emit('模型加载失败，保留上一个模型。' + str(state.get('errors', '超时')))
        elif target is self.view:
            self.snapshot = state
            token = self.pending_variant
            if token is not None:
                error = state.get('switchError') or {}
                if error.get('token') == token:
                    self.finish_variant()
                    self.failed.emit(error.get('message', '服装加载失败'))
                elif state.get('completedOutfit') == token:
                    self.finish_variant()
                    self.last_finished = state.get('motionFinished', 0)
                    self.ready.emit(state)
                    return
            error = state.get('actionError')
            if error and error != self.last_action_error:
                self.motion_failed.emit(error['id'], error['message'])
            self.last_action_error = error
            count = state.get('motionFinished', 0)
            if count > self.last_finished:
                self.last_finished = count
                self.motion_finished.emit()

    def _finish_outfit_transition(self, previous):
        if self.closing:
            return
        previous.page().runJavaScript('window.sdkProbe && sdkProbe.dispose()',
                                      lambda _: self._retire_view(previous))

    def _retire_view(self, previous):
        if self.closing:
            return
        self._delete_view(previous)
        if self.retiring_view is previous:
            self.retiring_view = None

    def _delete_view(self, view):
        self.layout.removeWidget(view)
        view.page().deleteLater()
        view.deleteLater()

    def command(self, text, callback=None):
        if self.view and not self.closing:
            self.view.page().runJavaScript('window.sdkProbe && (' + text + ')', callback or (lambda _: None))

    def parameter_ranges(self):
        return dict(self.ranges)

    def set_parameters(self, values):
        safe = {k: max(self.ranges[k][0], min(self.ranges[k][1], float(v)))
                for k, v in values.items() if k in self.bound}
        self.command('sdkProbe.parameters(' + json.dumps(safe, allow_nan=False) + ')')

    def play_motion(self, group, index=0, fade_seconds=.35, *, auto_lead=None):
        args = f'{json.dumps(group)},{int(index)}'
        if auto_lead is not None:
            args += ',' + json.dumps(auto_lead, allow_nan=False)
        self.command(f'sdkProbe.play({args})')

    def set_expression(self, name):
        self.command('sdkProbe.namedExpression(' + json.dumps(name) + ')')

    def set_frame_rate(self, fps, force=False):
        value = max(0, min(60, int(fps)))
        if force or value != self.fps:
            self.fps = value
            self.command(f'sdkProbe.setFps({value})')

    def close(self):
        if self.closing:
            return
        self.closing = True
        self.poll.stop()
        self.close_ack = False
        views = [v for v in (self.view, self.candidate, self.retiring_view) if v is not None]
        remaining = [len(views)]
        def done(raw):
            remaining[0] -= 1
            if remaining[0] == 0:
                self.close_ack = True
                self.closed.emit(True)
        for view in views:
            view.page().runJavaScript('window.sdkProbe ? sdkProbe.dispose().disposed : true', done)
        if not views:
            self.close_ack = True
            self.closed.emit(True)
        QTimer.singleShot(2000, lambda: self.closed.emit(False) if not self.close_ack else None)

    def shutdown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        import shiboken6
        for view in (self.view, self.candidate, self.retiring_view):
            if view:
                page = view.page()
                view.setPage(None)
                shiboken6.delete(page)
        shiboken6.delete(self.profile)

