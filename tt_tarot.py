"""Local daily draws and readings. The ISO local date is the draw identity."""
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import secrets


THEMES = {"general": "综合运势", "study": "学业", "work": "工作", "love": "感情", "mood": "心情"}
DEFAULTS = {"enabled": True, "reminders": True, "voice": False, "minute": 540, "theme": "general"}


def build_tarot_summary_tts_text(card_data, draw, *, today=True):
    """Read the five summary fields in the form of one conversational report."""
    key = draw.meaning_prefix
    keywords = card_data[key + "_keywords"]
    keywords_text = "、".join(keywords[:-1]) + "和" + keywords[-1] if len(keywords) > 1 else keywords[0]
    opening = "今天抽到的是" if today else "这一天抽到的是"
    return (
        f"{opening}{card_data['name_cn']}，{draw.orientation}。"
        f"它让人想到{keywords_text}。"
        f"{card_data[key + '_meaning']}"
        f"今天有个小建议，{card_data['daily_advice']}"
        f"TT 也想对你说，{card_data['tt_message']}"
    )


def build_tarot_detail_tts_text(detail_text):
    """Read just the selected, generated interpretation and a short transition."""
    text = detail_text.strip()
    return f"接下来，慢慢和你说说这张牌更深一层的意思。\n\n{text}" if text else ""


@dataclass(frozen=True)
class Draw:
    day: str
    card_id: str
    reversed: bool
    theme: str
    revealed: bool
    notified: bool
    snooze_until: float

    @property
    def orientation(self):
        return "逆位" if self.reversed else "正位"

    @property
    def meaning_prefix(self):
        return "reversed" if self.reversed else "upright"


