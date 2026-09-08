import json, time, urllib.request
API = "http://127.0.0.1:8188"

ITEMS = {
 "loc_office_night": "A single wide photo of an open-plan office at night in a Chinese tech company: rows of desks with glowing monitors, office chairs, dark windows with city lights outside, cold blue monitor glow mixed with dark shadows, empty, no people, photorealistic, cinematic, no readable text",
 "loc_home": "A single wide photo of a modest rural Chinese home interior: old wooden square table and stools, enamel wash basin, a warm tungsten desk lamp, worn concrete walls, a wooden door opening to a small courtyard, morning light, no people, photorealistic, no readable text",
 "loc_train": "A single wide photo of an old Chinese green-skinned train hard-seat carriage interior: dark green bench seats facing each other, small folding table by a large window, luggage racks, farmland outside, warm daylight, no people, photorealistic, no readable text",
 "loc_bus_stop": "A single wide photo of a Chinese city street bus stop: a vertical bus stop sign pole with a printed paper notice taped on it, sidewalk, plane trees, empty road, storefronts across the street, overcast daylight, no people, photorealistic, no readable text",
 "loc_subway_gate": "A single wide photo of a Chinese subway station concourse: a row of silver turnstile gates with QR scanners, cold white ceiling lights, tiled reflective floor, hanging directional signs, no people, photorealistic, no readable text",
 "loc_subway_car": "A single wide photo of a moving Chinese subway car interior: stainless steel poles and hanging hand straps, molded seats, closed doors, dark tunnel streaking past windows, fluorescent light, no people, photorealistic, no readable text",
 "loc_platform": "A single wide photo of a Chinese subway platform: platform screen doors, yellow tactile paving, glowing advertising light boxes, ceiling strip lights, polished floor, no people, photorealistic, no readable text",
 "loc_hospital": "A single wide photo of a modern hospital single room: adjustable hospital bed with white linens, stainless bedside cabinet, modern IV pole with digital monitor, visitor chair, soft morning light through frosted window, no people, photorealistic, no readable text",
 "loc_street_dusk": "A single wide photo of a quiet rural Chinese village dirt road at golden hour: uneven muddy path with tire tracks and small puddles, low brick and adobe houses with tiled roofs, wooden fence, weeds, warm nostalgic light, no people, photorealistic, no readable text",
 "prop_phone_lin": "A single photorealistic product photo of one modern black smartphone, slim bezel-less design, dark glass back, lying flat on a plain light gray surface, screen off, soft even light, no readable text",
 "prop_phone_wang": "A single photorealistic product photo of one old worn smartphone with a visibly cracked shattered screen, scuffed body, dated design, lying flat on a plain light gray surface, soft even light, no readable text",
 "prop_basket": "A single photorealistic product photo of one woven reusable shopping tote bag filled with folded clothes and a few packaged snack bags visible at the opening, on a plain light gray surface, soft even light, no readable text",
 "prop_bus_sign": "A single photorealistic photo of one tall vertical Chinese city bus stop sign pole, metal panel with a printed A4 paper notice taped on it, plain light background, soft even light, no readable text",
}

def build(text, seed, aid):
    return {
        "10": {"class_type": "UNETLoader", "inputs": {
            "unet_name": "Krea-2/krea2_turbo_fp8_scaled.safetensors", "weight_dtype": "default"}},
        "11": {"class_type": "CLIPLoader", "inputs": {
            "clip_name": "qwen3vl/qwen3vl_4b_fp8_scaled.safetensors", "type": "krea2", "device": "default"}},
        "12": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_vae.safetensors"}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": text, "clip": ["11", 0]}},
        "13": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["6", 0]}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 1344, "height": 768, "batch_size": 1}},
        "3": {"class_type": "KSampler", "inputs": {
            "model": ["10", 0], "positive": ["6", 0], "negative": ["13", 0],
            "latent_image": ["5", 0], "seed": seed, "steps": 8, "cfg": 1.0,
            "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["12", 0]}},
        "29": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": "ref1_" + aid}},
    }

def submit(g):
    body = json.dumps({"prompt": g, "client_id": "studio-refs"}).encode()
    req = urllib.request.Request(API + "/prompt", data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=60).read())["prompt_id"]

def wait(pid):
    for _ in range(180):
        time.sleep(3)
        h = json.loads(urllib.request.urlopen(API + "/history/" + pid, timeout=15).read())
        e = h.get(pid, {})
        st = e.get("status", {})
        if st.get("completed"):
            for out in e.get("outputs", {}).values():
                for img in out.get("images", []):
                    return img["filename"]
        if st.get("status_str") == "error":
            raise RuntimeError(json.dumps(e.get("status"), ensure_ascii=False)[:400])
    raise TimeoutError(pid)

seed = 72000
for aid, text in ITEMS.items():
    t0 = time.time()
    fn = wait(submit(build(text, seed, aid)))
    print(f"{aid} seed={seed} -> {fn} ({time.time()-t0:.1f}s)", flush=True)
    seed += 1
print("REFS_DONE")
