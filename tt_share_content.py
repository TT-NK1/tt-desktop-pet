"""Validated content sources for TT's media messages."""

import ctypes
from dataclasses import dataclass, replace
from hashlib import sha256
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import random
import re
import unicodedata
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlsplit
from xml.etree import ElementTree

import httpx
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize, Qt
from PySide6.QtGui import QImageReader

from tt_chat_images import ChatImage
from tt_edge_research import (BING, BING_CHINA, NO_WEB, PRIVATE_QUERY, URL, LookupBudget, PageMetadata, bing_search,
                              bounded_get, canonical_video, cleaned, edge_search_url, is_relevant, public_url, read_video)


SOURCES = {'bilibili': 'B 站', 'youtube': 'YouTube', 'music': '音乐', 'meme': '表情包'}
VIDEO_SOURCES = {'哔哩哔哩': 'bilibili', 'YouTube': 'youtube'}
DEFAULT_QUERY = {'bilibili': '音乐 动画', 'youtube': 'animation music', 'music': 'instrumental'}
IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp'}
MAX_IMAGE_BYTES = 8_000_000
SHARE_START = re.compile(
    r'^(?:(?:TT|提提|你|请|能不能|可不可以|可以|能|帮我|给我|麻烦你)[\s，,：:]*)*'
    r'(?:发送|发(?:来)?|分享|推(?:荐)?|挑|找(?:找|一下)?|搜(?:搜|一下)?|来(?=[一二两三四五六七八九十几首张个段条点些0-9]))(?:给我|我)?[\s，,：:]*'
    r'|^(?:请)?给我(?=(?:[一二两三四五六七八九十几]|[1-9][0-9]?)?首|一?[张个段条]|视频链接|图片|歌曲链接)', re.I
)
SHARE_MEDIA = re.compile(
    r'(?:视频|短片)(?:的)?(?:链接|卡片)?|(?:图片|照片|图像|表情包|动图|壁纸|插画|配图|图)(?:的?链接)?'
    r'|(?:轻音乐|纯音乐|音乐|歌曲|歌|曲子)(?:的)?(?:链接|卡(?:片)?)?'
)
SHARE_END = re.compile(r'(?:给我(?:听|看)?|我)?(?:听听|听一下|看看|看一下|听|看)?$')
SHARE_QUANTITY = re.compile(r'^(?:几[首张个段条]|(?:一)?[首张个段条]|一些|一点|点)\s*')
WATCH_START = re.compile(r'^(?:我)?(?:突然|现在|有点|也|还)*(?:想(?:要)?看看?|想找点|要看)\s*')
LISTEN_START = re.compile(
    r'^(?:(?:我)?(?:也|还|更)?(?:想要|想|要)听|让我听(?:一?下(?=\s*《))?|听听?(?=[一二两三四五六七八九十几首点些歌《0-9]))\s*'
)
SONG_NUMERALS = dict(zip('一二两三四五六七八九十', (1, 2, 2, 3, 4, 5, 6, 7, 8, 9, 10)))
SONG_COLLOQUIAL_RANGES = {'一两': 2, '一二': 2, '两三': 3, '二三': 3, '三四': 4,
                          '四五': 5, '五六': 6, '六七': 7, '七八': 8, '八九': 9}
SONG_NUMBER = r'[1-9][0-9]?|[一二两三四五六七八九十]'
SONG_NUMBER_RANGE = (
    rf'(?P<spoken>{"|".join(SONG_COLLOQUIAL_RANGES)})'
    rf'|(?P<number>{SONG_NUMBER})(?:\s*[-–—－~～到至]\s*(?P<upper>{SONG_NUMBER}))?'
)
SONG_AMOUNT = re.compile(
    rf'^(?:个\s*)?(?:(?:{SONG_NUMBER_RANGE})\s*首|几首|多首|一些|一点|点|些)\s*'
)
PLAY_SONG = re.compile(
    rf'^(?:(?:请|帮我|给我|能不能)\s*)*(?:播放(?:一下)?|[放点](?=\s*(?:{SONG_NUMBER_RANGE}|几|多)?\s*首))\s*'
)
FAVORITE_MUSIC = re.compile(
    r'(?:我(?:平时|平常|通常|一直)?(?:喜欢|爱|常|经常)(?:听)?|'
    r'我(?:可能|应该|大概)会喜欢|合我(?:口味|胃口))(?:的)?')
MUSIC_RECOMMEND_QUESTION = re.compile(
    r'(?:有没有|有什么|有哪(?:首|些))\s*(?:好听|适合我|合我口味|我喜欢听)的?\s*(?:歌曲|歌|音乐)(?:推荐)?')
MUSIC_PLATFORM = re.compile(r'(?:(?:用|从|在)\s*)?(?P<name>网易云(?:音乐)?|Apple\s*Music)(?:\s*[上里的])?', re.I)
SHARE_TRAILING = re.compile(r'[吧呗呀嘛吗呢哦\s，,。！!？?]+$')
BARE_SONG_REQUEST = re.compile(
    r'(?:那(?:么|就)?[，,\s]*)?(?:你)?(?:请|能不能|可不可以|可以|帮我|给我|先|再|就|直接)*'
    r'(?:发(?:给我|我)?|分享(?:给我)?|推(?:荐)?(?:给我)?|给我|来)(?:一)?首(?P<song>歌(?:曲)?)?'
    r'(?:给我|我)?(?:听听|听一下|听)?'
)
SONG_ORDINAL = re.compile(rf'第\s*({SONG_NUMBER})\s*首')
REFERENCED_SONG = (r'(?:(?:你)?(?:刚才|刚刚|之前)(?:(?:推荐|提到|说|发)的)?\s*)?'
                   r'(?:(?:这|那)(?:一)?首|' + SONG_ORDINAL.pattern + r')(?:歌曲|歌|曲子)?')
