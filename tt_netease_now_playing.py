"""Read NetEase's current track and playback timeline when Windows has no session."""

from dataclasses import dataclass, field
from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
import time
from urllib.parse import urlsplit, urlunsplit


LOCAL_SOURCE = 'netease-local-observation'
LOG_WINDOW = 1024 * 1024
QUEUE_LIMIT = 8 * 1024 * 1024

# NetEase's byte substitution and event meanings are documented by the MIT-licensed
# dsh-pet-qqmusic project: https://github.com/344272586-sketch/dsh-pet-qqmusic
DECODE = bytes((((byte >> 4) ^ (((byte & 15) + 8) % 16)) * 16
                + (byte >> 6) * 4 + (~(byte >> 4) & 3)) & 255 for byte in range(256))
EVENT = re.compile(r'"(playOneTrackInPlayingList|checkPlayPrivilege|setPlayingPosition|native播放state)"\s*,'.encode())
HEADER = re.compile(rb'^\[(\d+):\d+:\d{4}/\d{6}:(\d+):[^\]]+\]\s*\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\]')
NUMBER = re.compile(rb'\s*(-?\d+(?:\.\d+)?)')
ART_HOST = re.compile(r'p\d+\.music\.126\.net')


def cover_url(value):
    if not isinstance(value, str) or len(value) > 1024:
        return ''
    try:
        url = urlsplit(value)
    except ValueError:
        return ''
    host = url.netloc.lower()
    if url.scheme not in ('http', 'https') or not ART_HOST.fullmatch(host) or not url.path.startswith('/'):
        return ''
    return urlunsplit(('https', host, url.path, 'param=160y160', ''))


@dataclass(frozen=True)
class Track:
    title: str
    artists: tuple[str, ...] = ()
    album: str = ''
    duration: int = 0
    artwork_url: str = field(default='', compare=False)

    def matches(self, window_title):
        if self.title == window_title:
            return True
        return any(window_title == f'{self.title} - {separator.join(self.artists)}'
                   for separator in ('/', ' / ', ', ', '、', ' & '))


def get_track(entry):
    if not isinstance(entry, dict):
        return None
    item = entry.get('track', entry)
    if not isinstance(item, dict) or not isinstance(item.get('name'), str) or not item['name'].strip():
        return None
    artists = item.get('artists')
    names = (tuple(a['name'][:200] for a in artists if isinstance(a, dict) and isinstance(a.get('name'), str))
             if isinstance(artists, list) else ())
    album = item.get('album')
    album = album if isinstance(album, dict) else {}
    duration = item.get('duration')
    return Track(item['name'][:512], names, str(album.get('name', ''))[:200],
                 duration if type(duration) is int and 0 < duration < 2_147_483_647 else 0,
                 cover_url(album.get('picUrl')) or cover_url(album.get('cover')))


