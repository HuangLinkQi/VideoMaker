#!/usr/bin/env python3
"""复审 R01–R10 回归。"""
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
from planning import (  # noqa: E402
    apply_shot_patch, apply_utterance_patch, can_submit_render,
    propagate_put, recompute, resource_version, scenes_were_edited,
    stamp_media_identities,
)
from prompt_compiler import compile_shot  # noqa: E402
from schema import valid_state  # noqa: E402
from worker_schedule import actionable_events, finish_dispatch  # noqa: E402


def _base():
    st = {
        "name": "t", "schema_version": 2, "revision": 4, "stage": "keyframes",
        "execution": {"mode": "paused", "reason": "x"},
        "assets": [
            {"id": "linchuan", "type": "character", "status": "approved",
             "name": "林川", "revision_id": "lin-1", "image": "characters/linchuan.png"},
            {"id": "wangguifen", "type": "character", "status": "approved",
             "name": "王桂芬", "revision_id": "wg-1",
             "image": "characters/wangguifen.png",
             "voice": {"file": "characters/wangguifen_voice.wav", "status": "approved",
                       "revision_id": "voice-old", "file_sha256": "aaa"}},
            {"id": "loc_home", "type": "scene", "status": "approved",
             "image": "scenes/loc_home.png",
             "variants": [
                 {"id": "loc_home_night", "file": "scenes/loc_home.png", "status": "approved"},
                 {"id": "loc_home_morning", "file": "scenes/home_morning.png",
                  "status": "approved"},
             ]},
            {"id": "loc_office_night", "type": "scene", "status": "approved",
             "image": "scenes/loc_office_night.png"},
            {"id": "prop_phone_lin", "type": "prop", "status": "approved",
             "image": "props/prop_phone_lin.png", "revision_id": "phone-1"},
            {"id": "zhuguan", "type": "character", "status": "approved",
             "name": "主管", "revision_id": "zg-1"},
        ],
        "scenes": [{"id": "sc01", "title": "x", "duration": 8,
                    "dialogue": {"status": "approved", "lines": [{"text": "old"}]},
                    "start": {"image": "keyframes/kf_sc01_start.png", "status": "approved"},
                    "end": {"image": "keyframes/kf_sc01_end.png", "status": "approved"},
                    "video": {"status": "pending"}, "storyboard": {"status": "pending"}}],
        "jobs": [],
    }
    attach_to_state(st)
    sh = next(s for s in st["shots"] if s["id"] == "sh001")
    sh["video"] = {"file": "videos/sh001.mp4", "status": "approved", "validity": "current"}
    sh["validity"] = "current"
    sh["status"] = "approved"
    sh["start"] = {"image": "keyframes/kf_sc01_start.png", "status": "approved",
                   "validity": "current"}
    return st


class R01SamePathHash(unittest.TestCase):
    def test_same_filename_new_hash_stales_and_approve_conflicts(self):
        old = _base()
        apply_utterance_patch(old, "ut001", {
            "wav": "dialogues/ut001.wav", "measured_seconds": 2.0, "status": "approved",
        })
        new = json.loads(json.dumps(old))
        wg = next(a for a in new["assets"] if a["id"] == "wangguifen")
        wg["voice"]["file"] = "characters/wangguifen_voice.wav"
        wg["voice"]["file_sha256"] = "bbb"
        wg["voice"]["revision_id"] = "bbb"
        wg["voice"]["status"] = "review"
        propagate_put(old, new)
        ut = next(u for u in new["utterances"] if u["id"] == "ut001")
        self.assertIsNone(ut["wav"])
        self.assertEqual(ut["status"], "pending")
        sh = next(s for s in new["shots"] if s["id"] == "sh001")
        self.assertEqual(sh["video"]["validity"], "stale")
        self.assertEqual(resource_version(new, "approve_voice", "wangguifen"), "bbb")
        self.assertNotEqual(resource_version(old, "approve_voice", "wangguifen"),
                            resource_version(new, "approve_voice", "wangguifen"))


