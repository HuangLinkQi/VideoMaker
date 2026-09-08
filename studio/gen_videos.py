#!/usr/bin/env python3
"""按拆细后的分镜逐条生成（一条视频一个微事件）。不拼接凑时长。"""
import json, os, time, urllib.request

API = "http://127.0.0.1:8188"
SEED = 142
# 8s=192 7s=175 6s=141
JOBS = [
    {"id":"sc01","first":"kf_sc01_start.png","last":"kf_sc01_end.png","length":192,
     "prompt":"【总时长8秒｜一个微事件：加班→手机亮→接到耳边，听见母亲开口】\n镜头1（0-3s）林川敲键盘，桌上黑手机震动亮起，他转头。\n镜头2（3-8s）右手拿起手机贴右耳。画外母亲：「川川，睡了吗？」他听着，肩膀松下来，尚未回答。落幅对齐尾帧。只有他一人。成片无音轨。"},
    {"id":"sc26","first":"kf_sc01_end.png","last":None,"length":175,
     "prompt":"【总时长7秒｜一个微事件：接听中回答母亲】\n仅首帧。林川持机贴右耳，疲惫放软，嘴唇开合与台词逐字对应：「没呢，妈。您咋还没睡？」说完呼一口气，不放下手机。只有他一人。成片无音轨。"},
    {"id":"sc02","first":"kf_sc02_start.png","last":None,"length":192,
     "prompt":"【总时长8秒｜一个微事件：王桂芬问儿子回不回家】\n仅首帧。她坐矮凳、碎屏手机贴左耳，带着笑听完，小心开口，嘴唇开合与台词逐字对应：「中秋忙不忙，回家吗？」说完屏息等。不要提生日。不要放下手机。成片无音轨。"},
    {"id":"sc03","first":"kf_sc01_end.png","last":"kf_sc03_start.png","length":192,
     "prompt":"【总时长8秒｜一个微事件：回话「回不去」+主管入画】\n镜头1（0-5s）林川持机：「妈，中秋我这边挺忙的，怕是回不去了……」嘴唇开合与台词逐字对应。\n镜头2（5-8s）张主管从身后走进，停在右肩旁；林川余光一瞥。落幅对齐尾帧。成片无音轨。"},
    {"id":"sc27","first":"kf_sc03_start.png","last":None,"length":141,
     "prompt":"【总时长6秒｜一个微事件：余光看见主管，对妈妈说先挂】\n仅首帧。林川看了一眼身后主管，压低声音：「主管来了，有点忙，先挂了啊。」嘴唇开合与台词逐字对应。仍持机，尚未放下。主管不说话。成片无音轨。"},
    {"id":"sc04","first":"kf_sc03_start.png","last":None,"length":192,
     "prompt":"【总时长8秒｜一个微事件：挂断放下，敲键盘交差】\n仅首帧。林川把手机放到桌上，双手回键盘敲几下，看着屏幕说：「第一版方案改的差不多了。」嘴唇开合与台词逐字对应。主管在身后听，先不指屏幕。成片无音轨。"},
    {"id":"sc28","first":"kf_sc04_start.png","last":None,"length":175,
     "prompt":"【总时长7秒｜一个微事件：主管否定并指向屏幕】\n林川手机已在桌上。主管前倾、右手指向显示器：「还不行，方案都没闭环。」嘴唇开合与台词逐字对应。林川不说话。成片无音轨。"},
    {"id":"sc05","first":"kf_sc04_start.png","last":None,"length":192,
     "prompt":"【总时长8秒｜一个微事件：主管留下要求并走出画面】\n仅首帧。主管说：「继续改，要从客户视角考虑这个功能。」嘴唇开合与台词逐字对应。说完转身向右走出画面，林川独自留下。成片无音轨。"},
    {"id":"sc29","first":"kf_sc05_start.png","last":None,"length":175,
     "prompt":"【总时长7秒｜一个微事件：独自烦躁】\n林川对着代码，左手插进头发，低声：「业务闭环，你倒是说怎么闭环呀。」嘴唇开合与台词逐字对应。说完再挠一下头。只有他一人。成片无音轨。"},
]

def build(job):
    g = {
        "1": {"class_type": "UNETLoader", "inputs": {
            "unet_name": "minimax-h3/minimax_h3_fl2va_pruned_int8_convrot.safetensors",
            "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {
            "clip_name": "minimax-h3/qwen3vl_32b_minimax_h3_int8_convrot.safetensors",
            "type": "minimax", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {
            "vae_name": "minimax-h3/minimax_h3_video_vae_fp16.safetensors"}},
        "10": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": job["prompt"]}},
        "11": {"class_type": "LoadImage", "inputs": {"image": job["first"]}},
        "20": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {
            "clip": ["2", 0], "vae": ["3", 0], "prompt": ["10", 0],
            "width": 1344, "height": 768, "length": job["length"],
            "first_frame": ["11", 0]}},
        "30": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
        "31": {"class_type": "BasicScheduler", "inputs": {
            "model": ["1", 0], "scheduler": "simple", "steps": 12, "denoise": 1.0}},
        "32": {"class_type": "RandomNoise", "inputs": {"noise_seed": SEED}},
        "33": {"class_type": "BasicGuider", "inputs": {
            "model": ["1", 0], "conditioning": ["20", 0]}},
        "34": {"class_type": "SamplerCustomAdvanced", "inputs": {
            "noise": ["32", 0], "guider": ["33", 0], "sampler": ["30", 0],
            "sigmas": ["31", 0], "latent_image": ["20", 1]}},
        "40": {"class_type": "VAEDecode", "inputs": {"samples": ["34", 0], "vae": ["3", 0]}},
        "50": {"class_type": "CreateVideo", "inputs": {"images": ["40", 0], "fps": 24}},
        "60": {"class_type": "SaveVideo", "inputs": {
            "video": ["50", 0], "filename_prefix": f"vid/{job['id']}",
            "format": "auto", "codec": "auto"}},
    }
    if job.get("last"):
        g["12"] = {"class_type": "LoadImage", "inputs": {"image": job["last"]}}
        g["20"]["inputs"]["last_frame"] = ["12", 0]
    return g

def submit(g):
    body = json.dumps({"prompt": g, "client_id": "studio-vid"}).encode()
    req = urllib.request.Request(API+"/prompt", data=body, method="POST",
                                 headers={"Content-Type":"application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=180).read())["prompt_id"]

def wait(pid):
    for _ in range(900):
        time.sleep(5)
        h = json.loads(urllib.request.urlopen(API+"/history/"+pid, timeout=60).read())
        e = h.get(pid, {})
        st = e.get("status", {})
        if st.get("completed"):
            files = []
            for out in e.get("outputs", {}).values():
                for v in out.get("videos", out.get("gifs", out.get("images", []))):
                    files.append(v.get("filename", ""))
            return files
        if st.get("status_str") == "error":
            raise RuntimeError(json.dumps(e.get("status"), ensure_ascii=False)[:500])
    raise TimeoutError(pid)

if __name__ == "__main__":
    raise SystemExit(
        "legacy 入口已禁用：JOBS 写死 9 条微事件，不再作为默认生成入口。"
        "请从锁定 revision 的镜头表编译任务；当前项目 execution.mode=paused。"
    )
