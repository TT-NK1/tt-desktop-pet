import json
import sqlite3
from pathlib import Path

from .model_manager import ModelError, inspect_model, local_file


COMMON_ACTIONS = {"idle": "自然待机", "look_around": "左右张望"}


class OutfitManager:
    def __init__(self, project, database=None):
        self.project = Path(project)
        self.root = self.project / "assets" / "character"
        runtime = self.project / "runtime" / "live2d"
        runtime.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(database if database is not None else runtime / "settings.sqlite3")
        self.db.execute("CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self.config = json.loads((self.root / "outfits.json").read_text(encoding="utf-8-sig"))
        self.selected = self.get_setting("outfit", "street")
        if not self.config.get(self.selected, {}).get("model"):
            self.selected = "street"

    def get_setting(self, name, default=None):
        row = self.db.execute("SELECT value FROM settings WHERE name=?", (name,)).fetchone()
        return json.loads(row[0]) if row else default

    def save_setting(self, name, value):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)",
                            (name, json.dumps(value, ensure_ascii=False)))

    def action_labels(self, key):
        return {**COMMON_ACTIONS, **self.config[key].get("special_actions", {})}

    def outfit_motion(self, key, previous):
        if self.get_setting("switch_outfit_action", True):
            remembered = self.get_setting("outfit_motions", {})
            preferred = remembered.get(key, "idle")
        else:
            preferred = previous
        if preferred in self.config[key].get("motions", {}) and preferred in self.action_labels(key):
            return preferred
        return "idle"

    def remember_motion(self, key, motion):
        remembered = self.get_setting("outfit_motions", {})
        remembered[key] = motion
        self.save_setting("outfit_motions", remembered)

    def model_path(self, key):
        if key not in self.config:
            raise ModelError("未知服装")
        entry = self.config[key]
        if not entry.get("model"):
            raise ModelError(f"{entry['name']}尚未提供绑定模型")
        return local_file(self.root, entry["model"])

    def prepare(self, key):
        path = self.model_path(key)
        return path, inspect_model(path)

    def commit(self, key):
        # The renderer validates the model on load. A resident model is already checked.
        self.model_path(key)
        self.save_setting("outfit", key)
        self.selected = key

    def close(self):
        self.db.close()

