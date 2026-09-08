#!/usr/bin/env python3
"""《别晃》首尾帧批量生成 - Krea-2 Turbo + TextEncodeKrea2Ref 人物参考

用法: python3 gen_keyframes_studio.py 1      # 第 1 批 = sc01-sc05
      python3 gen_keyframes_studio.py 2      # 第 2 批 = sc06-sc10 ...
"""
import json
import sys
import time
import urllib.request

API = "http://127.0.0.1:8188"

CHARS = {  # id -> (参考图=正面单视图裁剪, 文字描述)
 "linchuan": ("char_linchuan_front.png",
   "Lin Chuan, a 30-year-old Chinese male programmer, 175cm tall, slim, short neat black hair, "
   "thin-rim rectangular glasses, light blue dress shirt with rolled-up sleeves, dark trousers"),
 "wangguifen": ("char_wangguifen_front.png",
   "Wang Guifen, a 58-year-old Chinese rural woman, 158cm tall, slim, short gray-streaked black hair "
   "in a low bun, faded dark cotton-padded jacket over a worn floral blouse, "
   "loose dark trousers, cloth shoes, carrying a woven shopping tote bag with folded clothes and snacks"),
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

# 场景/道具参考图（无标尺干净版，ref_ 前缀）
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
 "prop_phone_lin": ("ref_prop_phone_lin.png", "a modern black smartphone, slim bezel-less design, dark glass back"),
 "prop_phone_wang": ("ref_prop_phone_wang.png", "an old worn smartphone with a visibly cracked screen"),
 "prop_basket": ("ref_prop_basket.png", "a 45cm woven tote bag filled with folded clothes and packaged snack bags"),
 "prop_bus_sign": ("ref_prop_bus_sign.png", "a tall metal bus stop sign pole with a printed paper notice taped on it"),
}

# 每场引用的场景/道具资产
SCENE_ASSETS = {
 "sc01": ("loc_office_night", ["prop_phone_lin"]),
 "sc02": ("loc_office_night", ["prop_phone_lin"]),
 "sc03": ("loc_home", ["prop_phone_wang"]),
 "sc04": ("loc_office_night", ["prop_phone_lin"]),
 "sc05": ("loc_home", ["prop_basket"]),
 "sc06": ("loc_train", []),
 "sc07": ("loc_train", []),
 "sc08": ("loc_bus_stop", ["prop_bus_sign"]),
 "sc09": ("loc_bus_stop", []),
 "sc10": ("loc_subway_gate", ["prop_phone_wang"]),
 "sc11": ("loc_subway_gate", ["prop_phone_wang"]),
 "sc12": ("loc_subway_car", ["prop_phone_wang"]),
 "sc13": ("loc_subway_car", []),
 "sc14": ("loc_subway_car", []),
 "sc15": ("loc_platform", ["prop_phone_wang"]),
 "sc16": ("loc_platform", ["prop_phone_wang", "prop_basket"]),
 "sc17": ("loc_platform", []),
 "sc18": ("loc_hospital", ["prop_phone_wang"]),
 "sc19": ("loc_hospital", ["prop_phone_wang"]),
 "sc20": ("loc_street_dusk", []),
 "sc21": ("loc_street_dusk", []),
 "sc22": ("loc_hospital", []),
}

STYLE = (". Photorealistic cinematic film still, natural lighting, shallow depth of field, "
         "35mm lens, muted realistic color grading, no text, no watermark, no readable characters")

