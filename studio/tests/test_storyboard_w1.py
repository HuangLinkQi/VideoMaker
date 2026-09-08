#!/usr/bin/env python3
"""分镜改造 W1/W2 工程合同：迁移、人审、三模式、暂停、jobs 202。"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
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
from planning import location_ref_file  # noqa: E402
from prompt_compiler import compile_unit  # noqa: E402
from storyboard import (  # noqa: E402
    apply_edit, apply_review, build_view, migrate_apply, migrate_dry_run,
    plan_hash_shot, project_plan_hash, sequence_plan_hash,
)


def _f1():
    st = {
        "name": "f1", "schema_version": 2, "revision": 1, "stage": "keyframes",
        "execution": {"mode": "paused", "reason": "qa"},
        "capability": {"max_frames": 192, "max_seconds": 8, "fps": 24,
                       "modes_verified": ["first_only", "first_last"],
                       "last_only_verified": False, "simulate_jobs": True},
        "assets": [
            {"id": "linchuan", "type": "character", "status": "approved",
             "name": "林川", "image": "characters/linchuan.png"},
            {"id": "loc_office_night", "type": "scene", "status": "approved",
             "image": "scenes/loc_office_night.png",
             "ref_file": "scenes/loc_office_night_wide.png"},
        ],
        "scenes": [], "jobs": [], "utterances": [],
        "sequences": [{"id": "seq01", "title": "接听", "shot_ids": ["sh001"],
                       "target_frames": 144, "timeline_in_frame": 0,
                       "timeline_out_frame": 144}],
        "shots": [{
            "id": "sh001", "sequence_id": "seq01", "title": "办公室接听",
            "target_frames": 144, "timeline_in_frame": 0, "timeline_out_frame": 144,
            "action": "接起电话后维持持机", "entry_state": "手机在桌面",
            "exit_state": "持机贴耳", "shot_size": "中景",
            "camera": "固定中景起始，连续缓慢推近至中近景并停住",
            "location_id": "loc_office_night",
            "cast": [{"asset_id": "linchuan", "on_screen": True}],
            "props": [], "utterance_ids": [],
            "conditioning_mode": "first_only", "shooting_mode": "one_take",
            "validity": "current", "status": "draft",
            "start": {"image": "keyframes/start_v1.png", "status": "review",
                      "validity": "current", "revision_id": "s1"},
            "end": {"image": "keyframes/end_v1.png", "status": "removed",
                    "validity": "current"},
        }],
    }
    return st


class Migrate(unittest.TestCase):
    def test_dry_run_no_units_written_and_keeps_counts(self):
        from board_draft import attach_to_state
        st = {
            "name": "t", "schema_version": 2, "revision": 12, "stage": "keyframes",
            "execution": {"mode": "paused"}, "assets": [], "scenes": [], "jobs": [],
        }
        attach_to_state(st)
        report = migrate_dry_run(st)
        self.assertTrue(report["dry_run"])
        self.assertFalse(report["writes"])
        self.assertEqual(report["utterances"], len(st["utterances"]))
        self.assertEqual(report["shots"], 38)
        self.assertFalse(st.get("generation_units"))

    def test_apply_reviews_are_draft(self):
        st = _f1()
        migrate_apply(st)
        self.assertTrue(st["generation_units"])
        self.assertEqual(st["generation_units"][0]["shot_ids"], ["sh001"])
        self.assertEqual(st["shots"][0]["storyboard_review"]["status"], "draft")
        self.assertEqual(st["storyboard"]["status"], "draft")


class ReviewGate(unittest.TestCase):
    def test_shot_then_sequence_then_project(self):
        st = migrate_apply(_f1())
        h = plan_hash_shot(st, st["shots"][0])
        rec = apply_review(st, "shot", "sh001", "approved", h, source="human")
        self.assertEqual(rec["source"], "human")
        seq = st["sequences"][0]
        with self.assertRaises(PermissionError):
            apply_review(st, "project", "f1", "approved", project_plan_hash(st), source="human")
        apply_review(st, "sequence", "seq01", "approved", sequence_plan_hash(st, seq), source="human")
        apply_review(st, "project", "f1", "approved", project_plan_hash(st), source="human")
        self.assertEqual(st["storyboard"]["status"], "approved")

    def test_edit_then_restore_text_does_not_revive(self):
        st = migrate_apply(_f1())
        apply_review(st, "shot", "sh001", "approved",
                     plan_hash_shot(st, st["shots"][0]), source="human")
        from planning import apply_shot_patch
        apply_shot_patch(st, "sh001", {"blocking": "右侧"})
        from storyboard import invalidate_plan
        invalidate_plan(st, shot_ids=["sh001"], reason="blocking")
        apply_shot_patch(st, "sh001", {"blocking": ""})
        self.assertNotEqual((st["shots"][0].get("storyboard_review") or {}).get("status"), "approved")

    def test_missing_action_rejected(self):
        st = migrate_apply(_f1())
        st["shots"][0]["action"] = ""
        with self.assertRaises(PermissionError):
            apply_review(st, "shot", "sh001", "approved",
                         plan_hash_shot(st, st["shots"][0]), source="human")


class Structure(unittest.TestCase):
    def test_split_keeps_total_frames(self):
        st = migrate_apply(_f1())
        out = apply_edit(st, "split", {"shot_id": "sh001", "split_seconds": 3})
        self.assertEqual(sum(s["target_frames"] for s in st["shots"] if not s.get("removed")), 144)
        self.assertEqual(len(st["sequences"][0]["shot_ids"]), 2)
        self.assertTrue(out["created"])

    def test_nonadjacent_merge_rejected(self):
        st = migrate_apply(_f1())
        apply_edit(st, "add", {"sequence_id": "seq01", "title": "x"})
        apply_edit(st, "add", {"sequence_id": "seq01", "title": "y"})
        ids = st["sequences"][0]["shot_ids"]
        with self.assertRaises(ValueError):
            apply_edit(st, "merge", {"shot_ids": [ids[0], ids[2]]})


class Modes(unittest.TestCase):
    def test_exclusive_frame_slots(self):
        st = migrate_apply(_f1())
        uid = st["generation_units"][0]["id"]
        for mode, first, last in (("first_only", True, False),
                                  ("first_last", True, True),
                                  ("last_only", False, True)):
            st["generation_units"][0]["conditioning_mode"] = mode
            man = compile_unit(st, uid)
            self.assertEqual(man["has_first_frame"], first, mode)
            self.assertEqual(man["has_last_frame"], last, mode)
            self.assertFalse(man["omni_reference"])
            self.assertFalse(man["native_audio"])


class MorningGap(unittest.TestCase):
    def test_morning_sheet_is_gap(self):
        loc = {
            "id": "loc_home", "type": "scene", "status": "approved",
            "image": "scenes/loc_home.png",
            "ref_file": "scenes/home_night_wide.png",
            "variants": [
                {"id": "loc_home_morning", "file": "scenes/loc_home.png",
                 "status": "approved"},
            ],
        }
        res = location_ref_file(loc, "loc_home_morning")
        self.assertFalse(res["ok"])
        self.assertIn("morning", res["error"])


class Http(unittest.TestCase):
    def setUp(self):
        import server
        self.server = server
        self.tmp = tempfile.mkdtemp()
        self.proj = os.path.join(self.tmp, "f1")
        os.makedirs(self.proj)
        st = migrate_apply(_f1())
        st["revision"] = 1
        json.dump(st, open(os.path.join(self.proj, "state.json"), "w"), ensure_ascii=False)
        open(os.path.join(self.proj, "events.jsonl"), "a").close()
        self._old = server.PROJECTS
        server.PROJECTS = self.tmp
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.server.PROJECTS = self._old
        shutil.rmtree(self.tmp)

    def req(self, method, tail, body=None):
        url = f"http://127.0.0.1:{self.port}/api/project/f1/{tail}"
        data = None if body is None else json.dumps(body).encode()
        r = urllib.request.Request(url, data=data, method=method)
        r.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode())

    def test_get_storyboard_no_write(self):
        before = open(os.path.join(self.proj, "state.json"), "rb").read()
        code, r = self.req("GET", "storyboard")
        self.assertEqual(code, 200)
        self.assertIn("sequences", r)
        self.assertEqual(open(os.path.join(self.proj, "state.json"), "rb").read(), before)

    def test_forge_patch_rejected(self):
        code, r = self.req("PATCH", "state", {
            "revision": 1,
            "shots": [{"id": "sh001", "storyboard_review": {"status": "approved"}}],
        })
        self.assertEqual(code, 400)

    def test_review_and_paused_jobs(self):
        code, view = self.req("GET", "storyboard")
        sh = view["sequences"][0]["shots"][0]
        code, r = self.req("POST", "storyboard/review", {
            "revision": 1, "scope": "shot", "target": "sh001",
            "decision": "approved", "content_hash": sh["plan_hash"], "source": "human",
        })
        self.assertEqual(code, 200, r)
        code, r = self.req("POST", "jobs/submit", {
            "revision": 2, "unit_id": view["generation_units"][0]["id"],
        })
        self.assertEqual(code, 423)

    def test_jobs_202_when_running(self):
        st = json.load(open(os.path.join(self.proj, "state.json"), encoding="utf-8"))
        st["execution"]["mode"] = "running"
        apply_review(st, "shot", "sh001", "approved",
                     plan_hash_shot(st, st["shots"][0]), source="human")
        apply_review(st, "sequence", "seq01", "approved",
                     sequence_plan_hash(st, st["sequences"][0]), source="human")
        apply_review(st, "project", "f1", "approved",
                     project_plan_hash(st), source="human")
        st["revision"] = 1
        json.dump(st, open(os.path.join(self.proj, "state.json"), "w"), ensure_ascii=False)
        uid = st["generation_units"][0]["id"]
        code, r = self.req("POST", "jobs/submit", {"revision": 1, "unit_id": uid})
        self.assertEqual(code, 202, r)
        self.assertTrue(r.get("job_id"))
        self.assertNotEqual(r.get("error"), "render submit not enabled in this iteration")

    def test_standardize_paused_202(self):
        code, r = self.req("POST", "event", {
            "type": "standardize_storyboard", "target": "sh001",
            "target_revision": "x", "scope": "shot",
        })
        self.assertEqual(code, 202, r)

    def test_missing_revision_409(self):
        code, r = self.req("POST", "storyboard/edit", {"op": "add", "sequence_id": "seq01"})
        self.assertEqual(code, 409)

    def test_illegal_frames_400(self):
        code, r = self.req("PATCH", "state", {
            "revision": 1, "shots": [{"id": "sh001", "target_frames": -1}],
        })
        self.assertEqual(code, 400)

    def test_migrate_dry_run(self):
        code, r = self.req("POST", "storyboard/migrate", {"revision": 1, "dry_run": True})
        self.assertEqual(code, 200)
        self.assertTrue(r["dry_run"])
        self.assertEqual(r["shots"], 1)

    def test_prompt_result_missing_dialogue_400(self):
        st = json.load(open(os.path.join(self.proj, "state.json")))
        st["utterances"] = [{"id": "ut001", "text": "川川，还在加班吗", "speaker": "linchuan"}]
        st["shots"][0]["utterance_ids"] = ["ut001"]
        json.dump(st, open(os.path.join(self.proj, "state.json"), "w"))
        code, r = self.req("POST", "storyboard/prompt-result", {
            "revision": 1, "target": "sh001", "event_id": "e1",
            "input_hash": plan_hash_shot(st, st["shots"][0]),
            "skill": {"name": "minimax-h3-prompt-standardizer", "hash": "abc"},
            "prompt_zh": "一个人走路",
        })
        self.assertEqual(code, 400)


if __name__ == "__main__":
    unittest.main()
