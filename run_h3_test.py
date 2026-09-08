"""
修正 MiniMax H3 工作流配置并通过 API 提交
"""

import json
import urllib.request
import urllib.error
import time

WORKFLOW_PATH = '/root/ComfyUI/user/default/workflows/海螺全能参考-video_minimax_h3_r2v.json'
API = 'http://127.0.0.1:8188'

with open(WORKFLOW_PATH, 'r') as f:
    wf = json.load(f)

nodes = wf['nodes']

modified = []
for n in nodes:
    ctype = n.get('type', '')
    wv = n.get('widgets_values', [])

    if ctype == 'UNETLoader' and wv and 'fl2va_int8_convrot' in str(wv):
        old = wv[0]
        wv[0] = 'minimax-h3/minimax_h3_ref2va_pruned_int8_convrot.safetensors'
        modified.append(f"UNET: {old} -> {wv[0]}")

    elif ctype == 'LoadImage' and wv:
        old = wv[0]
        if '0.png' in str(old) or old == '0.png':
            wv[0] = 'test_ref_1.png'
            modified.append(f"LoadImage#1: {old} -> test_ref_1.png")
        elif '5cb38bfd' in str(old):
            wv[0] = 'test_ref_2.png'
            modified.append(f"LoadImage#2: {old} -> test_ref_2.png")

    elif ctype == 'PrimitiveStringMultiline' and wv:
        wv[0] = (
            "A cinematic portrait shot. <Picture 1> is a smiling young woman with short black hair. "
            "<Picture 2> is a smiling young man with curly hair. "
            "Use <Picture 1> as the main character. She stands in a sunlit park, turns her head slowly, "
            "her hair flowing gently in the breeze, soft golden hour lighting, cinematic shallow depth of field, "
            "smooth camera motion, photorealistic."
        )
        modified.append(f"Prompt: 简化为 {len(wv[0])} 字符")

    elif ctype == 'PrimitiveFloat' and wv:
        old = wv[0]
        wv[0] = 5.0
        modified.append(f"Duration: {old} -> 5.0s")

    elif ctype == 'ResolutionSelector' and wv and len(wv) >= 2:
        old_mp = wv[1]
        wv[1] = 0.4
        modified.append(f"Megapixels: {old_mp} -> 0.4 (~864x480)")

print("=== 修正项 ===")
for m in modified:
    print(f"  {m}")

link_map = {}
for link in wf.get('links', []):
    link_id = link[0]
    src_node = link[1]
    src_slot = link[2]
    link_map[link_id] = (str(src_node), src_slot)

