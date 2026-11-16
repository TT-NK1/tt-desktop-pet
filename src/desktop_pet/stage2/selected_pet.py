import sys
from tt_single_instance import create_application
from pathlib import Path

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication, QMessageBox

from . import window as pet_window


# Use only the five user-selected outfits and their six poses.
pet_window.OUTFITS = (
    "运动服", "居家毛衣", "围巾冬装", "居家 T 恤", "花纹浴衣"
)
pet_window.POSES = (
    "用电脑", "逗猫", "伸懒腰", "看书", "捧杯", "睡觉"
)

FILES = (
    "sports.png", "sweater.png", "winter.png",
    "shirt.png", "yukata.png",
)
ASSET_DIR = (
    Path(__file__).resolve().parents[3]
    / "assets" / "private" / "selected_originals"
)


def load_selected_sprites():
    outfits = []
    for filename in FILES:
        path = ASSET_DIR / filename
        image = QImage(str(path))
        if image.isNull():
            raise ValueError(f"无法读取图片：\n{path}")
        if image.width() != 1536 or image.height() != 1024:
            raise ValueError(f"图片尺寸与当前图集配置不符：{filename}")

        poses = []
        for row in range(2):
            # The lower-left reading poses extend beyond x=512.
            edges = (0, 512, 1024, 1536) if row == 0 else (0, 550, 1024, 1536)
            for column in range(3):
                left, right = edges[column:column + 2]
                cell = image.copy(left, row * 512, right - left, 512)
                # Keep the source alpha channel; do not remove any colors.
                sprite = QPixmap.fromImage(cell).scaled(
                    275, 275,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                poses.append(sprite)
        outfits.append(poses)
    return outfits


class SelectedPet(pet_window.DesktopPet):
    def closeEvent(self, event):
        self.timer.stop()
        super().closeEvent(event)
        if event.isAccepted():
            app = QApplication.instance()
            if app is not None:
                app.quit()


def main():
    app = create_application(sys.argv)
    if app is None:
        return 0
    try:
        sprites = load_selected_sprites()
    except ValueError as error:
        QMessageBox.critical(None, "桌宠素材检查", str(error))
        return 1

    pet = SelectedPet(sprites)
    area = app.primaryScreen().availableGeometry()
    pet.move(area.bottomRight() - QPoint(pet.width() + 20, pet.height() + 20))
    pet.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
