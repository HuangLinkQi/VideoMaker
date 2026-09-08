#!/usr/bin/env python3
"""按 batch_manifest.json 打包每个任务的最新输出为规范名。用法: python3 pack_batch.py"""
import glob
import json
import os
import shutil

GPU_OUT = "/root/ComfyUI/output"
jobs = json.load(open("/root/ComfyUI/batch_manifest.json"))
shutil.rmtree("/tmp/gb", ignore_errors=True)
os.makedirs("/tmp/gb")
n = 0
for j in jobs:
    fs = sorted(glob.glob(f"{GPU_OUT}/batch_{j['id']}_{j['view']}_*.png"), key=os.path.getmtime)
    if not fs:
        print("MISS", j["id"], j["view"])
        continue
    shutil.copy(fs[-1], f"/tmp/gb/batch_{j['id']}_{j['view']}.png")
    n += 1
print("PACKED", n)
