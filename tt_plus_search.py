"""Conversational style and transcript formatting for TT."""
from dataclasses import replace
import json
import re

import httpx

from PySide6.QtGui import QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QPushButton
from tt_edge_research import (
    NO_WEB, PRIVATE_QUERY, RESEARCH_RULE, ROUTING_INSTRUCTIONS, URL, LookupBudget, collect, lookup_for, research_message,
    routed_lookup, routing_input, routing_plan, sources_footer,
)
from tt_personality import PersonalityDialog, load_personality
from tt_share_content import (
    MUSIC_PLATFORM, ShareRequest, find_music_cards, find_requested_media, music_reply,
    recent_chat_media, routed_share, share_request,
)


VIDEO_FOLLOWUP = re.compile(r"这个视频|这段视频|刚才.{0,6}(?:视频|链接|字幕)|视频里|它讲了什么|视频作者|原视频|字幕")

DEFAULT_PERSONALITY = """
闲聊时用 TT 的第一人称和用户说话。TT 是陪在桌边、有一点慢热的桌宠，
亲近后会轻轻开玩笑，有自己的偏好和小情绪。用口语，可以认真听，也可以表达不同意见。
用户问你的心情或「今天过得怎么样」时，先直接说 TT 此刻的感受或桌边的氛围，
比如「今天挺安静的。你一来，桌边就热闹一点了。」这种问候不需要解释 AI 能力。
遇到实际能力或事实的问题要如实回答。别编造线下活动、外部观察或彼此过去没发生的事。
别用括号描写动作，也别把闲聊写成报告或给用户一组选项。
"""

CHAT_FLOW = """
日常聊天从用户刚说的那一点接话，用和当前关系、情绪相称的口语。普通闲聊一句说得完就说一句；
有另一个念头再另发一句，分条用空行。别把用户的话概括一遍再回应，别套用固定的安慰、
分析、建议或结尾格式。有值得接着说的事才追问，没有就自然收住。
用已经确认的相关记忆接续具体的事情，也用记忆里的喜好决定选什么，别例行提起记忆来源。
认真答疑、清单、代码和用户明确要求详细回答的情形按任务需要展开。
"""
CHAT_STYLE = DEFAULT_PERSONALITY + CHAT_FLOW
MUSIC_SHARING = (
    '当你向用户分享或推荐可收听的歌曲时，每首单独输出一个【音乐卡｜歌名｜歌手】。'
    '应用会逐首匹配并展示单曲卡片。正文可以简短说推荐理由，但不要输出音乐网址、搜索链接或平台入口，'
    '也不要声称已经找到或播放了歌曲。普通地聊到歌曲时可以直接写歌名。'
)


def decoration_instruction(flags):
    names = {'kaomoji': '颜文字（如 (｡･ω･｡)）', 'emoji': 'Emoji（如 ✨）'}
    use = [label for key, label in names.items() if flags.get(key) is True]
    avoid = [label for key, label in names.items() if flags.get(key) is False]
    lines = []
    if use:
        lines.append('用户偏好的文字聊天风格：普通闲聊请自然加入一两个 ' + '、'.join(use)
                     + '，让它们跟随内容和语气，避免固定贴同一个符号。正式答复、严肃难过的话题、代码或结构化输出保持简洁。')
    if avoid:
        lines.append('这一轮的文字正文请省略 ' + '、'.join(avoid) + '。')
    return '\n'.join(lines)


def wants_links(prompt):
    if re.search(r"(?:不要|别|不用|不需要|不想要|去掉|删除|不显示).{0,12}(?:链接|网址|URL|url)", prompt):
        return False
    return bool(re.search(r"(?:给|发|附|提供|打开|找).{0,16}(?:链接|网址)|(?:链接|网址).{0,8}(?:给我|发来)|https?://|(?:找|听).{0,12}(?:歌|音乐)|(?:找|看).{0,12}(?:视频|教程)", prompt))


