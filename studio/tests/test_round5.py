#!/usr/bin/env python3
"""第四轮复核 V401–V405。"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
STUDIO = os.path.dirname(HERE)
sys.path.insert(0, STUDIO)

from board_draft import attach_to_state  # noqa: E402
from planning import (  # noqa: E402
    apply_shot_patch, location_ref_file, propagate_put, shot_generation_input,
)
from prompt_compiler import compile_shot  # noqa: E402
from server import legacy_scene_dialogue_rel  # noqa: E402


def _base():
    st = {
        "name": "t", "schema_version": 2, "revision": 4, "stage": "keyframes",
        "execution": {"mode": "paused", "reason": "x"},
        "assets": [
            {"id": "linchuan", "type": "character", "status": "approved",
             "name": "林川", "image": "characters/linchuan.png"},
            {"id": "wangguifen", "type": "character", "status": "approved",
             "name": "王桂芬", "image": "characters/wangguifen.png",
             "voice": {"file": "characters/wangguifen_voice.wav", "status": "approved"}},
            {"id": "loc_home", "type": "scene", "status": "approved",
             "image": "scenes/loc_home.png",
             "variants": [
                 {"id": "loc_home_night", "file": "scenes/loc_home.png", "status": "approved",
                  "file_sha256": "night-old"},
                 {"id": "loc_home_morning", "file": "scenes/home_morning.png",
                  "status": "approved"},
             ]},
            {"id": "loc_office_night", "type": "scene", "status": "approved",
             "image": "scenes/loc_office_night.png"},
            {"id": "prop_phone_lin", "type": "prop", "status": "approved",
             "image": "props/prop_phone_lin.png"},
        ],
        "scenes": [], "jobs": [],
    }
    attach_to_state(st)
    sh = next(s for s in st["shots"] if s["id"] == "sh001")
    sh["video"] = {"file": "videos/sh001.mp4", "status": "approved", "validity": "current"}
    sh["validity"] = "current"
    sh["status"] = "approved"
    sh["start"] = {"image": "keyframes/kf.png", "status": "approved", "validity": "current"}
    return st


def _frontend_location(st, sh):
    with open(os.path.join(STUDIO, "web", "app.js"), encoding="utf-8") as f:
        js = f.read()
    source = js[js.index("function locationRef(sh) {"):js.index("\nfunction shotRefs(sh)")]
    program = (
        "const x=JSON.parse(process.argv[1]);"
        "function findAsset(id){return x.state.assets.find(a=>a.id===id);}\n"
        + source +
        "\nprocess.stdout.write(JSON.stringify(locationRef(x.sh)));"
    )
    p = subprocess.run(
        ["node", "-e", program, json.dumps({"state": st, "sh": sh})],
        capture_output=True, text=True, check=True)
    return json.loads(p.stdout)


class V401Digest(unittest.TestCase):
    def test_put_on_screen_stales_video(self):
        old = _base()
        new = json.loads(json.dumps(old))
        sh = next(s for s in new["shots"] if s["id"] == "sh001")
        before = shot_generation_input(old, next(s for s in old["shots"] if s["id"] == "sh001"))
        sh["cast"][1]["on_screen"] = True
        self.assertNotEqual(shot_generation_input(new, sh), before)
        propagate_put(old, new)
        sh = next(s for s in new["shots"] if s["id"] == "sh001")
        self.assertEqual(sh["video"]["validity"], "stale")
        out = compile_shot(new, "sh001")
        ids = [r["asset_id"] for r in out["refs_passed"]]
        self.assertIn("wangguifen", ids)

    def test_patch_on_screen_stales_video(self):
        st = _base()
        sh = next(s for s in st["shots"] if s["id"] == "sh001")
        cast = json.loads(json.dumps(sh["cast"]))
        cast[1]["on_screen"] = True
        apply_shot_patch(st, "sh001", {"cast": cast})
        sh = next(s for s in st["shots"] if s["id"] == "sh001")
        self.assertEqual(sh["video"]["validity"], "stale")

    def test_put_prop_state_stales_video(self):
        old = _base()
        new = json.loads(json.dumps(old))
        sh = next(s for s in new["shots"] if s["id"] == "sh001")
        sh["props"][0]["state"] = "broken_on_floor"
        propagate_put(old, new)
        sh = next(s for s in new["shots"] if s["id"] == "sh001")
        self.assertEqual(sh["video"]["validity"], "stale")
        out = compile_shot(new, "sh001")
        prop = next(r for r in out["refs_passed"] + out["refs_overflow"] if r["slot"] == "prop")
        self.assertEqual(prop["state"], "broken_on_floor")

    def test_patch_prop_state_stales_video(self):
        st = _base()
        sh = next(s for s in st["shots"] if s["id"] == "sh001")
        props = json.loads(json.dumps(sh["props"]))
        props[0]["state"] = "broken_on_floor"
        apply_shot_patch(st, "sh001", {"props": props})
        sh = next(s for s in st["shots"] if s["id"] == "sh001")
        self.assertEqual(sh["video"]["validity"], "stale")

    def test_put_character_view_stales_frames(self):
        old = _base()
        new = json.loads(json.dumps(old))
        lin = next(a for a in new["assets"] if a["id"] == "linchuan")
        lin["views"] = {"body_front": "characters/linchuan_front_new.png"}
        propagate_put(old, new)
        sh = next(s for s in new["shots"] if s["id"] == "sh001")
        self.assertEqual(sh["video"]["validity"], "stale")
        self.assertEqual(sh["start"]["validity"], "stale")
        out = compile_shot(new, "sh001")
        char = next(r for r in out["refs_passed"] if r["slot"] == "character")
        self.assertEqual(char["file"], "characters/linchuan_front_new.png")

    def test_put_prop_view_stales_frames(self):
        old = _base()
        new = json.loads(json.dumps(old))
        phone = next(a for a in new["assets"] if a["id"] == "prop_phone_lin")
        phone["views"] = {"front": "props/phone_front_new.png"}
        propagate_put(old, new)
        sh = next(s for s in new["shots"] if s["id"] == "sh001")
        self.assertEqual(sh["video"]["validity"], "stale")
        self.assertEqual(sh["start"]["validity"], "stale")
        out = compile_shot(new, "sh001")
        prop = next(r for r in out["refs_passed"] + out["refs_overflow"] if r["slot"] == "prop")
        self.assertEqual(prop["file"], "props/phone_front_new.png")


class V402VariantPriority(unittest.TestCase):
    def test_parent_night_ref_does_not_shadow_morning(self):
        loc = {
            "id": "loc_home", "type": "scene", "status": "approved",
            "image": "scenes/loc_home.png",
            "ref_file": "scenes/home_night_wide.png",
            "variants": [
                {"id": "loc_home_night", "file": "scenes/loc_home.png", "status": "approved"},
                {"id": "loc_home_morning", "file": "scenes/home_morning.png",
                 "status": "approved"},
            ],
        }
        res = location_ref_file(loc, "loc_home_morning")
        self.assertTrue(res["ok"])
        self.assertEqual(res["file"], "scenes/home_morning.png")

    def test_variant_ref_overrides_file(self):
        loc = {
            "id": "loc_home", "type": "scene", "status": "approved",
            "image": "scenes/loc_home.png",
            "ref_file": "scenes/home_night_wide.png",
            "variants": [
                {"id": "loc_home_morning", "file": "scenes/home_morning.png",
                 "ref_file": "scenes/home_morning_wide.png", "status": "approved"},
            ],
        }
        res = location_ref_file(loc, "loc_home_morning")
        self.assertEqual(res["file"], "scenes/home_morning_wide.png")


class V405ViewsEye(unittest.TestCase):
    def test_views_eye_used_when_variant_is_sheet(self):
        loc = {
            "id": "loc_home", "type": "scene", "status": "approved",
            "image": "scenes/loc_home.png",
            "views": {"eye": "scenes/home_custom_eye.png"},
            "variants": [
                {"id": "loc_home_night", "file": "scenes/loc_home.png", "status": "approved"},
                {"id": "loc_home_morning", "file": "scenes/home_morning.png",
                 "status": "approved"},
            ],
        }
        res = location_ref_file(loc, "loc_home_night")
        self.assertEqual(res["file"], "scenes/home_custom_eye.png")

    def test_frontend_matches_compiler_variants(self):
        st = _base()
        loc = next(a for a in st["assets"] if a["id"] == "loc_home")
        sh = next(s for s in st["shots"] if s["id"] == "sh001")
        sh["location_id"] = "loc_home"
        cases = [
            ("loc_home_morning", None, None, "scenes/home_morning.png"),
            ("loc_home_morning", "scenes/home_night_wide.png", None, "scenes/home_morning.png"),
            ("loc_home_morning", "scenes/home_night_wide.png", "scenes/home_morning_wide.png",
             "scenes/home_morning_wide.png"),
            ("loc_home_night", None, None, "scenes/home_custom_eye.png"),
        ]
        for vid, parent_ref, var_ref, expected in cases:
            loc["ref_file"] = parent_ref
            loc["views"] = {"eye": "scenes/home_custom_eye.png"}
            morning = next(v for v in loc["variants"] if v["id"] == "loc_home_morning")
            morning["file"] = "scenes/home_morning.png"
            if var_ref:
                morning["ref_file"] = var_ref
            else:
                morning.pop("ref_file", None)
            sh["location_variant_id"] = vid
            backend = location_ref_file(loc, vid)["file"]
            front = _frontend_location(st, sh)
            self.assertEqual(backend, expected, vid)
            self.assertEqual(front, expected, vid)


class V403WorkerPrompt(unittest.TestCase):
    def test_ready_snapshot_excludes_blocked(self):
        with open(os.path.join(STUDIO, "agent_worker.py"), encoding="utf-8") as f:
            src = f.read()
        tree = ast.parse(src)
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                  and n.name == "build_task_message")
        g = {
            "json": json, "os": os,
            "PROJECTS": "/tmp", "GLOBAL_CFG": "/tmp/none.json",
            "read_json": lambda path, default: default,
            "read_queue": lambda name: [
                {"event_id": "A", "type": "approve_voice", "target": "wangguifen"},
                {"event_id": "B", "type": "approve_voice", "target": "linchuan"},
            ],
            "model_display": lambda: "fake",
        }
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "worker", "exec"), g)
        msg = g["build_task_message"]("t", [
            {"event_id": "B", "type": "approve_voice", "target": "linchuan"},
        ])
        self.assertIn("  - B |", msg)
        self.assertNotIn("  - A |", msg)
        self.assertIn('["B"]', msg)
        self.assertNotIn('["A", "B"]', msg)
        self.assertNotIn("ack 前复查未确认队列非空则一并处理", msg)
        run_src = ast.unparse(next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
                                   and n.name == "run"))
        self.assertIn("build_task_message(name, ready)", run_src)


class V404LegacyDialogue(unittest.TestCase):
    def test_legacy_rel_helper(self):
        self.assertTrue(legacy_scene_dialogue_rel("dialogues/sc01.wav"))
        self.assertTrue(legacy_scene_dialogue_rel("dialogues/sc01__abcd.wav"))
        self.assertFalse(legacy_scene_dialogue_rel("dialogues/ut001.wav"))
        self.assertFalse(legacy_scene_dialogue_rel("characters/wangguifen_voice.wav"))

    def test_frontend_consumes_upload_path(self):
        with open(os.path.join(STUDIO, "web", "app.js"), encoding="utf-8") as f:
            js = f.read()
        dlg = js[js.index('input[data-dlg-up]'):js.index("const views =")]
        self.assertIn("r.path || rel", dlg)
        self.assertNotIn("sc.dialogue.file = rel;", dlg)


if __name__ == "__main__":
    unittest.main()