SEND_SONG = r'(?:发送|发(?:一下|下|个链接)?|分享|给)(?:给我|我)?'
SONG_REFERENCE_REQUEST = re.compile(
    r'(?:那(?:么|就)?[，,\s]*)?(?:(?:你|请|能不能|可不可以|可以|能|帮我|给我|麻烦你|直接)\s*)*'
    rf'(?:(?:把\s*)?{REFERENCED_SONG}(?:的?(?:链接|卡片))?\s*{SEND_SONG}'
    rf'|{SEND_SONG}\s*{REFERENCED_SONG}(?:的?(?:链接|卡片))?)(?:给我|我)?(?:听听|听一下|听)?'
)
MUSIC_CONVERSATION = re.compile(r'音乐|乐队|歌手|主唱|专辑|歌曲|的歌|首歌|哪首|那首|这首|🎧')
POETRY_CONVERSATION = re.compile(r'诗词|古诗|诗歌|诗句|诗人|写诗|诗一首|一首诗|哪首诗')
SONG_TITLE = re.compile(r'《([^《》\n]{1,70})》')
ARTIST_NAME = r"[A-Za-z0-9][A-Za-z0-9.&'+ -]{0,39}|[\u3040-\u30ff\u3400-\u9fff·]{2,12}?"
ARTIST_LISTENING = re.compile(
    rf'^(?:我)?(?:今天|现在|最近|又|突然|有点|还|也|真的|很|特别|就|想)*'
    rf'(?:想听(?:一点|点|几首)?|想放|想找|想来点|听听)\s*(?P<artist>{ARTIST_NAME})的(?:歌(?:曲)?|音乐)(?:了)?$',
    re.I,
)
NAMED_SONG = re.compile(r'(?:(?P<artist>' + ARTIST_NAME + r')的\s*)?《(?P<title>[^《》\n]{1,70})》')
MUSIC_SHARE_INTENT = re.compile(
    r'(?:推荐|想听|要听|挑|发|找|放|点).{0,40}(?:歌曲|首歌|歌单|的歌|音乐)'
    r'|(?:歌曲|首歌|歌单|的歌|音乐).{0,28}(?:推荐|给我发|分享|挑几首)'
)
ARTIST_SHARE_FOLLOWUP = re.compile(
    r'^(?:你|请|能不能|可不可以|帮我|给我|可以|再|就|先)*'
    r'(?:发(?:给我|我)?|来|放|推(?:荐)?|找)(?:个)?\s*'
)
ARTIST_SEPARATOR = re.compile(r'[、，；;/]|\s*[&＆,＋+]\s*|\s+(?:and|with|feat\.?|ft\.?)\s+|\s*×\s*', re.I)
ARTIST_QUESTION = re.compile(
    r'^(?:你|我)?(?:知道|认识|了解|听过|(?:很|最|也)?喜欢|最近在听|想听|聊聊)\s*'
    r'(?P<artist>' + ARTIST_NAME + r')(?:的(?:歌(?:曲)?|音乐|专辑))?$'
)
ARTIST_CREDIT = re.compile(
    r'(?:^|[\n。！？🎧，：]|(?:推荐|喜欢|听听|听过)\s*)\s*(?P<artist>' + ARTIST_NAME
    + r')\s*(?=的(?:歌(?:曲)?|音乐|专辑)|(?:这支|这个|这位)(?:乐队|组合|歌手)|《)'
)
MUSIC_REPLACEMENT = re.compile(
    r'(?:(?:能不能|可以|请)?(?:帮我|给我)?(?:再)?换'
    r'(?:(?:一)?[种个首](?:别的|其他的|不一样的)?(?:歌(?:曲)?)?|歌(?:曲)?|别的)'
    r'|(?:再|重新)(?:给我)?(?:来|发|推荐)(?:一)?首(?:别的)?(?:歌(?:曲)?)?)'
)
MUSIC_REPEAT_START = re.compile(r'^(?:(?:那|好)[，,\s]*)?(?:你|请|能不能|给我|帮我|就)*\s*(?:再|重新)(?:来|发|放|推荐)(?:给我|我)?\s*')
MUSIC_CARD_CONTEXT = re.compile(
    r'\n【分享的(?:Apple Music|网易云音乐)内容】(?P<title>[^\n]+)\n(?P<url>https://\S+)')
MEDIA_CARD_CONTEXT = re.compile(
    r'\n【分享的(?P<source>[^\n】]+)内容】(?P<title>[^\n]+)(?:\n(?P<url>https://\S+))?'
)
MEDIA_NEGATION = re.compile(
    r'(?:不要|别|不用|不需要|不必|无需)(?:再|给我|帮我)?\s*(?:发|分享|推|放|播放|找|搜)'
    r'(?!\s*(?:(?:任何|裸|搜索|网页|音乐|歌曲|视频|图片|平台)\s*)?(?:的)?(?:链接|网址|入口))'
)
MUSIC_REPLY_TOKEN = re.compile(
    r'[【\[]音乐卡[｜|]\s*(?P<title>[^｜|】\]\n]{1,80})[｜|]\s*(?P<artist>[^｜|】\]\n]{1,80})[】\]]'
    r'|\[(?P<label>[^\]\n]+)\]\((?P<destination>https?://[^\s)]+)\)'
    r'|(?P<url>' + URL.pattern + r')', re.I
)
MUSIC_HOSTS = {'y.qq.com', 'kugou.com', 'kuwo.cn', 'spotify.com',
               'music.youtube.com', 'soundcloud.com', 'bandcamp.com', 'deezer.com', 'tidal.com'}
APPLE_SEARCH = 'https://itunes.apple.com/search'
NETEASE_SEARCH = 'https://music.163.com/api/search/get'
NETEASE_HEADERS = {'Referer': 'https://music.163.com/'}
POP_STYLE = r'(?:华语)?流行(?:歌曲?|音乐)|有人唱的(?:歌曲?|音乐)?|带人声的(?:歌曲?|音乐)?'
JAPANESE_STYLE = re.compile(r'(?:日文|日语|日本|日系)(?:的)?(?:流行)?(?:歌曲?|音乐)|J[ -]?POP', re.I)
MUSIC_STYLE_REQUEST = re.compile(
    r'(?:有没有|没有|有|(?:我)?(?:更)?想听|(?:能不能|可以)?(?:再|给我)?换(?:给我)?|'
    r'(?:请|给我|再)?来)(?:一首|首|一点|一些|点)?\s*(?P<style>'
    + POP_STYLE + '|' + JAPANESE_STYLE.pattern + r')(?:听听)?', re.I
)
SHARE_GENERIC = {'music': '华语流行歌', 'video': '自然风景', 'image': 'red panda'}
# Original vocal recordings with single-track links verified in both catalogs.
POP_TRACKS = (
    ('晴天', '周杰伦', '535824738', '186016'),
    ('十年', '陈奕迅', '542922095', '66842'),
    ('遇见', '孙燕姿', '541857030', '287035'),
    ('江南', '林俊杰', '1071753628', '108914'),
)
JAPANESE_TRACKS = (
    ('Lemon', '米津玄師', '1537460612', '536622304'),
    ('打上花火', 'DAOKO 米津玄師', '1263790415', '496869422'),
    ('前前前世', 'RADWIMPS', '1434005783', '426881487'),
    ('君の知らない物語', 'supercell', '1537428425', '461089'),
)
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
    author: str = ''
    album: str = ''
    duration: int = 0

    @property
    def key(self):
        if self.kind == 'image' and self.image:
            return 'image:' + sha256(self.image).hexdigest()
        video = canonical_video(self.url) if self.kind == 'video' else None
        return ':'.join(video[:2]) if video else self.url


@dataclass(frozen=True)
class SongReference:
    title: str = ''
    artist: str = ''
    url: str = ''


@dataclass(frozen=True)
class ShareRequest:
    kind: str
    query: str
    platform: str = ''
    music_style: str = ''
    inherited_platform: bool = False
    song: SongReference | None = None
    avoid_topics: tuple[str, ...] = ()
    preference_topic: str = ''
    music_card_only: bool = False
    count: int = 1
    songs: tuple[SongReference, ...] = ()

    def search_term(self):
        styles = {'instrumental': 'instrumental piano', 'japanese': '日语流行歌'}
        return self.query or styles.get(self.music_style, SHARE_GENERIC[self.kind])

    def search_url(self, preferred_music=''):
        term = self.search_term()
        if self.kind == 'music':
            if (self.platform or preferred_music) == 'netease':
                if not self.query and not self.music_style:
                    return 'https://music.163.com/discover/toplist'
                return 'https://music.163.com/#/search/m/?' + urlencode({'s': term, 'type': 1})
            return edge_search_url('site:music.apple.com ' + term)
        if self.kind == 'image':
            return BING_IMAGES + 'search?' + urlencode({'q': term})
        if self.platform == 'youtube':
            return 'https://www.youtube.com/results?' + urlencode({'search_query': term})
        return 'https://search.bilibili.com/all?' + urlencode({'keyword': term})


