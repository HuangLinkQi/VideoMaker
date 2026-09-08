#!/usr/bin/env python3
"""时间区间、依赖失效、版本冲突、事件 ack/幂等、迁移。"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
STUDIO = os.path.dirname(HERE)
sys.path.insert(0, STUDIO)

from board_draft import attach_to_state  # noqa: E402
from events import ack_ids, append_event, new_event, pending  # noqa: E402
from jobs import claim, idempotency_key, leftover_generating_jobs, register  # noqa: E402
from migrate_state_v2 import build_v2, report_for  # noqa: E402
from planning import (  # noqa: E402
    apply_shot_patch, apply_utterance_patch, can_submit_render,
    countable_chars, estimate_line_seconds, interval_len, recompute, take_window,
)
from schema import TARGET_FRAMES, valid_state  # noqa: E402
from state_store import ConflictError, save_cas  # noqa: E402


class IntervalTests(unittest.TestCase):
    def test_half_open(self):
        self.assertEqual(interval_len(0, 144), 144)
        self.assertEqual(interval_len(144, 216), 72)

    def test_take_window_ok_and_short(self):
        ok = take_window(175, 168)
        self.assertTrue(ok["ok"])
        self.assertEqual(ok["source_out_frame"], 168)
        bad = take_window(141, 144)
        self.assertFalse(bad["ok"])


class SpeechTests(unittest.TestCase):
    def test_countable(self):
        self.assertEqual(countable_chars("川川，睡了吗？"), 5)
        self.assertEqual(countable_chars("sc01"), 4)

    def test_estimate(self):
        sec = estimate_line_seconds("川川，睡了吗？", cps=4, pause=0.8)
        self.assertAlmostEqual(sec, 5 / 4 + 0.8, places=3)


class StoreAndEventTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        st = {
            "name": "t", "schema_version": 2, "revision": 0, "stage": "script",
            "execution": {"mode": "paused", "reason": "test"},
            "assets": [], "scenes": [], "sequences": [], "shots": [],
            "utterances": [], "jobs": [],
        }
        with open(os.path.join(self.tmp, "state.json"), "w") as f:
            json.dump(st, f)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_cas_conflict(self):
        st = json.load(open(os.path.join(self.tmp, "state.json")))
        written = save_cas(self.tmp, st, expected_revision=0)
        self.assertEqual(written["revision"], 1)
        with self.assertRaises(ConflictError) as ctx:
            save_cas(self.tmp, written, expected_revision=0)
        self.assertEqual(ctx.exception.current_revision, 1)

    def test_ack_by_id_not_wipe(self):
        a = append_event(self.tmp, new_event("approve_asset", "linchuan"))
        b = append_event(self.tmp, new_event("approve_voice", "linchuan"))
        self.assertEqual(len(pending(self.tmp)), 2)
        ack_ids(self.tmp, [a["event_id"]])
        left = pending(self.tmp)
        self.assertEqual([e["event_id"] for e in left], [b["event_id"]])
        log = open(os.path.join(self.tmp, "events.jsonl")).read().strip().splitlines()
        self.assertEqual(len(log), 2)

    def test_job_idempotent_claim(self):
        key = idempotency_key("t", 1, "video", "abc")
        j = {"job_id": "job_a", "kind": "video", "target": "sh001",
             "status": "queued", "idempotency_key": key,
             "prompt_id": None, "claimed_by": "", "heartbeat_at": 0,
             "error": "", "outputs": [], "created_at": ""}
        first, created = register(self.tmp, j, expected_key=key)
        self.assertTrue(created)
        second, created2 = register(self.tmp, dict(j, job_id="job_b"), expected_key=key)
        self.assertFalse(created2)
        self.assertEqual(second["job_id"], "job_a")
        got, st = claim(self.tmp, "job_a", "w1")
        self.assertEqual(st, "ok")
        again, st2 = claim(self.tmp, "job_a", "w2")
        self.assertEqual(st2, "busy")


class PlanningPatchTests(unittest.TestCase):
    def setUp(self):
        self.state = {
            "name": "t", "schema_version": 2, "revision": 1, "stage": "keyframes",
            "execution": {"mode": "paused", "reason": "x"},
            "assets": [{"id": "linchuan", "type": "character", "status": "approved",
                        "name": "林川"}],
            "scenes": [], "jobs": [],
        }
        attach_to_state(self.state)

    def test_budget_189(self):
        r = recompute(self.state)
        self.assertEqual(r["planned_frames"], TARGET_FRAMES)
        self.assertEqual(r["planned_seconds"], 189.0)
        seq01 = next(s for s in r["sequences"] if s["id"] == "seq01")
        self.assertEqual(seq01["budget"]["planned_frames"], 504)
        seq02 = next(s for s in r["sequences"] if s["id"] == "seq02")
        self.assertEqual(seq02["budget"]["planned_frames"], 384)

    def test_duration_marks_stale(self):
        apply_shot_patch(self.state, "sh001", {"target_seconds": 5})
        sh = next(s for s in self.state["shots"] if s["id"] == "sh001")
        self.assertEqual(sh["target_frames"], 120)
        self.assertEqual(sh["validity"], "stale")
        self.assertEqual(sh["timeline_out_frame"] - sh["timeline_in_frame"], 120)

    def test_utterance_marks_stale(self):
        apply_utterance_patch(self.state, "ut001", {"text": "川川，还没睡吧？"})
        ut = next(u for u in self.state["utterances"] if u["id"] == "ut001")
        self.assertEqual(ut["status"], "pending")
        sh = next(s for s in self.state["shots"] if s["id"] == "sh001")
        self.assertEqual(sh["validity"], "stale")

    def test_submit_blocked_when_paused(self):
        ok, reason = can_submit_render(self.state, "sh001")
        self.assertFalse(ok)
        self.assertIn("paused", reason)


class MigrateTests(unittest.TestCase):
    def test_build_v2_keeps_removed_and_no_new_approved(self):
        old = {
            "name": "测试", "stage": "keyframes",
            "assets": [
                {"id": "linchuan", "type": "character", "status": "approved",
                 "name": "林川", "image": "characters/linchuan.png"},
                {"id": "prop_bus_sign", "type": "prop", "status": "removed",
                 "name": "站牌", "image": "props/prop_bus_sign.png"},
            ],
            "scenes": [
                {"id": "sc01", "title": "x", "duration": 8,
                 "start": {"status": "approved", "image": "keyframes/kf_sc01_start.png"},
                 "end": {"status": "approved"},
                 "video": {"status": "generating", "file": ""},
                 "storyboard": {"status": "pending"},
                 "dialogue": {"status": "pending", "lines": []}},
            ],
        }
        tmp = tempfile.mkdtemp()
        try:
            new = build_v2(old, tmp, "明天你生日", "freeze1")
            self.assertTrue(valid_state(new))
            self.assertEqual(new["execution"]["mode"], "paused")
            sign = next(a for a in new["assets"] if a["id"] == "prop_bus_sign")
            self.assertEqual(sign["status"], "removed")
            self.assertEqual(len(new["jobs"]), 1)
            self.assertEqual(new["jobs"][0]["status"], "recovery_required")
            self.assertIsNone(new["jobs"][0]["prompt_id"])
            for sh in new["shots"]:
                for key in ("start", "end", "video"):
                    self.assertNotEqual((sh.get(key) or {}).get("status"), "approved")
            self.assertEqual(new["scenes"][0]["video"]["status"], "blocked")
            rep = report_for(old, new, tmp)
            self.assertEqual(rep["illegal_approved_on_new_shots"], [])
            self.assertIn("prop_bus_sign", rep["removed_preserved"])
            leftover = leftover_generating_jobs(old["scenes"], "freeze1")
            self.assertEqual(len(leftover), 1)
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
