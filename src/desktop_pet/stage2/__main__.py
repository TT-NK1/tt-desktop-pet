import sys
from tt_single_instance import create_application
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QMessageBox
from .assets import load_sprites
from .window import DesktopPet

def main():
    app = create_application(sys.argv)
    if app is None:
        return 0
    try:
        sprites = load_sprites()
    except ValueError as error:
        QMessageBox.critical(None, "桌宠", str(error))
        return 1

    pet = DesktopPet(sprites)
    area = app.primaryScreen().availableGeometry()
    pet.move(area.bottomRight() - QPoint(pet.width() + 20, pet.height() + 20))
    pet.show()
    return app.exec()

if __name__ == "__main__":
    sys.exit(main())