class TarotManager:
    def __init__(self, root, db, *, now=datetime.now, randbelow=secrets.randbelow):
        self.root = Path(root) / "assets" / "tarot"
        self.db, self.now, self.randbelow = db, now, randbelow
        data = self.root / "data"
        cards = json.loads((data / "major_arcana.json").read_text(encoding="utf-8"))
        fields = ("id", "name_cn", "name_en", "image_path", "upright_meaning", "reversed_meaning",
                  "upright_keywords", "reversed_keywords", "love_meaning", "study_meaning",
                  "work_meaning", "mood_meaning", "daily_advice", "tt_message")
        if sorted(card["number"] for card in cards) != list(range(22)) or any(
            not all(card.get(field) for field in fields) for card in cards
        ):
            raise ValueError("大阿尔卡那牌义资料不完整")
        self.cards = {card["id"]: card for card in sorted(cards, key=lambda item: item["number"])}
        if len(self.cards) != 22:
            raise ValueError("塔罗资料有重复编号")
        self.order = tuple(self.cards)
        self.db.executescript((data / "migration_001.sql").read_text(encoding="utf-8"))

    def image_path(self, card_id):
        return self.root / self.cards[card_id]["image_path"]

    def setting(self, name):
        result = self.db.execute("SELECT value FROM tarot_settings WHERE name=?", (name,)).fetchone()
        default = DEFAULTS[name]
        try:
            value = json.loads(result[0]) if result else default
        except (ValueError, TypeError):
            return default
        if type(value) is not type(default):
            return default
        if name == "theme" and value not in THEMES or name == "minute" and not 0 <= value < 1440:
            return default
        return value

    def set_setting(self, name, value):
        if name not in DEFAULTS or type(value) is not type(DEFAULTS[name]):
            raise ValueError("无效的塔罗设置")
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO tarot_settings VALUES (?,?)", (name, json.dumps(value)))

    def find(self, day):
        row = self.db.execute("SELECT * FROM tarot_daily_draws WHERE draw_date=?", (day,)).fetchone()
        return Draw(*row) if row else None

    def today(self, now=None):
        if not self.setting("enabled"):
            return None
        day = (now or self.now()).date().isoformat()
        draw = self.find(day)
        if draw:
            return draw
        with self.db:
            self.db.execute(
                "INSERT INTO tarot_daily_draws (draw_date,card_id,reversed,theme) VALUES (?,?,?,?) "
                "ON CONFLICT(draw_date) DO NOTHING",
                (day, self.order[self.randbelow(len(self.order))], self.randbelow(2), self.setting("theme")),
            )
        return self.find(day)

    def reveal(self, day):
        with self.db:
            self.db.execute("UPDATE tarot_daily_draws SET revealed=1,notified=1 WHERE draw_date=?", (day,))

    def select_theme(self, day, theme):
        if theme not in THEMES:
            raise ValueError("无效的解读主题")
        with self.db:
            self.db.execute("UPDATE tarot_daily_draws SET theme=? WHERE draw_date=?", (theme, day))

    def reading(self, day, theme):
        row = self.db.execute("SELECT content FROM tarot_readings WHERE draw_date=? AND theme=?", (day, theme)).fetchone()
        return row[0] if row else ""

    def save_reading(self, day, theme, content):
        with self.db:
            self.db.execute(
                "INSERT INTO tarot_readings SELECT ?,?,? WHERE EXISTS "
                "(SELECT 1 FROM tarot_daily_draws WHERE draw_date=?) "
                "ON CONFLICT(draw_date,theme) DO NOTHING", (day, theme, content, day)
            )

    def history(self, date_prefix="", card_id="", offset=0, limit=40):
        return [Draw(*row) for row in self.db.execute(
            "SELECT * FROM tarot_daily_draws AS d WHERE d.draw_date LIKE ? "
            "AND (?='' OR d.card_id=?) ORDER BY d.draw_date DESC LIMIT ? OFFSET ?",
            (date_prefix + "%", card_id, card_id, limit, offset)
        )]

    def favorites(self):
        return {row[0] for row in self.db.execute("SELECT card_id FROM tarot_favorites")}

    def set_favorite(self, card_id, favorite):
        with self.db:
            if favorite:
                self.db.execute("INSERT OR IGNORE INTO tarot_favorites VALUES (?)", (card_id,))
            else:
                self.db.execute("DELETE FROM tarot_favorites WHERE card_id=?", (card_id,))

    def clear_history(self):
        # Keep today's result, even when the user clears older history.
        current = self.now().date().isoformat()
        with self.db:
            self.db.execute("DELETE FROM tarot_readings WHERE draw_date<>?", (current,))
            self.db.execute("DELETE FROM tarot_daily_draws WHERE draw_date<>?", (current,))

    def remember_notification(self, day, snooze_until=0):
        with self.db:
            self.db.execute("UPDATE tarot_daily_draws SET notified=?,snooze_until=? WHERE draw_date=?",
                            (int(not snooze_until), snooze_until, day))

    def reminder_due(self, draw, now):
        return (draw is not None and self.setting("reminders") and not draw.notified
                and now.hour * 60 + now.minute >= self.setting("minute")
                and now.timestamp() >= draw.snooze_until)

    def interpretation(self, draw, theme):
        card = self.cards[draw.card_id]
        text = card[draw.meaning_prefix + "_meaning"]
        if theme != "general":
            text += "\n\n" + THEMES[theme] + " · " + card[theme + "_meaning"]
        return text

    def ai_payload(self, draw, theme, model):
        card = self.cards[draw.card_id]
        return {
            "model": model, "store": False, "stream": True,
            "instructions": (
                "你是桌宠TT。用中文给温柔、自然、克制的塔罗娱乐和自我反思。不预测未来，"
                "不承诺运势，不制造恐惧，不给医疗、法律或财务建议，不要求付费。"
                "只依据给定牌名、正逆位及牌义，不编造用户经历。约250字，有重点地覆盖"
                "今日牌意、整体、学业工作、人际感情、情绪照顾、注意事项、TT的一句话。"
            ),
            "input": [{"role": "user", "content": (
                    f"{card['name_cn']} {draw.orientation}。重点：{THEMES[theme]}。\n"
                    f"{self.interpretation(draw, theme)}\n"
                    + "\n".join(f"{label}：{card[key + '_meaning']}" for key, label in THEMES.items()
                                if key != "general")
            )}],
        }
