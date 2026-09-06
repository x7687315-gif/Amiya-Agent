"""一次性开发工具：为每套皮肤采样壁纸的「边缘环均值色」与「占比最多色」。

用途：skin.json 的 dominant_color 原先是手填值（pale 的 #A29DA0 灰与亮色壁纸
严重不符 → 用户看到"一圈黑边"）。改为从壁纸本身采样：
- edge：外圈 12px 环的均值色（壁纸与周围填充的接缝色，延续最无缝）
- mode：量化到 32 级后占比最多的色（用户备选口径）
dominant_color 写入 edge（接缝无缝优先），mode 仅打印供对照。
运行：Python39（PyQt5） python tools/sample_skin_dominant.py
"""
import json
import sys
from pathlib import Path

from PyQt5.QtGui import QImage

ROOT = Path(__file__).resolve().parents[1]
SKINS = ROOT / "resources" / "skins"
RING = 12  # 边缘环宽度（px）


def sample(path: Path):
    img = QImage(str(path))
    if img.isNull():
        raise SystemExit(f"无法读取 {path}")
    w, h = img.width(), img.height()

    def region_mean(pred):
        rs = gs = bs = n = 0
        for y in range(0, h, 2):
            for x in range(0, w, 2):
                if pred(x, y):
                    c = img.pixelColor(x, y)
                    rs += c.red(); gs += c.green(); bs += c.blue(); n += 1
        return (rs // n, gs // n, bs // n) if n else (0, 0, 0)

    # 四条边环（各 RING px）：壁纸与周围填充的接缝色，逐边延续最无缝
    edges = {
        "top": region_mean(lambda x, y: y < RING),
        "bottom": region_mean(lambda x, y: y >= h - RING),
        "left": region_mean(lambda x, y: x < RING),
        "right": region_mean(lambda x, y: x >= w - RING),
    }
    # 整环均值：侧栏/头部的平和纯色
    rs = gs = bs = n = 0
    for rgb in edges.values():
        rs += rgb[0]; gs += rgb[1]; bs += rgb[2]; n += 1
    ring = (rs // n, gs // n, bs // n)
    # 占比最多色（量化 32 级）：仅打印供对照（用户备选口径）
    buckets = {}
    for y in range(0, h, 4):
        for x in range(0, w, 4):
            c = img.pixelColor(x, y)
            key = (c.red() // 32, c.green() // 32, c.blue() // 32)
            acc = buckets.get(key, [0, 0, 0, 0])
            acc[0] += c.red(); acc[1] += c.green(); acc[2] += c.blue(); acc[3] += 1
            buckets[key] = acc
    best = max(buckets.values(), key=lambda a: a[3])
    mode = (best[0] // best[3], best[1] // best[3], best[2] // best[3])
    return ring, edges, mode


def hx(rgb):
    return "#%02X%02X%02X" % rgb


def main():
    for skin_dir in sorted(SKINS.iterdir()):
        meta_path = skin_dir / "skin.json"
        bg = skin_dir / "background.jpg"
        if not meta_path.is_file() or not bg.is_file():
            continue
        ring, edges, mode = sample(bg)
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        old = meta.get("dominant_color")
        meta["dominant_color"] = hx(ring)
        meta["edge_colors"] = {k: hx(v) for k, v in edges.items()}
        meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{meta['id']:8s} old={old} ring={hx(ring)} "
              f"top={hx(edges['top'])} bottom={hx(edges['bottom'])} mode={hx(mode)}")


if __name__ == "__main__":
    sys.exit(main())
