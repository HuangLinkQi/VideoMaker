#!/usr/bin/env python3
"""导演工作台（分镜板）生成 - Krea-2 Turbo + TextEncodeKrea2Ref 参考图

一张图呈现一个场景的分镜布置：左侧大建立格 + 右侧 2x2 小格，
每格标时长（0-3s 等），人物/场景/道具与已批资产一致。
注意红线：prompt 不渲染中文（对白在页面上展示，不画进图里）。

输入(stdin, JSON 数组，每场一项；panels 由 agent 按知识库 22 套方法论拆镜):
  [{"scene": "sc01", "chars": ["linchuan"],
    "refs": ["loc_office_night", "prop_phone_lin"],
    "panels": ["0-3s wide shot: man alone at desk, monitor glow",
               "3-6s medium: he reaches for ringing phone",
               "6-8s close-up: tired eyes, answers call"]}]

用法（GPU 服务器上）: cat /tmp/panels_sc01.json | python3 gen_storyboard.py [retry]
输出: 每行一个产物文件名（sb_<scene>_XXXX.png），agent 拉回后入库
seed 规则: 60000 + retry*100 + 场序（同 gen_keyframes 的批内递增风格）
"""
import json
import sys
import time
import urllib.request

API = "http://127.0.0.1:8188"

# 与 studio/gen_keyframes.py 保持同步（自包含，服务器上单文件运行）
CHARS = {
 "linchuan": ("char_linchuan_front.png",
   "Lin Chuan, a 30-year-old Chinese male programmer, 175cm tall, slim, short neat black hair, "
   "thin-rim rectangular glasses, light blue dress shirt with rolled-up sleeves, dark trousers"),
 "wangguifen": ("char_wangguifen_front.png",
   "Wang Guifen, a 58-year-old Chinese rural woman, 158cm tall, slim, short gray-streaked black hair "
   "in a low bun, faded dark cotton-padded jacket over a worn floral blouse, "
   "loose dark trousers, cloth shoes, carrying a bamboo basket with vegetables and eggs"),
 "zhouye": ("char_zhouye_front.png",
   "Zhou Ye, a 20-year-old Chinese male college student, 176cm tall, short clean black hair, "
   "navy windbreaker over a white t-shirt, jeans, backpack"),
 "xiaoyu": ("char_xiaoyu_front.png",
   "Xiao Yu, a 12-year-old Chinese schoolgirl, 145cm tall, shoulder-length black hair with bangs "
   "and a pink hair clip, white school shirt, navy pleated skirt, small school backpack"),
 "wangguifen_young": ("char_wangguifen_young_front.png",
   "young Wang Guifen, a 30-year-old Chinese rural woman, 160cm tall, long black braided hair, "
   "plain faded old cotton coat, dark trousers, cloth shoes"),
 "linchuan_child": ("char_linchuan_child_front.png",
   "young Lin Chuan, a 5-year-old Chinese boy, 105cm tall, short black bowl-cut hair, "
   "small blue cotton coat, dark pants, tiny canvas schoolbag"),
}
REFS = {
 "loc_office_night": ("ref_loc_office_night.png", "the same night office location as the reference image"),
 "loc_home": ("ref_loc_home.png", "the same rural home interior as the reference image"),
 "loc_train": ("ref_loc_train.png", "the same green train carriage as the reference image"),
 "loc_bus_stop": ("ref_loc_bus_stop.png", "the same city bus stop street as the reference image"),
 "loc_subway_gate": ("ref_loc_subway_gate.png", "the same subway concourse turnstiles as the reference image"),
 "loc_subway_car": ("ref_loc_subway_car.png", "the same subway car interior as the reference image"),
 "loc_platform": ("ref_loc_platform.png", "the same subway platform as the reference image"),
 "loc_hospital": ("ref_loc_hospital.png", "the same hospital room as the reference image"),
 "loc_street_dusk": ("ref_loc_street_dusk.png", "the same 1990s dusk street as the reference image"),
 "prop_phone_lin": ("ref_prop_phone_lin.png", "the same black modern smartphone as the reference image"),
 "prop_phone_wang": ("ref_prop_phone_wang.png", "the same old cracked-screen smartphone as the reference image"),
 "prop_basket": ("ref_prop_basket.png", "the same 45cm bamboo basket with vegetables eggs and buns as the reference image"),
 "prop_bus_sign": ("ref_prop_bus_sign.png", "the same bus stop sign pole as the reference image"),
}

