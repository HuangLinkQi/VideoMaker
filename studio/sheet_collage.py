#!/usr/bin/env python3
"""资产设定图拼贴：多视角面板 -> 白平衡统一 -> 按组插入比例尺段。

比例尺（ruler.py）画在一段独立留白上，插到 --ruler-before 指定的面板之前：
人物五格 = 特写正 特写侧 [尺] 全身正 全身侧 全身背（尺子贴全身组，不压特写）；
场景三格 = [尺] 定场 中景 细节。

用法: python3 studio/sheet_collage.py --out out.png --height-cm 175 --label 林川 \
      --ruler-before 2 face_front.png face_side.png full_front.png full_side.png full_back.png
"""
from __future__ import annotations

import argparse
import tempfile

import numpy as np
from PIL import Image

from ruler import add_ruler

H = 1152


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("panels", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--height-cm", type=int, required=True)
    ap.add_argument("--label", default="")
    ap.add_argument("--ruler-before", type=int, default=0,
                    help="比例尺段插在第几个面板之前（0=最左）")
    ap.add_argument("--margin", type=int, default=200, help="比例尺段宽度")
    ap.add_argument("--no-ruler", action="store_true", help="不插入比例尺段（clean 版）")
    args = ap.parse_args()

    panels = [Image.open(p).convert("RGB") for p in args.panels]
    panels = [p.resize((round(p.width * H / p.height), H), Image.LANCZOS) for p in panels]

    strips = [np.asarray(p, dtype=np.float32)[:20].reshape(-1, 3) for p in panels]
    medians = [np.median(s, axis=0) for s in strips]
    target = np.median(np.stack(medians), axis=0)
    bg = tuple(int(c) for c in target)

    corrected = []
    for p, m in zip(panels, medians):
        arr = np.asarray(p, dtype=np.float32) + (target - m)
        corrected.append(Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)))

    k = max(0, min(args.ruler_before, len(corrected)))
    ruler_seg = None
    if not args.no_ruler:
        with tempfile.NamedTemporaryFile(suffix=".png") as tf:
            seg = Image.new("RGB", (args.margin, H), bg)
            seg.save(tf.name)
            font = max(16, (args.margin + sum(p.width for p in corrected)) // 40)
            add_ruler(tf.name, tf.name, args.height_cm, args.label, font_size=font)
            ruler_seg = Image.open(tf.name).convert("RGB")

    ordered = corrected[:k] + ([ruler_seg] if ruler_seg else []) + corrected[k:]
    W = sum(p.width for p in ordered)
    sheet = Image.new("RGB", (W, H), bg)
    x = 0
    for p in ordered:
        sheet.paste(p, (x, 0))
        x += p.width
    sheet.save(args.out)
    print(f"{args.out}  size={sheet.size} panels={len(corrected)} ruler_before={k}")


if __name__ == "__main__":
    main()
