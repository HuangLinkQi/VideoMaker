#!/usr/bin/env python3
"""资产批量重生成驱动（参考锚定编辑管线，Qwen-Image-Edit-2511）。

子命令:
  gen       上传锚点 -> GPU 批量视角编辑 -> tar 拉回 /tmp/batch_out
  finalize  本地拼贴(clean+展示版) -> 归档旧图 -> 入库 -> 更新 state -> 追加参数记录

人物五格：特写正 + 特写侧 + [尺] + 全身正 + 全身侧 + 全身背（尺 before=2）
场景三格：[尺] + 定场锚点图 + 中景 + 细节（尺 before=0）
道具三格：[尺] + 正面锚点图 + 侧 + 三四分之一（尺 before=0）

用法:
  python3 studio/batch_assets.py --project projects/测试 gen  [--only id,id]
  python3 studio/batch_assets.py --project projects/测试 finalize
linchuan 已完成自动跳过；prop_bus_sign(removed) 跳过。
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = Path("projects/测试")
TMP_OUT = Path("/tmp/batch_out")
GPU_HOST = "b5183f074fd649fdab913836eb4c31e171.gz15.chenyu.cn"
GPU_PORT = 24213
GPU_PW = "pS8VXR4FaUjl"
GPU_DIR = "/root/ComfyUI"
SKIP = {"prop_bus_sign"}

CM_OPTS = ["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
           "-o", "ControlMaster=auto", "-o", "ControlPath=/tmp/ssh-gpu-%r@%h-%p",
           "-o", "ControlPersist=15m"]
SSH_BASE = ["sshpass", "-p", GPU_PW, "ssh", "-p", str(GPU_PORT), *CM_OPTS, f"root@{GPU_HOST}"]
SCP_BASE = ["sshpass", "-p", GPU_PW, "scp", "-P", str(GPU_PORT), *CM_OPTS]


def sh(cmd: list, timeout: int = 300, input_bytes: bytes | None = None,
       retries: int = 3) -> subprocess.CompletedProcess:
    # 远端 sshpass 偶发 Permission denied（交接踩坑#10），隔几秒重试
    err = None
    for i in range(retries):
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=timeout, input=input_bytes)
        except subprocess.TimeoutExpired:
            err = "timeout"
            continue
        if r.returncode == 0:
            return r
        err = r.stderr.decode()[-500:]
        transient = ("Permission denied" in err or "Connection reset" in err
                     or "kex_exchange_identification" in err)
        if not transient:
            break
        time.sleep(4 * (i + 1))
    raise RuntimeError(f"FAIL {cmd[:3]}...\n{err}")


def gpu(cmd: str, timeout: int = 300) -> str:
    r = sh(SSH_BASE + [cmd], timeout=timeout)
    return r.stdout.decode()


def gpu_send(local: Path, remote: str) -> None:
    sh(SCP_BASE + [str(local), f"root@{GPU_HOST}:{remote}"])


def gpu_fetch(remote: str, local: Path) -> None:
    sh(SCP_BASE + [f"root@{GPU_HOST}:{remote}", str(local)])


# ---------- 规划 ----------

def char_prompts(ident: str, extra: str = "") -> dict:
    """extra: 追加的状态描述（如林川的疲惫感），注入每个视角。
    特写必须带 ONLY head and shoulders 硬约束——否则全身锚会叠出弯腰小人。"""
    who = f"the exact same {ident}"
    Who = who[0].upper() + who[1:]
    e = extra
    head_only = (" ONLY the head and shoulders filling the entire frame, absolutely no body, no torso, "
                 "no shoes, no full figure,")
    return {
        "face_front": (
            f"Close-up headshot portrait of {who},{head_only} facing the camera, "
            f"identical face, identical hairstyle, neutral expression{e}, plain light gray seamless studio "
            "background, even soft studio lighting, sharp focus, photorealistic, no text"),
        "face_side": (
            f"Close-up headshot portrait of {who},{head_only} left side profile "
            f"view, identical face, identical hairstyle, neutral expression{e}, plain light gray seamless "
            "studio background, even soft studio lighting, sharp focus, photorealistic, no text"),
        "full_front": (
            f"{Who} facing the camera directly, front view, full body head to toe, standing, arms relaxed"
            f"{e}, replace the background with a plain light gray seamless studio backdrop, even soft "
            "studio lighting, sharp focus, photorealistic, no text"),
        "full_side": (
            f"From another angle: {who} rotated 90 degrees to show the left side profile, full body head "
            f"to toe, standing, arms relaxed, identical face, identical hairstyle and clothing{e}, plain "
            "light gray seamless studio background, even soft studio lighting, photorealistic, no text"),
        "full_back": (
            f"From another angle: {who} seen from behind, back view 180 degrees, full body head to toe, "
            f"standing, arms relaxed, identical hairstyle from the back, identical clothing{e}, plain "
            "light gray seamless studio background, even soft studio lighting, photorealistic, no text"),
    }


CHAR_ANGLES = {"face_front": 0, "face_side": 1, "full_front": 0, "full_side": 1, "full_back": 1}
CHAR_PANELS = ["face_front", "face_side", "full_front", "full_side", "full_back"]

IDENT = {
    "linchuan":
        "30-year-old Chinese male programmer, identical thin black rectangular eyeglasses, identical "
        "short neat black hair, identical light blue dress shirt with sleeves rolled up, dark trousers, "
        "white sneakers",
    "wangguifen":
        "58-year-old Chinese rural woman, identical short gray-streaked black hair in a low bun, "
        "identical weathered face, identical faded dark cotton-padded jacket over a worn floral blouse, "
        "loose dark trousers, cloth shoes",
    "zhouye":
        "20-year-old Chinese male college student, identical short clean black hair, identical navy "
        "windbreaker over a white t-shirt, jeans, white sneakers",
    "xiaoyu":
        "12-year-old Chinese schoolgirl with child proportions, identical shoulder-length black hair with "
        "bangs and a small pink hair clip, identical white school shirt, navy necktie, navy pleated skirt, "
        "white socks, white sneakers",
    "wangguifen_young":
        "30-year-old Chinese rural woman, identical long black hair in two braids, identical plain faded "
        "old cotton coat, dark trousers, cloth shoes",
    "linchuan_child":
        "5-year-old Chinese little boy with round face, identical short watermelon-cut black hair, "
        "identical faded oversized patched blue cotton jacket, dark pants, cloth shoes",
    "zhuguan":
        "45-year-old Chinese male tech-company manager, identical short neat black hair, identical plaid "
        "checkered shirt with a white work ID badge on a blue lanyard, identical dark chino trousers, "
        "black leather shoes",
    "cunzhang":
        "45-year-old Chinese rural village head, identical hearty warm smile, identical weathered tan "
        "face, identical gray-flecked short hair, identical dark gray zip-up jacket over a white shirt, "
        "dark cargo trousers, black cloth shoes",
}

# 用户反馈（2026-09-06 晚）：疲惫感收敛——只求眼神无神、略微疲惫，不凸显黑眼圈
TIRED = " He looks slightly tired after long work hours, with a calm weary gaze and dull listless eyes."

SCENE_FOCUS = {
    "loc_office_night": ("open-plan office with rows of desks and dark monitors", True),
    "loc_home": ("rural home interior with wooden square table and stools", True),
    "loc_train": ("old green-skinned train hard-seat carriage", True),
    "loc_bus_stop": ("city street bus stop with plane trees", False),
    "loc_subway_gate": ("subway concourse with silver turnstile gates", True),
    "loc_subway_car": ("subway car interior with molded seats and poles", True),
    "loc_platform": ("subway platform with screen doors", True),
    "loc_hospital": ("hospital single room with an adjustable bed", True),
    "loc_street_dusk": ("rural dirt street with low brick houses", False),
    "loc_village_road": ("village dirt road entrance with brick houses, dark tiled roofs and stacked firewood", False),
    "loc_bus_station": ("small-town bus station with a simple waiting shed, ticket window and a parked white minibus", False),
}
SCENE_TAIL = (", identical architecture materials and lighting, no people, photorealistic cinematic, "
              "all signs posters and screens completely blank, no readable text, no text")


def scene_prompts(aid: str) -> dict:
    """四视角：平视 -> 反打 -> 左侧 -> 右侧（沿用 gen_asset_views.py 的场景规范）。
    平视也是编辑生成（锚点只做参考不直接上版面），修旧图的曝光/乱码瑕疵。"""
    desc, interior = SCENE_FOCUS[aid]
    place = "room" if interior else "location"
    return {
        "eye": (
            f"The exact same {place} — {desc} — wide establishing view at eye level showing the whole "
            f"{place}, balanced natural exposure, no haze, no blowout{SCENE_TAIL}"),
        "reverse": (
            f"From another angle: the exact same {place} — {desc} — seen from the opposite direction, "
            f"the camera turned around 180 degrees to show the opposite side of the same {place}{SCENE_TAIL}"),
        "left": (
            f"From another angle: the exact same {place} — {desc} — with the camera moved to the left side "
            f"of the {place}, now showing the left side wall and its surroundings{SCENE_TAIL}"),
        "right": (
            f"From another angle: the exact same {place} — {desc} — with the camera moved to the right side "
            f"of the {place}, now showing the right side wall and its surroundings{SCENE_TAIL}"),
    }


PROP_TAIL = (", centered on a plain light gray seamless background, even studio lighting, photorealistic "
             "product photo, no extra copies of the object, no text")


PROP_IDENT = {
    "prop_phone_lin": "one modern black smartphone with slim bezel-less design, dark glass back",
    "prop_phone_wang":
        "one old worn smartphone whose screen cracked from being dropped: thin irregular crack lines "
        "scattered across the front glass, subtle and realistic everyday drop damage, not shattered to "
        "pieces, screen off, scuffed dark plastic body",
    "prop_basket":
        "a blue cloth bundle (furoshiki wrap) tied in a knot on top, fully closed and wrapped, plain "
        "blue cloth surface with soft folds, nothing spilling out, contents hidden inside",
    "prop_mooncake":
        "one traditional Chinese mooncake wrapped in plain crinkled brown oil paper tied with paper "
        "string, plain and rustic, no readable text",
    "prop_note":
        "one folded cream-colored handwritten paper note with visible crease folds, closed fold, "
        "no readable text, no visible writing",
}

# 每个道具的视角集（默认 正/侧/3/4；纸条只要主视图；碎屏手机要真背视图）
PROP_VIEWS = {
    "prop_note": ["front"],
    "prop_phone_wang": ["front", "side", "back"],
}


def prop_prompts(aid: str) -> dict:
    """道具面板全部生成。锚点 = 单一素材图（用户反馈：细节以素材为准，prompt 只写视角变化）。"""
    p = {
        "front": ("The exact same object from the reference image, facing the camera directly, "
                  "straight front view" + PROP_TAIL),
        "side": ("The exact same object from the reference image, rotated 90 degrees around its vertical "
                 "axis to show its side view, keep the knot shape, strap color, material, folds and "
                 "proportions exactly identical, only the viewing angle changes" + PROP_TAIL),
        "three_quarter": ("The exact same object from the reference image, rotated to a three-quarter view, "
                          "keep every detail exactly identical, only the viewing angle changes" + PROP_TAIL),
        "back": ("The exact same object from the reference image, turned around to show its back panel, "
                 "keep every detail exactly identical, only the viewing angle changes" + PROP_TAIL),
    }
    if aid == "prop_note":
        # 用户要求：加可读文字（内容不必正确）。Qwen-Image 原生支持中文渲染，
        # 此处对该道具打破 no-CJK 红线（该红线本为 Krea-2 而设）
        note_tail = (", centered on a plain light gray seamless background, even studio lighting, "
                     "photorealistic product photo, no extra copies of the object")
        p["front"] = ("The exact same cream-colored folded paper note from the reference image, lying flat, "
                      "front view. On the paper there is now a handwritten Chinese message in blue ballpoint "
                      "pen, casually scrawled but clearly readable: 坐大巴转火车，再转公交车15路 "
                      "Keep the paper color, creases and folds exactly identical" + note_tail)
    return {v: p[v] for v in PROP_VIEWS.get(aid, ["front", "side", "three_quarter"])}


# 无锚新资产的 bootstrap 基图（Krea-2 txt2img，studio/gen_base.py）
BOOTSTRAP = {
    "zhuguan": {"orient": "portrait", "prompt":
        "Exactly one person in the frame, no duplicates. Full body head-to-toe studio photo of a "
        "45-year-old Chinese male tech-company manager, short neat black hair, rectangular face with a "
        "tired serious expression, plaid checkered button-up shirt with a white work ID badge on a blue "
        "lanyard, dark chino trousers, black leather shoes, standing facing the camera, arms relaxed, "
        "plain light gray seamless studio background, even soft studio lighting, photorealistic, no text"},
    "cunzhang": {"orient": "portrait", "prompt":
        "Exactly one person in the frame, no duplicates. Full body head-to-toe studio photo of a "
        "45-year-old Chinese rural village head with a hearty warm smile, weathered tan face, short "
        "gray-flecked hair, open dark gray zip-up jacket over a white shirt, dark cargo trousers, black "
        "cloth shoes, standing facing the camera, arms relaxed, plain light gray seamless studio "
        "background, even soft studio lighting, photorealistic, no text"},
    "prop_basket": {"orient": "square", "prompt": "Studio product photo of " + PROP_IDENT["prop_basket"] + PROP_TAIL},
    "prop_mooncake": {"orient": "square", "prompt": "Studio product photo of " + PROP_IDENT["prop_mooncake"] + PROP_TAIL},
    "prop_note": {"orient": "square", "prompt": "Studio product photo of " + PROP_IDENT["prop_note"] + PROP_TAIL},
    "loc_village_road": {"orient": "landscape", "prompt":
        "An empty Chinese village dirt road entrance at dawn: compacted earth road leading between low "
        "brick houses with dark tiled roofs, firewood stacks against the walls, thin morning mist, soft "
        "golden dawn light, eye level, photorealistic cinematic, no people, no text"},
    "loc_bus_station": {"orient": "landscape", "prompt":
        "An empty small-town Chinese bus station: a simple corrugated waiting shed with benches, a small "
        "ticket window booth, a white minibus parked at the side, concrete ground, soft morning light, "
        "eye level, photorealistic cinematic, no people, no text"},
}


def anchor_of(a: dict) -> Path:
    aid, t = a["id"], a["type"]
    if t == "character":
        return PROJECT / "characters" / f"char_{aid}_front.png"
    if t == "scene":
        return PROJECT / "scenes" / f"{aid}_wide.png"   # 单视角裁剪，避免继承三格分栏
    return PROJECT / "props" / f"{aid}_front.png"


def build_plans(assets: list, only: set) -> tuple[list, list]:
    plans, jobs = [], []
    seed = 88110
    for a in assets:
        aid, t = a["id"], a["type"]
        if aid in SKIP or (only and aid not in only):
            continue
        if t == "character":
            base = char_prompts(IDENT[aid], extra=TIRED if aid == "linchuan" else "")
        elif t == "scene":
            base = scene_prompts(aid)
        else:
            base = prop_prompts(aid)
        default_img = {"character": f"characters/{aid}.png", "scene": f"scenes/{aid}.png",
                       "prop": f"props/{aid}.png"}[t]
        display = a.get("image") or default_img
        views = list(base.keys())
        for v in views:
            angles = CHAR_ANGLES.get(v, 1 if t == "scene" else 0)
            jobs.append({"id": aid, "view": v, "ref": f"anchor_{aid}.png",
                         "prompt": base[v], "seed": seed, "angles": angles})
            seed += 1
        plans.append({
            "id": aid, "type": t, "name": a["name"], "height": a["height_cm"],
            "views": views, "ruler_before": 2 if t == "character" else 0,
            "display": display, "clean": display.replace(".png", "_clean.png"),
            "anchor": str(anchor_of(a)),
            "front_out": f"characters/char_{aid}_front.png" if t == "character" else None,
            "face_out": f"characters/char_{aid}_face.png" if t == "character" else None,
        })
    return plans, jobs


# ---------- 子命令 ----------

def cmd_gen(only: set) -> None:
    st = json.loads((PROJECT / "state.json").read_text(encoding="utf-8"))
    plans, jobs = build_plans(st["assets"], only)
    print(f"PLANS={len(plans)} JOBS={len(jobs)}")
    if not jobs:
        return
    for p in plans:
        anchor = Path(p["anchor"])
        if not anchor.is_file() and p["id"] in BOOTSTRAP:  # 新资产：先生成基图作锚
            b = BOOTSTRAP[p["id"]]
            gpu(f"cd {GPU_DIR} && python3 gen_base.py base_{p['id']} {b['orient']} "
                f"\"{b['prompt']}\" 91{abs(hash(p['id'])) % 900:03d}", timeout=600)
            gpu_fetch(f"{GPU_DIR}/output/base_{p['id']}_00001_.png", anchor)
            print("BOOTSTRAPPED", p["id"], flush=True)
        gpu_send(anchor, f"{GPU_DIR}/input/anchor_{p['id']}.png")
        print("UPLOADED", p["id"], flush=True)

    mf = PROJECT / ".batch_manifest.json"
    mf.write_text(json.dumps(jobs, ensure_ascii=False), encoding="utf-8")
    gpu_send(mf, f"{GPU_DIR}/batch_manifest.json")

    out = gpu(f"cd {GPU_DIR} && python3 run_batch.py batch_manifest.json", timeout=2400)
    print(out[-4000:])
    ok = {ln.split()[1] + "_" + ln.split()[2] for ln in out.splitlines() if ln.startswith("OK ")}
    missing = [j["id"] + "_" + j["view"] for j in jobs if f"{j['id']}_{j['view']}" not in ok]
    if missing:
        print("MISSING:", missing)
        raise SystemExit(1)

    # 打包：按 manifest 逐任务挑最新输出（ComfyUI 重跑会自增序号），规范命名后拉回
    out2 = gpu(f"python3 {GPU_DIR}/pack_batch.py && tar czf /tmp/batch_out.tgz -C /tmp/gb .", timeout=300)
    print(out2.strip().splitlines()[-1])
    gpu_fetch("/tmp/batch_out.tgz", Path("/tmp/batch_out.tgz"))
    shutil.rmtree(TMP_OUT, ignore_errors=True)
    TMP_OUT.mkdir(parents=True)
    sh(["tar", "xzf", "/tmp/batch_out.tgz", "-C", str(TMP_OUT)])
    got = sorted(f.name for f in TMP_OUT.glob("batch_*.png"))
    print(f"DOWNLOADED {len(got)} -> {TMP_OUT}")


def cmd_finalize(dry: bool, only: set) -> None:
    from PIL import Image  # noqa: F401  (sheet_collage 依赖)
    st_path = PROJECT / "state.json"
    st = json.loads(st_path.read_text(encoding="utf-8"))
    plans, _ = build_plans(st["assets"], only)
    ts = time.strftime("%Y%m%d-%H%M%S")
    updated = []

    for p in plans:
        aid, t = p["id"], p["type"]
        panel_files = []
        for v in p["views"]:
            f = TMP_OUT / f"batch_{aid}_{v}.png"
            if not f.is_file():
                print(f"SKIP {aid}: missing panel {v}")
                break
            panel_files.append(str(f))
        if len(panel_files) != len(p["views"]):
            continue

        disp = PROJECT / p["display"]
        clean = PROJECT / p["clean"]
        # 人物/场景/道具的全部面板都来自生成（锚点只做参考）；拼贴是纯贴图
        collage_panels = list(panel_files)
        common = ["--height-cm", str(p["height"]), "--label", p["name"],
                  "--ruler-before", str(p["ruler_before"])]
        if not dry:
            disp.parent.mkdir(exist_ok=True)
            vdir = disp.parent / "versions"
            vdir.mkdir(exist_ok=True)
            for target in (disp, clean):
                if target.is_file():
                    shutil.move(target, vdir / f"{target.stem}__{ts}{target.suffix}")
            subprocess.run(["python3", str(HERE / "sheet_collage.py"), "--out", str(clean),
                            "--no-ruler", *common, *collage_panels], check=True, capture_output=True)
            subprocess.run(["python3", str(HERE / "sheet_collage.py"), "--out", str(disp),
                            *common, *collage_panels], check=True, capture_output=True)
            if p["front_out"]:
                shutil.copy(panel_files[p["views"].index("full_front")], PROJECT / p["front_out"])
            if p["face_out"]:
                shutil.copy(panel_files[p["views"].index("face_front")], PROJECT / p["face_out"])

        for a in st["assets"]:
            if a["id"] == aid:
                a["status"] = "review"
                a["image"] = p["display"]
                a["note"] = "v2 参考锚定编辑批量版（Qwen-Image-Edit-2511 锚定旧正面crop/定场clean图）"
                if t == "character":
                    a["face"] = p["face_out"]
        updated.append(aid)
        print("FINALIZED" if not dry else "DRY", aid, disp)

    if not dry:
        st_path.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
        with open(PROJECT / "生成参数记录.md", "a", encoding="utf-8") as f:
            f.write(f"\n## 2026-09-06 批量改版（参考锚定编辑）\n"
                    f"- {len(updated)} 资产重出：{', '.join(updated)}；seed 88110 起连续分配，"
                    f"清单 projects/测试/.batch_manifest.json；脚本 studio/batch_assets.py + GPU run_batch.py\n"
                    f"- 人物五格 / 场景三格（锚=loc_clean）/ 道具三格（锚=prop_clean）；旧图均归档 __{ts}\n")
    print("UPDATED:", len(updated), updated)


def main() -> None:
    global PROJECT
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="projects/测试")
    ap.add_argument("cmd", choices=["gen", "finalize"])
    ap.add_argument("--only", default="")
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    PROJECT = Path(args.project)
    if args.cmd == "gen":
        cmd_gen({s for s in args.only.split(",") if s})
    else:
        cmd_finalize(args.dry, {s for s in args.only.split(",") if s})


if __name__ == "__main__":
    main()