class NetEaseNowPlaying:
    def __init__(self, root=None):
        self.root = root if root is not None else Path(os.environ.get('LOCALAPPDATA', '')) / 'NetEase/CloudMusic'
        self.pid = None
        self.window_title = ''
        self.queue_stamp = None
        self.queue = []
        self.reset_log()

    def reset_log(self):
        self.file_id = None
        self.offset = None
        self.remainder = b''
        self.status = 'Opened'
        self.position = None
        self.anchor = 0.
        self.track = None

    def queue_track(self, title):
        path = self.root / 'webdata/file/playingList'
        try:
            info = path.stat()
            stamp = (info.st_mtime_ns, info.st_size)
            if self.queue_stamp != stamp:
                with path.open('rb') as stream:
                    raw = stream.read(QUEUE_LIMIT + 1)
                data = json.loads(raw) if len(raw) <= QUEUE_LIMIT else {}
                entries = data.get('list', []) if isinstance(data, dict) else []
                self.queue = [track for item in entries if (track := get_track(item))] if isinstance(entries, list) else []
                self.queue_stamp = stamp
        except (OSError, ValueError):
            self.queue = []
            self.queue_stamp = None
        matches = {track for track in self.queue if track.matches(title)}
        return next(iter(matches)) if len(matches) == 1 else None

    def elapsed(self, now):
        if self.position is None:
            return None
        return self.position + int(max(0, now - self.anchor) * 1000) if self.status == 'Playing' else self.position

    def playback_event(self, row, now, wall, uptime):
        event = EVENT.search(row)
        if not event:
            return
        header = HEADER.match(row)
        if not header or int(header[1]) != self.pid:
            return
        try:
            age = wall - datetime.strptime(header[3].decode('ascii'), '%Y-%m-%d %H:%M:%S').timestamp()
        except (ValueError, OSError, OverflowError):
            return
        precise_age = (uptime - int(header[2])) / 1000
        if abs(precise_age - age) < 4:
            age = precise_age
        if age < -2:
            return
        at = now - max(0, age)
        if at < self.anchor - 2:
            return
        name = event[1]
        payload = row[event.end():].lstrip()
        if name in (b'playOneTrackInPlayingList', b'checkPlayPrivilege'):
            start = payload.find(b'{')
            if start < 0:
                return
            try:
                track = get_track(json.JSONDecoder().raw_decode(payload[start:].decode('utf-8'))[0])
            except (ValueError, RecursionError):
                return
            if track and (track != self.track or name == b'playOneTrackInPlayingList'):
                self.track, self.position, self.anchor, self.status = track, 0, at, 'Opened'
            elif track and track.artwork_url and not self.track.artwork_url:
                self.track = track
            return
        match = NUMBER.match(payload)
        if not match:
            return
        value = float(match[1])
        if not math.isfinite(value):
            return
        if name == b'setPlayingPosition' and 0 <= value < 2_147_483:
            self.position, self.anchor = round(value * 1000), at
        elif name != b'setPlayingPosition' and value in (1, 2, 4):
            self.position, self.anchor = self.elapsed(at), at
            self.status = {1: 'Playing', 2: 'Paused', 4: 'Opened'}[value]

    def update_log(self, now, wall, uptime):
        try:
            with (self.root / 'cloudmusic.elog').open('rb') as stream:
                info = os.fstat(stream.fileno())
                reset = (self.offset is None or info.st_ino != self.file_id or info.st_size < self.offset)
                offset = max(0, info.st_size - LOG_WINDOW) if reset else self.offset
                if info.st_size - offset > LOG_WINDOW:
                    reset, offset = True, info.st_size - LOG_WINDOW
                stream.seek(offset)
                data = stream.read(info.st_size - offset).translate(DECODE)
                end = stream.tell()
        except OSError:
            return
        if reset:
            self.reset_log()
        rows = (self.remainder + data).split(b'\n')
        self.remainder = rows.pop()[-LOG_WINDOW:]
        self.offset, self.file_id = end, info.st_ino
        for row in rows:
            self.playback_event(row, now, wall, uptime)

    def read(self, window, *, now=None, wall=None):
        pid = window.get('pid')
        if type(pid) is not int or pid <= 0:
            self.pid, self.window_title = None, ''
            self.reset_log()
            return None
        if self.pid != pid:
            self.pid, self.window_title = pid, ''
            self.reset_log()
        title = str(window.get('title') or '').strip()
        if title == '网易云音乐':
            title = ''
        previous = self.window_title
        now = time.monotonic() if now is None else now
        self.update_log(now, time.time() if wall is None else wall, window.get('uptime', 0))
        if not title and self.track is None:
            title = previous
        if title and previous and title != previous and not (self.track and self.track.matches(title)):
            self.position = None
        if title:
            self.window_title = title
        track = self.track if self.track and (not title or self.track.matches(title)) else self.queue_track(title)
        if not track and ' - ' in title:
            name, _, artist = title.rpartition(' - ')
            if name.strip() and artist.strip():
                track = Track(name, (artist,))
        if not track:
            return None
        artwork_url = track.artwork_url
        if not artwork_url and title and (queued := self.queue_track(title)):
            artwork_url = queued.artwork_url
        position = self.elapsed(now)
        duration = track.duration if position is not None else 0
        status = self.status
        if duration and status == 'Playing' and position >= duration:
            status = 'Opened'
        return dict(source=LOCAL_SOURCE, title=track.title, artist=' / '.join(track.artists), album=track.album,
                    artwork_url=artwork_url,
                    status=status, position=min(position or 0, duration), duration=duration, capabilities={})
