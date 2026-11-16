"""Apple's official MusicKit Web in an isolated in-memory Qt browser profile."""
from datetime import datetime
import json
from pathlib import Path
import re
import time
import weakref
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import jwt
from keyring.backends.Windows import WinVaultKeyring
from keyring.errors import PasswordDeleteError
from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PySide6.QtWidgets import QDialog, QVBoxLayout

from tt_music_services import MusicState


SERVICE = 'TT.DesktopPet.AppleMusic'
ORIGIN = 'http://127.0.0.1:4318'


def developer_claims(value):
    if not value:
        return {}, '尚未配置 Developer Token。'
    if len(value)>4000 or not re.fullmatch(r'[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', value):
        return {}, 'Developer Token 的 JWT 格式无效。'
    try:
        header = jwt.get_unverified_header(value)
        claims = jwt.decode(value, options={'verify_signature':False})
        if header.get('alg')!='ES256' or not re.fullmatch(r'[A-Z0-9]{10}',header.get('kid','')) or not re.fullmatch(r'[A-Z0-9]{10}',claims.get('iss','')):
            return {}, '需要 ES256 和有效格式的 Team ID、Key ID。'
        iat, exp = int(claims['iat']), int(claims['exp'])
        details = {'team':claims['iss'],'key':header['kid'],'expires':exp}
        if exp <= time.time():
            return details, 'Developer Token 已过期。'
        if exp<=iat or exp-iat>15777000 or iat>time.time()+60:
            return details, 'Developer Token 的签发时间或有效期无效。'
        if 'origin' in claims and (not isinstance(claims['origin'],list) or ORIGIN not in claims['origin']):
            return details, 'Developer Token 的来源列表不包含 '+ORIGIN+'。'
        return details, ''
    except (jwt.InvalidTokenError, ValueError, TypeError, KeyError, OverflowError):
        return {}, 'Developer Token 的声明无效。'


class MusicKitHandler(BaseHTTPRequestHandler):
    pages = {'/':('tt_musickit.html','text/html'), '/apple.js':('tt_musickit.js','text/javascript')}

    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.headers.get('Host') != '127.0.0.1:4318' or self.path not in self.pages:
            self.send_error(404)
            return
        name, content_type = self.pages[self.path]
        data = Path(__file__).with_name(name).read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', content_type+'; charset=utf-8')
        self.send_header('Referrer-Policy', 'strict-origin')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'self' https://*.apple.com; "
                         "style-src 'unsafe-inline'; connect-src https://*.apple.com https://*.icloud.com https://*.mzstatic.com; "
                         "img-src https://*.mzstatic.com data:; frame-src https://*.apple.com https://*.icloud.com; "
                         "media-src https://*.apple.com https://*.mzstatic.com blob:; worker-src blob: 'self'; frame-ancestors 'none'")
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def apple_host(url):
    host = url.host().lower()
    return url.scheme()=='https' and any(host == domain or host.endswith('.'+domain)
                                        for domain in ('apple.com','icloud.com','mzstatic.com'))


