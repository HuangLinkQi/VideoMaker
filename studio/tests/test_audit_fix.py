#!/usr/bin/env python3
"""对照审核报告 F01–F12 的回归。"""
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
from jobs import find_by_key, idempotency_key, register, set_status  # noqa: E402
from migrate_state_v2 import MigrationConflict, apply, build_v2  # noqa: E402
from planning import (  # noqa: E402
    apply_shot_patch, apply_utterance_patch, can_submit_render,
    propagate_put, recompute, resource_version,
)
from prompt_compiler import compile_shot  # noqa: E402
from schema import valid_state  # noqa: E402
from worker_schedule import actionable_events, mark_dispatched, prune_dispatched  # noqa: E402


def _base():
    st = {
        "name": "t", "schema_version": 2, "revision": 1, "stage": "keyframes",
        "execution": {"mode": "paused", "reason": "x"},
        "assets": [
            {"id": "linchuan", "type": "character", "status": "approved",
             "name": "林川", "revision_id": "lin-1", "image": "characters/linchuan.png"},
            {"id": "wangguifen", "type": "character", "status": "approved",
             "name": "王桂芬", "revision_id": "wg-1",
             "image": "characters/wangguifen.png",
             "voice": {"file": "characters/wangguifen_voice.wav", "status": "approved",
                       "revision_id": "voice-old"}},
            {"id": "loc_home", "type": "scene", "status": "approved",
             "image": "scenes/loc_home.png",
             "variants": [
                 {"id": "loc_home_night", "tod": "night", "file": "scenes/loc_home.png",
                  "status": "approved"},
                 {"id": "loc_home_morning", "tod": "morning", "file": "",
                  "status": "pending"},
             ]},
            {"id": "loc_office_night", "type": "scene", "status": "approved",
             "image": "scenes/loc_office_night.png"},
            {"id": "prop_phone_lin", "type": "prop", "status": "approved",
             "image": "props/prop_phone_lin.png"},
            {"id": "zhuguan", "type": "character", "status": "approved",
             "name": "主管", "revision_id": "zg-1", "image": "characters/zhuguan.png"},
        ],
        "scenes": [], "jobs": [],
    }
    attach_to_state(st)
    return st


class F01ResultAndReview(unittest.TestCase):
    def test_wav_result_not_cleared(self):
        st = _base()
        apply_utterance_patch(st, "ut001", {
            "wav": "dialogues/ut001-v2.wav", "measured_seconds": 2.05, "status": "approved",
        })
        ut = next(u for u in st["utterances"] if u["id"] == "ut001")
        self.assertEqual(ut["wav"], "dialogues/ut001-v2.wav")
        self.assertEqual(ut["measured_seconds"], 2.05)
        self.assertEqual(ut["status"], "approved")

    def test_review_shot_not_stale(self):
        st = _base()
        sh = next(s for s in st["shots"] if s["id"] == "sh001")
        sh["validity"] = "current"
        apply_shot_patch(st, "sh001", {"status": "approved", "validity": "current"})
        sh = next(s for s in st["shots"] if s["id"] == "sh001")
        self.assertEqual(sh["status"], "approved")
        self.assertEqual(sh["validity"], "current")

    def test_text_change_then_clears_wav(self):
        st = _base()
        apply_utterance_patch(st, "ut001", {
            "wav": "dialogues/ut001-v2.wav", "measured_seconds": 2.05, "status": "approved",
        })
        apply_utterance_patch(st, "ut001", {"text": "川川，还没睡吧？"})
        ut = next(u for u in st["utterances"] if u["id"] == "ut001")
        self.assertIsNone(ut["wav"])
        self.assertEqual(ut["status"], "pending")


class F02PutPropagation(unittest.TestCase):
    def test_voice_change_stales_dialogue(self):
        old = _base()
        apply_utterance_patch(old, "ut001", {
            "wav": "dialogues/ut001.wav", "measured_seconds": 2.0, "status": "approved",
        })
        sh = next(s for s in old["shots"] if s["id"] == "sh001")
        sh["video"] = {"file": "videos/sh001.mp4", "status": "approved", "validity": "current"}
        sh["validity"] = "current"
        new = json.loads(json.dumps(old))
        wg = next(a for a in new["assets"] if a["id"] == "wangguifen")
        wg["voice"]["file"] = "characters/wangguifen_voice_v2.wav"
        wg["voice"]["status"] = "review"
        wg["voice"]["revision_id"] = "voice-new"
        propagate_put(old, new)
        ut = next(u for u in new["utterances"] if u["id"] == "ut001")
        self.assertIsNone(ut["wav"])
        self.assertEqual(ut["status"], "pending")
        sh = next(s for s in new["shots"] if s["id"] == "sh001")
        self.assertEqual(sh["validity"], "stale")
        self.assertEqual(sh["video"]["validity"], "stale")


