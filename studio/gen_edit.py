#!/usr/bin/env python3
"""参考锚定编辑（Qwen-Image-Edit-2511 + Lightning 4步 + multiple-angles LoRA）。

一张基准图 -> 视角编辑：基准经 FluxKontextImageScale 归一后同时喂给
TextEncodeQwenImageEditPlus(image1) 和 VAEEncode(初始 latent，输出尺寸=输入)。
模型链：2511 fp8mixed -> Lightning 4steps LoRA -> (可选) multiple-angles LoRA
       -> ModelSamplingAuraFlow(shift 3.1) -> CFGNorm。
配方来自服务器上验证过的社区工作流（ComfyUI-qwenmultiangle + 用户多角度控制流）。

用法（GPU）: python3 gen_edit.py <ref> <outprefix> <instruction> [seed] [steps] [angles_lora(0/1)]
  angles_lora=1 时加载 multiple-angles LoRA（视角旋转类编辑用）。
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

API = os.environ.get("COMFY_API", "http://127.0.0.1:8188")

UNet = "qwen/qwen_image_edit_2511_fp8mixed.safetensors"
LoRA_Lightning = "qwen/Qwen-Image-Edit-2511-Lightning-4steps-V1.0-fp32.safetensors"
LoRA_Angles = "qwen/qwen-image-edit-2511-multiple-angles-lora.safetensors"
CLIP = "qwen/qwen_2.5_vl_7b_fp8_scaled.safetensors"
VAE = "qwen_image_vae.safetensors"


def build(ref: str, prompt: str, seed: int, steps: int = 4, angles: bool = True) -> dict:
    head = {
        "10": {"class_type": "UNETLoader", "inputs": {"unet_name": UNet, "weight_dtype": "default"}},
        "11": {"class_type": "LoraLoaderModelOnly", "inputs": {
            "model": ["10", 0], "lora_name": LoRA_Lightning, "strength_model": 1.0}},
        "13": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["11", 0], "shift": 3.1}},
        "14": {"class_type": "CFGNorm", "inputs": {"model": ["13", 0], "strength": 1.0}},
        "20": {"class_type": "CLIPLoader", "inputs": {"clip_name": CLIP, "type": "qwen_image", "device": "default"}},
        "21": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
        "30": {"class_type": "LoadImage", "inputs": {"image": ref}},
        "31": {"class_type": "FluxKontextImageScale", "inputs": {"image": ["30", 0]}},
        "40": {"class_type": "VAEEncode", "inputs": {"pixels": ["31", 0], "vae": ["21", 0]}},
        "50": {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {
            "clip": ["20", 0], "prompt": prompt, "vae": ["21", 0], "image1": ["31", 0]}},
        "51": {"class_type": "FluxKontextMultiReferenceLatentMethod", "inputs": {
            "conditioning": ["50", 0], "reference_latents_method": "index_timestep_zero"}},
        "60": {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {
            "clip": ["20", 0], "prompt": "", "vae": ["21", 0], "image1": ["31", 0]}},
        "61": {"class_type": "FluxKontextMultiReferenceLatentMethod", "inputs": {
            "conditioning": ["60", 0], "reference_latents_method": "index_timestep_zero"}},
        "70": {"class_type": "KSampler", "inputs": {
            "model": ["14", 0], "positive": ["51", 0], "negative": ["61", 0], "latent_image": ["40", 0],
            "seed": seed, "steps": steps, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple",
            "denoise": 1.0}},
        "80": {"class_type": "VAEDecode", "inputs": {"samples": ["70", 0], "vae": ["21", 0]}},
        "90": {"class_type": "SaveImage", "inputs": {"images": ["80", 0], "filename_prefix": "EDITOUT"}},
    }
    if angles:
        head["12"] = {"class_type": "LoraLoaderModelOnly", "inputs": {
            "model": ["11", 0], "lora_name": LoRA_Angles, "strength_model": 1.0}}
        head["13"]["inputs"]["model"] = ["12", 0]
    return head


def submit(g: dict) -> str:
    body = json.dumps({"prompt": g, "client_id": "studio-edit"}).encode()
    req = urllib.request.Request(API + "/prompt", data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=60).read())["prompt_id"]


def wait(pid: str) -> str:
    t0 = time.time()
    for _ in range(240):
        time.sleep(3)
        h = json.loads(urllib.request.urlopen(API + "/history/" + pid, timeout=15).read())
        e = h.get(pid, {})
        st = e.get("status", {})
        if st.get("completed"):
            for out in e.get("outputs", {}).values():
                for img in out.get("images", []):
                    print(f"DONE {img['filename']} {time.time() - t0:.1f}s", flush=True)
                    return img["filename"]
        if st.get("status_str") == "error":
            raise RuntimeError(json.dumps(e.get("status"), ensure_ascii=False)[:500])
    raise TimeoutError(pid)


def main() -> None:
    ref, prefix, prompt = sys.argv[1], sys.argv[2], sys.argv[3]
    seed = int(sys.argv[4]) if len(sys.argv) > 4 else 88001
    steps = int(sys.argv[5]) if len(sys.argv) > 5 else 4
    angles = (sys.argv[6] != "0") if len(sys.argv) > 6 else True
    g = build(ref, prompt, seed, steps, angles)
    g["90"]["inputs"]["filename_prefix"] = prefix
    wait(submit(g))


if __name__ == "__main__":
    main()
