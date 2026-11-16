import base64
import hashlib
import json
import re
import secrets
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import jwt
import keyring

from PySide6.QtCore import QThread, Signal, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QLabel, QMessageBox,
    QPushButton, QVBoxLayout, QWidget
)


ISSUER = "https://auth.openai.com"
DISCOVERY = ISSUER + "/.well-known/openid-configuration"
RESOURCE = "https://api.openai.com/v1"
SERVICE = "AI-Desktop-Pet.ChatGPT"
SCOPES = (
    "openid profile email offline_access "
    "resource.invoke chatgpt.tokens.use.direct"
)


def require_windows_vault():
    backend = keyring.get_keyring()
    if (
        type(backend).__module__ != "keyring.backends.Windows"
        or type(backend).__name__ != "WinVaultKeyring"
    ):
        raise RuntimeError("Windows credential backend required")


def read_json(name):
    value = keyring.get_password(SERVICE, name)
    return json.loads(value) if value else None


def write_json(name, value):
    keyring.set_password(
        SERVICE, name,
        json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    )


def remove_key(name):
    try:
        keyring.delete_password(SERVICE, name)
    except keyring.errors.PasswordDeleteError:
        pass


def load_credentials():
    pointer = read_json("active")
    if not pointer:
        return None
    parts = []
    count = int(pointer["count"])
    if not 1 <= count <= 256:
        raise ValueError("Invalid credential record")
    for index in range(count):
        part = keyring.get_password(
            SERVICE, f'{pointer["generation"]}:{index}'
        )
        if part is None:
            raise ValueError("Incomplete credential record")
        parts.append(part)
    return json.loads("".join(parts))


def save_credentials(record):
    # 分块保存，避免超过 Windows 单条凭证的容量限制。
    payload = json.dumps(record, ensure_ascii=True, separators=(",", ":"))
    chunks = [payload[i:i + 800] for i in range(0, len(payload), 800)]
    if len(chunks) > 256:
        raise ValueError("Credential record too large")

    old = read_json("active")
    generation = uuid.uuid4().hex
    written = []

    try:
        for index, chunk in enumerate(chunks):
            name = f"{generation}:{index}"
            keyring.set_password(SERVICE, name, chunk)
            written.append(name)
        write_json("active", {
            "generation": generation,
            "count": len(chunks),
        })
    except Exception:
        for name in written:
            remove_key(name)
        raise

    if old:
        for index in range(int(old["count"])):
            try:
                remove_key(f'{old["generation"]}:{index}')
            except Exception:
                # 新凭证已完整保存，不把旧记录清理失败当作登录失败。
                pass


def clear_credentials():
    pointer = read_json("active")
    if pointer:
        for index in range(int(pointer["count"])):
            remove_key(f'{pointer["generation"]}:{index}')
        remove_key("active")
    # 保留安装标识和客户端注册信息，供同一账户重新登录。
    # 这不是撤销 OpenAI 账户中的应用授权。


def host_id():
    value = keyring.get_password(SERVICE, "host_id")
    if not value:
        value = "urn:uuid:" + str(uuid.uuid4())
        keyring.set_password(SERVICE, "host_id", value)
    return value


def challenge(verifier):
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def trusted_auth_url(value):
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "auth.openai.com"
        or parsed.port not in (None, 443)
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise ValueError("Unexpected authentication endpoint")
    return value


def verify_identity(token, jwks, client_id, nonce):
    header = jwt.get_unverified_header(token)
    algorithm = header.get("alg")
    if algorithm not in ("RS256", "ES256"):
        raise ValueError("Unsupported signature algorithm")

    candidates = [
        item for item in jwks["keys"]
        if item.get("kid") == header.get("kid")
        and item.get("use", "sig") == "sig"
        and item.get("alg", algorithm) == algorithm
    ]
    if len(candidates) != 1:
        raise ValueError("Signing key unavailable")

    public_key = jwt.PyJWK.from_dict(
        candidates[0], algorithm=algorithm
    ).key

    claims = jwt.decode(
        token,
        public_key,
        algorithms=[algorithm],
        audience=client_id,
        issuer=ISSUER,
        leeway=5,
        options={
            "require": ["iss", "aud", "sub", "exp", "iat", "nonce"]
        },
    )

    if not isinstance(claims["sub"], str) or not claims["sub"]:
        raise ValueError("Missing account identity")
    if not secrets.compare_digest(str(claims["nonce"]), nonce):
        raise ValueError("Nonce mismatch")
    audience = claims["aud"]
    if isinstance(audience, list) and len(audience) > 1:
        if claims.get("azp") != client_id:
            raise ValueError("Authorized party mismatch")
    return claims


