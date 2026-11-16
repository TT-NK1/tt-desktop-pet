"""Validated content sources for TT's media messages."""

import ctypes
from dataclasses import dataclass
from hashlib import sha256
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import random
import re
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
from xml.etree import ElementTree

import httpx
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize, Qt
from PySide6.QtGui import QImageReader

from tt_edge_research import (BING, BING_CHINA, NO_WEB, PRIVATE_QUERY, URL, LookupBudget, PageMetadata, bing_search,
                              bounded_get, canonical_video, cleaned, edge_search_url, is_relevant, public_url)


SOURCES = {'bilibili': 'B 站', 'youtube': 'YouTube', 'music': '音乐', 'meme': '表情包'}
VIDEO_SOURCES = {'哔哩哔哩': 'bilibili', 'YouTube': 'youtube'}
DEFAULT_QUERY = {'bilibili': '音乐 动画', 'youtube': 'animation music', 'music': 'instrumental'}
IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp'}
MAX_IMAGE_BYTES = 8_000_000
SHARE_START = re.compile(
    r'^(?:(?:TT|提提|你|请|能不能|可不可以|可以|能|帮我|给我|麻烦你)[\s，,：:]*)*'
    r'(?:发送|发|分享|推荐|找(?:一下)?|搜(?:一下)?|来(?=[一首张个段条]))(?:给我|我)?[\s，,：:]*'
    r'|^(?:请)?给我(?=(?:一)?[首张个段条]|视频链接|图片|歌曲链接)', re.I
)
SHARE_MEDIA = re.compile(
    r'(?:视频|短片)(?:的)?(?:链接|卡片)?|(?:图片|照片|图像|图)(?:的?链接)?'
    r'|(?:轻音乐|纯音乐|音乐|歌曲|歌|曲子)(?:的)?(?:链接|卡片)?'
)
SHARE_END = re.compile(r'(?:给我(?:听|看)?|我)?(?:听听|听一下|看看|看一下|听|看)?$')
SHARE_QUANTITY = re.compile(r'^(?:几[首张个段条]|(?:一)?[首张个段条]|一些|一点|点)\s*')
MUSIC_PLATFORM = re.compile(r'(?:(?:用|从|在)\s*)?(?P<name>网易云(?:音乐)?|Apple\s*Music)(?:\s*[上里的])?', re.I)
SHARE_GENERIC = {'music': 'instrumental piano', 'video': '自然风景', 'image': 'red panda'}
BING_IMAGES = 'https://cn.bing.com/images/'
IMAGE_CDN = re.compile(r'ts[0-9]+\.mm\.bing\.net|tse[0-9]+(?:-mm\.cn|\.mm)\.bing\.net')
MEDIA_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0 Safari/537.36'


@dataclass(frozen=True)
class SharedMedia:
    kind: str
    source: str
    title: str
    detail: str
    url: str = ''
    image: bytes = b''

    @property
    def key(self):
        if self.image:
            return 'image:' + sha256(self.image).hexdigest()
        video = canonical_video(self.url) if self.kind == 'video' else None
        return ':'.join(video[:2]) if video else self.url


@dataclass(frozen=True)
class ShareRequest:
    kind: str
    query: str
    platform: str = ''

    def search_url(self, preferred_music=''):
        term = self.query or SHARE_GENERIC[self.kind]
        if self.kind == 'music':
            if (self.platform or preferred_music) == 'netease':
                if not self.query:
                    return 'https://music.163.com/discover/toplist'
                return 'https://music.163.com/#/search/m/?' + urlencode({'s': term, 'type': 1})
            return edge_search_url('site:music.apple.com ' + term)
        if self.kind == 'image':
            return BING_IMAGES + 'search?' + urlencode({'q': term})
        if self.platform == 'youtube':
            return 'https://www.youtube.com/results?' + urlencode({'search_query': term})
        return 'https://search.bilibili.com/all?' + urlencode({'keyword': term})


