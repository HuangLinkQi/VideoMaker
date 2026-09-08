#!/bin/bash
# CosyVoice2 独立环境安装 v2（virtualenv 方案，不依赖 ensurepip，不碰系统包）
exec > /root/tts_setup.log 2>&1
set -x

PIPM="https://mirrors.aliyun.com/pypi/simple/"

echo "== user-level virtualenv"
python3 -m pip install --user -i $PIPM virtualenv 2>&1 | tail -2
rm -rf /root/cosyenv
python3 -m virtualenv -p /usr/bin/python3 /root/cosyenv 2>&1 | tail -2
source /root/cosyenv/bin/activate || exit 1
pip --version || exit 1

echo "== deps"
cd /root/cosyvoice
grep -viE "^(torch|onnxruntime-gpu)" requirements.txt > /tmp/req2.txt
pip install -i $PIPM -r /tmp/req2.txt 2>&1 | tail -3
pip install -i $PIPM onnxruntime 2>&1 | tail -1
pip install torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cpu 2>&1 | tail -2

echo "== weights (~6GB via hf-mirror)"
pip install -i $PIPM "huggingface_hub[cli]" 2>&1 | tail -1
export HF_ENDPOINT=https://hf-mirror.com
huggingface-cli download FunAudioLLM/CosyVoice2-0.5B --local-dir /root/models/CosyVoice2-0.5B 2>&1 | tail -3

echo "== smoke"
python3 -c "
import sys; sys.path.insert(0, 'third_party/Matcha-TTS')
from cosyvoice.cli.cosyvoice import CosyVoice2
print('IMPORT_OK')
" 2>&1 | tail -3
echo SETUP_DONE
