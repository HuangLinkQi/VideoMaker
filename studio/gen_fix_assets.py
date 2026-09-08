#!/usr/bin/env python3
"""重生成 4 个待修改资产（2026-09-05）- Krea-2 Turbo"""
import json
import time
import urllib.request

API = "http://127.0.0.1:8188"

TURNAROUND = ("Character turnaround reference sheet, three full-body views of the same child "
              "standing in one row (front view, side view, back view), even spacing, "
              "plain light gray seamless studio background, soft even lighting, "
              "ultra realistic photograph, natural skin texture. Subject: ")
SCENE_SHEET = (". Film location concept sheet, three views of the exact same place in one row "
               "(wide establishing view, medium view, detail close-up), consistent architecture "
               "and lighting across views, no people, no readable text, no watermark")
PROP_SHEET = (". Product design reference sheet, three views of the exact same object in one row "
              "(front view, side view, three-quarter view), plain light gray seamless background, "
              "soft even studio lighting, no readable text, no watermark")

JOBS = {
 "linchuan_child": TURNAROUND +
   "a 5-year-old Chinese little boy, round face with short watermelon-cut black hair, "
   "big eyes, wearing a faded and slightly oversized patched blue cotton jacket, "
   "dark pants with worn knees, cloth shoes with scuffed soles, "
   "a simple worn canvas schoolbag with frayed straps, quiet shy expression, "
   "poor rural village child appearance",
 "loc_hospital": "modern hospital ward, single patient room, clean white walls with light gray "
   "accents, modern LED ceiling panels, sleek hospital bed with adjustable rails, "
   "modern IV pole with digital monitor, stainless steel bedside table, minimalist design, "
   "morning soft natural light through large frosted window, photorealistic" + SCENE_SHEET,
 "loc_street_dusk": "A quiet rural Chinese village dirt road, uneven muddy earth path with tire "
   "tracks and small puddles, low brick and adobe houses with tiled roofs, scattered weeds, "
   "a wooden fence section, warm golden hour light, nostalgic rural atmosphere, "
   "photorealistic" + SCENE_SHEET,
 "prop_basket": "A woven reusable shopping tote bag filled with folded clothes and a few modern "
   "packaged snacks visible at the opening (small bags of chips and cookies), "
   "photorealistic product photo" + PROP_SHEET,
}

def build(text):
    return {
        "10": {"class_type": "UNETLoader", "inputs": {
            "unet_name": "Krea-2/krea2_turbo_fp8_scaled.safetensors", "weight_dtype": "default"}},
        "11": {"class_type": "CLIPLoader", "inputs": {
            "clip_name": "qwen3vl/qwen3vl_4b_fp8_scaled.safetensors", "type": "krea2", "device": "default"}},
        "12": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_vae.safetensors"}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": text, "clip": ["11", 0]}},
        "13": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["6", 0]}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 2048, "height": 1152, "batch_size": 1}},
        "3": {"class_type": "KSampler", "inputs": {
            "model": ["10", 0], "positive": ["6", 0], "negative": ["13", 0],
            "latent_image": ["5", 0], "seed": 0, "steps": 8, "cfg": 1.0,
            "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["12", 0]}},
        "29": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": "fix"}},
    }

def submit(text, seed):
    g = build(text)
    g["3"]["inputs"]["seed"] = seed
    g["29"]["inputs"]["filename_prefix"] = f"fix_{seed}"
    body = json.dumps({"prompt": g, "client_id": "studio-fix"}).encode()
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

seed = 71000
for aid, text in JOBS.items():
    t0 = time.time()
    pid = submit(text, seed)
    fn = wait(pid)
    print(f"{aid} seed={seed} -> {fn} ({time.time()-t0:.1f}s)", flush=True)
    seed += 1
print("FIX_DONE")
