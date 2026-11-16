"""Manage local IndexTTS reference recordings and their profile names."""
import shutil
import wave
from pathlib import Path
from uuid import uuid4


def voice_directory():
    from tt_indextts_resources import RUNTIME
    return RUNTIME / "voices"


def prepare_db(db):
    db.execute("""CREATE TABLE IF NOT EXISTS tt_voice_profiles (
        name TEXT PRIMARY KEY, reference_path TEXT NOT NULL, reference_text TEXT NOT NULL
    )""")


def profiles(db):
    return db.execute("SELECT name, reference_path FROM tt_voice_profiles ORDER BY name").fetchall()


def with_reference(db, settings):
    selection = settings.get("voice_name")
    row = db.execute("SELECT reference_path FROM tt_voice_profiles WHERE name=?", (selection,)).fetchone()
    return dict(settings, voice_reference=row[0] if row else "")


def add_reference(db, name, source, transcript):
    name = name.strip()
    if not name or len(name) > 64:
        raise ValueError("请输入 1 至 64 个字符的音色名称")
    if db.execute("SELECT 1 FROM tt_voice_profiles WHERE name=? COLLATE NOCASE", (name,)).fetchone():
        raise ValueError("这个音色名称已存在")
    source = Path(source).resolve(strict=True)
    with wave.open(str(source), "rb") as audio:
        duration = audio.getnframes() / audio.getframerate()
    if not 0 < duration <= 300 or source.stat().st_size > 24 * 1024**2:
        raise ValueError("请选择有效的 WAV，最长 300 秒且不超过 24 MiB")
    directory = voice_directory() / uuid4().hex
    directory.mkdir(parents=True)
    target = directory / "reference.wav"
    try:
        shutil.copy2(source, target)
        with db:
            db.execute("INSERT INTO tt_voice_profiles VALUES (?,?,?)", (name, str(target), transcript))
    except Exception:
        target.unlink(missing_ok=True)
        directory.rmdir()
        raise


def add_settings(form, dialog, db, settings, changed):
    from PySide6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QInputDialog,
                                   QLabel, QMessageBox, QPushButton)
    from tt_theme import qt_file_dialog

    form.addRow("语音引擎", QLabel("IndexTTS 2.0（本地）", dialog))
    chooser = QComboBox(dialog)
    form.addRow("参考音色", chooser)

    def populate():
        saved = settings["voice_name"]
        chooser.blockSignals(True)
        chooser.clear()
        chooser.addItem("请选择音色", "")
        for name, reference in profiles(db):
            label = name if Path(reference).is_file() else name + "（参考录音缺失）"
            chooser.addItem(label, name)
        if saved and chooser.findData(saved) < 0:
            chooser.addItem(saved + "（当前不可用）", saved)
        chooser.setCurrentIndex(max(0, chooser.findData(saved)))
        chooser.blockSignals(False)

    def add():
        path, _ = QFileDialog.getOpenFileName(dialog, "选择参考录音", "", "WAV (*.wav)",
                                             options=qt_file_dialog(dialog))
        if not path:
            return
        name, confirmed = QInputDialog.getText(dialog, "添加音色", "音色名称")
        if not confirmed:
            return
        try:
            add_reference(db, name, path, "")
        except (ValueError, OSError, wave.Error) as error:
            QMessageBox.warning(dialog, "无法添加音色", str(error))
            return
        changed("voice_name", name.strip())
        populate()

    def remove():
        name = chooser.currentData()
        if name and QMessageBox.question(
            dialog, "移除音色", f"从音色列表移除「{name}」？参考录音会保留。"
        ) == QMessageBox.StandardButton.Yes:
            with db:
                db.execute("DELETE FROM tt_voice_profiles WHERE name=?", (name,))
            changed("voice_name", "")
            populate()

    chooser.currentIndexChanged.connect(lambda _: changed("voice_name", chooser.currentData() or ""))
    buttons = QHBoxLayout()
    add_button = QPushButton("添加参考音色", dialog)
    remove_button = QPushButton("移除音色", dialog)
    add_button.clicked.connect(add)
    remove_button.clicked.connect(remove)
    buttons.addWidget(add_button)
    buttons.addWidget(remove_button)
    form.addRow(buttons)
    populate()
