#!/usr/bin/env python3
"""把分视图拼成审核用设定图：中文视角名 + 比例尺。模型出图阶段不写中文。

人物设定图（对齐用户给的角色卡）：
  左 3/4 = 全身正/侧/背 + 比例尺
  右 1/4 = 正脸、侧脸 + 眼/鼻/嘴/耳/发型特写（从 2K 正脸裁）

场景：2×2（平视空镜 / 反打 / 左侧 / 右侧）
道具：正面/侧面/3/4 + 特写，保留比例尺

用法:
  python3 studio/compose_sheet.py character --id linchuan --label 林川 --height 175 \\
      --dir raw_dir --out-dir projects/测试/characters
  python3 studio/compose_sheet.py scene --id loc_home --label 母亲家 --height 300 \\
      --dir raw_dir --out-dir projects/测试/scenes
  python3 studio/compose_sheet.py prop --id prop_phone_lin --label 林川手机 --height 15 \\
      --dir raw_dir --out-dir projects/测试/props
"""
from __future__ import annotations

import argparse
import os

from PIL import Image, ImageDraw, ImageFont

SHEET = (2560, 1440)  # 2K 16:9 审核图
BAR = 36
GAP = 8
BG = (28, 30, 34)
LABEL_BG = (18, 20, 24)
WHITE = (236, 238, 240)
RULER_BG = (255, 255, 255, 170)
RULER_FG = (20, 20, 20, 255)


def font(size):
    for path in (
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "DejaVuSans.ttf",
    ):
        try:
            return ImageFont.truetype(path, size, index=0)
        except OSError:
            continue
    return ImageFont.load_default()


def open_rgb(path):
    return Image.open(path).convert("RGB")