def previous_music_request(history):
    for prompt, answer, _ in reversed(history):
        cards = tuple(MUSIC_CARD_CONTEXT.finditer(answer))
        match = cards[-1] if cards else None
        if match and (card := link_card(match['url'])) and card.kind == 'music':
            platform = 'netease' if card.source == '网易云音乐' else 'apple'
            track_id = music_track_id(card)
            prior = share_request(prompt)
            if prior and prior.kind == 'music':
                query = prior.song.artist if prior.song else prior.query if not prior.songs else ''
                if query or prior.music_style:
                    return ShareRequest('music', query, platform, prior.music_style,
                                        avoid_topics=prior.avoid_topics)
            style = 'japanese' if any(track_id in track[2:] for track in JAPANESE_TRACKS) else ''
            return ShareRequest('music', '', platform, style)
        # A short retry can follow a text-only reply from an earlier attempt to change the song.
        if not MUSIC_REPLACEMENT.fullmatch(SHARE_TRAILING.sub('', MUSIC_PLATFORM.sub('', prompt)).strip(' ，,：:')):
            break
    return None


def music_from_conversation(history):
    previous = previous_music_request(history)
    if previous:
        return previous
    if not history:
        return None
    prompt, answer, _ = history[-1]
    context = prompt + '\n' + answer
    if PRIVATE_QUERY.search(context) or POETRY_CONVERSATION.search(context):
        return None
    if (song := referenced_song(history[-1:])).title:
        return ShareRequest('music', ' '.join(part for part in (song.artist, song.title) if part), song=song)
    prior_request = share_request(prompt)
    if prior_request and prior_request.kind == 'music' and (prior_request.query or prior_request.music_style):
        return prior_request
    japanese = JAPANESE_STYLE.search(context)
    if not japanese and not MUSIC_CONVERSATION.search(context):
        return None
    # Resolve only the song title and artist locally. Send those terms, rather than the conversation, to search.
    artist = ARTIST_QUESTION.fullmatch(SHARE_TRAILING.sub('', prompt.strip())) or ARTIST_CREDIT.search(answer)
    title = SONG_TITLE.search(answer)
    query = ' '.join(part for part in (artist['artist'].strip() if artist else '', title[1].strip() if title else '')
                     if part)
    return ShareRequest('music', query, music_style='japanese' if japanese and not query else '')


def referenced_song(history, index=None):
    for prompt, answer, _ in reversed(history):
        context = prompt + '\n' + answer
        if PRIVATE_QUERY.search(context) or POETRY_CONVERSATION.search(context):
            break
        cards = tuple(MUSIC_CARD_CONTEXT.finditer(answer))
        if cards:
            if index is not None and index <= len(cards):
                return SongReference(cards[index - 1]['title'], url=cards[index - 1]['url'])
            if index is None and len(cards) == 1:
                return SongReference(cards[0]['title'], url=cards[0]['url'])
            break
        if not (MUSIC_CONVERSATION.search(context) or JAPANESE_STYLE.search(context)
                or re.search(r'副歌|旋律|单曲|好听', context)):
            break
        titles = {match[1].strip(): match for match in SONG_TITLE.finditer(answer) if match[1].strip()}
        if not titles:
            continue
        if index is not None:
            if index > len(titles):
                break
            title = tuple(titles)[index - 1]
            return SongReference(title)
        if len(titles) > 1:
            break
        if titles:
            title, match = next(iter(titles.items()))
            credit = re.split(r'[，,。！？…：:\n]', answer[:match.start()])[-1].strip('*_`🎧 ')
            credit = re.sub(r'^(?:我(?:先|就)?(?:推荐|选|推|喜欢)|(?:给你|帮你)(?:选|找|推荐|推)|'
                            r'(?:推荐|听|试|放)(?:给你|听|一下)?)\s*', '', credit).strip('*_` ')
            artist = re.fullmatch(rf'(?P<name>{ARTIST_NAME})\s*的?', credit)
            if artist:
                return SongReference(title, artist['name'].strip())
            hint = ARTIST_QUESTION.fullmatch(SHARE_TRAILING.sub('', prompt.strip()))
            return SongReference(title, hint['artist'].strip() if hint else '')
    return SongReference()


def song_reference_index(text):
    ordinal = SONG_ORDINAL.search(text)
    if not ordinal:
        return None
    index = ordinal[1]
    return int(index) if index.isdecimal() else SONG_NUMERALS[index]


def recent_chat_media(history):
    if not history:
        return ()
    _, answer, _ = history[-1]
    media = []
    for match in MEDIA_CARD_CONTEXT.finditer(answer):
        title, source = match['title'], match['source']
        if PRIVATE_QUERY.search(title) or URL.search(title) or not title.strip():
            continue
        url = match['url'] or ''
        if source not in {'Apple Music', '网易云音乐', 'bilibili', 'youtube', '必应图片', 'meme'}:
            continue
        card = link_card(url) if url else None
        kind = card.kind if card else 'image' if source in {'必应图片', 'meme'} else ''
        if kind:
            media.append({'kind': kind, 'title': title[:90], 'source': source})
        if len(media) == 5:
            break
    return tuple(media)


def routed_share(plan, prompt):
    if (plan.get('action') not in ('share', 'clarify') or MEDIA_NEGATION.search(prompt)
            or NO_WEB.search(prompt) or PRIVATE_QUERY.search(prompt)):
        return None
    kind = plan.get('kind')
    if kind not in ('music', 'video', 'image'):
        return None
    if plan['action'] == 'clarify':
        return kind
    query, platform = plan.get('query', ''), plan.get('platform', '')
    platforms = {'music': ('', 'apple', 'netease'), 'video': ('', 'bilibili', 'youtube'), 'image': ('',)}
    if (not isinstance(query, str) or len(query) > 100 or '\n' in query or URL.search(query)
            or PRIVATE_QUERY.search(query) or NO_WEB.search(query) or platform not in platforms[kind]):
        return None
    query = ' '.join(query.split())
    if kind != 'music':
        return ShareRequest(kind, query, platform) if query else kind
    count, raw_songs = plan.get('count', 1), plan.get('songs', [])
    if type(count) is not int or not 1 <= count <= 99 or not isinstance(raw_songs, list) or len(raw_songs) > 15:
        return None
    songs = []
    for song in raw_songs:
        if not isinstance(song, dict):
            return None
        title, artist = song.get('title'), song.get('artist', '')
        if any(not isinstance(value, str) or len(value) > 80 or '\n' in value
               or URL.search(value) or PRIVATE_QUERY.search(value) or NO_WEB.search(value)
               for value in (title, artist)) or not title.strip():
            return None
        songs.append(SongReference(title.strip(), artist.strip()))
    songs = tuple(dict.fromkeys(songs))
    if amount := SONG_AMOUNT.match(query):
        query = query[amount.end():]
    style = ''
    if not songs:
        if JAPANESE_STYLE.fullmatch(query):
            style = 'japanese'
        elif re.fullmatch(POP_STYLE, query):
            style = 'pop'
        elif query in ('纯音乐', '轻音乐'):
            style = 'instrumental'
    query = '' if style else re.sub(r'\s*的(?:歌曲?|音乐)$', '', query).strip()
    return ShareRequest('music', query, platform, style, count=len(songs) or count, songs=songs,
                        music_card_only=True)


