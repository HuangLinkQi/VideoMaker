#!/usr/bin/env python3
"""sc02 单场重出：仅首帧引导（尾帧已移除，标准 i2v），验证连贯性"""
import json, time, urllib.request

API = "http://127.0.0.1:8188"
PROMPT = "【总时长7秒｜首帧=左手持手机贴左耳通话中】\n视觉风格：写实电影质感，深夜办公室冷蓝显示器光，35mm浅景深，固定机位在他左侧。\n镜头：固定中景，全程不动。\n动作：林川左手持手机贴左耳，一边说话一边瞟着显示器；说完后把手机从耳边移开，屏幕朝下放到桌上，闭眼揉了揉眼睛，疲惫地从椅子上半起身。\n对白与口型：他语速很快地敷衍：「妈，我忙完再说，先挂了。」嘴唇开合与台词逐字对应，尾音仓促；挂断后闭嘴，下颌绷紧。\n表演：赶时间的敷衍——说话快、视线不离显示器；挂断后停一秒才揉眼，情绪从敷衍滑向一丝愧疚。\n声音：成片无声，仅保留口型；情绪参考：手机落桌轻响、椅轮轻响。\n关键锁定：只有他一个人；手机在左手；挂断动作完成即释放，不再举手机。"
LENGTH = 175

g = {
    "1": {"class_type": "UNETLoader", "inputs": {
        "unet_name": "minimax-h3/minimax_h3_fl2va_pruned_int8_convrot.safetensors",
        "weight_dtype": "default"}},
    "2": {"class_type": "CLIPLoader", "inputs": {
        "clip_name": "minimax-h3/qwen3vl_32b_minimax_h3_int8_convrot.safetensors",
        "type": "minimax", "device": "default"}},
    "3": {"class_type": "VAELoader", "inputs": {
        "vae_name": "minimax-h3/minimax_h3_video_vae_fp16.safetensors"}},
    "10": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": PROMPT}},
    "11": {"class_type": "LoadImage", "inputs": {"image": "kf_sc02_start.png"}},
    "20": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {
        "clip": ["2", 0], "vae": ["3", 0], "prompt": ["10", 0],
        "width": 1344, "height": 768, "length": LENGTH,
        "first_frame": ["11", 0]}},
    "30": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
    "31": {"class_type": "BasicScheduler", "inputs": {
        "model": ["1", 0], "scheduler": "simple", "steps": 12, "denoise": 1.0}},
    "32": {"class_type": "RandomNoise", "inputs": {"noise_seed": 42}},
    "33": {"class_type": "BasicGuider", "inputs": {
        "model": ["1", 0], "conditioning": ["20", 0]}},
    "34": {"class_type": "SamplerCustomAdvanced", "inputs": {
        "noise": ["32", 0], "guider": ["33", 0], "sampler": ["30", 0],
        "sigmas": ["31", 0], "latent_image": ["20", 1]}},
    "40": {"class_type": "VAEDecode", "inputs": {"samples": ["34", 0], "vae": ["3", 0]}},
    "50": {"class_type": "CreateVideo", "inputs": {"images": ["40", 0], "fps": 24}},
    "60": {"class_type": "SaveVideo", "inputs": {
        "video": ["50", 0], "filename_prefix": "vid/sc02sf", "format": "auto", "codec": "auto"}},
}

def submit(g):
    body = json.dumps({"prompt": g, "client_id": "studio-vid"}).encode()
    req = urllib.request.Request(API + "/prompt", data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=60).read())["prompt_id"]

def wait(pid):
    for _ in range(600):
        time.sleep(5)
        h = json.loads(urllib.request.urlopen(API + "/history/" + pid, timeout=15).read())
        e = h.get(pid, {})
        st = e.get("status", {})
        if st.get("completed"):
            files = []
            for out in e.get("outputs", {}).values():
                for v in out.get("videos", out.get("images", [])):
                    files.append(v.get("filename", ""))
            return files
        if st.get("status_str") == "error":
            raise RuntimeError(json.dumps(e.get("status"), ensure_ascii=False)[:400])
    raise TimeoutError(pid)

t0 = time.time()
try:
    files = wait(submit(g))
    print(f"sc02sf -> {files} ({time.time()-t0:.0f}s)", flush=True)
except Exception as ex:
    print(f"sc02sf FAILED: {ex}", flush=True)
print("SINGLE_DONE")