class F03ResourceVersion(unittest.TestCase):
    def test_approve_uses_asset_revision_not_state(self):
        st = _base()
        st["revision"] = 4
        self.assertEqual(resource_version(st, "approve_asset", "wangguifen"), "wg-1")
        self.assertEqual(resource_version(st, "approve_voice", "wangguifen"), "voice-old")
        self.assertNotEqual(resource_version(st, "approve_asset", "wangguifen"), 4)


class F04LocationVariant(unittest.TestCase):
    def test_morning_does_not_fallback_to_night(self):
        st = _base()
        night = compile_shot(st, "sh002")
        loc_n = next(r for r in night["refs_passed"] if r["slot"] == "location")
        self.assertIn("loc_home", loc_n["file"])
        morning = compile_shot(st, "sh009")
        loc_m = [r for r in morning["refs_passed"] if r["slot"] == "location"]
        self.assertEqual(loc_m, [])
        self.assertTrue(any("loc_home_morning" in w for w in morning["warnings"] + morning["blocked"]))


class F05SubmitGate(unittest.TestCase):
    def test_draft_and_missing_frame_rejected_when_running(self):
        st = _base()
        st["execution"]["mode"] = "running"
        ok, reason = can_submit_render(st, "sh001")
        self.assertFalse(ok)
        self.assertIn("draft", reason)
        sh = next(s for s in st["shots"] if s["id"] == "sh001")
        sh["status"] = "approved"
        sh["validity"] = "current"
        sh["start"] = {"image": "keyframes/kf.png", "status": "review", "validity": "stale"}
        ok, reason = can_submit_render(st, "sh001")
        self.assertFalse(ok)
        sh["start"] = {"image": "", "status": "pending"}
        ok, reason = can_submit_render(st, "sh009")
        self.assertFalse(ok)


class F06MigrateCas(unittest.TestCase):
    def test_refuse_stale_candidate_and_second_apply(self):
        tmp = tempfile.mkdtemp()
        try:
            old = {
                "name": "t", "stage": "keyframes", "schema_version": 2, "revision": 4,
                "execution": {"mode": "paused", "reason": "x"},
                "assets": [{"id": "linchuan", "type": "character", "status": "approved",
                            "name": "林川"}],
                "scenes": [], "jobs": [],
            }
            attach_to_state(old)
            with open(os.path.join(tmp, "state.json"), "w") as f:
                json.dump(old, f)
            cand = build_v2(old, tmp, "script", "f")
            old["revision"] = 5
            old["review_mark"] = "keep-me"
            with open(os.path.join(tmp, "state.json"), "w") as f:
                json.dump(old, f)
            with self.assertRaises(MigrationConflict):
                apply(tmp, cand, tmp, expected_revision=4)
            st = json.load(open(os.path.join(tmp, "state.json")))
            self.assertEqual(st["revision"], 5)
            self.assertEqual(st.get("review_mark"), "keep-me")
        finally:
            shutil.rmtree(tmp)


class F07Schedule(unittest.TestCase):
    def test_startup_pending_is_actionable(self):
        queue = [{"event_id": "A", "type": "approve_voice"}]
        self.assertEqual([e["event_id"] for e in actionable_events(queue, set())], ["A"])

    def test_new_event_during_processing_not_swallowed(self):
        dispatched = mark_dispatched(set(), ["A"])
        queue = [{"event_id": "A"}, {"event_id": "B"}]
        ready = actionable_events(queue, dispatched)
        self.assertEqual([e["event_id"] for e in ready], ["B"])
        still = {"A", "B"}
        held = prune_dispatched(dispatched, still)
        self.assertEqual(held, {"A"})
        ready2 = actionable_events(queue, held)
        self.assertEqual([e["event_id"] for e in ready2], ["B"])


