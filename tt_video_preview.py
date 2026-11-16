"""Read a timestamped tile from Bilibili's public seek preview."""

from dataclasses import dataclass
import json
from struct import iter_unpack
from urllib.parse import parse_qs, urlsplit

import httpx
from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QRect, Signal
from PySide6.QtGui import QImage, QImageReader

from tt_edge_research import BILIBILI_INFO, bounded_get, canonical_video, public_url
from tt_background_task import BackgroundTask


@dataclass(frozen=True)
class PreviewFrame:
    seconds: int
    image: QImage


def json_data(client, url, params, cancelled):
    _, raw, _ = bounded_get(client, url, cancelled, params=params, limit=140_000)
    try:
        reply = json.loads(raw)
    except (ValueError, RecursionError):
        return {}
    if isinstance(reply, dict) and reply.get('code') == 0 and isinstance(reply.get('data'), dict):
        return reply['data']
    return {}


def preview_asset(value):
    if not isinstance(value, str):
        return ''
    if value.startswith('//'):
        value = 'https:' + value
    url = public_url(value)
    if url and url.scheme == 'https' and url.hostname.endswith('.hdslb.com'):
        return url.geturl()
    return ''


def crop_tile(raw, column, row, width, height):
    source = QBuffer()
    source.setData(QByteArray(raw))
    source.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(source, b'jpeg')
    size = reader.size()
    area = QRect(column * width, row * height, width, height)
    if size.isEmpty() or size.width() * size.height() > 24_000_000 or not QRect(0, 0, size.width(), size.height()).contains(area):
        return QImage()
    reader.setClipRect(area)
    return reader.read()


def bilibili_preview(client, url, seconds, cancelled):
    video = canonical_video(url)
    if (not video or video[0] != '哔哩哔哩'
            or type(seconds) not in (float, int) or not 0 <= seconds < 86400):
        return None
    _, code, normalized = video
    params = {'bvid': code} if code.startswith('BV') else {'aid': code[2:]}
    part = int(parse_qs(urlsplit(normalized).query).get('p', ['1'])[0])
    if part > 1:
        details = json_data(client, BILIBILI_INFO, params, cancelled)
        pages = details.get('pages')
        if not isinstance(pages, list) or part > len(pages):
            return None
        entry = pages[part - 1]
        if not isinstance(entry, dict) or entry.get('page') != part or type(entry.get('cid')) is not int:
            return None
        params['cid'] = entry['cid']
    data = json_data(client, 'https://api.bilibili.com/x/player/videoshot', {**params, 'index': 1}, cancelled)
    columns, rows, width, height = (data.get(key) for key in ('img_x_len', 'img_y_len', 'img_x_size', 'img_y_size'))
    if not all(type(value) is int and 1 <= value <= limit
               for value, limit in zip((columns, rows, width, height), (32, 32, 960, 960))):
        return None
    index = data.get('index')
    if not index and (timeline := preview_asset(data.get('pvdata'))):
        _, raw, _ = bounded_get(client, timeline, cancelled, limit=80_000)
        if len(raw) % 2 == 0:
            index = [value for value, in iter_unpack('>H', raw)]
    sprites = data.get('image')
    if not isinstance(index, list) or not isinstance(sprites, list) or not index or not sprites:
        return None
    candidates = ((abs(seconds - mark), i, mark) for i, mark in enumerate(index[:len(sprites) * rows * columns])
                  if type(mark) is int and 0 <= mark < 86400)
    delta, tile, mark = min(candidates, default=(float('inf'), 0, 0))
    if delta > 8:
        return None
    sheet, offset = divmod(tile, rows * columns)
    sprite = preview_asset(sprites[sheet])
    if not sprite:
        return None
    _, raw, _ = bounded_get(client, sprite, cancelled, limit=3_500_000)
    row, column = divmod(offset, columns)
    image = crop_tile(raw, column, row, width, height)
    return None if image.isNull() or cancelled.is_set() else PreviewFrame(mark, image)


class PreviewJob(BackgroundTask):
    ready = Signal(object, object)

    def __init__(self, url, seconds):
        super().__init__()
        self.url = url
        self.seconds = seconds

    def run(self):
        frame = None
        try:
            headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.bilibili.com/'}
            with httpx.Client(timeout=httpx.Timeout(4, connect=2.5), headers=headers,
                              follow_redirects=False, trust_env=False) as client:
                frame = bilibili_preview(client, self.url, self.seconds, self.cancelled)
        except (OSError, httpx.RequestError, ValueError):
            pass
        self.publish('ready', self, frame)
