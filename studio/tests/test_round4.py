#!/usr/bin/env python3
"""第三轮复核 V301–V305。"""
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
    apply_shot_patch, apply_utterance_patch, carry_media_identities,
    location_ref_file, propagate_put, stamp_media_identities,
)
from prompt_compiler import compile_shot  # noqa: E402
from worker_schedule import filter_ready, record_attempt  # noqa: E402


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


class V302LocationPlate(unittest.TestCase):
    def test_office_uses_wide_not_sheet(self):
        st = _base()
        loc = next(a for a in st["assets"] if a["id"] == "loc_office_night")
        res = location_ref_file(loc, "loc_office_night_night")
        self.assertTrue(res["ok"])
        self.assertTrue(res["file"].endswith("_wide.png"))
        self.assertFalse(res["file"].endswith("loc_office_night.png"))
        out = compile_shot(st, "sh001")
        loc_r = next(r for r in out["refs_passed"] if r["slot"] == "location")
        self.assertEqual(loc_r["file"], "scenes/loc_office_night_wide.png")

    def test_morning_plate_kept(self):
        st = _base()
        loc = next(a for a in st["assets"] if a["id"] == "loc_home")
        res = location_ref_file(loc, "loc_home_morning")
        self.assertEqual(res["file"], "scenes/home_morning.png")


class V303Digest(unittest.TestCase):
    def test_variant_file_change_stales(self):
        old = _base()
        new = json.loads(json.dumps(old))
        loc = next(a for a in new["assets"] if a["id"] == "loc_home")
        night = next(v for v in loc["variants"] if v["id"] == "loc_home_night")
        night["file"] = "scenes/loc_home_night_v2.png"
        night["file_sha256"] = "night-new"
        propagate_put(old, new)
        sh = next(s for s in new["shots"] if s["id"] == "sh002")
        self.assertEqual(sh.get("validity"), "stale")

    def test_put_entry_stales_like_patch(self):
        old = _base()
        new = json.loads(json.dumps(old))
        next(s for s in new["shots"] if s["id"] == "sh001")["entry_state"] = "changed"
        propagate_put(old, new)
        self.assertEqual(next(s for s in new["shots"] if s["id"] == "sh001")["video"]["validity"],
                         "stale")
        st = _base()
        apply_shot_patch(st, "sh001", {"entry_state": "changed"})
        self.assertEqual(next(s for s in st["shots"] if s["id"] == "sh001")["video"]["validity"],
                         "stale")

    def test_put_audio_layout_stales_video(self):
        old = _base()
        apply_utterance_patch(old, "ut001", {
            "wav": "dialogues/ut001.wav", "measured_seconds": 2.0, "status": "approved",
        })
        sh = next(s for s in old["shots"] if s["id"] == "sh001")
        sh["video"] = {"file": "videos/x.mp4", "status": "approved", "validity": "current"}
        sh["validity"] = "current"
        new = json.loads(json.dumps(old))
        ut = next(u for u in new["utterances"] if u["id"] == "ut001")
        ut["sequence_in_sample"] = 0
        ut["sequence_out_sample"] = 96000
        propagate_put(old, new)
        ut = next(u for u in new["utterances"] if u["id"] == "ut001")
        self.assertEqual(ut["wav"], "dialogues/ut001.wav")
        sh = next(s for s in new["shots"] if s["id"] == "sh001")
        self.assertEqual(sh["video"]["validity"], "stale")

    def test_patch_start_hash_stales_video(self):
        st = _base()
        apply_shot_patch(st, "sh001", {
            "start": {"image": "keyframes/kf.png", "revision_id": "kf-v2",
                      "file_sha256": "newhash"},
        })
        sh = next(s for s in st["shots"] if s["id"] == "sh001")
        self.assertEqual(sh["start"]["revision_id"], "kf-v2")
        self.assertEqual(sh["video"]["validity"], "stale")


class V304IdentityInit(unittest.TestCase):
    def test_fill_hash_without_stale(self):
        old = _base()
        apply_utterance_patch(old, "ut001", {
            "wav": "dialogues/ut001.wav", "measured_seconds": 2.0, "status": "approved",
        })
        sh = next(s for s in old["shots"] if s["id"] == "sh001")
        sh["video"] = {"file": "videos/x.mp4", "status": "approved", "validity": "current"}
        sh["validity"] = "current"
        new = json.loads(json.dumps(old))
        wg = next(a for a in new["assets"] if a["id"] == "wangguifen")
        wg["voice"]["file_sha256"] = "init-hash"
        wg["voice"]["revision_id"] = "init-hash"
        stamped = json.loads(json.dumps(old))
        next(a for a in stamped["assets"] if a["id"] == "wangguifen")["voice"].update(
            {"file_sha256": "init-hash", "revision_id": "init-hash"})
        carry_media_identities(stamped, new)
        propagate_put(stamped, new)
        ut = next(u for u in new["utterances"] if u["id"] == "ut001")
        self.assertEqual(ut["wav"], "dialogues/ut001.wav")
        self.assertEqual(ut["status"], "approved")
        sh = next(s for s in new["shots"] if s["id"] == "sh001")
        self.assertEqual(sh["video"]["validity"], "current")


class V301VoicePath(unittest.TestCase):
    def test_new_path_is_replacement(self):
        old = _base()
        apply_utterance_patch(old, "ut001", {
            "wav": "dialogues/ut001.wav", "measured_seconds": 2.0, "status": "approved",
        })
        new = json.loads(json.dumps(old))
        wg = next(a for a in new["assets"] if a["id"] == "wangguifen")
        wg["voice"]["file"] = "characters/wangguifen_voice__abcd1234.wav"
        wg["voice"]["revision_id"] = "new"
        wg["voice"]["file_sha256"] = "new"
        propagate_put(old, new)
        ut = next(u for u in new["utterances"] if u["id"] == "ut001")
        self.assertIsNone(ut["wav"])


class V305Backoff(unittest.TestCase):
    def test_blocked_after_max_tries(self):
        attempts = {}
        now = 1000.0
        queue = [{"event_id": "A"}]
        for i in range(3):
            attempts = record_attempt(attempts, ["A"], now + i, False)
        self.assertTrue(attempts["A"]["blocked"])
        ready = filter_ready(queue, set(), attempts, now + 1000)
        self.assertEqual(ready, [])
        attempts2 = record_attempt({}, ["A"], now, False)
        self.assertFalse(attempts2["A"]["blocked"])
        self.assertGreater(attempts2["A"]["next_ok"], now)
        ready2 = filter_ready(queue, set(), attempts2, now)
        self.assertEqual(ready2, [])
        ready3 = filter_ready(queue, set(), attempts2, attempts2["A"]["next_ok"] + 0.1)
        self.assertEqual([e["event_id"] for e in ready3], ["A"])


if __name__ == "__main__":
    unittest.main()