def plain_words(text, *, markdown=False):
    parts = re.split(r"(```[\s\S]*?```)", text)
    for index in range(0, len(parts), 2):
        if not markdown:
            parts[index] = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", parts[index])
            parts[index] = re.sub(r"(?<![\w*])\*([^\s*][^*\n]*?)\*(?![\w*])", r"\1", parts[index])
        # Citation domains are presentation noise even when a requested URL is allowed.
        domain = r"(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}(?:/[^\s()（）]*)?"
        parts[index] = re.sub(r"[（(]\s*(?:(?:来源|参考|source)\s*[:：]\s*)?" + domain + r"\s*[）)]", "", parts[index], flags=re.IGNORECASE)
    return "".join(parts)


def without_links(text, *, markdown=False):
    parts = re.split(r"(```[\s\S]*?```)", plain_words(text, markdown=markdown))
    for index in range(0, len(parts), 2):
        parts[index] = _without_links_prose(parts[index])
    return "".join(parts).rstrip()


def _without_links_prose(text):
    domain = r"(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}(?:/[^\s()（）]*)?"
    text = re.sub(r"[（(]\s*(?:(?:来源|参考|source)\s*[:：]\s*)?" + domain + r"\s*[）)]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"!?\[([^\]\n]*)\]\(https?://[^\s)]*\)", r"\1", text)
    text = re.sub(r"https?://[^\s<>\u3000，。！？；、）】]+|www\.[^\s<>\u3000，。！？；、）】]+", "", text)
    text = re.sub(r"(?m)^\s*(?:来源|Sources|TT 找到的歌曲或视频页面)(?:（本次检索）)?\s*$", "", text)
    return text


def install_chat_style(base):
    previous_worker, previous_window = base.RequestWorker, base.ChatWindow

    class PlusWorker(previous_worker):
        lookup = None
        routing_prompt = ""
        research_result = None
        research_footer = ""
        allow_source_links = False
        persona_prompt = ""
        media_request = None
        media_item = None
        media_items = ()
        media_web_enabled = False
        compose_music_cards = False
        media_prompt = ''
        semantic_media = False
        media_allow_proxy = False
        media_active_proxy = None
        media_preferred_music = ''
        media_recent_music = ()
        read_aloud_text = ''

        def run(self):
            if self.read_aloud_text:
                if not self.cancelled.is_set():
                    self.publish('completed', self.read_aloud_text)
                return
            request = self.media_request
            if request is None:
                return super().run()
            self.complete_media(request)

        def complete_media(self, request):
            if self.cancelled.is_set():
                return
            if request.song is not None and not request.song.title:
                self.publish('completed', '你想听哪一首？告诉我歌名或第几首就行。')
                return
            if not self.media_web_enabled:
                self.publish('completed', '打开「聊天与语音设置」里的「启用自动联网查」，我就能帮你找来发给你。')
                return
            self.publish('phase_changed', {'video': '正在查找视频…', 'music': '正在查找歌曲…',
                                           'image': '正在查找图片…'}[request.kind])
            if request.kind == 'music' and (request.count > 1 or request.songs):
                cards = self.resolve_music_cards(request)
                if not self.cancelled.is_set():
                    if cards:
                        answer = '' if len(cards) >= request.count else f'这次找到了 {len(cards)} 首可发的歌。'
                    else:
                        answer = '我这次没找到可发的单曲卡片。'
                    self.publish('completed', answer)
                return
            try:
                self.media_item = find_requested_media(request, self.cancelled, allow_proxy=self.media_allow_proxy,
                                                       active_proxy=self.media_active_proxy,
                                                       preferred_music=self.media_preferred_music,
                                                       recent_music=self.media_recent_music)
            except (OSError, ValueError, httpx.RequestError):
                self.media_item = None
            if self.cancelled.is_set():
                return
            if request.kind == 'music' and self.media_item and self.media_item.kind != 'music':
                self.media_item = None
            if self.media_item is None:
                if request.kind == 'music':
                    name = f'《{request.song.title}》' if request.song else ''
                    answer = f'我这次没找到{name}可发的单曲卡片。'
                else:
                    label = {'video': '视频', 'image': '图片'}[request.kind]
                    search_url = request.search_url(self.media_preferred_music)
                    answer = f'我这次没找到合适的{label}。你可以[打开搜索结果]({search_url})看看。'
            elif request.kind == 'video':
                answer = '找到这个视频啦。'
            elif request.kind == 'image':
                url = self.media_item.url.replace('(', '%28').replace(')', '%29')
                answer = f'给你找到一张图。[查看图片来源]({url})'
            else:
                answer = ''
            self.publish('completed', answer)

        def resolve_music_cards(self, request):
            try:
                self.media_items = find_music_cards(request, self.cancelled,
                                                    allow_proxy=self.media_allow_proxy,
                                                    active_proxy=self.media_active_proxy,
                                                    preferred_music=self.media_preferred_music,
                                                    recent_music=self.media_recent_music)
            except (OSError, ValueError, httpx.RequestError):
                self.media_items = ()
            return self.media_items

        def chat_role(self):
            if not self.persona_prompt:
                return super().chat_role()
            return (
                "以用户设定的角色视角进行对话。角色内的聊天使用第一人称，"
                "设定里写明的称呼、关系、个人习惯和事实直接用在回应中。"
                "可以根据设定补足合理的虚构细节，并在后续对话中保持连贯。"
                "聊天记录和相关记忆用于接续上下文，若与当前角色设定冲突，以当前设定为准。"
                "角色内的问题按设定中的情境理解，保持角色视角。只有用户明确跳出角色、"
                "询问程序的实际能力或外部事实、要求执行现实操作时，才按现实层面回答。"
                "\n\n【用户保存的角色设定】\n"
                + self.persona_prompt
                + "\n\n【应用规则】"
            )

        def route_request(self, client, model):
            request = {
                "model": model, "input": [{"role": "user", "content": self.routing_prompt}],
                "instructions": ROUTING_INSTRUCTIONS,
                "store": False, "stream": True, "max_output_tokens": 420,
                "reasoning": {"effort": "low"},
            }
            budget = LookupBudget(self.cancelled, 4)
            try:
                with client.stream("POST", base.API + "/responses", json=request,
                                   timeout=httpx.Timeout(3.2, connect=1.5)) as response:
                    if response.status_code != 200:
                        return {}
                    for event in base.stream_events(response.iter_lines()):
                        if budget.is_set():
                            break
                        if event.get("type") == "response.completed":
                            return routing_plan(base.completed_text(event.get("response", {})))
                        if event.get("type") in ("response.failed", "response.incomplete", "error"):
                            break
            except (httpx.RequestError, ValueError):
                pass
            return {}

        def prepare_chat_payload(self, payload, proxy, *, client=None):
            if self.routing_prompt and client is not None and not self.cancelled.is_set():
                self.publish('phase_changed', '正在理解你的请求…')
                plan = self.route_request(client, payload["model"])
                if self.cancelled.is_set():
                    return None
                if self.semantic_media and (request := routed_share(plan, self.media_prompt)):
                    if isinstance(request, ShareRequest):
                        self.media_request = request
                        self.tt_show_links = self.allow_source_links
                        self.complete_media(request)
                    else:
                        question = {'music': '你想听哪首歌？说歌名或第几首就行。',
                                    'video': '你想看什么视频？给我个标题或关键词就行。',
                                    'image': '你想要什么样的图片？给我个主题就行。'}[request]
                        self.publish('completed', question)
                    return None
                self.lookup = routed_lookup(plan, self.lookup)
            if self.lookup:
                if self.allow_source_links:
                    self.tt_show_links = True
                rule = "When the app supplies public sources, cite their numbers as [1], [2]."
            elif getattr(self, "tt_show_links", False):
                rule = "Include requested URLs and source links."
            else:
                rule = ("The user wants natural conversational plain text without citations. Do not include web "
                        "addresses, parenthesized website domains, footnotes, or a sources appendix. "
                        "Answer conversationally in the selected reply language.")
            rule += " Do not use asterisks for bold, italics, roleplay actions or emphasis in ordinary conversation. Preserve valid syntax in code or mathematics when the user asks for those."
            decoration = decoration_instruction(getattr(self, "reply_decorations", {}))
            style = CHAT_FLOW if self.persona_prompt else CHAT_STYLE
            priority = ("角色事实和惯用表达按【用户保存的角色设定】执行。默认闲聊规则和示例只补充未规定的部分。"
                        "用户对本轮表达方式有明确要求时，按本轮要求回答。" if self.persona_prompt else "")
            music = MUSIC_SHARING if self.compose_music_cards else ''
            instructions = "\n".join(filter(None, (payload.get("instructions"), rule, style, decoration, priority, music)))
            payload = {**payload, "instructions": instructions}
            if self.lookup is None:
                return payload
            if self.research_result is None:
                self.publish('phase_changed', '正在读取公开资料…')
                self.research_result = collect(self.lookup, self.cancelled, proxy)
            result = self.research_result
            if getattr(self, "tt_show_links", False):
                self.research_footer = sources_footer(self.lookup, result)
            messages = payload["input"]
            return {**payload, "input": [*messages[:-1], research_message(result), messages[-1]],
                    "instructions": payload["instructions"] + "\n" + RESEARCH_RULE}

        def chat_reply(self, response):
            answer = super().chat_reply(response)
            if not answer:
                return answer
            answer += self.research_footer
            if not self.compose_music_cards:
                return answer
            answer, songs, removed_links = music_reply(answer, prompt=self.media_prompt if self.media_web_enabled else '')
            if songs and self.media_web_enabled:
                self.publish('phase_changed', '正在查找歌曲卡片…')
                mentioned = MUSIC_PLATFORM.search(self.media_prompt)
                platform = ('netease' if mentioned['name'].startswith('网易云') else 'apple') if mentioned else ''
                cards = self.resolve_music_cards(ShareRequest('music', '', platform, count=len(songs), songs=songs))
                if cards:
                    return answer or '歌给你放在下面啦。'
            if songs or removed_links:
                notice = '我这次没找到可发的单曲卡片。' if self.media_web_enabled else '开启自动联网查后，我可以帮你找歌曲卡片。'
                return answer + ('\n\n' if answer else '') + notice
            return answer

    class PlusWindow(previous_window):
        def __init__(self):
            super().__init__()
            self._reply_links = False
            self._reply_start = None
            self._video_lookup = None
            settings = getattr(self, "settings_page", self)
            edit_personality = QPushButton("自定义人格")
            edit_personality.clicked.connect(lambda: PersonalityDialog(self).exec())
            settings.layout().addWidget(edit_personality)

        def start_worker(self, worker):
            if isinstance(worker, PlusWorker):
                if worker.mode == "chat":
                    if worker.read_aloud_text:
                        self._reply_links = False
                        self._reply_start = None
                        return super().start_worker(worker)
                    prompt = self.pending_prompt
                    newest = self.pending_request or prompt
                    worker.persona_prompt = load_personality(self.db)
                    toggle = getattr(self, "web_enabled", None)
                    context = self.history if self.pending_context is None else self.pending_context
                    worker.media_request = (share_request(newest, history=context, exact_tracks=True) if hasattr(self, 'media')
                                            and not NO_WEB.search(prompt) and not self.pending_images else None)
                    memory = getattr(self, 'long_memory', None)
                    if memory and worker.media_request:
                        worker.media_request = memory.personalize_recommendation(worker.media_request, prompt)
                    if worker.media_request and worker.media_request.kind == 'music':
                        worker.media_request = replace(worker.media_request, music_card_only=True)
                    web_enabled = toggle is not None and toggle.isChecked()
                    if hasattr(self, 'media'):
                        from tt_clash_proxy import AUTO, routes

                        worker.compose_music_cards = True
                        worker.media_prompt = newest
                        worker.media_web_enabled = web_enabled and not (NO_WEB.search(prompt) or PRIVATE_QUERY.search(prompt))
                        worker.media_allow_proxy = routes.mode == AUTO
                        worker.media_active_proxy = routes.private_proxy
                        music_service = getattr(getattr(self, 'music_page', None), 'service', None)
                        worker.media_preferred_music = getattr(music_service, 'active', '')
                        worker.media_recent_music = tuple(url for _, answer, _ in context for url in URL.findall(answer))
                    if worker.media_request:
                        if web_enabled:
                            self.status.setText('正在查找可分享的内容……')
                    elif web_enabled:
                        worker.lookup = lookup_for(prompt)
                        if (worker.lookup is None and self._video_lookup
                                and VIDEO_FOLLOWUP.search(prompt) and not NO_WEB.search(prompt)):
                            account, session, lookup, result = self._video_lookup
                            if account == self.identity and session == self.session:
                                worker.lookup, worker.research_result = lookup, result
                        if worker.lookup is None or not worker.lookup.urls:
                            previous = next((turn[0] for turn in reversed(context) if turn[0]), "")
                            can_share = worker.media_web_enabled and not self.pending_images
                            if not NO_WEB.search(prompt) and not PRIVATE_QUERY.search(prompt):
                                worker.routing_prompt = routing_input(
                                    newest, previous, media=can_share,
                                    recent_media=recent_chat_media(context) if can_share else (),
                                )
                                worker.semantic_media = can_share and bool(
                                    worker.routing_prompt and json.loads(worker.routing_prompt).get('can_share')
                                )
                    denied = bool(re.search(r"(?:不要|别|不用|不需要|不想要|去掉|删除|不显示).{0,12}(?:链接|网址|URL|url)", prompt))
                    worker.allow_source_links = not denied
                    self._reply_links = not denied and (bool(worker.media_request or
                                                              (worker.lookup and worker.lookup.urls)) or wants_links(prompt))
                    self._reply_start = None
                    worker.tt_show_links = self._reply_links
                    # Older linked replies must not keep teaching the model to add URLs.
                    for item in worker.payload.get("input", []):
                        if item.get("role") == "assistant" and isinstance(item.get("content"), str):
                            item["content"] = without_links(item["content"])
            super().start_worker(worker)

        def add_delta(self, text):
            if hasattr(self, "chat_messages"):
                self.pending_answer += text
                self.queue_chat_delta()
                return
            if self._reply_start is None:
                self._reply_start = self.view.document().characterCount() - 1
            self.pending_answer += text
            cursor = self.view.textCursor()
            cursor.setPosition(self._reply_start)
            cursor.movePosition(QTextCursor.MoveOperation.End, QTextCursor.MoveMode.KeepAnchor)
            displayed = plain_words(self.pending_answer) if self._reply_links else without_links(self.pending_answer)
            if isinstance(self.worker, PlusWorker) and self.worker.compose_music_cards:
                displayed = music_reply(displayed, streaming=True)[0]
            cursor.insertText(displayed, QTextCharFormat())
            self.view.setTextCursor(cursor)
            self.view.ensureCursorVisible()

        def completed(self, answer):
            worker = self.worker
            if isinstance(worker, PlusWorker) and worker.read_aloud_text:
                return super().completed(answer)
            if isinstance(worker, PlusWorker):
                self._reply_links = getattr(worker, "tt_show_links", False)
                if worker.lookup and worker.lookup.urls and worker.research_result:
                    self._video_lookup = self.identity, self.session, worker.lookup, worker.research_result
            markdown = hasattr(self, 'chat_messages')
            answer = plain_words(answer, markdown=markdown)
            self.pending_answer = plain_words(self.pending_answer, markdown=markdown)
            if isinstance(worker, PlusWorker) and worker.compose_music_cards and not worker.media_request:
                answer = music_reply(answer)[0]
                self.pending_answer = music_reply(self.pending_answer, streaming=True)[0]
            if not self._reply_links:
                answer = without_links(answer, markdown=markdown)
                self.pending_answer = without_links(self.pending_answer, markdown=markdown)
            super().completed(answer)
            if isinstance(worker, PlusWorker) and self.history:
                cards = worker.media_items or worker.media_item
                if cards:
                    prompt, text, images = self.history[-1]
                    self.history[-1] = prompt, self.media.context_text(text, cards), images

    base.RequestWorker = PlusWorker
    base.ChatWindow = PlusWindow
