#!/usr/bin/env python3
"""资产设定图：一条工作流一次出完整图（禁止分视再拼接）。

人物：全身正/侧/背 turnaround 一张
场景：同地三视角一张
道具：正/侧/3/4 一张

Krea-2 Turbo 2048×1152。可选 old_<id>.png 作 TextEncodeKrea2Ref（仍是同一张图一次采样）。

用法（GPU）: python3 gen_assets.py all|characters|scenes|props|<id> [denoise=1.0] [seed=82000]
  denoise<1 且存在 old_<id>.png 时走 img2img（VAEEncode），否则 txt2img。
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

API = os.environ.get("COMFY_API", "http://127.0.0.1:8188")
W, H = 2048, 1152
NO_TEXT = ", no readable text, no watermark, no logo, no caption"

STYLE_CHAR = (
    ". Character turnaround reference sheet of ONE person, three full-body views "
    "in a single row (front 0 degrees, left side 90 degrees, back 180 degrees), "
    "the same identity in all three panels, even spacing, standing, arms relaxed, "
    "plain light gray seamless studio background, even soft lighting, photorealistic, "
    "head-to-toe including shoes, no extra people" + NO_TEXT
)
STYLE_SCENE = (
    ". Film location concept sheet of ONE place, three views of the exact same location "
    "in a single row (wide establishing, medium, detail), identical architecture lighting "
    "and furniture, no people, photorealistic cinematic" + NO_TEXT
)
STYLE_PROP = (
    ". Product design reference sheet of ONE object, three views in a single row "
    "(front, side, three-quarter), plain light gray seamless background, even studio lighting, "
    "photorealistic product photo, no extra copies of the object" + NO_TEXT
)

CHARS = {
    "linchuan":
        "a 30-year-old Chinese male programmer named Lin Chuan, slim 175cm, "
        "short neat black hair, rectangular face, MUST wear thin black rectangular eyeglasses, "
        "light blue dress shirt sleeves rolled up, dark trousers, white sneakers",
    "wangguifen":
        "a 58-year-old Chinese rural woman named Wang Guifen, slim 158cm, "
        "short gray-streaked black hair in a low bun, weathered face, "
        "faded dark cotton-padded jacket over a worn floral blouse, loose dark trousers, cloth shoes",
    "zhouye":
        "a 20-year-old Chinese male college student named Zhou Ye, 176cm, "
        "short clean black hair, navy windbreaker over a white t-shirt, jeans, white sneakers, backpack",
    "xiaoyu":
        "a 12-year-old Chinese schoolgirl named Xiao Yu, 145cm, child proportions, "
        "shoulder-length black hair with bangs and a small pink hair clip, "
        "white school shirt, navy necktie, navy pleated skirt, white socks, white sneakers, small backpack",
    "wangguifen_young":
        "a 30-year-old Chinese rural woman, younger Wang Guifen, 160cm, "
        "long black hair in two braids, plain faded old cotton coat, dark trousers, cloth shoes",
    "linchuan_child":
        "a 5-year-old Chinese little boy, young Lin Chuan, 105cm, "
        "round face, short watermelon-cut black hair, faded oversized patched blue cotton jacket, "
        "dark pants with worn knees, cloth shoes, worn canvas schoolbag",
}
SCENES = {
    "loc_office_night":
        "an empty open-plan Chinese tech-company office at night: desks with dark monitors, "
        "office chairs, floor-to-ceiling windows with city lights, cold blue residual glow",
    "loc_home":
        "an empty modest rural Chinese home interior: wooden square table, stools, enamel wash basin, "
        "warm tungsten desk lamp, worn concrete walls, wooden door to a courtyard, morning light",
    "loc_train":
        "an empty old Chinese green-skinned train hard-seat carriage: dark green bench seats, "
        "folding table by a large window, luggage rack, farmland light",
    "loc_bus_stop":
        "a Chinese city street bus stop on an empty sidewalk: one vertical metal bus-stop pole "
        "with a blank paper notice, plane trees, overcast daylight",
    "loc_subway_gate":
        "an empty Chinese subway concourse: silver turnstile gates, cold white ceiling lights, "
        "tiled reflective floor, hanging signs with no readable text",
    "loc_subway_car":
        "an empty moving Chinese subway car: stainless poles, hanging straps, molded seats, "
        "closed doors, dark tunnel beyond windows",
    "loc_platform":
        "an empty Chinese subway platform: platform screen doors, yellow tactile paving, "
        "glowing advertising light boxes with no readable text",
    "loc_hospital":
        "an empty modern hospital single room: one adjustable bed with white linens, "
        "stainless bedside cabinet, IV pole, visitor chair, morning frosted-window light",
    "loc_street_dusk":
        "a quiet 1990s Chinese rural dirt street at golden hour: muddy path, low brick houses, "
        "wooden fence, warm nostalgic light, no crowd",
}
PROPS = {
    "prop_phone_lin":
        "one modern black smartphone, slim bezel-less design, dark glass back, screen off",
    "prop_phone_wang":
        "one old worn smartphone with a visibly cracked shattered screen, scuffed plastic body",
    "prop_basket":
        "one woven reusable shopping tote bag filled with folded clothes and packaged snack bags",
}

REF_LOCK = (
    " The reference image is the same subject. Keep identity, clothing, materials and colors. "
    "Output a single contact sheet as described, not a copy of a photograph, no extra people or duplicate objects."
)


def find_input():
    for d in (os.environ.get("COMFY_DIR"), "/root/ComfyUI",
              os.path.expanduser("~/ComfyUI"), "/workspace/ComfyUI"):
        if d and os.path.isdir(os.path.join(d, "input")):
            return os.path.join(d, "input")
    return "/root/ComfyUI/input"


INP = find_input()


def old_ref(aid):
    for n in (f"old_{aid}.png", f"old_{aid}_front.png", f"old_{aid}_clean.png"):
        if os.path.isfile(os.path.join(INP, n)):
            return n
    return None


def build(aid, text, ref, denoise=1.0):
    g = {
        "10": {"class_type": "UNETLoader", "inputs": {
            "unet_name": "Krea-2/krea2_turbo_fp8_scaled.safetensors", "weight_dtype": "default"}},
        "11": {"class_type": "CLIPLoader", "inputs": {
            "clip_name": "qwen3vl/qwen3vl_4b_fp8_scaled.safetensors", "type": "krea2", "device": "default"}},
        "12": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_vae.safetensors"}},
        "13": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["6", 0]}},
        "3": {"class_type": "KSampler", "inputs": {
            "model": ["10", 0], "positive": ["6", 0], "negative": ["13", 0],
            "latent_image": ["5", 0], "seed": 0, "steps": 8, "cfg": 1.0,
            "sampler_name": "euler", "scheduler": "simple", "denoise": denoise}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["12", 0]}},
        "29": {"class_type": "SaveImage", "inputs": {
            "images": ["8", 0], "filename_prefix": f"sheet_{aid}"}},
    }
    use_i2i = ref and denoise < 0.999
    if use_i2i:
        g["20"] = {"class_type": "LoadImage", "inputs": {"image": ref}}
        g["21"] = {"class_type": "ImageScale", "inputs": {
            "image": ["20", 0], "upscale_method": "lanczos",
            "width": W, "height": H, "crop": "center"}}
        g["5"] = {"class_type": "VAEEncode", "inputs": {
            "pixels": ["21", 0], "vae": ["12", 0]}}
        g["6"] = {"class_type": "TextEncodeKrea2Ref", "inputs": {
            "clip": ["11", 0], "prompt": text + REF_LOCK, "vae": ["12", 0],
            "image1": ["20", 0]}}
    else:
        g["5"] = {"class_type": "EmptyLatentImage", "inputs": {
            "width": W, "height": H, "batch_size": 1}}
        if ref:
            g["20"] = {"class_type": "LoadImage", "inputs": {"image": ref}}
            g["6"] = {"class_type": "TextEncodeKrea2Ref", "inputs": {
                "clip": ["11", 0], "prompt": text + REF_LOCK, "vae": ["12", 0],
                "image1": ["20", 0]}}
        else:
            g["6"] = {"class_type": "CLIPTextEncode", "inputs": {
                "text": text, "clip": ["11", 0]}}
    return g


def submit(g, seed):
    g["3"]["inputs"]["seed"] = seed
    body = json.dumps({"prompt": g, "client_id": "studio-assets"}).encode()
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


def catalog():
    out = []
    for aid, sub in CHARS.items():
        out.append((aid, "character", sub + STYLE_CHAR))
    for aid, sub in SCENES.items():
        out.append((aid, "scene", sub + STYLE_SCENE))
    for aid, sub in PROPS.items():
        out.append((aid, "prop", sub + STYLE_PROP))
    return out


def main():
    denoise = 1.0
    seed = 82000
    rest = []
    for a in sys.argv[1:]:
        if a.startswith("denoise="):
            denoise = float(a.split("=", 1)[1])
        elif a.startswith("seed="):
            seed = int(a.split("=", 1)[1])
        else:
            rest.append(a)
    which = rest or ["all"]
    jobs = catalog()
    if which != ["all"]:
        if which == ["characters"]:
            jobs = [j for j in jobs if j[1] == "character"]
        elif which == ["scenes"]:
            jobs = [j for j in jobs if j[1] == "scene"]
        elif which == ["props"]:
            jobs = [j for j in jobs if j[1] == "prop"]
        else:
            jobs = [j for j in jobs if j[0] in which]
    if not jobs:
        print("NO_JOBS", which, file=sys.stderr)
        sys.exit(2)
    for i, (aid, kind, text) in enumerate(jobs):
        ref = old_ref(aid)
        t0 = time.time()
        fn = wait(submit(build(aid, text, ref, denoise), seed + i))
        print(f"SHEET {aid} {kind} seed={seed+i} denoise={denoise} ref={ref} -> {fn} ({time.time()-t0:.1f}s)",
              flush=True)
    print("ASSETS_DONE")


if __name__ == "__main__":
    main()