def share_request(prompt):
    text = prompt.split('\n\n【文本附件 · ', 1)[0].strip()
    if not text or NO_WEB.search(text) or PRIVATE_QUERY.search(text) or URL.search(text):
        return None
    music_platform = ''
    if not re.search(r'视频|短片|图片|照片|图像', text):
        if platform := MUSIC_PLATFORM.search(text):
            music_platform = 'netease' if platform['name'].startswith('网易云') else 'apple'
            text = MUSIC_PLATFORM.sub('', text).strip()
    text = re.sub(r'[吧呗呀嘛吗呢哦\s，,。！!？?]+$', '', text)
    start = SHARE_START.match(text)
    if start:
        target = text[start.end():]
    elif end := re.search(r'(?:发给我|发我)$', text):
        target = text[:end.start()]
    else:
        return None
    target = SHARE_QUANTITY.sub('', target.strip())
    tail = SHARE_END.search(target)
    target = target[:tail.start()].strip()
    media = list(SHARE_MEDIA.finditer(target))
    last = media[-1] if media else None
    if last and last.end() == len(target):
        noun = last[0]
        kind = ('video' if noun.startswith(('视频', '短片')) else
                'image' if noun.startswith(('图片', '照片', '图像', '图')) else 'music')
        query = target[:last.start()]
        if noun == '图' and target.endswith('地图'):
            query = target
    elif re.search(r'^(?:《[^》]{1,80}》|.{1,66}的《[^》]{1,80}》)$', target) and tail[0]:
        kind, query = 'music', target
    elif music_platform:
        kind, query = 'music', target
    else:
        return None
    if (re.search(r'(?:不要|不想要|别发|不用发|做法|怎么|的方法|'
                  r'(?:我|你)(?:自己|刚刚|刚才|之前|刚)?(?:拍|上传|保存|截|传|发)的)', query)
            or query.strip() in ('这张', '那张', '我的')):
        return None
    platform = music_platform if kind == 'music' else ''
    if kind == 'video':
        if re.search(r'YouTube|油管', query, re.I):
            platform = 'youtube'
            query = re.sub(r'YouTube|油管', '', query, flags=re.I)
        elif re.search(r'[Bb]\s*站|哔哩哔哩', query):
            platform = 'bilibili'
            query = re.sub(r'[Bb]\s*站|哔哩哔哩', '', query)
    query = SHARE_QUANTITY.sub('', query.strip(' ，,：:'))
    query = re.sub(r'^(?:关于|有关|的)\s*|(?:的|上)$', '', query.strip()).strip()
    query = re.sub(r'的(?=《)|[《》]', ' ', query).strip()
    if query in ('好听', '好看', '随便', '你喜欢', '你推荐', '一首', '一段', '一张', '一个'):
        query = ''
    if len(query) > 80:
        return None
    return ShareRequest(kind, ' '.join(query.split()), platform)


def video_card(url, title='', detail=''):
    video = canonical_video(url)
    if video is None:
        raise ValueError('请输入 B 站或 YouTube 的视频链接。')
    platform, code, url = video
    return SharedMedia('video', VIDEO_SOURCES[platform], cleaned(title, 160) or code,
                       cleaned(detail, 400), url)


def music_card(url, title, artist=''):
    parsed = public_url(url)
    if not parsed or parsed.scheme != 'https':
        raise ValueError('请输入有效的音乐单曲链接。')
    if parsed.hostname in {'music.163.com', 'www.music.163.com'}:
        page = urlsplit(parsed.fragment) if parsed.path == '/' and parsed.fragment else parsed
        ids = parse_qs(page.query).get('id', [])
        if page.path not in {'/song', '/m/song'} or len(ids) != 1 or not re.fullmatch(r'[0-9]+', ids[0]):
            raise ValueError('请输入网易云音乐的单曲链接。')
        url = 'https://music.163.com/song?id=' + ids[0]
        source = '网易云音乐'
    elif parsed.hostname in {'music.apple.com', 'itunes.apple.com'} and (page := re.fullmatch(
        r'/[a-z]{2}/(album|song)/[^/]+/(?:id)?[0-9]+/?', parsed.path
    )):
        track = parse_qs(parsed.query).get('i', [''])[0]
        query = urlencode({'i': track}) if re.fullmatch(r'[0-9]+', track) else ''
        if page[1] == 'album' and not query:
            raise ValueError('请使用 Apple Music 的单曲分享链接。')
        url = parsed._replace(netloc='music.apple.com', query=query, fragment='').geturl()
        source = 'Apple Music'
    else:
        raise ValueError('请选择网易云音乐或 Apple Music 的单曲链接。')
    title = cleaned(title, 160)
    if not title:
        raise ValueError('请填写歌名。')
    return SharedMedia('music', source, title, cleaned(artist, 160), url)