class R02ResultStalesVideo(unittest.TestCase):
    def test_new_wav_keeps_audio_stales_video(self):
        st = _base()
        apply_utterance_patch(st, "ut001", {
            "wav": "dialogues/ut001.wav", "measured_seconds": 1.8, "status": "approved",
        })
        sh = next(s for s in st["shots"] if s["id"] == "sh001")
        sh["video"] = {"file": "videos/sh001.mp4", "status": "approved", "validity": "current"}
        apply_utterance_patch(st, "ut001", {
            "wav": "dialogues/ut001-v2.wav", "measured_seconds": 2.05, "status": "approved",
        })
        ut = next(u for u in st["utterances"] if u["id"] == "ut001")
        self.assertEqual(ut["wav"], "dialogues/ut001-v2.wav")
        self.assertEqual(ut["status"], "approved")
        sh = next(s for s in st["shots"] if s["id"] == "sh001")
        self.assertEqual(sh["video"]["validity"], "stale")

    def test_same_result_replay_does_not_stale(self):
        st = _base()
        apply_utterance_patch(st, "ut001", {
            "wav": "dialogues/ut001.wav", "measured_seconds": 2.0, "status": "approved",
        })
        sh = next(s for s in st["shots"] if s["id"] == "sh001")
        sh["video"] = {"file": "videos/x.mp4", "status": "approved", "validity": "current"}
        sh["validity"] = "current"
        apply_utterance_patch(st, "ut001", {
            "wav": "dialogues/ut001.wav", "measured_seconds": 2.0, "status": "approved",
        })
        sh = next(s for s in st["shots"] if s["id"] == "sh001")
        self.assertEqual(sh["video"]["validity"], "current")


class R03PutContent(unittest.TestCase):
    def test_action_start_prop_stale_video(self):
        old = _base()
        for fields in (
            {"action": "new action"},
        ):
            new = json.loads(json.dumps(old))
            sh = next(s for s in new["shots"] if s["id"] == "sh001")
            sh.update(fields)
            propagate_put(old, new)
            sh = next(s for s in new["shots"] if s["id"] == "sh001")
            self.assertEqual(sh["video"]["validity"], "stale", fields)
        new = json.loads(json.dumps(old))
        next(s for s in new["shots"] if s["id"] == "sh001")["start"]["image"] = "keyframes/new.png"
        propagate_put(old, new)
        sh = next(s for s in new["shots"] if s["id"] == "sh001")
        self.assertEqual(sh["video"]["validity"], "stale")
        self.assertEqual(sh["start"]["validity"], "stale")
        new = json.loads(json.dumps(old))
        prop = next(a for a in new["assets"] if a["id"] == "prop_phone_lin")
        prop["image"] = "props/phone_v2.png"
        prop["revision_id"] = "phone-2"
        propagate_put(old, new)
        sh = next(s for s in new["shots"] if s["id"] == "sh001")
        self.assertEqual(sh["video"]["validity"], "stale")


class R04Gate(unittest.TestCase):
    def _ready(self):
        st = _base()
        st["execution"]["mode"] = "running"
        sh = next(s for s in st["shots"] if s["id"] == "sh001")
        sh["status"] = "approved"
        sh["validity"] = "current"
        sh["start"] = {"image": "kf.png", "status": "approved", "validity": "current"}
        sh["end"] = {"image": "kf2.png", "status": "approved", "validity": "current"}
        sh["conditioning_mode"] = "first_only"
        return st, sh

    def test_review_shot_blocked(self):
        st, sh = self._ready()
        sh["status"] = "review"
        ok, reason = can_submit_render(st, "sh001")
        self.assertFalse(ok)
        self.assertIn("review", reason)

    def test_last_only_blocked(self):
        st, sh = self._ready()
        sh["conditioning_mode"] = "last_only"
        ok, reason = can_submit_render(st, "sh001")
        self.assertFalse(ok)
        self.assertIn("last_only", reason)

    def test_first_last_end_stale_and_missing(self):
        st, sh = self._ready()
        sh["conditioning_mode"] = "first_last"
        sh["end"]["validity"] = "stale"
        ok, _ = can_submit_render(st, "sh001")
        self.assertFalse(ok)
        sh["end"] = {"image": "", "status": "pending"}
        ok, _ = can_submit_render(st, "sh001")
        self.assertFalse(ok)

    def test_pending_prop_blocked(self):
        st, sh = self._ready()
        prop = next(a for a in st["assets"] if a["id"] == "prop_phone_lin")
        prop["status"] = "pending"
        prop["image"] = ""
        ok, reason = can_submit_render(st, "sh001")
        self.assertFalse(ok)
        self.assertIn("prop", reason)

    def test_end_file_missing(self):
        tmp = tempfile.mkdtemp()
        try:
            st, sh = self._ready()
            sh["conditioning_mode"] = "first_last"
            os.makedirs(os.path.join(tmp, "characters"))
            open(os.path.join(tmp, "characters/char_linchuan_front.png"), "w").write("c")
            open(os.path.join(tmp, "kf.png"), "w").write("x")
            os.makedirs(os.path.join(tmp, "props"))
            open(os.path.join(tmp, "props/prop_phone_lin.png"), "w").write("p")
            os.makedirs(os.path.join(tmp, "scenes"))
            open(os.path.join(tmp, "scenes/loc_office_night.png"), "w").write("o")
            open(os.path.join(tmp, "scenes/loc_office_night_wide.png"), "w").write("w")
            ok, reason = can_submit_render(st, "sh001", project_dir=tmp)
            self.assertFalse(ok)
            self.assertIn("end", reason)
        finally:
            shutil.rmtree(tmp)


