"""TT desktop entry. The caller can supply its existing chat-enabled pet class."""
from pathlib import Path
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QMessageBox

from tt_app_icon import apply_desktop_icon, prepare_taskbar
from tt_single_instance import create_application


ROOT = Path(__file__).resolve().parent


def apply_startup_icon(app, root):
    icon = QIcon(str(root / 'assets/TT-M01.ico'))
    if not icon.isNull():
        app.setWindowIcon(icon)
    else:
        apply_desktop_icon(app)


def main(pet_factory=None):
    prepare_taskbar()
    app = create_application(sys.argv)
    if app is None:
        return 0

    # TT is a tool window. Its tray and pet menu own the application lifetime.
    app.setQuitOnLastWindowClosed(False)
    pet = None
    try:
        apply_startup_icon(app,ROOT)
        if pet_factory is None:
            from tt_live2d_view import TTDesktopPet

            pet_factory = TTDesktopPet
        pet = pet_factory(ROOT)
        pet.setWindowTitle("TT")
        # Keep the floating character out of the taskbar and Alt+Tab.
        if pet.windowType() != Qt.WindowType.Tool:
            pet.setWindowFlag(Qt.WindowType.Tool)
        pet.show()
        return app.exec()
    except (ImportError, OSError, ValueError, RuntimeError) as error:
        QMessageBox.critical(None, "TT 模型加载失败", str(error))
        return 1
    finally:
        try:
            if pet is not None:
                pet.shutdown()
        finally:
            app._tt_instance_lock.unlock()


if __name__ == "__main__":
    sys.exit(main())