def fit(im, box, bg=BG):
    """等比放入 box，letterbox。"""
    bw, bh = box
    iw, ih = im.size
    scale = min(bw / iw, bh / ih)
    nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
    im = im.resize((nw, nh), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (bw, bh), bg)
    canvas.paste(im, ((bw - nw) // 2, (bh - nh) // 2))
    return canvas, (bw - nw) // 2, (bh - nh) // 2, nw, nh


def labeled(im, text, box):
    """上方 BAR 高标签 + 图。"""
    bw, bh = box
    canvas = Image.new("RGB", (bw, bh), LABEL_BG)
    d = ImageDraw.Draw(canvas)
    f = font(max(18, BAR - 12))
    d.text((10, 6), text, fill=WHITE, font=f)
    inner, *_ = fit(im, (bw, bh - BAR), bg=(12, 12, 12))
    canvas.paste(inner, (0, BAR))
    return canvas


def crop_frac(im, x0, y0, x1, y1):
    w, h = im.size
    return im.crop((int(w * x0), int(h * y0), int(w * x1), int(h * y1)))


def add_ruler(canvas, x, top, bottom, height_cm, label):
    """在 canvas 左侧画比例尺，top/bottom 对齐人物/物体实际高度。"""
    ov = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    strip = max(70, x)
    d.rectangle([0, 0, strip, canvas.size[1]], fill=RULER_BG)
    cx = strip // 2
    d.line([cx, top, cx, bottom], fill=RULER_FG, width=3)
    step = 50 if height_cm >= 200 else (10 if height_cm >= 40 else 1)
    label_every = 100 if height_cm >= 200 else (50 if height_cm >= 40 else 5)
    f = font(max(16, canvas.size[0] // 70))
    cm = 0
    while cm <= height_cm:
        y = bottom - int((bottom - top) * cm / max(height_cm, 1))
        big = cm % label_every == 0
        tick = 20 if big else 11
        d.line([cx - tick, y, cx + tick, y], fill=RULER_FG, width=3 if big else 2)
        if big:
            if height_cm >= 200 and cm % 100 == 0 and cm:
                txt = f"{cm // 100}m"
            else:
                txt = "0" if cm == 0 else str(cm)
            d.text((cx + tick + 4, y - 12), txt, fill=RULER_FG, font=f)
        cm += step
    if label:
        d.text((6, 8), f"{label} {height_cm}cm", fill=RULER_FG, font=f)
    return Image.alpha_composite(canvas.convert("RGBA"), ov).convert("RGB")


def head_crop(im, top=0.50):
    """从半身/全身图裁出头部区域，供特写格使用。"""
    w, h = im.size
    if h / max(w, 1) >= 1.2:
        return im.crop((int(w * 0.08), 0, int(w * 0.92), int(h * top)))
    return im


def compose_character(args):
    face_f_raw = open_rgb(args.face_front)
    face_s_raw = open_rgb(args.face_side)
    face_f = head_crop(face_f_raw, 0.48)
    face_s = head_crop(face_s_raw, 0.42)
    body = [open_rgb(p) for p in (args.body_front, args.body_side, args.body_back)]
    W, H = SHEET
    ruler_w = 80
    face_w = W // 4
    body_w = W - ruler_w - face_w
    col_w = (body_w - GAP * 2) // 3

    sheet = Image.new("RGB", (W, H), BG)
    # 三视图
    body_top = H
    body_bot = 0
    labels = ("正视图", "侧视图", "后视图")
    for i, (im, lb) in enumerate(zip(body, labels)):
        x = ruler_w + i * (col_w + GAP)
        cell = labeled(im, lb, (col_w, H))
        sheet.paste(cell, (x, 0))
        # 估算人物在 cell 内的竖直范围（去掉标签栏后 letterbox）
        _, _ox, oy, _nw, nh = fit(im, (col_w, H - BAR), bg=(12, 12, 12))
        body_top = min(body_top, BAR + oy)
        body_bot = max(body_bot, BAR + oy + nh)

    # 右 1/4 面部特写板
    fx = ruler_w + body_w
    fw, fh = face_w, H
    # 上 55%：正脸 | 侧脸
    top_h = int(fh * 0.55)
    half = (fw - GAP) // 2
    sheet.paste(labeled(face_f, "面部特写（正面）", (half, top_h)), (fx, 0))
    sheet.paste(labeled(face_s, "面部特写（侧面）", (fw - half, top_h)), (fx + half + GAP, 0))
    # 中 22%：眼鼻嘴
    mid_y = top_h + GAP
    mid_h = int(fh * 0.22)
    third = (fw - GAP * 2) // 3
    parts = [
        ("眼睛特写", crop_frac(face_f, 0.12, 0.28, 0.88, 0.50)),
        ("鼻子特写", crop_frac(face_f, 0.32, 0.40, 0.68, 0.68)),
        ("嘴唇特写", crop_frac(face_f, 0.28, 0.60, 0.72, 0.82)),
    ]
    for i, (lb, crop) in enumerate(parts):
        sheet.paste(labeled(crop, lb, (third, mid_h)), (fx + i * (third + GAP), mid_y))
    # 下：耳 + 发型
    bot_y = mid_y + mid_h + GAP
    bot_h = fh - bot_y
    halfb = (fw - GAP) // 2
    ear = crop_frac(face_s, 0.42, 0.30, 0.88, 0.62)
    hair = crop_frac(face_f, 0.18, 0.00, 0.82, 0.28)
    sheet.paste(labeled(ear, "耳朵特写", (halfb, bot_h)), (fx, bot_y))
    sheet.paste(labeled(hair, "发型特写", (fw - halfb, bot_h)), (fx + halfb + GAP, bot_y))

    sheet = add_ruler(sheet, ruler_w, body_top, body_bot, args.height, args.label)
    os.makedirs(args.out_dir, exist_ok=True)
    sheet_path = os.path.join(args.out_dir, args.id + ".png")
    sheet.save(sheet_path, quality=95)
    # 干净参考：正脸（首尾帧用，不要整张设定图）
    front_path = os.path.join(args.out_dir, args.id + "_front.png")
    face_f_raw.save(front_path, quality=95)
    face_path = os.path.join(args.out_dir, args.id + "_face.png")
    # 单独竖图脸板（用户说可以单张竖图输出）
    face_board = Image.new("RGB", (1440, 2560), BG)
    fw2, fh2 = 1440, 2560
    top_h2 = int(fh2 * 0.55)
    half2 = (fw2 - GAP) // 2
    face_board.paste(labeled(face_f, "面部特写（正面）", (half2, top_h2)), (0, 0))
    face_board.paste(labeled(face_s, "面部特写（侧面）", (fw2 - half2, top_h2)), (half2 + GAP, 0))
    mid_y2 = top_h2 + GAP
    mid_h2 = int(fh2 * 0.22)
    third2 = (fw2 - GAP * 2) // 3
    for i, (lb, crop) in enumerate(parts):
        face_board.paste(labeled(crop, lb, (third2, mid_h2)), (i * (third2 + GAP), mid_y2))
    bot_y2 = mid_y2 + mid_h2 + GAP
    bot_h2 = fh2 - bot_y2
    halfb2 = (fw2 - GAP) // 2
    face_board.paste(labeled(ear, "耳朵特写", (halfb2, bot_h2)), (0, bot_y2))
    face_board.paste(labeled(hair, "发型特写", (fw2 - halfb2, bot_h2)), (halfb2 + GAP, bot_y2))
    face_board.save(face_path, quality=95)
    clean = os.path.join(args.out_dir, args.id + "_clean.png")
    # 三视图无标签干净拼图，供存档
    three = Image.new("RGB", (body_w, H), (240, 240, 240))
    for i, im in enumerate(body):
        cell, *_ = fit(im, (col_w, H), bg=(240, 240, 240))
        three.paste(cell, (i * (col_w + GAP), 0))
    three.save(clean, quality=95)
    print(sheet_path)
    print(face_path)
    print(front_path)
    print(clean)
    return sheet_path


def compose_scene(args):
    views = [
        (open_rgb(args.eye), "平视空镜"),
        (open_rgb(args.reverse), "反打视角"),
        (open_rgb(args.left), "左侧视图"),
        (open_rgb(args.right), "右侧视图"),
    ]
    W, H = SHEET
    ruler_w = 80
    cell_w = (W - ruler_w - GAP) // 2
    cell_h = (H - GAP) // 2
    sheet = Image.new("RGB", (W, H), BG)
    pos = [(ruler_w, 0), (ruler_w + cell_w + GAP, 0),
           (ruler_w, cell_h + GAP), (ruler_w + cell_w + GAP, cell_h + GAP)]
    for (im, lb), (x, y) in zip(views, pos):
        sheet.paste(labeled(im, lb, (cell_w, cell_h)), (x, y))
    sheet = add_ruler(sheet, ruler_w, int(H * 0.08), int(H * 0.92), args.height, args.label)
    os.makedirs(args.out_dir, exist_ok=True)
    path = os.path.join(args.out_dir, args.id + ".png")
    sheet.save(path, quality=95)
    # 首尾帧参考用平视空镜单张，不要整张四宫格
    clean = os.path.join(args.out_dir, args.id + "_clean.png")
    open_rgb(args.eye).save(clean, quality=95)
    print(path)
    print(clean)
    return path


def compose_prop(args):
    views = [
        (open_rgb(args.front), "正视图"),
        (open_rgb(args.side), "侧视图"),
        (open_rgb(args.threeq), "3/4 视图"),
        (open_rgb(args.detail), "细节特写"),
    ]
    W, H = SHEET
    ruler_w = 80
    cell_w = (W - ruler_w - GAP) // 2
    cell_h = (H - GAP) // 2
    sheet = Image.new("RGB", (W, H), BG)
    pos = [(ruler_w, 0), (ruler_w + cell_w + GAP, 0),
           (ruler_w, cell_h + GAP), (ruler_w + cell_w + GAP, cell_h + GAP)]
    for (im, lb), (x, y) in zip(views, pos):
        sheet.paste(labeled(im, lb, (cell_w, cell_h)), (x, y))
    sheet = add_ruler(sheet, ruler_w, int(H * 0.10), int(H * 0.90), args.height, args.label)
    os.makedirs(args.out_dir, exist_ok=True)
    path = os.path.join(args.out_dir, args.id + ".png")
    sheet.save(path, quality=95)
    clean = os.path.join(args.out_dir, args.id + "_clean.png")
    open_rgb(args.front).save(clean, quality=95)
    print(path)
    print(clean)
    return path


def infer_from_dir(ns):
    """--dir 下按约定文件名补齐路径。"""
    d, i = ns.dir, ns.id
    if not d:
        return ns
    def p(suffix):
        for name in (f"{i}_{suffix}.png", f"av_{i}_{suffix}.png",
                     f"av_{i}_{suffix}_00001_.png"):
            fp = os.path.join(d, name)
            if os.path.isfile(fp):
                return fp
        # 模糊：前缀匹配
        for fn in sorted(os.listdir(d)):
            if i in fn and suffix in fn and fn.lower().endswith((".png", ".jpg", ".jpeg")):
                return os.path.join(d, fn)
        return None
    if ns.kind == "character":
        ns.face_front = ns.face_front or p("face_front")
        ns.face_side = ns.face_side or p("face_side")
        ns.body_front = ns.body_front or p("body_front")
        ns.body_side = ns.body_side or p("body_side")
        ns.body_back = ns.body_back or p("body_back")
    elif ns.kind == "scene":
        ns.eye = ns.eye or p("eye")
        ns.reverse = ns.reverse or p("reverse")
        ns.left = ns.left or p("left")
        ns.right = ns.right or p("right")
    else:
        ns.front = ns.front or p("front")
        ns.side = ns.side or p("side")
        ns.threeq = ns.threeq or p("threeq")
        ns.detail = ns.detail or p("detail")
    return ns


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=("character", "scene", "prop"))
    ap.add_argument("--id", required=True)
    ap.add_argument("--label", default="")
    ap.add_argument("--height", type=int, required=True)
    ap.add_argument("--dir", default="")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--face-front"); ap.add_argument("--face-side")
    ap.add_argument("--body-front"); ap.add_argument("--body-side"); ap.add_argument("--body-back")
    ap.add_argument("--eye"); ap.add_argument("--reverse"); ap.add_argument("--left"); ap.add_argument("--right")
    ap.add_argument("--front"); ap.add_argument("--side"); ap.add_argument("--threeq"); ap.add_argument("--detail")
    ns = infer_from_dir(ap.parse_args())
    if ns.kind == "character":
        missing = [k for k in ("face_front", "face_side", "body_front", "body_side", "body_back")
                   if not getattr(ns, k)]
        if missing:
            raise SystemExit(f"missing views: {missing}")
        compose_character(ns)
    elif ns.kind == "scene":
        missing = [k for k in ("eye", "reverse", "left", "right") if not getattr(ns, k)]
        if missing:
            raise SystemExit(f"missing views: {missing}")
        compose_scene(ns)
    else:
        missing = [k for k in ("front", "side", "threeq", "detail") if not getattr(ns, k)]
        if missing:
            raise SystemExit(f"missing views: {missing}")
        compose_prop(ns)


if __name__ == "__main__":
    main()