SCENES = {
 "sc01": {"chars": ["linchuan"],
   "start": "Late night, empty open-plan office. {linchuan} sits alone at a desk, face lit only by the pale blue glow of a computer monitor, dark silhouettes of empty desks around, city lights through the window. His black smartphone lies on the desk vibrating, screen glowing with an incoming call. He reaches toward the phone, weary expression, dark circles under his eyes. Low-key lighting, cool blue tones, wide shot",
   "end": "Same late-night office. {linchuan} holds the black smartphone to his ear answering the call, elbow propped on the desk, tired eyes half closed, monitor glow on one side of his face. Dark office background, medium close-up, cool blue tones"},
 "sc02": {"chars": ["linchuan"],
   "start": "Same late-night office. {linchuan} sits at his desk holding the phone to his LEFT ear with his LEFT hand, camera on his left side, glowing monitor on the right of the frame, impatient weary expression, cool blue light",
   "end": "Same office, same camera on his left side, monitor on the right of the frame. {linchuan} has just lowered the phone from his LEFT ear, the phone now lies face-down on the desk by his LEFT hand, he rubs his eyes with his right hand, alone in the dark office. Medium shot, cool tones"},
 "sc03": {"chars": ["wangguifen"],
   "start": "Night, modest rural Chinese home interior: a wooden square table with a warm tungsten desk lamp on the RIGHT of the frame, worn concrete wall behind. {wangguifen} sits on a low wooden stool beside the table, empty-handed except an old cracked-screen smartphone held to her LEFT ear with her LEFT hand, the call just ended, gentle disappointed expression. Fixed camera from the doorway side, eye-level medium shot",
   "end": "Same room, same fixed camera from the doorway side, same eye-level medium shot, table and lamp still on the RIGHT of the frame. {wangguifen} still sits on the same low wooden stool, the phone now lowered onto her lap, she looks down at a small folded paper note held in both hands, thumb brushing it softly, bittersweet expression, warm lamplight"},
 "sc04": {"chars": ["linchuan"],
   "start": "Late-night office. {linchuan} holds his smartphone and shakes it lightly; a flashy colorful shopping-ad popup with abstract shapes fills the phone screen, no readable text. His face lit by the screen, ironic tired smirk",
   "end": "Late-night office. {linchuan} taps the corner of the phone screen to dismiss the popup, muttering to himself, screen glow reflected on his glasses, dark office behind, over-the-shoulder close-up"},
 "sc05": {"chars": ["wangguifen"],
   "start": "Early morning, rural Chinese kitchen, soft cool daylight through a small window. {wangguifen} folds clothes and packs them into a woven tote bag on the wooden table, a few packaged snack bags on top, a paper note tucked between the clothes",
   "end": "Same kitchen. {wangguifen} lifts the woven tote bag onto her arm, holding her cracked-screen old phone, pulling the wooden door closed behind her, morning light at the doorway"},
 "sc06": {"chars": ["wangguifen"],
   "start": "Morning countryside. A long-distance bus driving along a rural road between fields, seen from the roadside; through the bus window {wangguifen} sits holding her woven tote bag",
   "end": "An old green-skinned train crossing farmland in golden morning light; through a window {wangguifen} looks out at the fields, clutching the woven tote bag on her lap"},
 "sc07": {"chars": ["wangguifen"],
   "start": "Inside a green train carriage. {wangguifen} sits by the window holding her woven tote bag, lips moving silently as she rehearses words, nervous gentle expression, fields flashing past outside",
   "end": "Close-up of {wangguifen} by the train window, a soft shy smile forming as she mouths the words, warm light on her weathered face"},
 "sc08": {"chars": ["wangguifen", "zhouye"],
   "start": "Daytime city street, a bus stop sign with a paper notice taped on it. {wangguifen} stands under the sign holding her woven tote bag, anxious and lost, looking up and down the empty road",
   "end": "At the same bus stop. {zhouye} steps up beside {wangguifen}, greeting her with a warm smile; she turns to him with a hesitant hopeful look, city street background"},
 "sc09": {"chars": ["wangguifen", "zhouye"],
   "start": "At the bus stop. {zhouye} holds his smartphone showing the screen to {wangguifen}, tapping to install a map app; she leans in watching carefully, woven tote bag on her arm",
   "end": "At the bus stop. {zhouye} points at a blue route line on his phone screen explaining directions, {wangguifen} nods slowly half understanding, over-the-shoulder close shot"},
 "sc10": {"chars": ["wangguifen", "xiaoyu"],
   "start": "Subway station entrance gates. {wangguifen} stands hesitantly before a turnstile holding her woven tote bag, commuters streaming past, staring at her old cracked phone in confusion",
   "end": "At the turnstile. {xiaoyu} stands on tiptoe beside {wangguifen}, helping hold the phone over the scanner; the gate opens, both relieved, bright subway station lighting"},
 "sc11": {"chars": ["wangguifen", "xiaoyu"],
   "start": "Beside the subway gates. {xiaoyu} holds the cracked phone and points at the screen seriously, explaining something important; {wangguifen} listens with full attention",
   "end": "In the subway station. {xiaoyu} waves goodbye walking away with her little backpack; {wangguifen} nods slowly half understanding, clutching phone and tote bag, watching the girl leave"},
 "sc12": {"chars": ["wangguifen"],
   "start": "Inside a moving subway car. {wangguifen} sits holding her cracked phone; a flashy colorful ad popup with abstract shapes covers the screen, no readable text. Harsh fluorescent carriage light",
   "end": "Close-up of the cracked phone in her trembling hands: layered chaotic popups, shopping pages and download bars overlapping, colorful glare reflected on the anxious face of {wangguifen}"},
 "sc13": {"chars": ["wangguifen", "linchuan"],
   "start": "Inside the subway car, colorful ad light from a phone flickers on the tired face of {wangguifen}, motion blur of the dark tunnel outside the window",
   "end": "A bright cold meeting room. {linchuan} sits at a conference table; his phone vibrates with an incoming call, he glances at it and presses it silent, blank face, cold office light"},
 "sc14": {"chars": ["wangguifen"],
   "start": "Dark tunnel outside a subway window, colorful billboards streaking past; the face of {wangguifen} reflected in the window glass overlapping the passing ad lights",
   "end": "Extreme close-up of the eyes of {wangguifen} reflected in the subway window, ad lights and tunnel streaks layered over her gaze, melancholic, cinematic"},
 "sc15": {"chars": ["wangguifen"],
   "start": "Subway platform, doors open. {wangguifen} steps off unsteadily with her woven tote bag, crowd flowing around her, she stumbles slightly",
   "end": "On the platform. {wangguifen} stares at her phone where a big colorful ad popup covers the map route, anxiously tapping the screen, platform signs blurred behind"},
 "sc16": {"chars": ["wangguifen"],
   "start": "A cracked smartphone slips from the hand of {wangguifen} in mid-air, her woven tote bag tipping over, folded clothes and snack packets spilling out, frozen motion, platform floor below",
   "end": "Top-down on the platform floor: the cracked phone face-up still glowing with a colorful ad, folded clothes and colorful snack packets scattered, an overturned woven tote bag beside them"},
 "sc17": {"chars": ["wangguifen"],
   "start": "{wangguifen} kneels on the platform reaching for her phone among scattered clothes and snack packets, face pale, one hand pressed to her forehead, vision blurring",
   "end": "{wangguifen} collapsed on the platform floor; a subway staff member in uniform crouches beside her picking up the cracked phone to make a call, scattered groceries around"},
 "sc18": {"chars": ["wangguifen", "linchuan"],
   "start": "Hospital room, soft morning light. {wangguifen} slowly opens her eyes in a hospital bed, an IV stand beside her; {linchuan} sits by the bed red-eyed, holding her hand",
   "end": "Same hospital room. {wangguifen} speaks weakly with an apologetic gentle smile; {linchuan} looks down silent, jaw tight; the cracked old phone glows on the bedside table between them"},
 "sc19": {"chars": ["linchuan", "wangguifen"],
   "start": "Close-up of the cracked phone on the bedside table: an endless loop of colorful ad popups jumping between shopping and video apps, no readable text; the hand of {linchuan} reaches for it",
   "end": "Hospital room. {linchuan} grips the phone with both hands, knuckles white, head bowed, trembling, on the verge of breaking down; {wangguifen} in the bed blurred behind him"},
 "sc20": {"chars": ["wangguifen_young"],
   "start": "Memory, dusk, a 1990s Chinese street market in warm golden light. {wangguifen_young} runs through the crowd, long braid flying, shouting, panic on her face",
   "end": "Same memory dusk. {wangguifen_young} runs past market stalls and a bus stop, breathless, asking passersby, tears in her eyes, motion blur, warm golden light"},
 "sc21": {"chars": ["linchuan_child", "wangguifen_young"],
   "start": "Memory, dusk street corner. {linchuan_child} stands crying alone beside a wall, tiny schoolbag on his back, crowd blurred around him, warm golden dusk light",
   "end": "Same corner. {wangguifen_young} kneels and wraps the crying little boy tightly in her arms, eyes closed in relief, tears on her face, warm dusk light, emotional close-up"},
 "sc22": {"chars": ["wangguifen", "linchuan"],
   "start": "Hospital room close-up: the aged wrinkled hand of {wangguifen} resting on the blanket; the hand of {linchuan} gently covers hers, soft warm light, superimposed memory warmth",
   "end": "Hospital room. {linchuan} looks at his mother in the bed, a tear falling down his cheek, their clasped hands in the soft-focus foreground, freeze-frame stillness, warm light"},
}