def share_request(prompt, *, history=(), exact_tracks=False):
    text = prompt.split('\n\n【文本附件 · ', 1)[0].strip()
    if not text or NO_WEB.search(text) or PRIVATE_QUERY.search(text) or URL.search(text):
        return None
    text = re.sub(r'(用|在|从)\s*AM(?![A-Za-z])', r'\1Apple Music', text, flags=re.I)
    music_platform = ''
    if not re.search(r'视频|短片|图片|照片|图像', text):
        if platform := MUSIC_PLATFORM.search(text):
            music_platform = 'netease' if platform['name'].startswith('网易云') else 'apple'
            text = MUSIC_PLATFORM.sub('', text).strip()
    text = SHARE_TRAILING.sub('', text).strip(' ，,：:')
    if MUSIC_RECOMMEND_QUESTION.fullmatch(text):
        return ShareRequest('music', '', music_platform, count=1 if '有哪首' in text else 3)
    if SONG_REFERENCE_REQUEST.fullmatch(text) or (history and SONG_ORDINAL.fullmatch(text)):
        song = referenced_song(history, song_reference_index(text))
        if SONG_ORDINAL.fullmatch(text) and not song.title:
            return None
        query = ' '.join(part for part in (song.artist, song.title) if part)
        return ShareRequest('music', query, music_platform, song=song)
    last_clause = re.split(r'[，,。；;！？?!\n]', text)[-1].strip()
    if match := MUSIC_STYLE_REQUEST.fullmatch(last_clause):
        style = 'japanese' if JAPANESE_STYLE.fullmatch(match['style']) else 'pop'
        return ShareRequest('music', '', music_platform, style)
    if last_clause != text and (ask := ARTIST_SHARE_FOLLOWUP.match(last_clause)):
        prefix = text[:-len(last_clause)].rstrip(' ，,。；;！？?!\n')
        if (artist := ARTIST_LISTENING.fullmatch(prefix)) and (amount := SONG_AMOUNT.match(last_clause[ask.end():])):
            rest = last_clause[ask.end() + amount.end():]
            if re.fullmatch(r'(?:歌(?:曲)?|音乐)?(?:给我)?(?:听听|听一下|听)?', rest):
                return ShareRequest('music', artist['artist'].strip(), music_platform, count=song_count(amount))
    if artist := ARTIST_LISTENING.fullmatch(text):
        if JAPANESE_STYLE.fullmatch(artist['artist'] + '的歌'):
            return ShareRequest('music', '', music_platform, 'japanese')
        if not re.search(r'谁|什么|哪[个位首]|几首', artist['artist']):
            return ShareRequest('music', artist['artist'].strip(), music_platform)
    if repeat := MUSIC_REPEAT_START.match(text):
        target = text[repeat.end():]
        if (amount := SONG_AMOUNT.match(target)) and re.fullmatch(
                r'(?:歌(?:曲)?|音乐)?(?:给我)?(?:听听|听一下|听)?', target[amount.end():]):
            if prior := previous_music_request(history):
                return replace(prior, platform=music_platform or prior.platform,
                               inherited_platform=not music_platform, count=song_count(amount))
    if bare_song := BARE_SONG_REQUEST.fullmatch(text):
        inferred = music_from_conversation(history)
        if inferred:
            return replace(inferred, platform=music_platform or inferred.platform,
                           inherited_platform=bool(inferred.platform and not music_platform))
        if music_platform or bare_song['song'] or (not history and not MUSIC_REPLACEMENT.fullmatch(text)):
            return ShareRequest('music', '', music_platform)
        return None
    if MUSIC_REPLACEMENT.fullmatch(text) and (previous := previous_music_request(history)):
        return replace(previous, platform=music_platform or previous.platform, inherited_platform=not music_platform)
    playing = PLAY_SONG.match(text)
    listening = LISTEN_START.match(text) or playing
    watching = WATCH_START.match(text)
    start = SHARE_START.match(text)
    if start or listening or watching:
        target = text[(listening or watching or start).end():]
    elif end := re.search(r'(?:发给我|发我)$', text):
        target = text[:end.start()]
    else:
        return None
    target = target.strip()
    amount = SONG_AMOUNT.match(target)
    count = song_count(amount)
    if amount:
        target = target[amount.end():]
    target = SHARE_QUANTITY.sub('', target)
    tail = SHARE_END.search(target)
    target = target[:tail.start()].strip()
    titles = tuple(SONG_TITLE.finditer(target))
    if len(titles) > 1:
        prefix = target.removeprefix('把').lstrip()
        artist = ''
        if credit := NAMED_SONG.match(prefix):
            artist = (credit['artist'] or '').strip()
            if artist:
                prefix = prefix[credit.start('title') - 1:]
        if re.fullmatch(r'[\s、，,与和及]*(?:这(?:几|两|三)?首(?:歌(?:曲)?)?)?', SONG_TITLE.sub('', prefix)):
            songs = tuple(dict.fromkeys(SongReference(match[1].strip(), artist)
                                        for match in titles if match[1].strip()))
            if songs:
                return ShareRequest('music', '', music_platform, count=len(songs), songs=songs)
    if target in ('歌单', '播放列表') and not playing:
        return ShareRequest('music', '', music_platform, count=max(count, 3))
    if listening:
        if re.fullmatch(REFERENCED_SONG, target):
            song = referenced_song(history, song_reference_index(target))
            return ShareRequest('music', ' '.join(filter(None, (song.artist, song.title))), music_platform, song=song)
        if named := NAMED_SONG.fullmatch(target):
            song = SongReference(named['title'].strip(), (named['artist'] or '').strip())
            return ShareRequest('music', ' '.join(filter(None, (song.artist, song.title))), music_platform, song=song)
        title = normalized_music(target)
        known = next((track for track in POP_TRACKS + JAPANESE_TRACKS if title == normalized_music(track[0])), None)
        if known:
            return ShareRequest('music', known[0], music_platform, song=SongReference(known[0], known[1]))
        if (playing and re.fullmatch(r'[\w \-·・’\'&]{2,60}', target)
                and target not in ('列表', '音乐列表', '歌单', '音乐', '下一首', '上一首', '歌曲', '本地音乐', '当前歌曲')
                and not re.search(r'视频|歌曲|音乐|的歌|歌单|轻音乐|故事|语音', target)):
            return ShareRequest('music', target, music_platform, song=SongReference(target))
    if re.fullmatch(POP_STYLE, target):
        return ShareRequest('music', '', music_platform, 'pop', count=count)
    if JAPANESE_STYLE.fullmatch(target):
        return ShareRequest('music', '', music_platform, 'japanese', count=count)
    media = list(SHARE_MEDIA.finditer(target))
    last = media[-1] if media else None
    if last and last.end() == len(target):
        noun = last[0]
        kind = ('video' if noun.startswith(('视频', '短片')) else
                'image' if noun.startswith(('图片', '照片', '图像', '表情包', '动图', '壁纸', '插画', '配图', '图')) else 'music')
        query = target[:last.start()]
        if noun.startswith(('表情包', '动图', '壁纸', '插画', '配图')) or (noun == '图' and target.endswith('地图')):
            query = target
    elif (named := NAMED_SONG.fullmatch(target)) and (music_platform or tail[0].endswith(('听', '听听', '听一下'))
            or (named['artist'] and not text.startswith(('推荐', '给我推荐')))):
        kind, query = 'music', target
    elif music_platform:
        kind, query = 'music', target
    else:
        return None
    if (re.search(r'(?:不要|不想要|别发|不用发|做法|怎么|的方法|'
                  r'(?:我|你)(?:自己|刚刚|刚才|之前|刚)?(?:拍|上传|保存|截|传|发)的)', query)
            or query.strip() in ('这张', '那张', '这首', '那首', '我的')
            or (listening and kind != 'music') or (watching and kind == 'music')):
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
    if kind == 'music' and FAVORITE_MUSIC.fullmatch(query):
        query = ''
    if len(query) > 80:
        return None
    style = 'instrumental' if kind == 'music' and last and last[0].startswith(('纯音乐', '轻音乐')) else ''
    song = None
    if kind == 'music' and exact_tracks and (named := NAMED_SONG.fullmatch(target)):
        song = SongReference(named['title'].strip(), (named['artist'] or '').strip())
    return ShareRequest(kind, ' '.join(query.split()), platform, style, song=song,
                         count=count if kind == 'music' else 1)


