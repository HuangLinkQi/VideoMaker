#!/usr/bin/env python3
"""人物声线锚点生成（CosyVoice2 instruct2：按音色描述合成校准句）。

产物: /root/voices/<id>_voice.wav（24kHz mono，校准句读两遍 ≈ 8-10s）。
用法（GPU, cosyenv）: python3 gen_voices.py [id ...]
"""
import json
import sys

sys.path.insert(0, "/root/cosyvoice")
sys.path.insert(0, "/root/cosyvoice/third_party/Matcha-TTS")

import torch  # noqa: E402
import torchaudio  # noqa: E402
from cosyvoice.cli.cosyvoice import CosyVoice2  # noqa: E402

MODEL = "/root/models/CosyVoice2-0.5B"
OUT = "/root/voices"
CALIB = "今天天气不错，我们慢慢说。今天天气不错，我们慢慢说。"

# 音色指令来自 projects/测试/state.json 各人物 voice.desc
INSTRUCT = {
    "linchuan": "用三十岁中国男性的声音说话，必须是成年男声，中低音，语速偏快，带一点疲惫和不耐烦，普通话标准",
    "wangguifen": "用五十八岁中国农村母亲的声音说话，必须是中老年女声不是男声，温和略带沙哑，语速慢，带轻微乡音",
    "zhouye": "用二十岁中国男大学生的声音说话，必须是青年男声，清亮自然，语速正常，语气热心",
    "xiaoyu": "用十二岁小学女生的童声说话，必须是小女孩的声音不是成年男声，声音细而清亮，语速认真偏慢，不奶腔不撒娇",
    "wangguifen_young": "用三十岁中国女性的声音说话，必须是成年女声不是男声，女声明亮、气息充足，语气急切",
    "linchuan_child": "用五岁小男孩的细童声说话，必须是幼童男声不是成年女声，声线又细又高，音量小，带一点哭腔",
    "zhuguan": "用四十五岁男性主管的声音说话，必须是中年男声不是女声，中低音沉稳带压迫感，语速快而清晰，略带官腔",
    "cunzhang": "用四十五岁农村村长的声音说话，必须是中年男声不是女声，男声洪亮爽朗，语速偏快热情，乡音明显",
}


# 2026-09-07：官方文件名与实际性别反了（用户听审）。
# zero_shot_prompt.wav 实际是女声；cross_lingual_prompt.wav 实际是男声。
# 上一轮按文件名分配 → 王桂芬/小雨/年轻王桂芬出男声，主管/村长/年幼林川出女声。
MALE_PROMPT = "/root/cosyvoice/asset/cross_lingual_prompt.wav"
FEMALE_PROMPT = "/root/cosyvoice/asset/zero_shot_prompt.wav"
FEMALE_CHARS = {"wangguifen", "xiaoyu", "wangguifen_young"}


def main() -> None:
    import os
    os.makedirs(OUT, exist_ok=True)
    ids = sys.argv[1:] or list(INSTRUCT)
    m = CosyVoice2(MODEL, load_jit=False, load_trt=False, fp16=False)
    sr = m.sample_rate
    # 本仓库版 inference_instruct2 要求 prompt_wav 传文件路径（内部自行加载）
    prompts = {k: (MALE_PROMPT if k not in FEMALE_CHARS else FEMALE_PROMPT) for k in INSTRUCT}
    fails = []
    for cid in ids:
        try:
            outs = list(m.inference_instruct2(CALIB, INSTRUCT[cid], prompts[cid], stream=False))
            wav = torch.cat([o["tts_speech"] for o in outs], dim=1)  # [1, N]
            path = f"{OUT}/{cid}_voice.wav"
            import soundfile as sf
            sf.write(path, wav[0].numpy(), sr)  # 单声道干声 24kHz
            dur = wav.shape[1] / sr
            print(f"OK {cid} {dur:.1f}s sr={sr}", flush=True)
        except Exception as e:  # noqa: BLE001
            fails.append(cid)
            print(f"FAIL {cid} {repr(e)[:160]}", flush=True)
    print("FAILS:", fails or "none")
    print("VOICES_DONE")


if __name__ == "__main__":
    main()