def image_card(image, title=''):
    if isinstance(image, (str, os.PathLike)):
        path = Path(image)
        try:
            with path.open('rb') as stream:
                image = stream.read(MAX_IMAGE_BYTES + 1)
        except OSError as exc:
            raise ValueError('无法读取这张图片。') from exc
    if not isinstance(image, bytes) or not 0 < len(image) <= MAX_IMAGE_BYTES:
        raise ValueError('请选择一张 8 MB 以内的图片。')
    source = QBuffer()
    source.setData(QByteArray(image))
    source.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(source)
    size = reader.size()
    if not size.isValid() or size.width() * size.height() > 20_000_000:
        raise ValueError('图片格式无效或分辨率过大。')
    reader.setAutoTransform(True)
    reader.setScaledSize(size.scaled(QSize(1280, 1280), Qt.AspectRatioMode.KeepAspectRatio)
                         if max(size.width(), size.height()) > 1280 else size)
    decoded = reader.read()
    if decoded.isNull():
        raise ValueError('无法解码这张图片。')
    output = QBuffer()
    output.open(QIODevice.OpenModeFlag.WriteOnly)
    if not decoded.save(output, 'PNG') or output.size() > MAX_IMAGE_BYTES:
        raise ValueError('图片处理失败或超过大小限制。')
    return SharedMedia('image', 'meme', cleaned(title, 160) or '图片', '', image=bytes(output.data()))


def choose_image(folder, seen, cancelled):
    if not folder:
        return None
    with os.scandir(folder) as entries:
        files = [entry.path for entry in entries if entry.is_file(follow_symlinks=False)
                 and Path(entry.name).suffix.casefold() in IMAGE_EXTENSIONS]
    for file in random.sample(files, min(12, len(files))):
        if cancelled.is_set():
            return None
        try:
            card = image_card(file)
        except ValueError:
            continue
        if card.key not in seen:
            return card
    return None


def choose_music(client, topic, seen, cancelled, *, match_topic=False):
    markets = ('CN', 'TW', 'US') if match_topic and re.search(r'[\u3400-\u9fff]', topic) else ('CN', 'US')
    for country in markets:
        _, data, _ = bounded_get(client, 'https://itunes.apple.com/search', cancelled,
                                 params={'term': topic, 'media': 'music', 'entity': 'song',
                                         'country': country, 'limit': 12}, limit=192_000)
        try:
            results = json.loads(data).get('results', [])
        except (ValueError, AttributeError, RecursionError):
            continue
        for row in results if isinstance(results, list) else []:
            if not isinstance(row, dict) or row.get('kind') != 'song':
                continue
            try:
                card = music_card(row.get('trackViewUrl', ''), row.get('trackName'), row.get('artistName'))
            except (TypeError, ValueError):
                continue
            if card.key not in seen and (not match_topic or matches_music(topic, card)):
                return card
    return None


def matches_music(topic, card):
    def normalized(text):
        if os.name == 'nt' and re.search(r'[\u3400-\u9fff]', text):
            # Apple catalog metadata can use traditional characters for a simplified Chinese query.
            target = ctypes.create_unicode_buffer(len(text) * 2 + 1)
            if ctypes.windll.kernel32.LCMapStringW(0x0804, 0x02000000, text, -1, target, len(target)):
                text = target.value
        return re.sub(r'[\W_]+', '', text.casefold())

    title, artist = normalized(card.title), normalized(card.detail)
    term = normalized(topic)
    if term and (term in title or term in artist or term in title + artist or term in artist + title):
        return True
    words = [normalized(word) for word in re.split(r'\s+|的', topic)]
    return bool(term) and all(word in title or word in artist for word in words if word)


def choose_netease_music(client, topic, cancelled):
    urls = []
    if topic:
        rows = bing_search(client, f'site:music.163.com/song {topic}', cancelled, BING_CHINA)
        for row in rows:
            try:
                urls.append(music_card(row['url'], row['title']).url)
            except ValueError:
                continue
    else:
        _, body, _ = bounded_get(client, 'https://music.163.com/discover/toplist', cancelled, limit=470_000)
        ids = re.findall(rb'href="/song\?id=([0-9]+)"', body)
        urls = ['https://music.163.com/song?id=' + value.decode('ascii') for value in ids]
    for url in list(dict.fromkeys(urls))[:4]:
        if cancelled.is_set():
            break
        _, body, _ = bounded_get(client, url, cancelled, limit=360_000)
        parser = PageMetadata()
        parser.feed(body.decode('utf-8', errors='replace'))
        if parser.meta.get('og:type') != 'music.song':
            continue
        try:
            card = music_card(url, parser.meta.get('og:title'), parser.meta.get('og:music:artist'))
        except ValueError:
            continue
        if not topic or matches_music(topic, card):
            return card
    return None


def video_search_rss(client, source, topic, cancelled):
    site = 'www.bilibili.com/video' if source == 'bilibili' else 'www.youtube.com/watch'
    params = {'q': f'site:{site} {topic}', 'format': 'rss'}
    _, data, _ = bounded_get(client, BING_CHINA, cancelled, params=params, limit=128_000)
    try:
        feed = ElementTree.fromstring(data)
    except ElementTree.ParseError:
        return []
    return [{'url': entry.findtext('link', ''), 'title': entry.findtext('title', ''),
             'summary': entry.findtext('description', '')} for entry in feed.findall('./channel/item')]