prompt_graph = {}
SKIP_TYPES = {'MarkdownNote', 'Note', 'Reroute', 'Comment'}
for n in nodes:
    if n.get('type') in SKIP_TYPES:
        continue
    nid = str(n['id'])
    inputs_dict = {}
    wv = n.get('widgets_values', [])

    for inp in n.get('inputs', []):
        link_id = inp.get('link')
        if link_id is not None and link_id in link_map:
            src_node, src_slot = link_map[link_id]
            inputs_dict[inp['name']] = [src_node, src_slot]

    if n.get('type') == 'PrimitiveStringMultiline' and len(wv) > 0:
        if 'value' not in inputs_dict:
            inputs_dict['value'] = wv[0]
    if n.get('type') == 'PrimitiveFloat' and len(wv) > 0:
        if 'value' not in inputs_dict:
            inputs_dict['value'] = wv[0]
    if n.get('type') == 'LoadImage' and len(wv) > 0:
        if 'image' not in inputs_dict:
            inputs_dict['image'] = wv[0]
    if n.get('type') == 'UNETLoader' and len(wv) >= 2:
        if 'unet_name' not in inputs_dict:
            inputs_dict['unet_name'] = wv[0]
        if 'weight_dtype' not in inputs_dict:
            inputs_dict['weight_dtype'] = wv[1]
    if n.get('type') == 'CLIPLoader' and len(wv) >= 2:
        if 'clip_name' not in inputs_dict:
            inputs_dict['clip_name'] = wv[0]
        if 'type' not in inputs_dict:
            inputs_dict['type'] = wv[1]
    if n.get('type') == 'VAELoader' and len(wv) >= 1:
        if 'vae_name' not in inputs_dict:
            inputs_dict['vae_name'] = wv[0]
    if n.get('type') == 'ResolutionSelector' and len(wv) >= 3:
        if 'aspect_ratio' not in inputs_dict:
            inputs_dict['aspect_ratio'] = wv[0]
        if 'megapixels' not in inputs_dict:
            inputs_dict['megapixels'] = wv[1]
        if 'multiple' not in inputs_dict:
            inputs_dict['multiple'] = wv[2]
    if n.get('type') == 'KSamplerSelect' and len(wv) >= 1:
        if 'sampler_name' not in inputs_dict:
            inputs_dict['sampler_name'] = wv[0]
    if n.get('type') == 'BasicScheduler' and len(wv) >= 3:
        if 'scheduler' not in inputs_dict:
            inputs_dict['scheduler'] = wv[0]
        if 'steps' not in inputs_dict:
            inputs_dict['steps'] = wv[1]
        if 'denoise' not in inputs_dict:
            inputs_dict['denoise'] = wv[2]
    if n.get('type') == 'RandomNoise' and len(wv) >= 1:
        if 'noise_seed' not in inputs_dict:
            inputs_dict['noise_seed'] = wv[0]
    if n.get('type') == 'MiniMaxH3ReferenceToVideo' and len(wv) >= 5:
        if 'prompt' not in inputs_dict:
            inputs_dict['prompt'] = wv[0]
        if 'width' not in inputs_dict:
            inputs_dict['width'] = wv[1]
        if 'height' not in inputs_dict:
            inputs_dict['height'] = wv[2]
        if 'length' not in inputs_dict:
            inputs_dict['length'] = wv[3]
        if 'ref_image_size' not in inputs_dict:
            inputs_dict['ref_image_size'] = wv[4]
    if n.get('type') == 'CreateVideo' and len(wv) >= 2:
        if 'fps' not in inputs_dict:
            inputs_dict['fps'] = wv[0]
        if 'bit_depth' not in inputs_dict:
            inputs_dict['bit_depth'] = wv[1]
    if n.get('type') == 'SaveVideo' and len(wv) >= 3:
        if 'filename_prefix' not in inputs_dict:
            inputs_dict['filename_prefix'] = wv[0]
        if 'format' not in inputs_dict:
            inputs_dict['format'] = wv[1]
        if 'codec' not in inputs_dict:
            inputs_dict['codec'] = wv[2]

    prompt_graph[nid] = {
        'class_type': n['type'],
        'inputs': inputs_dict,
    }

payload = {'prompt': prompt_graph, 'client_id': 'cli_test_h3'}
data = json.dumps(payload).encode('utf-8')
req = urllib.request.Request(
    f'{API}/prompt',
    data=data,
    headers={'Content-Type': 'application/json'},
    method='POST',
)

print("\n=== 提交到 ComfyUI ===")
try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode('utf-8'))
        prompt_id = result.get('prompt_id')
        print(f"OK 提交成功, prompt_id = {prompt_id}")
        print(f"响应: {json.dumps(result, ensure_ascii=False, indent=2)[:800]}")
except urllib.error.HTTPError as e:
    print(f"ERR HTTPError {e.code}: {e.read().decode('utf-8')}")
    raise

wf_mod_path = '/root/ComfyUI/user/default/workflows/_test_minimax_h3_v1.json'
with open(wf_mod_path, 'w') as f:
    json.dump(wf, f, ensure_ascii=False, indent=2)
print(f"\nOK 修正后的工作流已保存: {wf_mod_path}")

with open('/tmp/last_prompt.json', 'w') as f:
    json.dump(prompt_graph, f, ensure_ascii=False, indent=2)
print(f"OK prompt graph 已保存: /tmp/last_prompt.json")