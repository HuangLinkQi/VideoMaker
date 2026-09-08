#!/usr/bin/env python3
"""首尾帧批量生成（Krea-2 + TextEncodeKrea2Ref，state 驱动）。

任务 JSON 由本地 batch_keyframes.py 组装（stdin 传入）:
  [{"sid": "sc01", "frame": "start", "prompt": "...", "refs": ["kf_ref_linchuan.png", ...], "seed": 51001},
   {"sid": "sc01", "frame": "end",   "prompt": "...", "refs": [...], "seed": 51002, "i2i_of": "sc01"}]

两阶段：先跑全部 start（收集产物文件名），再跑 end（i2i 吃同场 start 产物，denoise 0.55 保连贯）。
"""
import json
import sys
import time
import urllib.request

API = "http://127.0.0.1:8188"
STYLE = (". Photorealistic cinematic film still, natural lighting, shallow depth of field, "
         "35mm lens, muted realistic color grading, no text, no watermark, no readable characters")


def build_graph(prompt_text, ref_files, seed, img2img=None, denoise=1.0, prefix="kf", temp=False):
    g = {
        "10": {"class_type": "UNETLoader", "inputs": {
            "unet_name": "Krea-2/krea2_turbo_fp8_scaled.safetensors", "weight_dtype": "default"}},
        "11": {"class_type": "CLIPLoader", "inputs": {
            "clip_name": "qwen3vl/qwen3vl_4b_fp8_scaled.safetensors", "type": "krea2", "device": "default"}},
        "12": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_vae.safetensors"}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 1344, "height": 768, "batch_size": 1}},
        "30": {"class_type": "LoadImage", "inputs": {"image": img2img or "placeholder.png"}},
        "31": {"class_type": "VAEEncode", "inputs": {"pixels": ["30", 0], "vae": ["12", 0]}},
        "13": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["6", 0]}},
        "3": {"class_type": "KSampler", "inputs": {
            "model": ["10", 0], "positive": ["6", 0], "negative": ["13", 0],
            "latent_image": ["31" if img2img else "5", 0], "seed": seed, "steps": 8, "cfg": 1.0,
            "sampler_name": "euler", "scheduler": "simple", "denoise": denoise}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["12", 0]}},
        "29": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": prefix}},
    }
    if temp:  # 首帧存 temp：LoadImage 可读 temp（input/ 里没有刚生成的图）
        g["29"]["inputs"]["save_output"] = False
    ref_inputs = {"clip": ["11", 0], "prompt": prompt_text, "vae": ["12", 0]}
    for i, fname in enumerate(ref_files[:3], 1):
        nid = f"2{i}"
        g[nid] = {"class_type": "LoadImage", "inputs": {"image": fname}}
        ref_inputs[f"image{i}"] = [nid, 0]
    g["6"] = {"class_type": "TextEncodeKrea2Ref", "inputs": ref_inputs}
    return g


def submit(g):
    body = json.dumps({"prompt": g, "client_id": "studio-kf2"}).encode()
    req = urllib.request.Request(f"{API}/prompt", data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=60).read())["prompt_id"]


def wait(pid):
    for _ in range(200):
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


def run(job, i2i_file=None):
    prompt = job["prompt"] + STYLE
    is_start = job["frame"] == "start"
    g = build_graph(prompt, job["refs"], job["seed"],
                    img2img=i2i_file, denoise=job.get("dn", 0.55 if i2i_file else 1.0),
                    prefix=f"kf_{job['sid']}_{job['frame']}", temp=is_start)
    fn = wait(submit(g))
    print(f"OK {job['sid']}_{job['frame']} seed={job['seed']} -> {fn}", flush=True)
    return fn


def main():
    import shutil
    jobs = json.load(sys.stdin)
    starts = [j for j in jobs if j["frame"] == "start"]
    ends = [j for j in jobs if j["frame"] == "end"]
    start_files = {}
    for j in starts:
        try:
            start_files[j["sid"]] = run(j)  # 正常存 output/
        except Exception as e:  # noqa: BLE001
            print(f"FAIL {j['sid']}_{j['frame']} {repr(e)[:200]}", flush=True)
    # LoadImage 只认 input/ 目录：把首帧复制进 input/ 供尾帧 img2img
    for sid, fn in start_files.items():
        shutil.copy(f"/root/ComfyUI/output/{fn}", f"/root/ComfyUI/input/kf_i2i_{sid}.png")
    for j in ends:
        try:
            i2i = j.get("i2i_of")
            run(j, i2i_file=(f"kf_i2i_{i2i}.png" if i2i else None))
        except Exception as e:  # noqa: BLE001
            print(f"FAIL {j['sid']}_{j['frame']} {repr(e)[:200]}", flush=True)
    print("KF_DONE")


if __name__ == "__main__":
    main()
