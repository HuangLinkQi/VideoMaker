#!/usr/bin/env python3
"""给资产设定图叠加比例尺（尺子画在左侧，按素材真实高度标刻度）。

用法: python3 studio/ruler.py <输入.png> <输出.png> <高度cm> [标签]
刻度单位自动选择: >=200cm 用米级刻度, 否则用厘米级。
"""
import sys

from PIL import Image, ImageDraw, ImageFont


def _font(size):
    for name in (
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "DejaVuSans.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def add_ruler(src, dst, height_cm, label="", font_size=None):
    im = Image.open(src).convert("RGBA")
    w, h = im.size
    ov = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)

    strip_w = max(60, w // 14)
    d.rectangle([0, 0, strip_w, h], fill=(255, 255, 255, 160))

    x = strip_w // 2
    top, bottom = int(h * 0.06), int(h * 0.94)   # 尺子纵向占 88%
    d.line([x, top, x, bottom], fill=(20, 20, 20, 255), width=3)

    # 刻度间隔: 高素材(场景) 50cm, 矮素材(道具/小孩) 10cm
    step = 50 if height_cm >= 200 else 10
    label_every = 100 if height_cm >= 200 else 50
    f = _font(font_size or max(16, w // 40))
    cm = 0
    while cm <= height_cm:
        y = bottom - int((bottom - top) * cm / height_cm)
        big = (cm % label_every == 0)
        tick = 22 if big else 12
        d.line([x - tick, y, x + tick, y], fill=(20, 20, 20, 255), width=3 if big else 2)
        if big:
            txt = f"{cm // 100}m" if cm % 100 == 0 and cm > 0 else f"{cm}"
            if cm == 0:
                txt = "0"
            d.text((x + tick + 6, y - 14), txt, fill=(20, 20, 20, 255), font=f)
        cm += step

    if label:
        d.text((8, 8), label, fill=(20, 20, 20, 255), font=f)

    out = Image.alpha_composite(im, ov).convert("RGB")
    out.save(dst)
    print(f"{dst}  ruler={height_cm}cm label={label!r}")


if __name__ == "__main__":
    add_ruler(sys.argv[1], sys.argv[2], int(sys.argv[3]),
              sys.argv[4] if len(sys.argv) > 4 else "")