class AppleMusicProvider(QObject):
    changed = Signal()
    feedback = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.vault = WinVaultKeyring()
        self.developer = ''
        self.user = ''
        self.vault_ok = True
        try:
            self.developer = self.vault.get_password(SERVICE, 'developer') or ''
            self.user = self.vault.get_password(SERVICE, 'user') or ''
        except Exception:
            self.vault_ok = False
        self.state = MusicState(source='MusicKit')
        self.authorized = False
        self.active = False
        self.configured = False
        self.error = ''
        self.developer_check = ''
        self.subscribable = None
        self.sdk_ready = False
        self.net = QNetworkAccessManager(self)
        self.developer_reply = None
        self.volume = None
        self.art_url = ''
        self.window = None
        self.server = None
        self.profile = None
        self.view = None
        self.theme = None
        self.popups = []
        self.revision = 0
        self.sequence = 0
        self.polling = False
        self.waiting = None
        self.waiting_action = None
        self.queued_volume = None
        self.expire = QTimer(self, singleShot=True, interval=12000)
        self.expire.timeout.connect(self.command_timeout)
        self.timer = QTimer(self, interval=550)
        self.timer.timeout.connect(self.poll)

    @property
    def developer_ready(self):
        return not developer_claims(self.developer)[1]

    def developer_details(self):
        return developer_claims(self.developer)[0]

    def developer_status(self):
        details, error = developer_claims(self.developer)
        if not self.vault_ok:
            return 'Windows 凭据管理器不可用。'
        if error:
            return error
        expires = datetime.fromtimestamp(details['expires']).astimezone().isoformat(timespec='minutes')
        return '到期：'+expires+' · '+(self.developer_check or '本地格式检查通过，等待 Apple API 验证。')

    @staticmethod
    def sign_developer(team, key, filename):
        if not (re.fullmatch(r'[A-Z0-9]{10}',team) and re.fullmatch(r'[A-Z0-9]{10}',key)):
            return '', 'Team ID 和 Key ID 需各为 10 位大写字母或数字。'
        if not filename or Path(filename).suffix.lower()!='.p8':
            return '', '请选择 Apple Media Services 的 .p8 私钥。'
        try:
            from cryptography.hazmat.primitives.asymmetric import ec
            from cryptography.hazmat.primitives.serialization import load_pem_private_key
            with Path(filename).open('rb') as file:
                private = load_pem_private_key(file.read(16384),password=None)
            if not isinstance(private,ec.EllipticCurvePrivateKey) or not isinstance(private.curve,ec.SECP256R1):
                return '', 'Apple Developer Token 需要 P-256 私钥。'
            issued = int(time.time())
            return jwt.encode({'iss':team,'iat':issued,'exp':issued+30*86400,'origin':[ORIGIN]},private,
                              algorithm='ES256',headers={'kid':key}), ''
        except (ValueError, TypeError, OSError, ImportError, jwt.PyJWTError):
            return '', '无法读取或签署 Apple 私钥。请检查文件与格式。'

    def verify_developer(self):
        if self.developer_reply:
            return
        if not self.developer_ready:
            self.changed.emit()
            return
        request = QNetworkRequest(QUrl('https://api.music.apple.com/v1/storefronts?limit=1'))
        request.setRawHeader(b'Authorization',('Bearer '+self.developer).encode('ascii'))
        request.setRawHeader(b'Origin',ORIGIN.encode('ascii'))
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
        request.setTransferTimeout(9000)
        self.developer_check = '正在由 Apple Music API 验证…'
        reply = self.net.get(request)
        self.developer_reply = reply
        def checked():
            if self.developer_reply is reply:
                self.developer_reply = None
                code = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
                self.developer_check = {200:'Apple Music API 验证通过。',401:'Apple 拒绝令牌，请核对团队、Key ID、签名和有效期。',
                    403:'Apple 拒绝访问，请检查 Media Services 权限和来源。',429:'Apple 请求限流，请稍后重试。'}.get(
                        code, f'Apple API 返回 HTTP {code}。' if code else '无法连接 Apple Music API，请检查网络后重试。')
                self.changed.emit()
            reply.deleteLater()
        reply.finished.connect(checked)
        self.changed.emit()

    def save_developer(self, token):
        if not self.vault_ok:
            return 'Windows 凭据管理器不可用。'
        _, error = developer_claims(token)
        if error:
            return error
        if self.authorized or self.user:
            options = {'verify_signature':False}
            try:
                old_claims = jwt.decode(self.developer, options=options)
                new_claims = jwt.decode(token, options=options)
            except jwt.InvalidTokenError:
                return '请先清除旧的 Apple 授权，然后设置开发者令牌。'
            if old_claims.get('iss') != new_claims['iss']:
                return '更换 Apple 开发者团队前，请先断开当前 Apple 授权。'
        try:
            self.vault.set_password(SERVICE,'developer',token)
        except Exception:
            return '无法写入 Windows 凭据管理器。'
        self.developer = token
        self.reset_developer_check()
        self.error = ''
        if self.view:
            self.configured = False
            self.authorized = False
            self.view.reload()
        self.changed.emit()
        return ''

    def reset_developer_check(self):
        if self.developer_reply:
            reply, self.developer_reply = self.developer_reply, None
            reply.abort()
        self.developer_check = ''

    def store_user(self, token):
        try:
            if token:
                self.vault.set_password(SERVICE,'user',token)
            else:
                try:
                    self.vault.delete_password(SERVICE,'user')
                except PasswordDeleteError:
                    if self.vault.get_password(SERVICE,'user'):
                        raise
        except Exception:
            self.feedback.emit('无法更新 Windows 凭据管理器中的 Apple 授权。')
            return False
        self.user = token
        return True

    def clear_credentials(self):
        if self.user or self.authorized:
            return '请先断开 Apple 账户授权。'
        try:
            if self.developer:
                self.vault.delete_password(SERVICE,'developer')
        except Exception:
            return '未能从 Windows 凭据管理器清除 Developer Token。'
        self.developer = ''
        self.reset_developer_check()
        self.configured = False
        self.error = ''
        if self.view:
            self.window.hide()
            self.view.reload()
        self.changed.emit()
        return ''

    def show(self, parent):
        if not self.developer_ready:
            self.feedback.emit('可检查 MusicKit SDK 加载。授权前请配置 Developer Token。')
        if not self.view and not self.initialize(parent):
            return
        self.window.show()
        self.window.raise_()

    def initialize(self, parent):
        from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings, QWebEngineUrlRequestInterceptor
        from PySide6.QtWebEngineWidgets import QWebEngineView

        class Access(QWebEngineUrlRequestInterceptor):
            def interceptRequest(self, info):
                url = info.requestUrl()
                local = url.scheme()=='http' and url.host()=='127.0.0.1' and url.port()==4318
                if not (local or apple_host(url) or url.scheme() in ('blob','data','about')):
                    info.block(True)

        class ApplePage(QWebEnginePage):
            def javaScriptConsoleMessage(self, *_):
                pass

            def acceptNavigationRequest(self, url, _kind, main):
                if not main or url.scheme()=='about':
                    return True
                return apple_host(url) if self.property('authPopup') else url.toString() in (ORIGIN, ORIGIN+'/')

        try:
            self.server = ThreadingHTTPServer(('127.0.0.1',4318), MusicKitHandler)
            self.server.daemon_threads = True
        except OSError:
            self.feedback.emit('无法启动本机 MusicKit 页面。请检查 4318 端口是否已被占用。')
            return False
        Thread(target=self.server.serve_forever, kwargs={'poll_interval':.1}, daemon=True).start()
        self.profile = QWebEngineProfile(self)
        self.profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
        self.interceptor = Access(self.profile)
        self.profile.setUrlRequestInterceptor(self.interceptor)
        self.window = QDialog(parent)
        self.window.setWindowTitle('Apple Music · TT')
        self.window.resize(610,470)
        layout = QVBoxLayout(self.window)
        self.view = QWebEngineView(self.window)
        layout.addWidget(self.view)
        page = ApplePage(self.profile,self.view)
        self.view.setPage(page)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        page.settings().setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows,True)
        page.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls,False)

        owner = weakref.ref(self)
        def open_window(request):
            current = owner()
            if current is None or not request.isUserInitiated():
                return
            popup = QDialog(current.window)
            popup.setWindowTitle('Apple 官方账户授权')
            popup.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            popup.resize(545,660)
            row = QVBoxLayout(popup)
            view = QWebEngineView(popup)
            popup_page = ApplePage(current.profile,view)
            popup_page.setProperty('authPopup',True)
            view.setPage(popup_page)
            row.addWidget(view)
            current.popups.append(popup)
            popup.finished.connect(lambda _: current.popups.remove(popup) if popup in current.popups else None)
            popup_page.windowCloseRequested.connect(popup.close)
            request.openIn(popup_page)
            popup.show()
        page.newWindowRequested.connect(open_window)
        self.view.loadFinished.connect(self.loaded)
        self.theme = getattr(parent, 'theme', None)
        if self.theme:
            self.theme.changed.connect(self.apply_theme)
            self.apply_theme()
        self.view.load(QUrl(ORIGIN+'/'))
        self.timer.start()
        return True

    def apply_theme(self, *_):
        if not self.view or not self.theme:
            return
        from PySide6.QtGui import QColor

        palette = self.theme.palette
        self.view.page().setBackgroundColor(QColor(palette.surface if self.theme.dark else '#faf5fd'))
        css = palette.css('''
            :root { color-scheme: dark; }
            body { background: $surface; color: $text; }
            button, input, select { background: $input; color: $text; border-color: $border; }
            a { color: $accent; }
        ''') if self.theme.dark else ''
        self.view.page().runJavaScript('''(() => {
            let sheet = document.getElementById('tt-appearance');
            if (!sheet) {
                sheet = document.createElement('style');
                sheet.id = 'tt-appearance';
                document.head.appendChild(sheet);
            }
            sheet.textContent = %s;
        })()''' % json.dumps(css))

    def loaded(self, ok):
        if ok:
            self.apply_theme()
            self.revision = 0
            if self.developer_ready:
                self.view.page().runJavaScript('window.ttConfigure && ttConfigure('+json.dumps(self.developer)+','+json.dumps(self.user)+')')
            else:
                self.view.page().runJavaScript("window.ttMusicState && say('请先在 TT 中配置 Developer Token。')")
            self.set_active(self.active)
        else:
            self.feedback.emit('MusicKit 本机页面加载失败。')

    def set_active(self, active):
        if not active:
            self.queued_volume = None
        self.active = active
        if self.view:
            self.view.page().runJavaScript(f'window.ttSetActive && ttSetActive({json.dumps(active)})')

    def poll(self):
        if not self.view or self.polling:
            return
        if self.authorized and not self.developer_ready:
            self.authorized = False
            self.configured = False
            self.state = MusicState(source='MusicKit')
            self.error = 'Developer Token 已过期，请更新令牌。'
            self.changed.emit()
        if not self.active and not self.window.isVisible() and self.waiting is None:
            return
        self.polling = True
        self.view.page().runJavaScript(f'JSON.stringify(window.ttMusicState && ttMusicState({self.revision}))', self.received)

    def received(self, raw):
        self.polling = False
        try:
            packet = json.loads(raw or 'null')
        except (ValueError,TypeError):
            return
        if not packet:
            return
        self.configured = packet['configured']
        self.sdk_ready = packet.get('sdkReady',False)
        self.subscribable = packet.get('subscription')
        self.authorized = packet['authorized']
        self.error = packet['error']
        if not self.developer_ready:
            self.configured = False
            self.authorized = False
            self.changed.emit()
            return
        if packet['revision'] != self.revision:
            self.revision = packet['revision']
            self.store_user(packet.get('userToken') or '')
        value = packet['state']
        self.volume = value.pop('volume',None)
        self.art_url = value.pop('artUrl','')
        capabilities = frozenset(k for k,v in value.pop('capabilities').items() if v)
        self.state = MusicState(source='MusicKit',online=self.authorized,capabilities=capabilities,**value)
        received = packet.get('answer')
        if self.waiting and received and received['id']==self.waiting[0]:
            _, callback = self.waiting
            self.waiting = None
            self.waiting_action = None
            self.expire.stop()
            callback(received['ok'],received.get('error',''))
            self.flush_volume()
        self.changed.emit()

    def control(self, action, callback, **parameters):
        if not self.configured or not self.view:
            callback(False,self.error or '请先打开 MusicKit 并完成 Apple 授权。')
            return True
        if self.waiting:
            if action=='volume':
                self.queued_volume = (callback,parameters['position'])
                return True
            return False
        self.sequence += 1
        self.waiting = (self.sequence,callback)
        self.waiting_action = action
        self.expire.start()
        self.view.page().runJavaScript('ttMusicCommand('+json.dumps(self.sequence)+','+json.dumps(action)+','+
                                       json.dumps(parameters.get('position',0))+')')
        return True

    def command_timeout(self):
        if self.waiting:
            _, callback = self.waiting
            self.waiting = None
            self.waiting_action = None
            callback(False,'MusicKit 操作超时，请检查网络后重试。')
            self.flush_volume()
            self.changed.emit()

    @property
    def volume_pending(self):
        return self.waiting_action=='volume' or self.queued_volume is not None

    def flush_volume(self):
        request, self.queued_volume = self.queued_volume, None
        if request and self.active and 'volume' in self.state.capabilities:
            callback, value = request
            self.control('volume',callback,position=value)

    def disconnect(self, callback):
        if self.configured and self.authorized:
            return self.control('disconnect',callback)
        if self.store_user(''):
            callback(True,'已清除 TT 保存的 Apple 授权。')
        else:
            callback(False,'未能清除 Apple 授权。')
        return True

    def close(self):
        self.timer.stop()
        self.expire.stop()
        self.waiting = None
        for popup in tuple(self.popups):
            popup.close()
        if self.window:
            self.window.close()
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