def bilibili_popular(client, cancelled):
    _, data, _ = bounded_get(client, 'https://api.bilibili.com/x/web-interface/popular',
                             cancelled, params={'pn': 1, 'ps': 20}, limit=140_000)
    try:
        rows = json.loads(data)['data']['list']
    except (ValueError, TypeError, KeyError, RecursionError):
        return []
    return [{'url': 'https://www.bilibili.com/video/' + row['bvid'] + '/',
             'title': row.get('title', ''), 'summary': row.get('desc', '')}
            for row in rows if isinstance(row, dict) and isinstance(row.get('bvid'), str)]


class BilibiliResults(HTMLParser):
    def __init__(self, url, topic):
        super().__init__()
        self.url = url
        self.topic = topic
        self.anchor = ''
        self.card = None

    def handle_starttag(self, tag, attrs):
        if self.card:
            return
        attrs = dict(attrs)
        if tag == 'a':
            self.anchor = urljoin(self.url, attrs.get('href') or '')
        if tag == 'h3' and self.anchor and 'bili-video-card__info--tit' in (attrs.get('class') or '').split():
            title = cleaned(attrs.get('title'), 160)
            normalized_title = title.replace('黑暗之魂', '黑魂').replace('猫咪', '小猫')
            normalized_topic = self.topic.replace('黑暗之魂', '黑魂').replace('小猫咪', '小猫').replace('猫咪', '小猫')
            result = {'url': self.anchor, 'title': normalized_title, 'summary': ''}
            if normalized_topic in normalized_title or is_relevant(normalized_topic, result):
                self.card = first_video([{'url': self.anchor, 'title': title}], 'bilibili', ())

    def handle_endtag(self, tag):
        if tag == 'a':
            self.anchor = ''


def search_bilibili(client, topic, cancelled):
    url = 'https://search.bilibili.com/all'
    query = '小猫 猫咪 日常' if topic in {'小猫', '小猫咪', '猫咪'} else topic
    _, body, _ = bounded_get(client, url, cancelled, params={'keyword': query}, limit=850_000)
    parser = BilibiliResults(url, topic)
    parser.feed(body.decode('utf-8', errors='replace'))
    return parser.card


def first_video(rows, source, seen):
    for row in rows:
        if not cleaned(row.get('title'), 160):
            continue
        try:
            card = video_card(row['url'], row['title'], row.get('summary', ''))
        except ValueError:
            continue
        if card.source == source and card.key not in seen:
            return card
    return None


def youtube_search(client, topic, seen, cancelled):
    try:
        _, data, _ = bounded_get(client, 'https://www.youtube.com/results', cancelled,
                                 params={'search_query': topic}, limit=2_800_000)
        html = data.decode('utf-8', errors='replace')
        match = re.search(r'\bytInitialData\s*=\s*(?={)', html)
        if not match:
            return None
        payload, _ = json.JSONDecoder().raw_decode(html, match.end())
    except (httpx.RequestError, ValueError, RecursionError):
        return None
    pending = [payload]
    while pending and not cancelled.is_set():
        node = pending.pop()
        if isinstance(node, list):
            pending.extend(reversed(node))
        elif isinstance(node, dict):
            row = node.get('videoRenderer')
            if isinstance(row, dict) and isinstance(row.get('videoId'), str):
                title = row.get('title')
                runs = title.get('runs', []) if isinstance(title, dict) else []
                title = ''.join(run['text'] for run in runs if isinstance(run, dict)
                                and isinstance(run.get('text'), str)) if isinstance(runs, list) else ''
                card = first_video([{'url': 'https://www.youtube.com/watch?v=' + row['videoId'],
                                     'title': title}], 'youtube', seen)
                if card:
                    return card
            pending.extend(reversed(node.values()))
    return None


def existing_local_proxy():
    appdata = os.environ.get('APPDATA')
    if appdata:
        path = Path(appdata) / 'io.github.clash-verge-rev.clash-verge-rev' / 'verge.yaml'
        try:
            match = re.search(r'(?m)^verge_mixed_port:[ \t]*([0-9]{1,5})[ \t]*$', path.read_text('utf-8-sig'))
        except (OSError, UnicodeError):
            return None
        if match and 1 <= int(match[1]) <= 65535:
            return 'http://127.0.0.1:' + match[1]
    return None


