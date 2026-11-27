"""一次性开发工具：裁掉皮肤画布里烘焙的「延伸色带」+ 重采样边色。

背景：旧方案（docs/17）把「画作 + 右缘延伸色」一起烘焙进 2560×1600 画布；
v3 壁纸改为聊天框内 CONTAIN 居中后，烘焙带会作为灰块出现在画面里（probe v12
实证）。本工具：
1. 首次运行把原图备份为 background_source.jpg（再烘焙/回滚的源）；
2. 自动检测右侧/底部的平坦延伸带（列/行灰度标准差 < 阈值）并裁掉，
   覆盖写回 background.jpg（裁切上限 40%，防误裁）；
3. 重采样四边环均值色与整环均值，写回 skin.json 的 edge_colors /
   dominant_color（复用 sample_skin_dominant 的口径）。

运行：Python39（PyQt5） python tools/bake_skin_canvas.py
"""
import json
import shutil
import sys
from pathlib import Path

from PyQt5.QtGui import QImage

ROOT = Path(__file__).resolve().parents[1]
SKINS = ROOT / "resources" / "skins"
RING = 12
FLAT_STD = 3.0      # 灰度标准差低于此值视为平坦延伸带
MAX_CROP = 0.40     # 单维最多裁 40%，防误裁


def _col_std(img, x):
    import math
    n = img.height()
    vals = []
    for y in range(0, n, 4):
        c = img.pixelColor(x, y)
        vals.append(0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue())
    m = sum(vals) / len(vals)
    return math.sqrt(sum((v - m) ** 2 for v in vals) / len(vals))


def _row_std(img, y):
    import math
    n = img.width()
    vals = []
    for x in range(0, n, 4):
        c = img.pixelColor(x, y)
        vals.append(0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue())
    m = sum(vals) / len(vals)
    return math.sqrt(sum((v - m) ** 2 for v in vals) / len(vals))


def detect_band(img):
    """返回 (right_band_px, bottom_band_px)。"""
    w, h = img.width(), img.height()
    right = 0
    x = w - 1
    while x > w * (1 - MAX_CROP) and _col_std(img, x) < FLAT_STD:
        right += 1
        x -= 1
    bottom = 0
    y = h - 1
    while y > h * (1 - MAX_CROP) and _row_std(img, y) < FLAT_STD:
        bottom += 1
        y -= 1
    return right, bottom


def sample_edges(img):
    w, h = img.width(), img.height()

    def region_mean(pred):
        rs = gs = bs = n = 0
        for y in range(0, h, 2):
            for x in range(0, w, 2):
                if pred(x, y):
                    c = img.pixelColor(x, y)
                    rs += c.red(); gs += c.green(); bs += c.blue(); n += 1
        return (rs // n, gs // n, bs // n) if n else (0, 0, 0)

    edges = {
        "top": region_mean(lambda x, y: y < RING),
        "bottom": region_mean(lambda x, y: y >= h - RING),
        "left": region_mean(lambda x, y: x < RING),
        "right": region_mean(lambda x, y: x >= w - RING),
    }
    rs = gs = bs = n = 0
    for rgb in edges.values():
        rs += rgb[0]; gs += rgb[1]; bs += rgb[2]; n += 1
    return (rs // n, gs // n, bs // n), edges


def hx(rgb):
    return "#%02X%02X%02X" % rgb


def main():
    for skin_dir in sorted(SKINS.iterdir()):
        meta_path = skin_dir / "skin.json"
        bg = skin_dir / "background.jpg"
        src = skin_dir / "background_source.jpg"
        if not meta_path.is_file() or not bg.is_file():
            continue
        if not src.is_file():
            shutil.copy2(bg, src)
        img = QImage(str(src))
        right, bottom = detect_band(img)
        w, h = img.width(), img.height()
        if right or bottom:
            cropped = img.copy(0, 0, w - right, h - bottom)
            cropped.save(str(bg), "JPG", 92)
        else:
            shutil.copy2(src, bg)
        final = QImage(str(bg))
        ring, edges = sample_edges(final)
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["dominant_color"] = hx(ring)
        meta["edge_colors"] = {k: hx(v) for k, v in edges.items()}
        meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{meta['id']:8s} crop right={right} bottom={bottom} "
              f"-> {final.width()}x{final.height()} ring={hx(ring)}")


if __name__ == "__main__":
    sys.exit(main())
