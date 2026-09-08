#!/usr/bin/env python3
"""V501：首尾帧按 shots 操作；PATCH 保存；帧版本审批。"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
STUDIO = os.path.dirname(HERE)
ROOT = os.path.dirname(STUDIO)
sys.path.insert(0, STUDIO)

from board_draft import attach_to_state  # noqa: E402
from planning import (  # noqa: E402
    apply_shot_patch, can_submit_render, frame_event_key, resource_version,
)
from schema import EVENT_TYPES  # noqa: E402


def _base():
    st = {
        "name": "t", "schema_version": 2, "revision": 4, "stage": "keyframes",
        "execution": {"mode": "paused", "reason": "x"},
        "assets": [
            {"id": "linchuan", "type": "character", "status": "approved",
             "name": "林川", "image": "characters/linchuan.png"},
            {"id": "wangguifen", "type": "character", "status": "approved",
             "name": "王桂芬", "image": "characters/wangguifen.png"},
            {"id": "loc_office_night", "type": "scene", "status": "approved",
             "image": "scenes/loc_office_night.png"},
            {"id": "prop_phone_lin", "type": "prop", "status": "approved",
             "image": "props/prop_phone_lin.png"},
        ],
        "scenes": [
            {"id": "sc01", "title": "旧卡", "duration": 6,
             "start": {"image": "keyframes/kf_sc01_start.png", "status": "approved"},
             "end": {"image": "keyframes/kf_sc01_end.png", "status": "removed"}},
        ],
        "jobs": [],
    }
    attach_to_state(st)
    sh = next(s for s in st["shots"] if s["id"] == "sh001")
    sh["video"] = {"file": "videos/sh001.mp4", "status": "approved", "validity": "current"}
    sh["validity"] = "current"
    sh["status"] = "approved"
    sh["start"] = {
        "image": "keyframes/kf_sh001_start.png", "status": "review",
        "validity": "current", "revision_id": "start-v1", "file_sha256": "sha-start-v1",
        "prompt_zh": "旧中文", "prompt": "old en",
        "versions": [
            {"file": "keyframes/versions/kf_sh001_start__a.png", "ts": "2026-09-07"},
            {"file": "keyframes/kf_sh001_start.png", "ts": "2026-09-08"},
        ],
    }
    sh["end"] = {"image": "keyframes/kf_sc01_end.png", "status": "removed",
                 "validity": "current"}
    sh["conditioning_mode"] = "first_only"
    return st


def _render(st):
    contract = os.path.join(ROOT, "第五轮复核附件-video-studio-2026-09-08", "前端合同.cjs")
    p = subprocess.run(
        ["node", contract],
        input=json.dumps({"mode": "render", "state": st, "project": "t"}),
        capture_output=True, text=True, check=True)
    return json.loads(p.stdout)


class EventContract(unittest.TestCase):
    def test_new_event_types(self):
        self.assertTrue(EVENT_TYPES["approve_start"])
        self.assertTrue(EVENT_TYPES["approve_end"])
        self.assertEqual(frame_event_key("approve_start"), "start")
        self.assertEqual(frame_event_key("approve_end"), "end")

    def test_start_revision_not_end(self):
        st = _base()
        start = resource_version(st, "approve_start", "sh001")
        end = resource_version(st, "approve_end", "sh001")
        self.assertEqual(start, "start-v1")
        self.assertNotEqual(start, end)
        self.assertEqual(start, resource_version(st, "regenerate_start", "sh001"))

    def test_old_frame_version_differs_after_replace(self):
        st = _base()
        old = resource_version(st, "approve_start", "sh001")
        apply_shot_patch(st, "sh001", {
            "start": {"image": "keyframes/kf_sh001_start_v2.png",
                      "revision_id": "start-v2", "file_sha256": "sha-start-v2"},
        })
        new = resource_version(st, "approve_start", "sh001")
        self.assertEqual(old, "start-v1")
        self.assertEqual(new, "start-v2")
        self.assertNotEqual(old, new)


class PatchFrame(unittest.TestCase):
    def test_replace_start_keeps_removed_end(self):
        st = _base()
        apply_shot_patch(st, "sh001", {
            "start": {"image": "keyframes/other.png", "status": "review"},
        })
        sh = next(s for s in st["shots"] if s["id"] == "sh001")
        self.assertEqual(sh["start"]["image"], "keyframes/other.png")
        self.assertEqual(sh["end"]["status"], "removed")
        self.assertEqual(sh["end"]["image"], "keyframes/kf_sc01_end.png")
        self.assertEqual(sh["conditioning_mode"], "first_only")
        self.assertEqual(sh["video"]["validity"], "stale")
        self.assertEqual(sh["validity"], "current")

    def test_prompt_only_does_not_stale_video(self):
        st = _base()
        apply_shot_patch(st, "sh001", {
            "start": {"prompt_zh": "新中文", "prompt": "new en"},
        })
        sh = next(s for s in st["shots"] if s["id"] == "sh001")
        self.assertEqual(sh["start"]["prompt_zh"], "新中文")
        self.assertEqual(sh["start"]["image"], "keyframes/kf_sh001_start.png")
        self.assertEqual(sh["start"]["revision_id"], "start-v1")
        self.assertEqual(sh["video"]["validity"], "current")
        self.assertEqual(sh["end"]["status"], "removed")

    def test_restore_end_does_not_set_last_only(self):
        st = _base()
        apply_shot_patch(st, "sh001", {"end": {"status": "review"}})
        sh = next(s for s in st["shots"] if s["id"] == "sh001")
        self.assertEqual(sh["end"]["status"], "review")
        self.assertEqual(sh["start"]["image"], "keyframes/kf_sh001_start.png")
        self.assertEqual(sh["conditioning_mode"], "first_only")
        self.assertEqual(sh["video"]["validity"], "stale")

    def test_last_only_still_blocked(self):
        st = _base()
        st["execution"]["mode"] = "running"
        sh = next(s for s in st["shots"] if s["id"] == "sh001")
        sh["start"]["status"] = "approved"
        sh["end"] = {"image": "keyframes/end.png", "status": "approved",
                     "validity": "current"}
        sh["conditioning_mode"] = "last_only"
        sh["props"] = []
        sh["location_id"] = ""
        ok, reason = can_submit_render(st, "sh001")
        self.assertFalse(ok)
        self.assertIn("last_only", reason)

    def test_restamp_identity_from_disk(self):
        st = _base()
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "keyframes"))
            path = os.path.join(tmp, "keyframes", "new.png")
            with open(path, "wb") as f:
                f.write(b"frame-bytes-v2")
            apply_shot_patch(st, "sh001", {
                "start": {"image": "keyframes/new.png"},
            }, project_dir=tmp)
        sh = next(s for s in st["shots"] if s["id"] == "sh001")
        self.assertTrue(sh["start"]["file_sha256"])
        self.assertNotEqual(sh["start"]["revision_id"], "start-v1")
        self.assertEqual(resource_version(st, "approve_start", "sh001"),
                         sh["start"]["revision_id"])


class FrontendRender(unittest.TestCase):
    def test_v2_paused_shows_shot_controls_and_new_frames(self):
        st = _base()
        out = _render(st)
        kf = out["keyframes"]
        self.assertIn("keyframes/kf_sh001_start.png", kf)
        self.assertIn('data-a="approve_start"', kf)
        self.assertIn('data-a="regenerate_start"', kf)
        self.assertIn("data-pick=", kf)
        self.assertIn("data-prompt=", kf)
        self.assertIn("data-remove=", kf)
        self.assertIn('data-a="regenerate_shot"', kf)
        self.assertNotIn('data-a="approve_keyframes"', kf)
        self.assertIn("旧场景卡（只读）", kf)
        self.assertIn("sc01", kf)

    def test_v1_keeps_scene_approval(self):
        st = _base()
        st["schema_version"] = 1
        kf = _render(st)["keyframes"]
        self.assertIn('data-a="approve_keyframes"', kf)
        self.assertIn("data-pick=", kf)
        self.assertIn("data-prompt=", kf)


class HttpContract(unittest.TestCase):
    def setUp(self):
        import server
        self.server = server
        self.tmp = tempfile.mkdtemp()
        self.proj = os.path.join(self.tmp, "t")
        os.makedirs(self.proj)
        st = _base()
        st["revision"] = 1
        with open(os.path.join(self.proj, "state.json"), "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False)
        open(os.path.join(self.proj, "events.jsonl"), "a").close()
        self._old = server.PROJECTS
        server.PROJECTS = self.tmp
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.server.PROJECTS = self._old
        shutil.rmtree(self.tmp)

    def req(self, method, tail, body=None):
        url = f"http://127.0.0.1:{self.port}/api/project/t/{tail}"
        data = None if body is None else json.dumps(body).encode()
        r = urllib.request.Request(url, data=data, method=method)
        r.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode())

    def test_patch_prompt_and_legacy_put_400(self):
        code, r = self.req("PATCH", "state", {
            "revision": 1,
            "shots": [{"id": "sh001", "start": {"prompt_zh": "页面保存"}}],
        })
        self.assertEqual(code, 200, r)
        st = json.load(open(os.path.join(self.proj, "state.json"), encoding="utf-8"))
        sh = next(s for s in st["shots"] if s["id"] == "sh001")
        self.assertEqual(sh["start"]["prompt_zh"], "页面保存")
        self.assertEqual(sh["end"]["status"], "removed")
        st["scenes"][0]["start"]["prompt_zh"] = "旧卡不能写"
        code, r = self.req("PUT", "state", st)
        self.assertEqual(code, 400)
        self.assertIn("legacy scenes", r.get("error", ""))

    def test_approve_old_frame_version_409(self):
        code, r = self.req("POST", "event", {
            "type": "approve_start", "target": "sh001",
            "target_revision": "start-v1", "frame": "start",
        })
        self.assertEqual(code, 200, r)
        with open(os.path.join(self.proj, "events.jsonl"), encoding="utf-8") as f:
            ev = json.loads(f.readlines()[-1])
        self.assertEqual(ev.get("frame"), "start")
        self.assertEqual(ev.get("target"), "sh001")
        code, r = self.req("PATCH", "state", {
            "revision": 1,
            "shots": [{"id": "sh001", "start": {
                "image": "keyframes/kf_sh001_start_v2.png",
                "revision_id": "start-v2", "file_sha256": "sha-v2",
            }}],
        })
        self.assertEqual(code, 200, r)
        code, r = self.req("POST", "event", {
            "type": "approve_start", "target": "sh001",
            "target_revision": "start-v1", "frame": "start",
        })
        self.assertEqual(code, 409, r)
        code, r = self.req("POST", "event", {
            "type": "approve_start", "target": "sh001",
            "target_revision": "start-v2", "frame": "start",
        })
        self.assertEqual(code, 200, r)

    def test_paused_blocks_regenerate_not_approve(self):
        code, r = self.req("POST", "event", {
            "type": "regenerate_start", "target": "sh001",
            "target_revision": "start-v1",
        })
        self.assertEqual(code, 423, r)
        code, r = self.req("POST", "event", {
            "type": "approve_end", "target": "sh001",
            "target_revision": resource_version(_base(), "approve_end", "sh001"),
        })
        self.assertEqual(code, 200, r)

    def test_patch_conflict_409(self):
        code, r = self.req("PATCH", "state", {
            "revision": 0,
            "shots": [{"id": "sh001", "start": {"prompt_zh": "冲突"}}],
        })
        self.assertEqual(code, 409, r)
        st = json.load(open(os.path.join(self.proj, "state.json"), encoding="utf-8"))
        sh = next(s for s in st["shots"] if s["id"] == "sh001")
        self.assertEqual(sh["start"]["prompt_zh"], "旧中文")


if __name__ == "__main__":
    unittest.main()