def song_count(amount):
    if not amount:
        return 1
    if amount['spoken']:
        return SONG_COLLOQUIAL_RANGES[amount['spoken']]
    number = amount['upper'] or amount['number']
    return (int(number) if number.isdecimal() else SONG_NUMERALS[number]) if number else 3


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


def link_card(url):
    try:
        return video_card(url)
    except ValueError:
        parsed = public_url(url)
        if not parsed:
            return None
        if parsed.hostname == 'b23.tv' and re.fullmatch(r'/[\w-]{1,32}/?', parsed.path, re.ASCII):
            return SharedMedia('video', 'bilibili', 'B 站视频', '', parsed._replace(scheme='https').geturl())
        title = '网易云音乐单曲' if parsed.hostname in ('music.163.com', 'www.music.163.com') else 'Apple Music 单曲'
        try:
            return music_card(parsed._replace(scheme='https').geturl(), title)
        except ValueError:
            return None


def links_in_message(message):
    cards = {}
    head, marker, attachments = message.partition('\n\n【文本附件 · ')

    def substitute(match):
        url = match[0].rstrip('.,;!?)]')
        card = link_card(url)
        if not card or (len(cards) >= 2 and card.url not in cards):
            return match[0]
        cards.setdefault(card.url, card)
        return match[0][len(url):]

    return (URL.sub(substitute, head).strip() + marker + attachments).strip(), tuple(cards.values())


def card_cover(client, url, source, cancelled):
    if not isinstance(url, str):
        return b''
    if url.startswith('//'):
        url = 'https:' + url
    parsed = public_url(url)
    if not parsed:
        return b''
    domains = {'bilibili': '.hdslb.com', 'youtube': '.ytimg.com', '网易云音乐': '.music.126.net',
               'Apple Music': '.mzstatic.com'}
    if not parsed.hostname.endswith(domains[source]):
        return b''
    if source == '网易云音乐' and not parsed.query:
        parsed = parsed._replace(query='param=160y160')
    _, raw, _ = bounded_get(client, parsed._replace(scheme='https').geturl(), cancelled, limit=1_800_000)
    source_buffer = QBuffer()
    source_buffer.setData(QByteArray(raw))
    source_buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(source_buffer)
    size = reader.size()
    if size.isEmpty() or size.width() * size.height() > 12_000_000:
        return b''
    bounds = QSize(640, 400) if source in ('bilibili', 'youtube') else QSize(160, 160)
    if size.width() > bounds.width() or size.height() > bounds.height():
        reader.setScaledSize(size.scaled(bounds, Qt.AspectRatioMode.KeepAspectRatio))
    image = reader.read()
    return b'' if image.isNull() else bytes(ChatImage.encode(image, 'JPEG', 83))


def apple_track_id(parsed):
    return parse_qs(parsed.query).get('i', [''])[0] or parsed.path.rstrip('/').rsplit('/', 1)[-1].removeprefix('id')


def music_track_id(card):
    parsed = urlsplit(card.url)
    return (apple_track_id(parsed) if card.source == 'Apple Music'
            else parse_qs(parsed.query).get('id', [''])[0])


def is_music_link(url):
    parsed = public_url(url)
    if not parsed:
        return False
    host = parsed.hostname or ''
    if host in {'music.apple.com', 'itunes.apple.com', 'music.163.com', 'www.music.163.com',
                'music.yandex.com', 'music.amazon.com', 'music.amazon.cn', 'music.migu.cn'}:
        return True
    if any(host == domain or host.endswith('.' + domain) for domain in MUSIC_HOSTS):
        return True
    if parsed.path in {'/search', '/s'} and re.search(r'(^|\.)(?:bing\.com|google\.[a-z.]+|baidu\.com)$', host):
        query = ' '.join(value for values in parse_qs(parsed.query).values() for value in values)
        return bool(re.search(r'music\.apple\.com|itunes\.apple\.com|music\.163\.com|'
                              r'Apple\s*Music|网易云|歌(?:曲|名)|试听|听歌', query, re.I))
    return False


def music_reply(text, *, streaming=False, prompt=''):
    """Collect track references and remove song destinations from assistant prose."""
    songs = []
    removed = False

    def substitute(match):
        nonlocal removed
        if match['title'] is not None:
            if title := match['title'].strip():
                songs.append(SongReference(title, match['artist'].strip()))
            return ''
        label = match['label'] or ''
        url = match['destination'] or match['url'].rstrip('.,;!?)]')
        if not is_music_link(url):
            return match[0]
        removed = True
        if (card := link_card(url)) and card.kind == 'music':
            named = SONG_TITLE.search(label)
            songs.append(SongReference(named[1] if named else '', url=card.url))
        else:
            start = match.string.rfind('\n', 0, match.start()) + 1
            if named := SONG_TITLE.search(label or match.string[start:match.start()]):
                songs.append(SongReference(named[1]))
        return label if label and (SONG_TITLE.search(label)
                                   or not re.search(r'打开|搜索|入口|点击|查看|链接|试听', label)) else ''

    parts = re.split(r'(```[\s\S]*?```)', text)
    for index in range(0, len(parts), 2):
        part = MUSIC_REPLY_TOKEN.sub(substitute, parts[index])
        if streaming:
            # A partial link or card instruction must not appear while tokens arrive.
            part = re.sub(r'【(?:音(?:乐(?:卡[^】]*)?)?)?\Z|\[[^\]\n]*\]\(https?://[^)\n]*\Z', '', part)
        parts[index] = part
    if prompt and not songs and MUSIC_SHARE_INTENT.search(prompt) and not POETRY_CONVERSATION.search(prompt):
        clauses = re.split(r'[，,。；;！？?!\n]', prompt)
        artist = next((match['artist'].strip() for clause in clauses
                       if (match := ARTIST_LISTENING.fullmatch(clause.strip()))), '')
        for index in range(0, len(parts), 2):
            songs.extend(SongReference(match[1].strip(), artist) for match in SONG_TITLE.finditer(parts[index]))
    return ''.join(parts).strip(), tuple(dict.fromkeys(songs)), removed


def music_metadata(client, item, cancelled):
    if item.source == '网易云音乐':
        track = music_track_id(item)
        _, data, _ = bounded_get(client, 'https://music.163.com/api/song/detail', cancelled,
                                 params={'ids': f'[{track}]'}, headers=NETEASE_HEADERS, limit=144_000)
        for row in catalog_rows(data, 'songs'):
            if str(row.get('id')) != track:
                continue
            card = netease_song_card(row)
            album = row.get('album') or row.get('al') or {}
            if card:
                return card.title, card.detail, card.album, album.get('picUrl') if isinstance(album, dict) else ''
        _, raw, _ = bounded_get(client, item.url, cancelled, limit=350_000)
        parser = PageMetadata()
        parser.feed(raw.decode('utf-8', errors='replace'))
        data = parser.meta
        if data.get('og:type') != 'music.song':
            return ()
        return tuple(data.get(key) for key in ('og:title', 'og:music:artist', 'og:music:album', 'og:image'))
    if item.source == 'Apple Music':
        parsed = urlsplit(item.url)
        track = apple_track_id(parsed)
        _, raw, _ = bounded_get(client, 'https://itunes.apple.com/lookup', cancelled, limit=115_000,
                                params={'id': track, 'country': parsed.path.split('/')[1].upper()})
        try:
            rows = json.loads(raw).get('results', [])
            data = next((row for row in rows if str(row.get('trackId')) == track), {})
        except (ValueError, AttributeError, TypeError):
            return ()
        if data:
            return tuple(data.get(key) for key in ('trackName', 'artistName', 'collectionName', 'artworkUrl100'))
    return ()


