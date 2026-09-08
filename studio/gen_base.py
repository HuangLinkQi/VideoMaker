#!/usr/bin/env python3
"""无锚新资产的 bootstrap 基图（Krea-2 Turbo txt2img，单视角单主体）。
用法（GPU）: python3 gen_base.py <out_prefix> portrait|square "<prompt>\""""
import json
import os
import sys
import time
import urllib.request

API = os.environ.get("COMFY_API", "http://127.0.0.1:8188")
SIZE = {"portrait": (1152, 2048), "square": (1536, 1536), "landscape": (1344, 768)}


def main() -> None:
    prefix, orient, prompt = sys.argv[1], sys.argv[2], sys.argv[3]
    w, h = SIZE[orient]
    seed = int(sys.argv[4]) if len(sys.argv) > 4 else 91000
    g = {
        "10": {"class_type": "UNETLoader", "inputs": {
            "unet_name": "Krea-2/krea2_turbo_fp8_scaled.safetensors", "weight_dtype": "default"}},
        "11": {"class_type": "CLIPLoader", "inputs": {
            "clip_name": "qwen3vl/qwen3vl_4b_fp8_scaled.safetensors", "type": "krea2", "device": "default"}},
        "12": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_vae.safetensors"}},
        "13": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["6", 0]}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["11", 0]}},
        "3": {"class_type": "KSampler", "inputs": {
            "model": ["10", 0], "positive": ["6", 0], "negative": ["13", 0], "latent_image": ["5", 0],
            "seed": seed, "steps": 8, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple",
            "denoise": 1.0}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["12", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": prefix}},
    }
    body = json.dumps({"prompt": g, "client_id": "studio-base"}).encode()
    req = urllib.request.Request(API + "/prompt", data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    pid = json.loads(urllib.request.urlopen(req, timeout=60).read())["prompt_id"]
    t0 = time.time()
    for _ in range(240):
        time.sleep(3)
        hst = json.loads(urllib.request.urlopen(API + "/history/" + pid, timeout=15).read())
        e = hst.get(pid, {})
        st = e.get("status", {})
        if st.get("completed"):
            for out in e.get("outputs", {}).values():
                for img in out.get("images", []):
                    print(f"DONE {img['filename']} {time.time() - t0:.1f}s", flush=True)
                    return
        if st.get("status_str") == "error":
            raise RuntimeError(json.dumps(e.get("status"), ensure_ascii=False)[:400])
    raise TimeoutError(pid)


if __name__ == "__main__":
    main()