def build_graph(scene_id, frame, prompt_text, ref_files, seed, img2img=None, denoise=1.0):
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
        "29": {"class_type": "SaveImage", "inputs": {
            "images": ["8", 0], "filename_prefix": f"kf_{scene_id}_{frame}"}},
    }
    ref_inputs = {"clip": ["11", 0], "prompt": prompt_text, "vae": ["12", 0]}
    for i, fname in enumerate(ref_files[:3], 1):
        nid = f"2{i}"
        g[nid] = {"class_type": "LoadImage", "inputs": {"image": fname}}
        ref_inputs[f"image{i}"] = [nid, 0]
    g["6"] = {"class_type": "TextEncodeKrea2Ref", "inputs": ref_inputs}
    return g

def submit(g):
    body = json.dumps({"prompt": g, "client_id": "studio-kf"}).encode()
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
    batch = int(sys.argv[1])
    retry = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    ids = sorted(SCENES)[(batch - 1) * 5: batch * 5]
    if len(sys.argv) > 3:  # 可选：只跑指定场景，逗号分隔
        ids = [i for i in sys.argv[3].split(",") if i in SCENES]
    only_frame = sys.argv[4] if len(sys.argv) > 4 else None  # 可选：start/end
    if not ids:
        print("empty batch"); return
    seed = 50000 + batch * 1000 + retry * 100
    for sid in ids:
        sc = SCENES[sid]
        # 人数硬约束：防参考图多视图布局污染出复制体
        n = len(sc["chars"])
        people = ("Exactly one single person in the frame, no duplicates. "
                  if n == 1 else f"Exactly {n} people in the frame, no duplicates. ")
        for frame in ("start", "end"):
            if only_frame and frame != only_frame:
                continue
            text = people + sc[frame].format(**{c: CHARS[c][1] for c in sc["chars"]})
            i2i = f"kf_{sid}_start_latest.png" if frame == "end" else None
            if i2i:
                text = ("Continue from the input image, keep the exact same room, furniture positions, "
                        "camera angle and lighting. Only the action changes: ") + text
            # 场景/道具参考：文字提示 + 图片一起喂（图片最多 3 张，人物优先）
            loc_id, prop_ids = SCENE_ASSETS.get(sid, (None, []))
            ref_files = [CHARS[c][0] for c in sc["chars"][:2]]
            hints = []
            if loc_id:
                hints.append("Setting: " + REFS[loc_id][1])
                ref_files.append(REFS[loc_id][0])
            for p in prop_ids:
                hints.append("Prop: " + REFS[p][1])
                # 道具不喂参考图：白底产品图会被当成拼贴元素直贴进画面，文字描述即可
            text = text + ". " + ". ".join(hints) + STYLE
            t0 = time.time()
            pid = submit(build_graph(sid, frame, text, ref_files, seed,
                                     img2img=i2i, denoise=0.55 if i2i else 1.0))
            fn = wait(pid)
            print(f"{sid}_{frame} seed={seed} refs={len(ref_files)} -> {fn} ({time.time()-t0:.1f}s)", flush=True)
            seed += 1
    print("BATCH_DONE")

main()