def enrich_card(client, item, cancelled):
    if item.kind == 'video':
        video = canonical_video(item.url)
        if not video and urlsplit(item.url).hostname == 'b23.tv':
            _, _, location = bounded_get(client, item.url, cancelled, limit=16_000)
            if location:
                video = canonical_video(urljoin(item.url, location))
        if not video:
            return item
        platform, code, url = video
        rows = read_video(client, platform, url, cancelled)
        row = rows[0] if rows else {}
        cover = row.get('thumbnail') or (f'https://i.ytimg.com/vi/{code}/hqdefault.jpg' if platform == 'YouTube' else '')
        return replace(item, url=url, title=cleaned(row.get('title'), 160) or item.title,
                       detail=cleaned(row.get('summary'), 400) or item.detail,
                       author=cleaned(row.get('author'), 100),
                       duration=row.get('duration_seconds', 0),
                       image=card_cover(client, cover, item.source, cancelled))
    if not (fields := music_metadata(client, item, cancelled)):
        return item
    title, artist, album, cover = fields
    return replace(item, title=cleaned(title, 160) or item.title, detail=cleaned(artist, 160) or item.detail,
                   album=cleaned(album, 160), image=card_cover(client, cover, item.source, cancelled))


def fetch_card_previews(cards, cancelled, proxy=None):
    budget = LookupBudget(cancelled, 4)
    with httpx.Client(proxy=proxy, trust_env=False, follow_redirects=False,
                      timeout=httpx.Timeout(2.4, connect=1.6), event_hooks={'request': [budget.set_timeout]},
                      headers={'User-Agent': MEDIA_AGENT}) as client:
        return tuple(enrich_card(client, card, budget) if not budget.is_set() else card for card in cards)


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


def catalog_rows(data, *path):
    try:
        value = json.loads(data)
        for key in path:
            value = value[key]
    except (KeyError, ValueError, TypeError, RecursionError):
        return ()
    return tuple(row for row in value if isinstance(row, dict)) if isinstance(value, list) else ()


def choose_music(client, topic, seen, cancelled, *, match_topic=False, song=None, excluded=()):
    if re.search(r'[\u3040-\u30ff]', topic):
        markets = ('CN', 'JP', 'HK', 'TW', 'US')
    elif re.search(r'[\u3400-\u9fff]', topic):
        markets = ('CN', 'HK', 'TW', 'US', 'JP')
    else:
        markets = ('CN', 'US', 'HK', 'TW', 'JP')
    seen_tracks = {music_track_id(card) for url in seen
                   if (card := link_card(url)) and card.source == 'Apple Music'}
    local_tracks = {}
    regional = None
    searches = [(country, topic) for country in markets]
    if song and song.artist:
        searches.extend((country, song.title) for country in markets[:2])
    for country, query in searches:
        if cancelled.is_set():
            break
        _, data, _ = bounded_get(client, APPLE_SEARCH, cancelled,
                                 params={'term': query, 'media': 'music', 'entity': 'song',
                                         'country': country, 'limit': 20}, limit=230_000)
        candidates = []
        for row in catalog_rows(data, 'results'):
            if row.get('kind') != 'song':
                continue
            try:
                card = apple_song_card(row, country)
            except (TypeError, ValueError):
                continue
            track_id = music_track_id(card)
            if track_id in seen_tracks or music_identity(card) in excluded:
                continue
            matched = matches_song(song, card) if song else (not match_topic or matches_music(topic, card))
            if country == 'CN':
                if matched:
                    return card
                continue
            if matched or (song and (same_title(song.title, card.title)
                                     or (song.artist and matches_artist(song.artist, card.detail)))):
                candidates.append((track_id, card, matched))
        pending = tuple(dict.fromkeys(track for track, _, _ in candidates if track not in local_tracks))
        if pending:
            found = apple_china_tracks(client, pending, cancelled)
            local_tracks.update((track, found.get(track)) for track in pending)
        for track, card, matched in candidates:
            china = local_tracks[track]
            if china and music_identity(china) not in excluded:
                localized = (song and (same_title(song.title, china.title) or same_title(song.title, card.title))
                             and (not song.artist or matches_artist(song.artist, china.detail, (card.detail,))))
                if matched or localized:
                    return china
            if matched and regional is None:
                regional = card
    return regional


