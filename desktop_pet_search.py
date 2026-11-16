"""TT launcher for chat, desktop pet, and music controls."""
import re
import sys
from urllib.parse import parse_qs, urlsplit

import desktop_pet_cozy as cozy


base = cozy.base


def media_link(value):
    if not isinstance(value, str):
        return False
    try:
        url = urlsplit(value)
        if url.scheme != "https" or url.username or url.password or url.port:
            return False
    except ValueError:
        return False
    if url.hostname == "www.bilibili.com":
        return re.fullmatch(r"/video/(?:BV[0-9A-Za-z]+|av[0-9]+)/?", url.path) is not None
    if url.hostname in {"music.163.com", "www.music.163.com"}:
        page = urlsplit(url.fragment) if url.path == "/" and url.fragment else url
        song_id = parse_qs(page.query).get("id", [""])[0]
        return page.path in {"/song", "/m/song"} and song_id.isascii() and song_id.isdecimal()
    return False


def configure_companion():
    from tt_room_layout import install
    from tt_feedback import install_feedback
    from tt_proactive_ui import install_voice
    from tt_plus_search import install_chat_style
    from tt_memory_ui import install as install_memory

    install(base, cozy.CozyWindow)
    install_feedback(base)
    install_chat_style(base)
    install_voice(base)
    install_memory(base)


def main():
    if "--self-test" in sys.argv:
        assert media_link("https://music.163.com/#/song?id=123")
        assert media_link("https://www.bilibili.com/video/BV1abc123/")
        assert not media_link("https://search.bilibili.com/all?keyword=test")
        assert not media_link("https://www.bilibili.com.example.org/video/BV1abc123/")
        print("PASS: media link validation")
        return 0
    from tt_share_player import initialize_video_backend

    initialize_video_backend()
    configure_companion()
    return base.pet_app.main(base.ChatPet)


if __name__ == "__main__":
    sys.exit(main())