class F08PlanningStored(unittest.TestCase):
    def test_duration_updates_state_planning_and_head37(self):
        st = _base()
        apply_shot_patch(st, "sh001", {"target_seconds": 7})
        self.assertEqual(st["planning"]["planned_seconds"], 190.0)
        seq01 = next(s for s in st["sequences"] if s["id"] == "seq01")
        self.assertEqual(seq01["budget"]["planned_seconds"], 22.0)
        self.assertEqual(st["planning"]["head37_frames"], 22 * 24 + 16 * 24)


class F09PropFillsSlot(unittest.TestCase):
    def test_one_person_location_prop_all_passed(self):
        st = _base()
        out = compile_shot(st, "sh001")
        ids = [r["asset_id"] for r in out["refs_passed"]]
        self.assertIn("linchuan", ids)
        self.assertIn("loc_office_night", ids)
        self.assertIn("prop_phone_lin", ids)
        self.assertEqual(out["refs_overflow"], [])
        self.assertFalse(any("最多" in w for w in out["warnings"]))

    def test_two_person_plus_location_plus_prop_overflows_prop(self):
        st = _base()
        out = compile_shot(st, "sh006")
        self.assertEqual(len(out["refs_passed"]), 3)
        self.assertTrue(out["refs_overflow"])
        self.assertTrue(any("最多" in w for w in out["warnings"]))


class F10ReplayDone(unittest.TestCase):
    def test_done_job_replays(self):
        tmp = tempfile.mkdtemp()
        try:
            key = idempotency_key("t", 1, "video", "abc")
            j = {"job_id": "job_a", "kind": "video", "target": "sh001",
                 "status": "queued", "idempotency_key": key,
                 "prompt_id": None, "claimed_by": "", "heartbeat_at": 0,
                 "error": "", "outputs": [], "created_at": ""}
            register(tmp, j, expected_key=key)
            set_status(tmp, "job_a", "done", outputs=["videos/a.mp4"])
            b = dict(j, job_id="job_b")
            got, created = register(tmp, b, expected_key=key)
            self.assertFalse(created)
            self.assertEqual(got["job_id"], "job_a")
            self.assertEqual(got["outputs"], ["videos/a.mp4"])
        finally:
            shutil.rmtree(tmp)


class F11Schema(unittest.TestCase):
    def test_negative_frames_and_missing_utterance(self):
        st = _base()
        self.assertTrue(valid_state(st))
        with self.assertRaises(ValueError):
            apply_shot_patch(st, "sh001", {"target_frames": -24})
        st2 = _base()
        sh = next(s for s in st2["shots"] if s["id"] == "sh001")
        sh["utterance_ids"] = ["ut_missing"]
        self.assertFalse(valid_state(st2))


class F12SharedUtterance(unittest.TestCase):
    def test_unique_play_on_sequence(self):
        st = {
            "name": "t", "schema_version": 2, "revision": 1, "stage": "keyframes",
            "execution": {"mode": "paused", "reason": "x"},
            "assets": [], "scenes": [], "jobs": [],
            "sequences": [{"id": "seq01", "title": "x", "shot_ids": ["sh001", "sh002"],
                           "target_frames": 48}],
            "shots": [
                {"id": "sh001", "sequence_id": "seq01", "target_frames": 24,
                 "timeline_in_frame": 0, "timeline_out_frame": 24,
                 "utterance_ids": ["ut001"], "validity": "current", "status": "draft",
                 "conditioning_mode": "first_only"},
                {"id": "sh002", "sequence_id": "seq01", "target_frames": 24,
                 "timeline_in_frame": 24, "timeline_out_frame": 48,
                 "utterance_ids": ["ut001"], "validity": "current", "status": "draft",
                 "conditioning_mode": "first_only"},
            ],
            "utterances": [{"id": "ut001", "text": "hi", "speaker": "linchuan",
                            "measured_seconds": 2.0, "status": "approved"}],
        }
        r = recompute(st)
        seq = r["sequences"][0]
        self.assertEqual(seq["budget"]["speech_seconds"], 2.0)
        self.assertTrue(seq["budget"]["speech_measured"])
        self.assertEqual(seq["shots"][0]["speech_seconds"], 2.0)
        self.assertEqual(seq["shots"][1]["speech_seconds"], 0.0)


if __name__ == "__main__":
    unittest.main()