def apple_song_card(row, country):
    title = cleaned(row.get('trackName'), 160)
    artist = row.get('artistName')
    track = str(row.get('trackId', ''))
    try:
        item = music_card(row.get('trackViewUrl', ''), title, artist)
        if track and music_track_id(item) != track:
            raise ValueError('Track ID differs from its catalog link')
    except ValueError:
        if not re.fullmatch(r'[0-9]+', track):
            raise
        item = music_card(f'https://music.apple.com/{country.lower()}/song/{quote(title, safe="")}/{track}',
                          title, artist)
    duration = row.get('trackTimeMillis')
    return replace(item, album=cleaned(row.get('collectionName'), 160),
                   duration=duration // 1000 if type(duration) is int and duration > 0 else 0)


def apple_china_tracks(client, tracks, cancelled):
    _, data, _ = bounded_get(client, 'https://itunes.apple.com/lookup', cancelled,
                             params={'id': ','.join(tracks), 'country': 'CN'}, limit=230_000)
    result = {}
    for row in catalog_rows(data, 'results'):
        track = str(row.get('trackId', ''))
        if row.get('kind') != 'song' or track not in tracks:
            continue
        try:
            card = apple_song_card(row, 'CN')
        except (TypeError, ValueError):
            continue
        if music_track_id(card) == track and urlsplit(card.url).path.startswith('/cn/'):
            result[track] = card
    return result


def normalized_music(text):
    text = unicodedata.normalize('NFKC', text)
    if os.name == 'nt' and re.search(r'[\u3400-\u9fff]', text):
        # Apple catalog metadata can use traditional characters for a simplified Chinese query.
        target = ctypes.create_unicode_buffer(len(text) * 2 + 1)
        if ctypes.windll.kernel32.LCMapStringW(0x0804, 0x02000000, text, -1, target, len(target)):
            text = target.value
    return re.sub(r'[\W_]+', '', text.casefold())


def same_title(first, second):
    first = normalized_music(first)
    return bool(first) and first == normalized_music(second)


def artist_names(credit):
    return {name for value in (credit, *ARTIST_SEPARATOR.split(credit)) if (name := normalized_music(value))}


def matches_artist(wanted, credit, aliases=()):
    known = artist_names(credit)
    for alias in aliases:
        known.update(artist_names(alias))
    parts = {normalized_music(value) for value in ARTIST_SEPARATOR.split(wanted) if value.strip()}
    return normalized_music(wanted) in known or bool(parts and parts <= known)


def matches_song(song, card, aliases=()):
    return (same_title(song.title, card.title)
            and (not song.artist or matches_artist(song.artist, card.detail, aliases)))


def music_identity(card):
    return (normalized_music(card.title),
            frozenset(normalized_music(part) for part in ARTIST_SEPARATOR.split(card.detail) if part.strip()))


def matches_music(topic, card):
    title, artist = normalized_music(card.title), normalized_music(card.detail)
    term = normalized_music(topic)
    if term and (term in title or term in artist or term in title + artist or term in artist + title):
        return True
    words = [normalized_music(word) for word in re.split(r'\s+|的', topic)]
    return bool(term) and all(word in title or word in artist for word in words if word)


def netease_credits(row):
    artists = row.get('artists') or row.get('ar')
    return tuple(artist for artist in artists if isinstance(artist, dict)) if isinstance(artists, list) else ()


def netease_song_card(row):
    track = str(row.get('id', ''))
    title = cleaned(row.get('name'), 160)
    credit = ' & '.join(name for artist in netease_credits(row) if (name := cleaned(artist.get('name'), 120)))
    if not re.fullmatch(r'[0-9]+', track) or not title or not credit:
        return None
    album = row.get('album') or row.get('al')
    duration = row.get('duration') or row.get('dt')
    return replace(music_card('https://music.163.com/song?id=' + track, title, credit),
                   album=cleaned(album.get('name'), 160) if isinstance(album, dict) else '',
                   duration=duration // 1000 if type(duration) is int and duration > 0 else 0)


def netease_aliases(artist):
    for key in ('alias', 'alia', 'transNames', 'tns', 'trans'):
        entries = artist.get(key)
        if isinstance(entries, str):
            entries = [entries]
        if isinstance(entries, list):
            yield from (part for part in entries if isinstance(part, str) and part.strip())


def netease_artist_ids(client, artist, cancelled):
    _, data, _ = bounded_get(client, NETEASE_SEARCH, cancelled,
                             params={'s': artist, 'type': 100, 'limit': 8}, headers=NETEASE_HEADERS, limit=125_000)
    return {row['id'] for row in catalog_rows(data, 'result', 'artists')
            if type(row.get('id')) is int
            and matches_artist(artist, cleaned(row.get('name'), 120), netease_aliases(row))}


def netease_catalog_match(client, topic, cancelled, song, seen, excluded):
    query_terms = tuple(dict.fromkeys((topic, song.title))) if song and song.artist else (topic,)
    artist_ids = {}
    for query in query_terms:
        _, raw, _ = bounded_get(client, NETEASE_SEARCH, cancelled,
                                params={'s': query, 'type': 1, 'limit': 25}, headers=NETEASE_HEADERS, limit=240_000)
        unverified = []
        for row in catalog_rows(raw, 'result', 'songs'):
            card = netease_song_card(row)
            if not card or card.url in seen or music_identity(card) in excluded:
                continue
            if song and not same_title(song.title, card.title):
                continue
            credits = netease_credits(row)
            aliases = tuple(alias for artist in credits for alias in netease_aliases(artist))
            if song:
                matched = matches_artist(song.artist, card.detail, aliases) if song.artist else True
            else:
                matched = matches_music(topic, card) or matches_artist(topic, card.detail, aliases)
            if matched:
                return card
            unverified.append((card, {artist['id'] for artist in credits if type(artist.get('id')) is int}))
        names = ARTIST_SEPARATOR.split(song.artist) if song else [topic]
        names = [name.strip() for name in names if name.strip()]
        if unverified and names and all(re.fullmatch(ARTIST_NAME, name) for name in names):
            for name in names:
                if name not in artist_ids:
                    artist_ids[name] = netease_artist_ids(client, name, cancelled)
            for card, ids in unverified:
                if all(matches_artist(name, card.detail) or bool(artist_ids[name] & ids) for name in names):
                    return card
    return None


def choose_netease_music(client, topic, cancelled, *, song=None, seen=(), excluded=()):
    if topic and (item := netease_catalog_match(client, topic, cancelled, song, seen, excluded)):
        return item
    if cancelled.is_set():
        return None
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
    for url in [url for url in dict.fromkeys(urls) if url not in seen][:4]:
        if cancelled.is_set():
            break
        item = link_card(url)
        fields = music_metadata(client, item, cancelled) if item else ()
        if not fields:
            continue
        try:
            card = replace(music_card(url, fields[0], fields[1]), album=cleaned(fields[2], 160))
        except ValueError:
            continue
        matches = matches_song(song, card) if song else (not topic or matches_music(topic, card))
        if matches and music_identity(card) not in excluded:
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
    def __init__(self, url, topic, required_topic='', avoid_topics=()):
        super().__init__()
        self.url = url
        self.topic = topic
        self.required_topic = required_topic
        self.avoid_topics = avoid_topics
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
                self.card = first_video([{'url': self.anchor, 'title': title}], 'bilibili', (),
                                        required_topic=self.required_topic, avoid_topics=self.avoid_topics)

    def handle_endtag(self, tag):
        if tag == 'a':
            self.anchor = ''


def search_bilibili(client, topic, cancelled, *, required_topic='', avoid_topics=()):
    url = 'https://search.bilibili.com/all'
    query = '小猫 猫咪 日常' if topic in {'小猫', '小猫咪', '猫咪'} else topic
    _, body, _ = bounded_get(client, url, cancelled, params={'keyword': query}, limit=850_000)
    parser = BilibiliResults(url, topic, required_topic, avoid_topics)
    parser.feed(body.decode('utf-8', errors='replace'))
    return parser.card


def avoids_topics(text, topics):
    text = text.casefold()
    return not any(topic.casefold() in text for topic in topics)


def first_video(rows, source, seen, *, required_topic='', avoid_topics=(), preference_topic=''):
    for row in rows:
        if not cleaned(row.get('title'), 160):
            continue
        try:
            card = video_card(row['url'], row['title'], row.get('summary', ''))
        except ValueError:
            continue
        if required_topic and (not matches_music(required_topic, card) or re.search(
                r'翻唱|伴奏|AI[翻唱卡]|\b(?:cover|karaoke|instrumental)\b', card.title, re.I)):
            continue
        if preference_topic and not is_relevant(preference_topic, {'title': card.title,
                                                                  'summary': card.detail, 'url': card.url}):
            continue
        if card.source == source and card.key not in seen and avoids_topics(card.title + ' ' + card.detail, avoid_topics):
            return card
    return None


def youtube_search(client, topic, seen, cancelled, *, required_topic='', avoid_topics=(), preference_topic=''):
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
                                     'title': title}], 'youtube', seen, required_topic=required_topic,
                                   avoid_topics=avoid_topics, preference_topic=preference_topic)
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


def choose_video(client, source, topic, seen, cancelled, *, allow_popular=True, allow_proxy=True,
                 required_topic='', avoid_topics=(), preference_topic=''):
    filters = {'required_topic': required_topic, 'avoid_topics': avoid_topics,
               'preference_topic': preference_topic}
    card = first_video(video_search_rss(client, source, topic, cancelled), source, seen, **filters)
    if card or cancelled.is_set():
        return card
    site = 'bilibili.com' if source == 'bilibili' else 'youtube.com'
    card = first_video(bing_search(client, f'site:{site} {topic}', cancelled, BING), source, seen,
                       **filters)
    if card or cancelled.is_set():
        return card
    if source == 'bilibili':
        return first_video(bilibili_popular(client, cancelled), source, seen,
                           avoid_topics=avoid_topics) if allow_popular else None
    proxy = existing_local_proxy() if allow_proxy else None
    if proxy:
        with httpx.Client(proxy=proxy, trust_env=False, timeout=httpx.Timeout(6, connect=3),
                          headers=client.headers) as routed:
            card = youtube_search(routed, topic, seen, cancelled, **filters)
        if card or cancelled.is_set():
            return card
    return youtube_search(client, topic, seen, cancelled, **filters)


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


def choose_catalog_music(platform, recent_music, tracks):
    last_shared = {}
    for index, url in enumerate(recent_music):
        card = link_card(url)
        if card and card.kind == 'music':
            last_shared[(card.source, music_track_id(card))] = index
    choices = []
    for title, artist, apple_id, netease_id in tracks:
        apple = f'https://music.apple.com/cn/song/{quote(title)}/{apple_id}'
        netease = f'https://music.163.com/song?id={netease_id}'
        rank = max(last_shared.get(('Apple Music', apple_id), -1),
                   last_shared.get(('网易云音乐', netease_id), -1))
        choices.append((rank, netease if platform == 'netease' else apple, title, artist))
    _, url, title, artist = min(choices, key=lambda choice: choice[0])
    return music_card(url, title, artist)