def choose_video(client, source, topic, seen, cancelled, *, allow_popular=True, allow_proxy=True):
    card = first_video(video_search_rss(client, source, topic, cancelled), source, seen)
    if card or cancelled.is_set():
        return card
    site = 'bilibili.com' if source == 'bilibili' else 'youtube.com'
    card = first_video(bing_search(client, f'site:{site} {topic}', cancelled, BING), source, seen)
    if card or cancelled.is_set():
        return card
    if source == 'bilibili':
        return first_video(bilibili_popular(client, cancelled), source, seen) if allow_popular else None
    proxy = existing_local_proxy() if allow_proxy else None
    if proxy:
        with httpx.Client(proxy=proxy, trust_env=False, timeout=httpx.Timeout(6, connect=3),
                          headers=client.headers) as routed:
            card = youtube_search(routed, topic, seen, cancelled)
        if card or cancelled.is_set():
            return card
    return youtube_search(client, topic, seen, cancelled)


class BingImages(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []

    def handle_starttag(self, tag, attrs):
        if tag != 'a' or len(self.rows) >= 8:
            return
        attrs = dict(attrs)
        if 'iusc' not in (attrs.get('class') or '').split() or not attrs.get('m'):
            return
        try:
            row = json.loads(attrs['m'])
        except (ValueError, RecursionError):
            return
        if isinstance(row, dict):
            self.rows.append(row)


def choose_public_image(client, term, cancelled):
    _, body, _ = bounded_get(client, BING_IMAGES + 'async', cancelled,
                             params={'q': term, 'first': 1, 'count': 6}, limit=570_000)
    parser = BingImages()
    parser.feed(body.decode('utf-8', errors='replace'))
    for row in parser.rows[:4]:
        if cancelled.is_set():
            break
        image_url, source_url = public_url(row.get('turl')), public_url(row.get('purl'))
        if (not image_url or image_url.scheme != 'https' or not IMAGE_CDN.fullmatch(image_url.hostname)
                or not image_url.path.startswith('/th') or not source_url):
            continue
        _, data, _ = bounded_get(client, image_url.geturl(), cancelled, limit=1_500_000)
        try:
            card = image_card(data, row.get('t') or term)
        except ValueError:
            continue
        return SharedMedia('image', '必应图片', card.title, '', source_url.geturl(), card.image)
    return None


def find_requested_media(request, cancelled, *, allow_proxy=False, active_proxy=None, preferred_music=''):
    term = request.query or SHARE_GENERIC[request.kind]
    cancelled = LookupBudget(cancelled, 4.5)
    proxy = (active_proxy or existing_local_proxy()) if allow_proxy else None
    order = (proxy, None) if request.platform == 'youtube' else (None, proxy)
    for route in order if proxy else (None,):
        if cancelled.is_set():
            break
        with httpx.Client(proxy=route, trust_env=False, timeout=httpx.Timeout(1.8, connect=1.4),
                          headers={'User-Agent': MEDIA_AGENT},
                          event_hooks={'request': [cancelled.set_timeout]},
                          follow_redirects=False) as client:
            if request.kind == 'music':
                first = request.platform or ('netease' if preferred_music == 'netease' else 'apple')
                order = (first,) if request.platform else (first, 'apple' if first == 'netease' else 'netease')
                for provider in order:
                    if cancelled.is_set():
                        break
                    item = (choose_netease_music(client, request.query, cancelled) if provider == 'netease'
                            else choose_music(client, term, (), cancelled, match_topic=bool(request.query)))
                    if item:
                        return item
                continue
            elif request.kind == 'video':
                source = request.platform or 'bilibili'
                item = search_bilibili(client, term, cancelled) if source == 'bilibili' else None
                if not item and not cancelled.is_set():
                    item = choose_video(client, source, term, (), cancelled,
                                        allow_popular=not request.query, allow_proxy=False)
            else:
                item = choose_public_image(client, term, cancelled)
            if item:
                return item
    return None


def find_topic(sources, topic, folder, seen, cancelled):
    """Return a real candidate or None. Only configured public keywords leave TT."""
    order = list(sources)
    random.shuffle(order)
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0 Safari/537.36'}
    with httpx.Client(timeout=httpx.Timeout(5, connect=3), headers=headers, follow_redirects=False) as client:
        for source in order[:2]:
            if cancelled.is_set():
                break
            term = topic or DEFAULT_QUERY.get(source)
            try:
                if source == 'meme':
                    found = choose_image(folder, seen, cancelled)
                elif source == 'music':
                    found = choose_music(client, term, seen, cancelled)
                else:
                    found = choose_video(client, source, term, seen, cancelled)
            except (OSError, httpx.RequestError):
                continue
            if found:
                return found
    return None
