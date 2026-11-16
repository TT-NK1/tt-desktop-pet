from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter, QPixmap

# Continue loading private artwork from the original project folder.
ASSET_DIR = Path(__file__).resolve().parents[3]

OUTFITS = ("日常休闲", "居家毛衣", "运动服", "居家 T 恤", "围巾冬装", "花纹浴衣")

POSES = ("待机", "用电脑", "逗猫", "伸懒腰", "看书", "捧杯", "睡觉")

OUTFIT_FILES = (
    "pet_sweater.png",
    "pet_sports.png",
    "pet_shirt.png",
    "pet_winter.png",
    "pet_yukata.png",
)

TOP_TRIM = (
    (0.00, 0.00, 0.00),
    (0.02, 0.02, 0.025),
    (0.07, 0.08, 0.03),
    (0.12, 0.035, 0.12),
)

def load_image(filename):
    path = ASSET_DIR / filename
    image = QImage(str(path))
    if image.isNull():
        raise ValueError(f"无法打开图片\n{path}")
    return image.convertToFormat(QImage.Format.Format_RGBA8888)

def remove_green(image):
    pixels = image.bits().cast("B")

    for index in range(0, len(pixels), 4):
        red, green, blue = pixels[index:index + 3]
        other = max(red, blue)
        excess = green - other

        if green >= 110 and excess >= 70:
            pixels[index + 3] = 0
        elif excess > 12:
            # 降低半透明轮廓上的绿边。
            pixels[index + 1] = other + 12
            pixels[index + 3] = pixels[index + 3] * (70 - min(excess, 70)) // 58

def split_atlas(image, rows, *, original=False, lower_second_cut=None):
    sprites = []
    for row in range(rows):
        top = row * image.height() // rows
        bottom = (row + 1) * image.height() // rows
        edges = [0, image.width() // 3, image.width() * 2 // 3, image.width()]

        if row == 1 and lower_second_cut is not None:
            edges[2] = round(image.width() * lower_second_cut)

        for column in range(3):
            left, right = edges[column:column + 2]
            cell = image.copy(left, top, right - left, bottom - top)

            if original:
                trim = round(cell.height() * TOP_TRIM[row][column])
                if trim:
                    painter = QPainter(cell)
                    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
                    painter.fillRect(
                        0, 0, cell.width(), trim, Qt.GlobalColor.transparent
                    )
                    painter.end()

            sprite = QPixmap.fromImage(cell).scaled(
                275, 275,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            sprites.append(sprite)

    return sprites

def load_sprites():
    original = split_atlas(load_image("pet_idle_atlas.png"), 4, original=True)
    sprites = [[original[0], *original[6:]]]

    for outfit_index, filename in enumerate(OUTFIT_FILES, start=1):
        image = load_image(filename)
        remove_green(image)
        # 冬装和浴衣的睡姿略过了等分线，单独调整下排切线。
        cut = 1018 / 1536 if filename in ("pet_winter.png", "pet_yukata.png") else None
        poses = split_atlas(image, 2, lower_second_cut=cut)
        sprites.append([original[outfit_index], *poses])

    return sprites