def media_client(proxy, budget):
    return httpx.Client(proxy=proxy, trust_env=False, timeout=httpx.Timeout(1.8, connect=1.4),
                        headers={'User-Agent': MEDIA_AGENT}, event_hooks={'request': [budget.set_timeout]},
                        follow_redirects=False)


def find_music_request(request, cancelled, preferred_music, recent_music, proxy, *, selected=()):
    first = request.platform or ('netease' if preferred_music == 'netease' else 'apple')
    style = request.music_style
    song = request.song
    selected_ids = {music_track_id(card) for card in selected}
    if request.music_card_only and song is None and (style == 'japanese' or JAPANESE_STYLE.fullmatch(request.query)):
        catalog = [track for track in JAPANESE_TRACKS if not selected_ids.intersection(track[2:])
                   and avoids_topics(' '.join(track[:2]), request.avoid_topics)]
        if catalog or not selected:
            return choose_catalog_music(first, recent_music, catalog) if catalog else None
        style = 'japanese'
    if song is not None:
        if song.url and (card := link_card(song.url)) and card.kind == 'music':
            provider = 'netease' if card.source == '网易云音乐' else 'apple'
            if not request.platform or request.platform == provider:
                for known in POP_TRACKS + JAPANESE_TRACKS:
                    if music_track_id(card) != known[3 if provider == 'netease' else 2]:
                        continue
                    item = music_card(card.url, known[0], known[1])
                    if not song.title or matches_song(song, item):
                        return item
                for route in (None, proxy) if proxy else (None,):
                    if cancelled.is_set():
                        break
                    budget = LookupBudget(cancelled, 2.5)
                    with media_client(route, budget) as client:
                        fields = music_metadata(client, card, budget)
                    if fields and fields[0]:
                        item = replace(music_card(card.url, fields[0], fields[1]), album=cleaned(fields[2], 160))
                        if not song.title or matches_song(song, item):
                            return item
                        break
        if not song.title:
            return None
        catalog = tuple(track for track in POP_TRACKS + JAPANESE_TRACKS
                        if matches_song(song, SharedMedia('music', '', track[0], track[1])))
        if catalog:
            return choose_catalog_music(first, (), catalog)
    if request.query and song is None and not style:
        topic = normalized_music(request.query)
        played = {music_track_id(card) for url in recent_music if (card := link_card(url)) and card.kind == 'music'}
        catalog = [track for track in POP_TRACKS + JAPANESE_TRACKS if normalized_music(track[1]) == topic
                   and not played.intersection(track[2:])
                   and avoids_topics(' '.join(track[:2]), request.avoid_topics)]
        if catalog:
            return choose_catalog_music(first, recent_music, catalog)
    if not request.query and style in ('', 'pop'):
        catalog = [track for track in POP_TRACKS if not selected_ids.intersection(track[2:])
                   and avoids_topics(' '.join(track[:2]), request.avoid_topics)]
        if catalog or not selected:
            return choose_catalog_music(first, recent_music, catalog) if catalog else None
    strict_platform = bool(request.platform and not request.inherited_platform)
    providers = (first,) if strict_platform else (first, 'apple' if first == 'netease' else 'netease')
    term = 'J-Pop' if style == 'japanese' and selected else request.search_term()
    excluded = {music_identity(card) for card in selected}
    regional = None
    for provider in providers:
        for route in (None, proxy) if proxy else (None,):
            if cancelled.is_set():
                return regional
            budget = LookupBudget(cancelled, 4.5)
            with media_client(route, budget) as client:
                if style == 'japanese' and not selected:
                    item = choose_catalog_music(provider, recent_music, JAPANESE_TRACKS)
                    if fields := music_metadata(client, item, budget):
                        title, artist, album, _ = fields
                        if title and artist:
                            return replace(item, detail=cleaned(artist, 160), album=cleaned(album, 160))
                    continue
                elif provider == 'netease':
                    seen = recent_music if not song else ()
                    item = choose_netease_music(client, term, budget, song=song, seen=seen, excluded=excluded)
                else:
                    item = choose_music(client, term, () if song else recent_music, budget, song=song,
                                        match_topic=bool(request.query), excluded=excluded)
                if item and avoids_topics(item.title + ' ' + item.detail, request.avoid_topics):
                    if item.source == 'Apple Music' and not urlsplit(item.url).path.startswith('/cn/'):
                        regional = regional or item
                        break
                    return item
    if regional:
        return regional
    if strict_platform or song or request.music_card_only:
        return None

    if style == 'japanese':
        song = choose_catalog_music(first, recent_music, JAPANESE_TRACKS)
        term = f'{song.detail} {song.title}'
    for source in ('bilibili', 'youtube'):
        if cancelled.is_set():
            return None
        budget = LookupBudget(cancelled, 2.8)
        with media_client(proxy if source == 'youtube' else None, budget) as client:
            if source == 'bilibili':
                item = search_bilibili(client, term, budget, required_topic=term, avoid_topics=request.avoid_topics)
                if item:
                    return item
            item = choose_video(client, source, term + ' MV', (), budget, allow_popular=False,
                                allow_proxy=False, required_topic=term, avoid_topics=request.avoid_topics)
            if item:
                return item
    return None


def find_music_cards(request, cancelled, *, allow_proxy=False, active_proxy=None, preferred_music='', recent_music=()):
    if cancelled.is_set():
        return ()
    budget = LookupBudget(cancelled, 12)
    proxy = (active_proxy or existing_local_proxy()) if allow_proxy else None
    request = replace(request, music_card_only=True)
    selected = []
    identities = set()
    named = bool(request.songs)
    items = request.songs if named else (request.song,) * request.count
    for song in items:
        if budget.is_set():
            break
        query = ' '.join(filter(None, (song.artist, song.title))) if song else request.query
        item = find_music_request(replace(request, song=song, query=query), budget, preferred_music,
                                  recent_music + tuple(card.url for card in selected), proxy, selected=selected)
        if not item or item.kind != 'music':
            if named:
                continue
            break
        identity = music_identity(item)
        if identity in identities:
            if named:
                continue
            break
        identities.add(identity)
        selected.append(item)
    return tuple(selected)


def find_requested_media(request, cancelled, *, allow_proxy=False, active_proxy=None, preferred_music='',
                         recent_music=()):
    if cancelled.is_set():
        return None
    proxy = (active_proxy or existing_local_proxy()) if allow_proxy else None
    if request.kind == 'music':
        return find_music_request(request, cancelled, preferred_music, recent_music, proxy)
    term = request.search_term()
    cancelled = LookupBudget(cancelled, 4.5)
    order = (proxy, None) if request.platform == 'youtube' else (None, proxy)
    for route in order if proxy else (None,):
        if cancelled.is_set():
            break
        with media_client(route, cancelled) as client:
            if request.kind == 'video':
                source = request.platform or 'bilibili'
                filters = {'avoid_topics': request.avoid_topics} if request.avoid_topics else {}
                item = search_bilibili(client, term, cancelled, **filters) if source == 'bilibili' else None
                if not item and not cancelled.is_set():
                    if request.preference_topic:
                        filters['preference_topic'] = request.preference_topic
                    item = choose_video(client, source, term, (), cancelled,
                                        allow_popular=not request.query, allow_proxy=False, **filters)
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
