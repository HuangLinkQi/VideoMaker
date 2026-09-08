#!/usr/bin/env python3
"""批量执行 gen_edit 任务清单。用法: python3 run_batch.py batch_manifest.json"""
import importlib.util
import json
import sys

spec = importlib.util.spec_from_file_location("gen_edit", "/root/ComfyUI/gen_edit.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

jobs = json.load(open(sys.argv[1]))
fails = []
for j in jobs:
    try:
        g = m.build(j["ref"], j["prompt"], int(j["seed"]), 4, bool(j.get("angles", 1)))
        g["90"]["inputs"]["filename_prefix"] = f"batch_{j['id']}_{j['view']}"
        fn = m.wait(m.submit(g))
        print("OK", j["id"], j["view"], fn, flush=True)
    except Exception as e:  # noqa: BLE001 — 单张失败不阻塞整批
        fails.append(f"{j['id']}_{j['view']}")
        print("FAIL", j["id"], j["view"], repr(e)[:200], flush=True)
print("FAILS:", fails if fails else "none")
print("BATCH_DONE")
