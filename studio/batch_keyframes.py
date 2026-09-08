#!/usr/bin/env python3
"""首尾帧批量（state 驱动）：组任务 -> GPU kf_batch.py -> 拉回 -> 入库 review。

用法: python3 studio/batch_keyframes.py --scenes sc01,sc02,sc03 [--seed 51000]
场景映射 SCENE_MAP 按当前剧本维护；prompt 直接取 state.scenes[].start/end.prompt。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PROJECT = ROOT / "projects" / "测试"
TMP = Path("/tmp/kf_out")
GPU_HOST = "b5183f074fd649fdab913836eb4c31e171.gz15.chenyu.cn"
GPU_PORT = 24213
GPU_PW = "pS8VXR4FaUjl"
GPU_DIR = "/root/ComfyUI"

CM = ["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
      "-o", "ControlMaster=auto", "-o", "ControlPath=/tmp/ssh-kf-%r@%h-%p", "-o", "ControlPersist=15m"]
SSH = ["sshpass", "-p", GPU_PW, "ssh", "-p", str(GPU_PORT), *CM, f"root@{GPU_HOST}"]
SCP = ["sshpass", "-p", GPU_PW, "scp", "-P", str(GPU_PORT), *CM]

# 当前剧本：场景 -> (地点资产, [人物], [道具(暂不喂图，仅 prompt 内文字)])
SCENE_MAP = {
    "sc01": ("loc_office_night", ["linchuan"]),
    "sc02": ("loc_home", ["wangguifen"]),
    "sc03": ("loc_office_night", ["linchuan", "zhuguan"]),
    "sc04": ("loc_office_night", ["linchuan", "zhuguan"]),
    "sc05": ("loc_office_night", ["linchuan"]),
    "sc06": ("loc_home", ["wangguifen"]),
    "sc07": ("loc_village_road", ["wangguifen", "cunzhang"]),
    "sc08": ("loc_village_road", ["wangguifen", "cunzhang"]),
    "sc09": ("loc_bus_station", ["wangguifen"]),
    "sc10": ("loc_train", ["wangguifen"]),
    "sc11": ("loc_bus_stop", ["wangguifen", "zhouye"]),
    "sc12": ("loc_bus_stop", ["wangguifen", "zhouye"]),
    "sc13": ("loc_subway_gate", ["wangguifen", "xiaoyu"]),
    "sc14": ("loc_subway_gate", ["wangguifen", "xiaoyu"]),
    "sc15": ("loc_subway_car", ["wangguifen"]),
    "sc16": ("loc_subway_car", ["wangguifen", "linchuan"]),
    "sc17": ("loc_subway_car", ["wangguifen"]),
    "sc18": ("loc_platform", ["wangguifen"]),
    "sc19": ("loc_platform", ["wangguifen"]),
    "sc20": ("loc_platform", ["wangguifen"]),
    "sc21": ("loc_hospital", ["wangguifen", "linchuan"]),
    "sc22": ("loc_hospital", ["linchuan", "wangguifen"]),
    "sc23": ("loc_street_dusk", ["wangguifen_young"]),
    "sc24": ("loc_street_dusk", ["linchuan_child", "wangguifen_young"]),
    "sc25": ("loc_hospital", ["wangguifen", "linchuan"]),
}


def sh(cmd: list, timeout: int = 300, input_bytes: bytes | None = None) -> subprocess.CompletedProcess:
    err = None
    for i in range(3):
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=timeout, input=input_bytes)
        except subprocess.TimeoutExpired:
            err = "timeout"
            continue
        if r.returncode == 0:
            return r
        err = r.stderr.decode()[-400:]
        time.sleep(4 * (i + 1))
    raise RuntimeError(f"FAIL {cmd[:3]}... {err}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenes", required=True)
    ap.add_argument("--seed", type=int, default=51000)
    args = ap.parse_args()
    ids = [s.strip() for s in args.scenes.split(",") if s.strip()]

    st = json.loads((PROJECT / "state.json").read_text(encoding="utf-8"))
    by_id = {s["id"]: s for s in st["scenes"]}

    jobs, uploads = [], []
    seed = args.seed
    for sid in ids:
        sc = by_id[sid]
        loc, chars = SCENE_MAP[sid]
        refs = []
        for c in chars[:2]:
            gpu_name = f"kf_ref_{c}.png"
            uploads.append((PROJECT / "characters" / f"char_{c}_front.png", gpu_name))
            refs.append(gpu_name)
        gpu_loc = f"kf_ref_{loc}.png"
        uploads.append((PROJECT / "scenes" / f"{loc}_wide.png", gpu_loc))
        refs.append(gpu_loc)
        jobs.append({"sid": sid, "frame": "start", "prompt": sc["start"]["prompt"],
                     "refs": refs, "seed": seed})
        jobs.append({"sid": sid, "frame": "end", "prompt": sc["end"]["prompt"],
                     "refs": refs, "seed": seed + 1, "i2i_of": sid})
        seed += 2

    seen = set()
    for local, gpu_name in uploads:
        if str(local) in seen:
            continue
        seen.add(str(local))
        sh(SCP + [str(local), f"root@{GPU_HOST}:{GPU_DIR}/input/{gpu_name}"])
        print("UPLOADED", gpu_name, flush=True)

    mf = json.dumps(jobs, ensure_ascii=False).encode()
    sh(SSH + [f"cat > {GPU_DIR}/kf_jobs.json"], input_bytes=mf)
    sh(SCP + [str(HERE / "kf_batch.py"), f"root@{GPU_HOST}:{GPU_DIR}/kf_batch.py"])

    out = sh(SSH + [f"cd {GPU_DIR} && python3 kf_batch.py < kf_jobs.json"], timeout=1500).stdout.decode()
    print(out[-2500:])
    ok_files = {ln.split()[1]: ln.split()[4] for ln in out.splitlines()
                if ln.startswith("OK ") and len(ln.split()) >= 5}
    all_files = " ".join(ok_files.values())
    if all_files:
        sh(SSH + [f"cd {GPU_DIR}/output && tar czf /tmp/kf.tgz {all_files}"])
        sh(SCP + [f"root@{GPU_HOST}:/tmp/kf.tgz", "/tmp/kf.tgz"])
    sh(["rm", "-rf", str(TMP)])
    sh(["bash", "-c", f"mkdir -p {TMP} && tar xzf /tmp/kf.tgz -C {TMP}/"])

    # 入库：keyframes/<file> + state 置 review（保留 prompt 字段，追加 versions 历史）
    ts = time.strftime("%Y%m%d-%H%M%S")
    kf_dir = PROJECT / "keyframes"
    kf_dir.mkdir(exist_ok=True)
    (kf_dir / "versions").mkdir(exist_ok=True)
    updated = []
    for sid in ids:
        sc = by_id[sid]
        for frame in ("start", "end"):
            if f"{sid}_{frame}" not in ok_files:
                print("MISSING", sid, frame)
                continue
            src = next(TMP.glob(f"kf_{sid}_{frame}_*.png"))
            dst = kf_dir / f"kf_{sid}_{frame}.png"
            if dst.is_file():
                (kf_dir / "versions" / f"kf_{sid}_{frame}__{ts}.png").write_bytes(dst.read_bytes())
            dst.write_bytes(src.read_bytes())
            node = sc.setdefault(frame, {})
            old = node.get("image")
            if old:
                node.setdefault("versions", [])
                node["versions"] = node.get("versions", []) + [{"file": old, "ts": ts}] if old != str(dst.relative_to(PROJECT)) else node["versions"]
            node["status"] = "review"
            node["image"] = str(dst.relative_to(PROJECT))
        updated.append(sid)
    st_path = PROJECT / "state.json"
    st_path.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    print("UPDATED:", updated)


if __name__ == "__main__":
    main()
