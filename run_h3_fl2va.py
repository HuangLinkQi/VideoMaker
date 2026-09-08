"""
构造 MiniMax H3 fl2va 工作流并通过 API 提交

节点: MiniMaxH3ImageToVideo (支持 first_frame / last_frame)
模型: minimax_h3_fl2va_pruned_int8_convrot
输入: 用户提供的 lastframe_card.png 作为尾帧
时长: 8秒 (192帧)
"""

import json
import urllib.request
import urllib.error

API = 'http://127.0.0.1:8188'

PROMPT = (
    "打开镜头，办公室白天场景。一个戴细框眼镜、穿深灰色短袖 T恤和黑色运动短裤的"
    "年轻东亚男性，坐在人体工学椅上，面向一张摆着笔记本电脑的办公桌，屏幕微亮。"
    "日光从画面左侧的落地窗洒入，冷白偏蓝的自然光形成明显方向感。"
    "他先安静坐着，双手自然搭在桌面边缘。\n\n"
    "约第 2 秒，他察觉镜头方向，椅子向后轻滚离桌面二十厘米。"
    "他双手撑住扶手，从椅子上平稳站起，重心从臀部转移到双脚。"
    "站定后约第 3.5 秒，双臂从身体两侧缓慢抬起，在胸前自然交叉，"
    "最后定格为双手交叉于胸前的姿态——这正是末帧画面里他的最终造型。\n\n"
    "画面定格后约第 5.5 秒，他交叉的双臂前浮现出一张半透明卡片：长方形，边角圆润，"
    "外缘金色描边，主体青蓝色全息光，悬浮于他胸前约二十厘米处。"
    "镜头在卡片出现的瞬间轻微推进（push-in）并微微上仰，与末帧卡片的视角对齐。"
    "背景由办公环境渐变切黑，卡片的青金色光成为画面主光源。"
    "最终画面凝固为他一手轻捏卡片边缘的特写。\n\n"
    "镜头：固定三脚架起始 → 尾段柔顺推进 → 收尾微仰。"
    "光线：冷白日光 → 渐变为深色背景 + 卡片全息青金反光。"
    "声音：键盘轻响、空调底噪 → 椅子滚轮摩擦 → 卡片出现的低频电子嗡鸣。"
)

# 用户图片是竖屏 768x1344, 接近 9:16
W, H = 768, 1344
LENGTH_8S = 192   # 8 秒 @ 24fps (17*11+5 = 192)

prompt_graph = {
    # 模型加载
    "1": {
        "class_type": "UNETLoader",
        "inputs": {
            "unet_name": "minimax-h3/minimax_h3_fl2va_pruned_int8_convrot.safetensors",
            "weight_dtype": "default",
        },
    },
    "2": {
        "class_type": "CLIPLoader",
        "inputs": {
            "clip_name": "minimax-h3/qwen3vl_32b_minimax_h3_int8_convrot.safetensors",
            "type": "minimax",
            "device": "default",
        },
    },
    "3": {
        "class_type": "VAELoader",
        "inputs": {"vae_name": "minimax-h3/minimax_h3_video_vae_fp16.safetensors"},
    },
    "4": {
        "class_type": "VAELoader",
        "inputs": {"vae_name": "minimax-h3/minimax_h3_audio_vae_fp32.safetensors"},
    },

    # 用户输入
    "10": {
        "class_type": "PrimitiveStringMultiline",
        "inputs": {"value": PROMPT},
    },
    "11": {
        "class_type": "LoadImage",
        "inputs": {"image": "lastframe_card.png"},
    },

    # 核心生成节点 (ImageToVideo, 末帧模式)
    "20": {
        "class_type": "MiniMaxH3ImageToVideo",
        "inputs": {
            "clip": ["2", 0],
            "vae": ["3", 0],
            "prompt": ["10", 0],
            "width": W,
            "height": H,
            "length": LENGTH_8S,
            "last_frame": ["11", 0],
        },
    },

    # 采样器设置
    "30": {
        "class_type": "KSamplerSelect",
        "inputs": {"sampler_name": "res_multistep"},
    },
    "31": {
        "class_type": "BasicScheduler",
        "inputs": {
            "model": ["1", 0],
            "scheduler": "simple",
            "steps": 20,
            "denoise": 1.0,
        },
    },
    "32": {
        "class_type": "RandomNoise",
        "inputs": {"noise_seed": 42},
    },

    # 引导
    "33": {
        "class_type": "BasicGuider",
        "inputs": {
            "model": ["1", 0],
            "conditioning": ["20", 0],
        },
    },

    # 采样
    "34": {
        "class_type": "SamplerCustomAdvanced",
        "inputs": {
            "noise": ["32", 0],
            "guider": ["33", 0],
            "sampler": ["30", 0],
            "sigmas": ["31", 0],
            "latent_image": ["20", 1],
        },
    },

    # 解码
    "40": {
        "class_type": "VAEDecode",
        "inputs": {
            "samples": ["34", 0],
            "vae": ["3", 0],
        },
    },
    "41": {
        "class_type": "VAEDecodeAudio",
        "inputs": {
            "samples": ["34", 0],
            "vae": ["4", 0],
        },
    },

    # 组装视频
    "50": {
        "class_type": "CreateVideo",
        "inputs": {
            "images": ["40", 0],
            "audio": ["41", 0],
            "fps": 24,
            "bit_depth": 8,
        },
    },

    # 保存
    "60": {
        "class_type": "SaveVideo",
        "inputs": {
            "video": ["50", 0],
            "filename_prefix": "video/fl2va_card",
            "format": "auto",
            "codec": "auto",
        },
    },
}

payload = {'prompt': prompt_graph, 'client_id': 'fl2va_card_v1'}
data = json.dumps(payload).encode('utf-8')
req = urllib.request.Request(
    f'{API}/prompt',
    data=data,
    headers={'Content-Type': 'application/json'},
    method='POST',
)

print("=== 提交 fl2va 工作流 ===")
print(f"  模型: minimax_h3_fl2va_pruned_int8_convrot")
print(f"  尺寸: {W}x{H}, 长度: {LENGTH_8S} 帧 (8秒 @ 24fps)")
print(f"  尾帧: lastframe_card.png")
print()
try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode('utf-8'))
        prompt_id = result.get('prompt_id')
        print(f"OK 提交成功, prompt_id = {prompt_id}")
        print(f"响应: {json.dumps(result, ensure_ascii=False, indent=2)[:500]}")
except urllib.error.HTTPError as e:
    print(f"ERR HTTPError {e.code}: {e.read().decode('utf-8')}")
    raise

# 同时保存工作流定义
wf_path = '/root/ComfyUI/user/default/workflows/_fl2va_card_v1.json'
with open(wf_path, 'w') as f:
    json.dump(prompt_graph, f, ensure_ascii=False, indent=2)
print(f"\nOK 工作流已保存: {wf_path}")
with open('/tmp/last_fl2va_prompt.json', 'w') as f:
    json.dump(prompt_graph, f, ensure_ascii=False, indent=2)
print(f"OK prompt graph: /tmp/last_fl2va_prompt.json")