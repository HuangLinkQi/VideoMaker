#!/usr/bin/env python3
"""资产分视图生成（GPU / ComfyUI）— 禁止一张图直接出三视图。

人物：脸正 → 脸侧 → 全身正/侧/背（后一张吃前一张当参考）
场景：平视空镜（五面墙）→ 反打 → 左侧 → 右侧
道具：正面 → 侧面 → 3/4 → 特写

Krea-2 Turbo 2K（最长边 2048，16 对齐）。中文标注不写进 prompt，交给本地 compose_sheet.py。

用法（在 GPU 上）:
  python3 gen_asset_views.py all
  python3 gen_asset_views.py characters
  python3 gen_asset_views.py linchuan loc_home
  cat jobs.json | python3 gen_asset_views.py

jobs.json 为数组: [{"id","type","subject","seed"?,"interior"?}]
每行打印 VIEW <id> <view> -> <filename>
结束打印 JSON 汇总 ASSET_VIEWS_DONE
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
import urllib.request

API = os.environ.get("COMFY_API", "http://127.0.0.1:8188")

# Krea-2 Turbo 原生 2K 上限：最长边 2048，边长须 16 对齐
SIZE = {
    "face": (1536, 2048),    # 3:4 头肩
    "body": (1280, 2048),    # 全身站立
    "scene": (2048, 1152),   # 16:9 2K
    "prop": (1536, 1536),    # 1:1 产品
}

NO_TEXT = ", no readable text, no watermark, no logo, no caption, no extra people, no measuring ruler"
STUDIO = ", plain light gray seamless studio background, even soft studio lighting, photorealistic"
ONE_VIEW = (
    " The reference image is the same subject. Output exactly ONE camera view, "
    "never a contact sheet, never multiple panels, never duplicate people or rooms."
)

# 场景是否室内（室内才强制五面墙）
INTERIOR = {
    "loc_office_night", "loc_home", "loc_train", "loc_subway_gate",
    "loc_subway_car", "loc_platform", "loc_hospital",
}

# 《别晃》默认题材（stdin 可覆盖 subject）
CATALOG = {
    "linchuan": ("character",
        "a 30-year-old Chinese male programmer named Lin Chuan, slim, 175cm, "
        "short neat black hair, rectangular face, MUST wear thin black rectangular eyeglasses, dark brown single-lid eyes, "
        "light skin, slightly wrinkled light blue dress shirt with sleeves rolled up, "
        "dark trousers, white sneakers"),
    "wangguifen": ("character",
        "a 58-year-old Chinese rural woman named Wang Guifen, slim, 158cm, "
        "short gray-streaked black hair in a low bun, weathered face with crow's feet, "
        "single eyelids, faded dark cotton-padded jacket over a worn floral blouse, "
        "loose dark trousers, cloth shoes"),
    "zhouye": ("character",
        "a 20-year-old Chinese male college student named Zhou Ye, 176cm, "
        "short clean black hair, round friendly face, navy windbreaker over a white t-shirt, "
        "jeans, white sneakers, backpack"),
    "xiaoyu": ("character",
        "a 12-year-old Chinese schoolgirl named Xiao Yu, 145cm, child proportions, "
        "shoulder-length straight black hair with bangs and a small pink hair clip, "
        "round youthful face, primary school uniform white shirt and navy pleated skirt, "
        "white mid-calf socks, white sneakers, small school backpack"),
    "wangguifen_young": ("character",
        "a 30-year-old Chinese rural woman, younger Wang Guifen, 160cm, "
        "long black braided hair, plain faded old cotton coat, dark trousers, cloth shoes"),
    "linchuan_child": ("character",
        "a 5-year-old Chinese little boy, young Lin Chuan, 105cm, "
        "round face, short watermelon-cut black hair, faded oversized patched blue cotton jacket, "
        "dark pants with worn knees, cloth shoes with scuffed soles, worn canvas schoolbag"),
    "loc_office_night": ("scene",
        "an empty open-plan Chinese tech-company office at night: a few desks with dark monitors, "
        "office chairs, floor-to-ceiling windows with city lights, cold blue residual monitor glow, "
        "simple uncluttered layout"),
    "loc_home": ("scene",
        "an empty modest rural Chinese home interior: one wooden square table, two stools, "
        "a warm tungsten desk lamp, worn concrete walls, a wooden door to a small courtyard, morning light"),
    "loc_train": ("scene",
        "an empty old Chinese green-skinned train hard-seat carriage: dark green bench seats, "
        "a small folding table by a large window, luggage rack, farmland light through the window"),
    "loc_bus_stop": ("scene",
        "a Chinese city street bus stop on an empty sidewalk: one vertical metal bus-stop pole "
        "with a blank paper notice taped on it, plane trees, overcast daylight, no traffic"),
    "loc_subway_gate": ("scene",
        "an empty Chinese subway concourse: a row of silver turnstile gates, cold white ceiling lights, "
        "tiled reflective floor, hanging directional signs with no readable text"),
    "loc_subway_car": ("scene",
        "an empty moving Chinese subway car: stainless poles, hanging hand straps, molded seats, "
        "closed doors, dark tunnel beyond the windows, fluorescent light"),
    "loc_platform": ("scene",
        "an empty Chinese subway platform: platform screen doors, yellow tactile paving, "
        "glowing advertising light boxes with no readable text, ceiling strip lights"),
    "loc_hospital": ("scene",
        "an empty modern hospital single room: one adjustable bed with white linens, "
        "stainless bedside cabinet, IV pole, visitor chair, morning light through frosted glass"),
    "loc_street_dusk": ("scene",
        "a quiet 1990s Chinese rural dirt street at golden hour: muddy path with tire tracks, "
        "low brick houses, a wooden fence, warm nostalgic light, no crowd"),
    "prop_phone_lin": ("prop",
        "one modern black smartphone, slim bezel-less design, dark glass back, screen off"),
    "prop_phone_wang": ("prop",
        "one old worn smartphone with a visibly cracked shattered screen, scuffed plastic body, dated design"),
    "prop_basket": ("prop",
        "one woven reusable shopping tote bag filled with folded clothes and a few packaged snack bags at the opening"),
    "prop_bus_sign": ("prop",
        "one tall vertical Chinese city bus-stop sign pole, metal panel with a blank A4 paper notice taped on it"),
}


def find_comfy():
    for d in (os.environ.get("COMFY_DIR"), "/root/ComfyUI",
              os.path.expanduser("~/ComfyUI"), "/workspace/ComfyUI"):
        if d and os.path.isdir(os.path.join(d, "input")):
            return d
    return "/root/ComfyUI"


COMFY = find_comfy()
INP = os.path.join(COMFY, "input")
OUT = os.path.join(COMFY, "output")


def view_prompt(kind, view, subject):
    """单视图英文 prompt。中文标注禁止出现。"""
    if kind == "character":
        who = f"Exactly one person. {subject}. Neutral closed-mouth expression, standing still"
        if view == "face_front":
            return (f"{who}, tight head-and-shoulders portrait only, face filling most of the frame, "
                    f"chest-up crop, do not show waist legs or feet, not a full-body photo, "
                    f"photorealistic studio, 85mm lens, sharp eyes, natural skin texture{STUDIO}{NO_TEXT}{ONE_VIEW}")
        if view == "face_side":
            return (f"{who}, tight head-and-shoulders portrait only, chest-up crop, "
                    f"do not show waist legs or feet, not a full-body photo, "
                    f"strict 90-degree left profile, 85mm lens{STUDIO}{NO_TEXT}{ONE_VIEW}")
        if view == "body_front":
            return (f"{who}, full body head-to-toe including shoes, facing camera, "
                    f"arms relaxed at sides, 50mm lens, feet not cropped{STUDIO}{NO_TEXT}{ONE_VIEW}")
        if view == "body_side":
            return (f"{who}, full body head-to-toe including shoes, strict 90-degree left side view, "
                    f"arms relaxed, 50mm lens{STUDIO}{NO_TEXT}{ONE_VIEW}")
        if view == "body_back":
            return (f"{who}, full body head-to-toe including shoes, back view facing away from camera, "
                    f"arms relaxed, 50mm lens{STUDIO}{NO_TEXT}{ONE_VIEW}")
    if kind == "scene":
        interior = subject  # caller passes place text
        five = ("simple layout, eye-level, 24mm architectural photography. "
                "The frame must show five surfaces: ceiling, floor, left wall, right wall, and the far back wall. "
                "Empty of people, uncluttered")
        ext = ("simple layout, eye-level, 24mm location photography, empty of people, uncluttered")
        if view == "eye":
            extra = five if "interior_flag" else ext
            return (f"Photorealistic empty location photograph. {interior}. {extra}{NO_TEXT}")
        if view == "reverse":
            return (f"Photorealistic photograph of the exact same empty location as the reference, "
                    f"camera rotated 180 degrees (reverse angle), same lens height, same 24mm, "
                    f"same proportions and lighting. {interior}. Empty of people{NO_TEXT}")
        if view == "left":
            return (f"Photorealistic left-side view of the exact same empty location as the references, "
                    f"camera on the left looking across, same lens height, same 24mm, matching proportions. "
                    f"{interior}. Empty of people{NO_TEXT}")
        if view == "right":
            return (f"Photorealistic right-side view of the exact same empty location as the references, "
                    f"camera on the right looking across, same lens height, same 24mm, matching proportions. "
                    f"{interior}. Empty of people{NO_TEXT}")
    if kind == "prop":
        obj = f"Exactly one object. {subject}. Photorealistic product photo, even studio light, plain light gray seamless background"
        if view == "front":
            return f"{obj}, front view, filling the frame, sharp{NO_TEXT}"
        if view == "side":
            return f"{obj}, strict 90-degree side view{NO_TEXT}"
        if view == "threeq":
            return f"{obj}, three-quarter view{NO_TEXT}"
        if view == "detail":
            return f"{obj}, extreme close-up of the most distinctive surface detail{NO_TEXT}"
    raise ValueError(f"unknown {kind}/{view}")


def scene_eye_prompt(subject, interior):
    if interior:
        geo = ("simple layout, eye-level, 24mm. The frame must show five surfaces: "
               "ceiling, floor, left wall, right wall, and the far back wall")
    else:
        geo = "simple layout, eye-level establishing shot, 24mm, matching left-right scale"
    return (f"Photorealistic empty location photograph. {subject}. {geo}. "
            f"Empty of people, uncluttered{NO_TEXT}")


def build_graph(prompt, size, ref_files, seed, prefix):
    w, h = size
    g = {
        "10": {"class_type": "UNETLoader", "inputs": {
            "unet_name": "Krea-2/krea2_turbo_fp8_scaled.safetensors", "weight_dtype": "default"}},
        "11": {"class_type": "CLIPLoader", "inputs": {
            "clip_name": "qwen3vl/qwen3vl_4b_fp8_scaled.safetensors", "type": "krea2", "device": "default"}},
        "12": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_vae.safetensors"}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}},
        "13": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["6", 0]}},
        "3": {"class_type": "KSampler", "inputs": {
            "model": ["10", 0], "positive": ["6", 0], "negative": ["13", 0],
            "latent_image": ["5", 0], "seed": seed, "steps": 8, "cfg": 1.0,
            "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["12", 0]}},
        "29": {"class_type": "SaveImage", "inputs": {
            "images": ["8", 0], "filename_prefix": prefix}},
    }
    if ref_files:
        ref_inputs = {"clip": ["11", 0], "prompt": prompt, "vae": ["12", 0]}
        for i, fname in enumerate(ref_files[:3], 1):
            nid = f"2{i}"
            g[nid] = {"class_type": "LoadImage", "inputs": {"image": fname}}
            ref_inputs[f"image{i}"] = [nid, 0]
        g["6"] = {"class_type": "TextEncodeKrea2Ref", "inputs": ref_inputs}
    else:
        g["6"] = {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["11", 0]}}
    return g


def submit(g):
    body = json.dumps({"prompt": g, "client_id": "studio-asset-views"}).encode()
    req = urllib.request.Request(API + "/prompt", data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=60).read())["prompt_id"]


def wait(pid):
    for _ in range(240):
        time.sleep(3)
        h = json.loads(urllib.request.urlopen(API + "/history/" + pid, timeout=15).read())
        e = h.get(pid, {})
        st = e.get("status", {})
        if st.get("completed"):
            for out in e.get("outputs", {}).values():
                for img in out.get("images", []):
                    return img["filename"]
        if st.get("status_str") == "error":
            raise RuntimeError(json.dumps(e.get("status"), ensure_ascii=False)[:400])
    raise TimeoutError(pid)


def copy_input(filename, stable):
    """把 output 里刚生成的图拷到 input，供下一张 LoadImage。"""
    src = os.path.join(OUT, filename)
    if not os.path.isfile(src):
        # 有的版本带 subfolder
        for root, _, files in os.walk(OUT):
            if filename in files:
                src = os.path.join(root, filename)
                break
    dst = os.path.join(INP, stable)
    os.makedirs(INP, exist_ok=True)
    shutil.copy2(src, dst)
    return stable


def gen(prompt, size, refs, seed, prefix):
    fn = wait(submit(build_graph(prompt, size, refs, seed, prefix)))
    stable = prefix + ".png"
    try:
        copy_input(fn, stable)
    except OSError as ex:
        print(f"WARN copy_input {fn}: {ex}", flush=True)
        stable = fn
    return fn, stable


def mix_refs(*groups):
    """最多 3 张参考，去重保序。"""
    out = []
    for g in groups:
        for x in (g or []):
            if x and x not in out:
                out.append(x)
            if len(out) == 3:
                return out
    return out


def old_ref_files(aid, extra=None):
    names = list(extra or [])
    for n in (f"old_{aid}.png", f"old_{aid}_front.png", f"old_{aid}_clean.png"):
        if os.path.isfile(os.path.join(INP, n)):
            names.append(n)
    return mix_refs(names)


def run_character(aid, subject, seed, old=None):
    out = {}
    old_body = old_ref_files(aid, old)
    old_face = [f"old_{aid}_face.png"] if os.path.isfile(
        os.path.join(INP, f"old_{aid}_face.png")) else old_body
    # 人物每张只喂 1 张参考，两张人像参考会出复制体
    p, s = gen(view_prompt("character", "face_front", subject), SIZE["face"],
               old_face[:1], seed, f"av_{aid}_face_front")
    print(f"VIEW {aid} face_front -> {p} refs={old_face[:1]}", flush=True)
    out["face_front"] = p
    face_ref = "av_{}_face_front.png".format(aid)
    p, s = gen(view_prompt("character", "face_side", subject), SIZE["face"],
               [face_ref], seed + 1, f"av_{aid}_face_side")
    print(f"VIEW {aid} face_side -> {p}", flush=True)
    out["face_side"] = p
    p, s = gen(view_prompt("character", "body_front", subject), SIZE["body"],
               (old_body[:1] or [face_ref]), seed + 2, f"av_{aid}_body_front")
    print(f"VIEW {aid} body_front -> {p}", flush=True)
    out["body_front"] = p
    body_front = "av_{}_body_front.png".format(aid)
    p, s = gen(view_prompt("character", "body_side", subject), SIZE["body"],
               [body_front], seed + 3, f"av_{aid}_body_side")
    print(f"VIEW {aid} body_side -> {p}", flush=True)
    out["body_side"] = p
    p, s = gen(view_prompt("character", "body_back", subject), SIZE["body"],
               [body_front], seed + 4, f"av_{aid}_body_back")
    print(f"VIEW {aid} body_back -> {p}", flush=True)
    out["body_back"] = p
    return out


def run_scene(aid, subject, seed, interior, old=None):
    out = {}
    old = old_ref_files(aid, old)
    p, s = gen(scene_eye_prompt(subject, interior) + ONE_VIEW, SIZE["scene"],
               old[:1], seed, f"av_{aid}_eye")
    print(f"VIEW {aid} eye -> {p} refs={old[:1]}", flush=True)
    out["eye"] = p
    eye = f"av_{aid}_eye.png"
    p, s = gen(view_prompt("scene", "reverse", subject) + ONE_VIEW, SIZE["scene"],
               [eye], seed + 1, f"av_{aid}_reverse")
    print(f"VIEW {aid} reverse -> {p}", flush=True)
    out["reverse"] = p
    p, s = gen(view_prompt("scene", "left", subject) + ONE_VIEW, SIZE["scene"],
               [eye], seed + 2, f"av_{aid}_left")
    print(f"VIEW {aid} left -> {p}", flush=True)
    out["left"] = p
    p, s = gen(view_prompt("scene", "right", subject) + ONE_VIEW, SIZE["scene"],
               [eye], seed + 3, f"av_{aid}_right")
    print(f"VIEW {aid} right -> {p}", flush=True)
    out["right"] = p
    return out


def run_prop(aid, subject, seed, old=None):
    out = {}
    old = old_ref_files(aid, old)
    p, s = gen(view_prompt("prop", "front", subject) + ONE_VIEW, SIZE["prop"],
               old[:1], seed, f"av_{aid}_front")
    print(f"VIEW {aid} front -> {p} refs={old[:1]}", flush=True)
    out["front"] = p
    front = f"av_{aid}_front.png"
    p, s = gen(view_prompt("prop", "side", subject) + ONE_VIEW, SIZE["prop"],
               [front], seed + 1, f"av_{aid}_side")
    print(f"VIEW {aid} side -> {p}", flush=True)
    out["side"] = p
    p, s = gen(view_prompt("prop", "threeq", subject) + ONE_VIEW, SIZE["prop"],
               [front], seed + 2, f"av_{aid}_threeq")
    print(f"VIEW {aid} threeq -> {p}", flush=True)
    out["threeq"] = p
    p, s = gen(view_prompt("prop", "detail", subject) + ONE_VIEW, SIZE["prop"],
               [front], seed + 3, f"av_{aid}_detail")
    print(f"VIEW {aid} detail -> {p}", flush=True)
    out["detail"] = p
    return out


GROUPS = {
    "characters": [k for k, v in CATALOG.items() if v[0] == "character"],
    "scenes": [k for k, v in CATALOG.items() if v[0] == "scene"],
    "props": [k for k, v in CATALOG.items() if v[0] == "prop"],
}


def jobs_from_args(argv):
    if not sys.stdin.isatty():
        raw = sys.stdin.read().strip()
        if raw:
            data = json.loads(raw)
            return data if isinstance(data, list) else data.get("jobs", [data])
    ids = []
    for a in argv:
        if a in ("all",):
            ids.extend(GROUPS["characters"] + GROUPS["scenes"]
                       + [x for x in GROUPS["props"] if x != "prop_bus_sign"])
        elif a in GROUPS:
            ids.extend(GROUPS[a])
        else:
            ids.append(a)
    jobs = []
    for i, aid in enumerate(ids):
        if aid not in CATALOG:
            print(f"SKIP unknown id {aid}", flush=True)
            continue
        kind, subject = CATALOG[aid]
        jobs.append({
            "id": aid, "type": kind, "subject": subject,
            "seed": 80000 + i * 10,
            "interior": aid in INTERIOR,
        })
    return jobs


def main():
    jobs = jobs_from_args(sys.argv[1:])
    if not jobs:
        print("usage: gen_asset_views.py all|characters|scenes|props|<id>", file=sys.stderr)
        sys.exit(2)
    summary = []
    for job in jobs:
        aid = job["id"]
        kind = job["type"]
        subject = job.get("subject") or CATALOG.get(aid, ("", ""))[1]
        seed = int(job.get("seed") or 80000)
        t0 = time.time()
        old = job.get("refs") or []
        if kind == "character":
            views = run_character(aid, subject, seed, old)
        elif kind == "scene":
            interior = job.get("interior")
            if interior is None:
                interior = aid in INTERIOR
            views = run_scene(aid, subject, seed, interior, old)
        elif kind == "prop":
            views = run_prop(aid, subject, seed, old)
        else:
            print(f"SKIP bad type {kind}", flush=True)
            continue
        rec = {"id": aid, "type": kind, "views": views, "sec": round(time.time() - t0, 1)}
        summary.append(rec)
        print("ASSET " + json.dumps(rec, ensure_ascii=False), flush=True)
    print("ASSET_VIEWS_DONE " + json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