class R05NoWideRewrite(unittest.TestCase):
    def test_registered_morning_file_kept_and_hashed(self):
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "scenes")
            os.makedirs(path)
            payload = b"morning-plate"
            with open(os.path.join(path, "home_morning.png"), "wb") as f:
                f.write(payload)
            st = _base()
            out = compile_shot(st, "sh009", project_dir=tmp)
            loc = next(r for r in out["refs_passed"] if r["slot"] == "location")
            self.assertEqual(loc["file"], "scenes/home_morning.png")
            self.assertFalse(loc["file"].endswith("_wide.png"))
            import hashlib
            self.assertEqual(loc["sha256"], hashlib.sha256(payload).hexdigest())
            self.assertFalse(any("wide" in (b or "") for b in out["blocked"]))
        finally:
            shutil.rmtree(tmp)


class R06ApproveContract(unittest.TestCase):
    def test_storyboard_not_bound_to_start_end(self):
        st = _base()
        kf = resource_version(st, "approve_keyframes", "sc01")
        sb = resource_version(st, "approve_storyboard", "sc01")
        self.assertEqual(kf, "keyframes/kf_sc01_start.png|keyframes/kf_sc01_end.png")
        self.assertNotEqual(sb, kf)
        self.assertEqual(resource_version(st, "approve_video", "sc01"), "pending")
        self.assertEqual(resource_version(st, "approve_dialogue", "sc01"), "approved")


class R07ScenesReadonly(unittest.TestCase):
    def test_scene_text_change_detected(self):
        old = _base()
        new = json.loads(json.dumps(old))
        new["scenes"][0]["dialogue"]["lines"][0]["text"] = "new line"
        self.assertTrue(scenes_were_edited(old, new))
        self.assertFalse(scenes_were_edited(old, json.loads(json.dumps(old))))


class R08TimelineAndMembership(unittest.TestCase):
    def test_put_reflows_interval(self):
        old = _base()
        new = json.loads(json.dumps(old))
        sh = next(s for s in new["shots"] if s["id"] == "sh001")
        sh["target_frames"] = 168
        propagate_put(old, new)
        sh = next(s for s in new["shots"] if s["id"] == "sh001")
        self.assertEqual(sh["timeline_out_frame"] - sh["timeline_in_frame"], 168)
        self.assertTrue(valid_state(new))

    def test_sequence_mismatch_invalid(self):
        st = _base()
        sh = next(s for s in st["shots"] if s["id"] == "sh001")
        sh["sequence_id"] = "seq02"
        self.assertFalse(valid_state(st))


class R09SequenceOrigin(unittest.TestCase):
    def test_nonzero_sequence_origin_splits_wav(self):
        st = {
            "name": "t", "schema_version": 2, "revision": 1, "stage": "keyframes",
            "execution": {"mode": "paused", "reason": "x"},
            "assets": [], "scenes": [], "jobs": [],
            "sequences": [{"id": "seq02", "title": "x", "shot_ids": ["sh006", "sh007"],
                           "target_frames": 48, "timeline_in_frame": 504,
                           "timeline_out_frame": 552}],
            "shots": [
                {"id": "sh006", "sequence_id": "seq02", "target_frames": 24,
                 "timeline_in_frame": 504, "timeline_out_frame": 528,
                 "utterance_ids": ["ut001"], "validity": "current", "status": "draft",
                 "conditioning_mode": "first_only"},
                {"id": "sh007", "sequence_id": "seq02", "target_frames": 24,
                 "timeline_in_frame": 528, "timeline_out_frame": 552,
                 "utterance_ids": ["ut001"], "validity": "current", "status": "draft",
                 "conditioning_mode": "first_only"},
            ],
            "utterances": [{"id": "ut001", "text": "hi", "speaker": "linchuan",
                            "measured_seconds": 2.0, "status": "approved",
                            "sequence_in_sample": 0, "sequence_out_sample": 96000}],
        }
        r = recompute(st)
        seq = r["sequences"][0]
        self.assertEqual(seq["shots"][0]["speech_seconds"], 1.0)
        self.assertEqual(seq["shots"][1]["speech_seconds"], 1.0)
        self.assertEqual(seq["budget"]["speech_seconds"], 2.0)


class R10FailedDispatch(unittest.TestCase):
    def test_error_does_not_hold_event(self):
        held = set()
        still = {"A"}
        after_ok = finish_dispatch(held, ["A"], still, True)
        self.assertEqual(after_ok, {"A"})
        after_err = finish_dispatch(held, ["A"], still, False)
        self.assertEqual(after_err, set())
        ready = actionable_events([{"event_id": "A"}], after_err)
        self.assertEqual([e["event_id"] for e in ready], ["A"])


if __name__ == "__main__":
    unittest.main()