class AuthWorker(QThread):
    browser = Signal(str)
    status = Signal(str)
    result = Signal(str)
    failure = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cancelled = threading.Event()
        self.stage = "准备登录"

    def check_cancelled(self):
        if self.cancelled.is_set():
            raise InterruptedError()

    def run(self):
        try:
            self.authenticate()
        except InterruptedError:
            self.failure.emit("已取消本次登录。")
        except httpx.HTTPStatusError as error:
            # 不输出请求网址、响应正文或授权凭证。
            self.failure.emit(
                f"{self.stage}失败：HTTP {error.response.status_code}。\n"
                "未切换到付费 API。请将这个状态码告诉我。"
            )
        except httpx.RequestError:
            self.failure.emit(
                f"{self.stage}失败：无法连接官方服务或连接超时。\n"
                "没有发送 AI 请求。"
            )
        except Exception as error:
            self.failure.emit(
                f"{self.stage}失败：{type(error).__name__}。\n"
                "为保护登录信息，未显示原始凭证或回调内容。"
            )

    def authenticate(self):
        require_windows_vault()
        registration = read_json("registration") or {}
        previous = load_credentials()
        client_id = registration.get("client_id", "dynamic_agent_client")
        new_registration = client_id == "dynamic_agent_client"
        installation = host_id()

        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        outcome = {}

        class Callback(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def setup(self):
                super().setup()
                self.connection.settimeout(3)

            def do_GET(self):
                parsed = urlsplit(self.path)
                if parsed.path != "/auth/callback":
                    self.send_error(404)
                    return
                try:
                    query = parse_qs(
                        parsed.query,
                        keep_blank_values=True,
                        max_num_fields=20,
                    )
                    if any(len(values) != 1 for values in query.values()):
                        raise ValueError()
                    returned = query.get("state", [""])[0]
                    if not secrets.compare_digest(returned, state):
                        raise ValueError()
                    if outcome:
                        raise ValueError()
                except ValueError:
                    self.send_error(400)
                    return

                outcome.update({
                    key: values[0] for key, values in query.items()
                })
                body = (
                    b"Authorization callback received. "
                    b"Return to the desktop app to see the result."
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except OSError:
                    pass

        with httpx.Client(timeout=20, follow_redirects=False) as client:
            self.stage = "读取官方登录配置"
            self.status.emit(self.stage)
            response = client.get(DISCOVERY)
            response.raise_for_status()
            metadata = response.json()
            if metadata.get("issuer") != ISSUER:
                raise ValueError("Issuer mismatch")

            authorize_url = trusted_auth_url(metadata["authorization_endpoint"])
            token_url = trusted_auth_url(metadata["token_endpoint"])
            jwks_url = trusted_auth_url(metadata["jwks_uri"])
            self.check_cancelled()

            with HTTPServer(("127.0.0.1", 0), Callback) as server:
                server.timeout = 0.5
                redirect = (
                    f"http://127.0.0.1:{server.server_port}/auth/callback"
                )
                parameters = {
                    "client_id": client_id,
                    "ext_agent_host_id": installation,
                    "response_type": "code",
                    "redirect_uri": redirect,
                    "scope": SCOPES,
                    "resource": RESOURCE,
                    "state": state,
                    "nonce": nonce,
                    "code_challenge_method": "S256",
                    "code_challenge": challenge(verifier),
                }
                if new_registration:
                    parameters["agent_name_hint"] = "Personal Windows Desktop Pet"
                elif previous and previous.get("id_token"):
                    parameters["id_token_hint"] = previous["id_token"]

                self.stage = "等待浏览器授权"
                self.status.emit(
                    "请在系统浏览器完成官方登录。\n"
                    "若不符合资格或不想授权，可取消。五分钟后自动超时。"
                )
                self.browser.emit(authorize_url + "?" + urlencode(parameters))
                deadline = time.monotonic() + 300

                while not outcome:
                    self.check_cancelled()
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Authorization timed out")
                    server.handle_request()

            self.check_cancelled()
            if outcome.get("error"):
                code = outcome["error"]
                safe_code = (
                    code if re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", code)
                    else "authorization_denied"
                )
                self.failure.emit(
                    f"官方授权未完成：{safe_code}\n"
                    "未发送 AI 请求，也未切换付费 API。"
                )
                return

            issued = outcome.get("client_id", client_id)
            if new_registration:
                if not issued or issued == "dynamic_agent_client":
                    raise ValueError("Missing issued client ID")
            elif issued != client_id:
                raise ValueError("Client registration mismatch")
            if not outcome.get("code"):
                raise ValueError("Missing authorization code")

            # 官方要求：先保留颁发的 client_id，再交换授权码。
            write_json("registration", {
                "client_id": issued,
                "subject": registration.get("subject"),
            })

            self.stage = "交换授权凭证"
            self.status.emit(self.stage)
            response = client.post(token_url, data={
                "grant_type": "authorization_code",
                "client_id": issued,
                "code": outcome["code"],
                "code_verifier": verifier,
                "redirect_uri": redirect,
                "resource": RESOURCE,
            })
            response.raise_for_status()
            tokens = response.json()
            self.check_cancelled()

            self.stage = "验证身份签名"
            self.status.emit(self.stage)
            response = client.get(jwks_url)
            response.raise_for_status()
            claims = verify_identity(
                tokens["id_token"], response.json(), issued, nonce
            )

            expected_subject = registration.get("subject")
            if expected_subject and claims["sub"] != expected_subject:
                raise ValueError("Account identity mismatch")

            if tokens.get("access_token"):
                if str(tokens.get("token_type", "")).lower() != "bearer":
                    raise ValueError("Unexpected token type")

            scope_value = tokens.get("scope", "")
            if not isinstance(scope_value, str):
                raise ValueError("Invalid granted scopes")
            granted = scope_value.split()
            allowed = (
                "chatgpt.tokens.use.direct" in granted
                and "resource.invoke" in granted
                and bool(tokens.get("access_token"))
            )

            record = {
                "client_id": issued,
                "subject": claims["sub"],
                "email": claims.get("email", ""),
                "ext_agent_host_id": installation,
                "id_token": tokens["id_token"],
                "access_token": tokens.get("access_token"),
                "refresh_token": tokens.get("refresh_token"),
                "token_type": tokens.get("token_type"),
                "scopes": granted,
                "expires_in": tokens.get("expires_in"),
                "saved_at": time.time(),
                "plan_permission_granted": allowed,
            }

            self.check_cancelled()
            self.stage = "保存到 Windows 凭证库"
            self.status.emit(self.stage)
            save_credentials(record)
            write_json("registration", {
                "client_id": issued,
                "subject": claims["sub"],
            })

            if allowed:
                self.result.emit(
                    "登录成功，身份验证通过。\n"
                    "已获得套餐使用权限，凭证已保存到 Windows 凭证库。\n"
                    "尚未调用模型；账户实际可用模型和额度待下一步验证。"
                )
            else:
                self.result.emit(
                    "身份验证通过，登录记录已保存。\n"
                    "但未获得完整的套餐调用权限，不能启用 AI 请求。\n"
                    "没有切换到付费 API。"
                )


class AuthWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.worker = None
        self.closing = False
        self.setWindowTitle("桌宠 · ChatGPT 官方授权")
        self.resize(560, 340)

        layout = QVBoxLayout(self)
        self.status = QLabel(
            "此窗口只测试官方授权，不发送聊天或模型请求。"
        )
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.consent = QCheckBox(
            "我同意为这个桌宠申请使用我的 ChatGPT 套餐额度"
        )
        layout.addWidget(self.consent)

        self.login = QPushButton("Continue with ChatGPT")
        self.login.setEnabled(False)
        self.login.clicked.connect(self.start_login)
        layout.addWidget(self.login)

        self.cancel = QPushButton("取消本次登录")
        self.cancel.setEnabled(False)
        self.cancel.clicked.connect(self.cancel_login)
        layout.addWidget(self.cancel)

        self.clear = QPushButton("清除本机保存的登录凭证")
        self.clear.clicked.connect(self.clear_login)
        layout.addWidget(self.clear)

        note = QLabel(
            "清除本机凭证不等于撤销官方账户授权。\n"
            "应用授权和 App limit 请在 ChatGPT 官方账户设置中管理。\n"
            "本窗口不读取或显示账户剩余额度。"
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        self.consent.toggled.connect(self.update_buttons)

        try:
            require_windows_vault()
            if load_credentials():
                self.status.setText(
                    "发现已保存的登录记录。\n"
                    "本次尚未联网验证有效期，也没有发送模型请求。"
                )
        except Exception as error:
            self.status.setText(
                f"凭证库检查失败：{type(error).__name__}"
            )

    def update_buttons(self):
        running = self.worker is not None and self.worker.isRunning()
        self.login.setEnabled(self.consent.isChecked() and not running)
        self.consent.setEnabled(not running)
        self.cancel.setEnabled(running)
        self.clear.setEnabled(not running)

    def open_browser(self, url):
        if not QDesktopServices.openUrl(QUrl(url)):
            self.cancel_login()
            self.status.setText("无法打开系统浏览器，已取消登录。")

    def start_login(self):
        if not self.consent.isChecked():
            return
        self.worker = AuthWorker(self)
        self.worker.browser.connect(self.open_browser)
        self.worker.status.connect(self.status.setText)
        self.worker.result.connect(self.status.setText)
        self.worker.failure.connect(self.status.setText)
        self.worker.finished.connect(self.finished)
        self.worker.start()
        self.update_buttons()

    def cancel_login(self):
        if self.worker:
            self.worker.cancelled.set()
            self.status.setText("正在取消，请等待当前网络操作结束。")

    def finished(self):
        self.update_buttons()
        if self.closing:
            self.close()

    def clear_login(self):
        answer = QMessageBox.question(
            self, "清除本机凭证",
            "只清除这个桌宠保存的登录令牌。\n"
            "不会退出浏览器，也不会撤销官方账户中的应用授权。\n"
            "确定清除吗？"
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            clear_credentials()
            self.status.setText("本机登录凭证已清除。")
        except Exception as error:
            self.status.setText(
                f"清除未完成：{type(error).__name__}"
            )

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.closing = True
            self.cancel_login()
            event.ignore()
        else:
            event.accept()


def self_test():
    # 不联网、不登录、不调用 AI。
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    expected = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    assert challenge(verifier) == expected

    for bad_url in (
        "http://auth.openai.com/test",
        "https://example.com/test",
        "https://auth.openai.com.example.com/test",
    ):
        try:
            trusted_auth_url(bad_url)
        except ValueError:
            pass
        else:
            raise AssertionError("Unsafe endpoint accepted")

    from cryptography.hazmat.primitives.asymmetric import rsa
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = json.loads(
        jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key())
    )
    public_jwk.update({"kid": "test-key", "use": "sig", "alg": "RS256"})
    claims = {
        "iss": ISSUER, "aud": "test-client", "sub": "test-subject",
        "iat": int(time.time()), "exp": int(time.time()) + 60,
        "nonce": "test-nonce",
    }
    token = jwt.encode(
        claims, private_key, algorithm="RS256", headers={"kid": "test-key"}
    )
    jwks = {"keys": [public_jwk]}
    verify_identity(token, jwks, "test-client", "test-nonce")

    for client_id, nonce in (
        ("wrong-client", "test-nonce"),
        ("test-client", "wrong-nonce"),
    ):
        try:
            verify_identity(token, jwks, client_id, nonce)
        except (ValueError, jwt.PyJWTError):
            pass
        else:
            raise AssertionError("Invalid identity accepted")

    require_windows_vault()
    test_name = "self-test-" + uuid.uuid4().hex
    try:
        keyring.set_password(SERVICE, test_name, "temporary-test-value")
        assert keyring.get_password(SERVICE, test_name) == "temporary-test-value"
    finally:
        remove_key(test_name)

    print("PASS: PKCE, endpoint checks, JWT signature, audience, nonce, Windows vault")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        self_test()
    else:
        app = QApplication(sys.argv)
        window = AuthWindow()
        window.show()
        sys.exit(app.exec())
