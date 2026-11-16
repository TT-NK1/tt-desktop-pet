"""App-scoped direct and Clash routes for TT on Windows."""

import atexit
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
from threading import Lock
import time


VERGE_FOLDER = 'io.github.clash-verge-rev.clash-verge-rev'
RUNTIME_FILES = ('clash-verge.yaml', 'clash.yaml')
DATA_FILES = ('cache.db', 'Country.mmdb', 'ASN.mmdb', 'geoip.dat', 'geosite.dat', 'geoip.metadb')
CONNECTION_PREFERENCE = 'network_connection'
AUTO = 'auto'
DIRECT = 'direct'


class RouteError(RuntimeError):
    pass


def config_directory():
    appdata = os.environ.get('APPDATA')
    return Path(appdata) / VERGE_FOLDER if appdata else None


def shared_port(directory):
    if not directory:
        return None
    for name, key in (('verge.yaml', 'verge_mixed_port'), ('clash-verge.yaml', 'mixed-port')):
        try:
            text = (directory / name).read_text(encoding='utf-8-sig')
        except (OSError, UnicodeError):
            continue
        found = re.search(rf'(?m)^{key}:[ \t]*(\d{{1,5}})[ \t]*(?:#.*)?$', text)
        if found and 1 <= int(found[1]) <= 65535:
            return int(found[1])
    return None


def listening(port):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=0.25):
            return True
    except OSError:
        return False


def core_executable():
    roots = (os.environ.get('ProgramW6432'), os.environ.get('ProgramFiles'))
    for root in roots:
        if root and (core := Path(root) / 'Clash Verge' / 'verge-mihomo.exe').is_file():
            return core
    return None


def private_config(source, directory, runtime, port, password):
    from tt_vendor import yaml

    try:
        raw = source.read_bytes()
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError('configuration too large')
        profile = yaml.safe_load(raw)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise RouteError('TT 无法读取 Clash 的运行配置。请在 Clash 里应用一次订阅后重试。') from exc
    if not isinstance(profile, dict) or not (profile.get('proxies') or profile.get('proxy-providers')):
        raise RouteError('Clash 的运行配置里没有节点。请先在 Clash 应用一个可用的订阅。')

    # Build a separate loopback-only configuration. Desktop listeners and routing
    # hooks from the shared instance must never be inherited.
    keys = ('proxies', 'proxy-groups', 'rules', 'mode', 'ipv6', 'hosts',
            'dns', 'proxy-providers', 'rule-providers', 'geodata-mode',
            'geodata-loader', 'geox-url', 'sniffer', 'unified-delay')
    config = {key: profile[key] for key in keys if key in profile}
    config.update({
        'mixed-port': port, 'allow-lan': False, 'bind-address': '127.0.0.1',
        'authentication': ['tt:' + password], 'skip-auth-prefixes': [],
        'external-controller': '', 'external-controller-pipe': '',
        'tun': {'enable': False}, 'log-level': 'error',
        'profile': {'store-selected': True}, 'geo-auto-update': False,
    })
    if isinstance(config.get('dns'), dict):
        config['dns'] = {**config['dns'], 'listen': ''}

    for name in DATA_FILES:
        origin = directory / name
        if origin.is_file():
            shutil.copyfile(origin, runtime / name)

    for kind in ('proxy-providers', 'rule-providers'):
        providers = config.get(kind)
        if not isinstance(providers, dict):
            continue
        for index, provider in enumerate(providers.values()):
            if not isinstance(provider, dict) or not isinstance(provider.get('path'), str):
                continue
            original = directory / provider['path']
            suffix = original.suffix if re.fullmatch(r'\.[A-Za-z0-9]{1,8}', original.suffix) else '.yaml'
            path = Path(kind) / f'{index}{suffix}'
            if original.is_file():
                (runtime / path).parent.mkdir(exist_ok=True)
                shutil.copyfile(original, runtime / path)
            provider['path'] = path.as_posix()

    destination = runtime / 'tt-clash.yaml'
    destination.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding='utf-8')
    return destination


class ClashRoutes:
    def __init__(self):
        self.mode = AUTO
        self.lock = Lock()
        self.process = None
        self.runtime = None
        self.private_proxy = None
        self.private_port = None

    def cancel_if_needed(self, cancelled):
        if cancelled.is_set():
            raise RouteError('已取消。')

    def choose(self, cancelled, probe):
        self.cancel_if_needed(cancelled)
        if probe():
            return None
        self.cancel_if_needed(cancelled)
        if self.mode == DIRECT:
            raise RouteError('当前网络无法直连聊天服务。可改回「自动（直连优先）」再重试。')

        directory = config_directory()
        port = shared_port(directory)
        if port and listening(port):
            shared = f'http://127.0.0.1:{port}'
            if probe(shared):
                return shared
            self.cancel_if_needed(cancelled)
            raise RouteError('已连接本机 Clash，但当前节点无法访问该服务。请检查 Clash 所选节点。')

        process, proxy, port = self.private_route(directory, cancelled)
        deadline = time.monotonic() + 18
        while time.monotonic() < deadline:
            self.cancel_if_needed(cancelled)
            if process.poll() is not None:
                raise RouteError('Clash 代理启动后退出了。请检查已应用的 Clash 配置是否有效。')
            if listening(port) and probe(proxy):
                return proxy
            cancelled.wait(.4)
        self.cancel_if_needed(cancelled)
        raise RouteError('TT 的 Clash 代理已尝试启动，但当前节点暂时无法访问该服务。')

    def private_route(self, directory, cancelled):
        with self.lock:
            self.cancel_if_needed(cancelled)
            if self.mode != AUTO:
                raise RouteError('已切换到仅直连，请重试。')
            if self.process is not None and self.process.poll() is None:
                return self.process, self.private_proxy, self.private_port
            self._dispose()
            source = next((directory / name for name in RUNTIME_FILES if (directory / name).is_file()),
                          None) if directory else None
            core = core_executable() if sys.platform == 'win32' else None
            if source is None or core is None:
                raise RouteError('TT 找不到已配置的 Clash Verge。请先打开 Clash Verge 并应用可用订阅。')
            password = secrets.token_urlsafe(24)
            with socket.socket() as reservation:
                reservation.bind(('127.0.0.1', 0))
                port = reservation.getsockname()[1]
                runtime = tempfile.TemporaryDirectory(prefix='tt-clash-')
                try:
                    path = private_config(source, directory, Path(runtime.name), port, password)
                    self.cancel_if_needed(cancelled)
                except RouteError:
                    runtime.cleanup()
                    raise
                except (OSError, ValueError) as exc:
                    runtime.cleanup()
                    raise RouteError('TT 无法准备独立代理的临时配置。') from exc
            try:
                process = subprocess.Popen(
                    [str(core), '-d', runtime.name, '-f', str(path)],
                    cwd=core.parent, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW,
                )
            except OSError as exc:
                runtime.cleanup()
                raise RouteError('TT 无法启动 Clash 的本地代理进程。') from exc
            self.process, self.runtime = process, runtime
            self.private_port = port
            self.private_proxy = f'http://tt:{password}@127.0.0.1:{port}'
            return process, self.private_proxy, port

    def _dispose(self):
        process, self.process = self.process, None
        runtime, self.runtime = self.runtime, None
        self.private_proxy = self.private_port = None
        if process is not None and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
            except ProcessLookupError:
                pass
        if runtime is not None:
            runtime.cleanup()

    def close(self):
        with self.lock:
            self._dispose()


routes = ClashRoutes()
atexit.register(routes.close)