STYLE = (". Director storyboard reference sheet for one film scene, comic-style multi-panel "
         "layout: one large establishing panel on the left, four smaller panels in a 2x2 grid "
         "on the right, thin white panel borders. The same characters appear consistently "
         "across all panels, same location and props, camera angles vary between panels "
         "(wide establishing, medium, close-up). Small duration labels in panel corners. "
         "Photorealistic cinematic storyboard, muted realistic color grading, "
         "no readable text except tiny duration numbers, no watermark")


def build_graph(prompt_text, ref_files, seed, scene_id):
    g = {
        "10": {"class_type": "UNETLoader", "inputs": {
            "unet_name": "Krea-2/krea2_turbo_fp8_scaled.safetensors", "weight_dtype": "default"}},
        "11": {"class_type": "CLIPLoader", "inputs": {
            "clip_name": "qwen3vl/qwen3vl_4b_fp8_scaled.safetensors", "type": "krea2", "device": "default"}},
        "12": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_vae.safetensors"}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 1344, "height": 768, "batch_size": 1}},
        "13": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["6", 0]}},
        "3": {"class_type": "KSampler", "inputs": {
            "model": ["10", 0], "positive": ["6", 0], "negative": ["13", 0],
            "latent_image": ["5", 0], "seed": seed, "steps": 8, "cfg": 1.0,
            "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["12", 0]}},
        "29": {"class_type": "SaveImage", "inputs": {
            "images": ["8", 0], "filename_prefix": f"sb_{scene_id}"}},
    }
    ref_inputs = {"clip": ["11", 0], "prompt": prompt_text, "vae": ["12", 0]}
    for i, fname in enumerate(ref_files[:3], 1):
        nid = f"2{i}"
        g[nid] = {"class_type": "LoadImage", "inputs": {"image": fname}}
        ref_inputs[f"image{i}"] = [nid, 0]
    g["6"] = {"class_type": "TextEncodeKrea2Ref", "inputs": ref_inputs}
    return g


def submit(g):
    body = json.dumps({"prompt": g, "client_id": "studio-sb"}).encode()
    req = urllib.request.Request(f"{API}/prompt", data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=60).read())["prompt_id"]


def wait(pid):
    for _ in range(180):
        time.sleep(3)
        h = json.loads(urllib.request.urlopen(f"{API}/history/{pid}", timeout=15).read())
        e = h.get(pid, {})
        st = e.get("status", {})
        if st.get("completed"):
            for out in e.get("outputs", {}).values():
                for img in out.get("images", []):
                    return img["filename"]
        if st.get("status_str") == "error":
            raise RuntimeError(json.dumps(e.get("status"), ensure_ascii=False)[:400])
    raise TimeoutError(pid)


def main():
    retry = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    items = json.loads(sys.stdin.read())
    base_seed = 60000 + retry * 100
    for idx, it in enumerate(items):
        sid = it["scene"]
        chars = it.get("chars", [])
        n = len(chars)
        people = ("Exactly one single person in every panel, no duplicates. "
                  if n == 1 else
                  f"Exactly {n} people appear in the panels, no duplicates. ")
        panels = " ".join(f"({p}) " for p in it.get("panels", []))
        text = (people + "Storyboard panels depict, in order: " + panels
                + ". " + ". ".join(CHARS[c][1] for c in chars[:2] if c in CHARS))
        ref_files = [CHARS[c][0] for c in chars[:2] if c in CHARS]
        hints = []
        for r in it.get("refs", []):
            if r in REFS:
                hints.append(REFS[r][1])
                if len(ref_files) < 3:
                    ref_files.append(REFS[r][0])
        if hints:
            text += ". References: " + "; ".join(hints)
        text += STYLE
        seed = base_seed + idx
        t0 = time.time()
        fn = wait(submit(build_graph(text, ref_files, seed, sid)))
        print(f"{fn} seed={seed} refs={len(ref_files)} ({time.time()-t0:.1f}s)", flush=True)
    print("STORYBOARD_DONE", flush=True)


if __name__ == "__main__":
    main()
