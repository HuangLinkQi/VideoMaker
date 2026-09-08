"""state.json schema v2 合同与校验。未知字段放行。"""
from __future__ import annotations

import re

FPS = 24
TARGET_SECONDS = 189
TARGET_FRAMES = TARGET_SECONDS * FPS  # 4536
SCHEMA_VERSION = 2

STAGES = {"script", "assets", "keyframes", "videos", "done"}
STATUSES = {
    "pending", "generating", "review", "approved", "regenerate",
    "removed", "blocked",
}
ASSET_TYPES = {"character", "scene", "prop"}
EXEC_MODES = {"paused", "running"}
CONDITIONING = {"first_only", "first_last", "last_only"}
VALIDITY = {"current", "stale"}
JOB_STATUSES = {
    "queued", "claimed", "running", "done", "failed",
    "blocked", "recovery_required", "cancelled",
}

EVENT_TYPES = {  # type -> 是否必须带 target
    "script_confirmed": False,
    "approve_asset": True, "regenerate_asset": True, "remove_asset": True,
    "approve_keyframes": True, "approve_start": True, "approve_end": True,
    "regenerate_start": True, "regenerate_end": True, "regenerate_scene": True,
    "remove_start": True, "remove_end": True,
    "restore_start": True, "restore_end": True,
    "approve_storyboard": True, "regenerate_storyboard": True,
    "approve_video": True, "regenerate_video": True,
    "insert_scene": True,
    "approve_voice": True, "regenerate_voice": True,
    "approve_dialogue": True, "regenerate_dialogue": True,
    "approve_shot": True, "regenerate_shot": True,
    "standardize_storyboard": True,
}

GENERATE_EVENT_TYPES = {
    "script_confirmed", "regenerate_asset", "regenerate_start",
    "regenerate_end", "regenerate_scene", "regenerate_storyboard",
    "regenerate_video", "regenerate_voice", "regenerate_dialogue",
    "insert_scene", "regenerate_shot",
}

EMPTY_STATE = {
    "name": "",
    "schema_version": SCHEMA_VERSION,
    "revision": 0,
    "stage": "script",
    "execution": {"mode": "running", "reason": ""},
    "script_revision_id": "",
    "active_edit_revision_id": "",
    "assets": [],
    "scenes": [],
    "sequences": [],
    "shots": [],
    "utterances": [],
    "jobs": [],
    "generation_units": [],
    "reviews": [],
}


def _status_ok(node):
    if node is None:
        return True
    if not isinstance(node, dict):
        return False
    st = node.get("status")
    return st is None or st in STATUSES


def _is_int(n):
    return isinstance(n, int) and not isinstance(n, bool)


def state_errors(state):
    """v2 结构错误列表。允许草案超额，不允许负时长、重复 ID、悬空引用。"""
    errs = []
    if not isinstance(state, dict) or state.get("stage") not in STAGES:
        return ["stage"]
    ver = state.get("schema_version", 1)
    if ver not in (1, 2):
        return ["schema_version"]
    if ver != 2:
        return errs
    rev = state.get("revision")
    if not _is_int(rev) or rev < 0:
        errs.append("revision")
    exe = state.get("execution") or {}
    if not isinstance(exe, dict) or exe.get("mode") not in EXEC_MODES:
        errs.append("execution.mode")
    seqs, shots, uts = state.get("sequences") or [], state.get("shots") or [], state.get("utterances") or []
    seq_ids, shot_ids, ut_ids = [], [], []
    for seq in seqs:
        if not isinstance(seq, dict) or not re.fullmatch(r"seq\d+", str(seq.get("id", ""))):
            errs.append("sequence.id")
            continue
        if seq["id"] in seq_ids:
            errs.append(f"duplicate sequence {seq['id']}")
        seq_ids.append(seq["id"])
        tf = seq.get("target_frames")
        if tf is not None and (not _is_int(tf) or tf < 0):
            errs.append(f"{seq['id']} target_frames")
        tin, tout = seq.get("timeline_in_frame"), seq.get("timeline_out_frame")
        if tin is not None and tout is not None and tout < tin:
            errs.append(f"{seq['id']} reversed interval")
    for ut in uts:
        if not isinstance(ut, dict) or not re.fullmatch(r"ut\d+", str(ut.get("id", ""))):
            errs.append("utterance.id")
            continue
        if ut["id"] in ut_ids:
            errs.append(f"duplicate utterance {ut['id']}")
        ut_ids.append(ut["id"])
    ut_set = set(ut_ids)
    seq_set = set(seq_ids)
    for sh in shots:
        if not isinstance(sh, dict) or not re.fullmatch(r"sh\d+", str(sh.get("id", ""))):
            errs.append("shot.id")
            continue
        if sh["id"] in shot_ids:
            errs.append(f"duplicate shot {sh['id']}")
        shot_ids.append(sh["id"])
        cm = sh.get("conditioning_mode")
        if cm is not None and cm not in CONDITIONING:
            errs.append(f"{sh['id']} conditioning_mode")
        val = sh.get("validity")
        if val is not None and val not in VALIDITY:
            errs.append(f"{sh['id']} validity")
        tf = sh.get("target_frames")
        if tf is not None and (not _is_int(tf) or tf < 0):
            errs.append(f"{sh['id']} target_frames")
        tin, tout = sh.get("timeline_in_frame"), sh.get("timeline_out_frame")
        if tin is not None and tout is not None:
            if not _is_int(tin) or not _is_int(tout) or tout < tin:
                errs.append(f"{sh['id']} reversed interval")
            elif tf is not None and tout - tin != tf:
                errs.append(f"{sh['id']} interval length")
        sid = sh.get("sequence_id")
        if sid and sid not in seq_set:
            errs.append(f"{sh['id']} unknown sequence {sid}")
        for uid in sh.get("utterance_ids") or []:
            if uid not in ut_set:
                errs.append(f"{sh['id']} missing utterance {uid}")
    listed = []
    owner = {}
    for seq in seqs:
        for sid in seq.get("shot_ids") or []:
            if sid in listed:
                errs.append(f"duplicate shot membership {sid}")
            listed.append(sid)
            owner[sid] = seq.get("id")
            if sid not in set(shot_ids):
                errs.append(f"{seq.get('id')} missing shot {sid}")
    for sh in shots:
        sid = sh.get("id")
        if sid in owner and sh.get("sequence_id") and sh.get("sequence_id") != owner[sid]:
            errs.append(f"{sid} sequence mismatch")
    for job in state.get("jobs") or []:
        if not isinstance(job, dict) or job.get("status") not in JOB_STATUSES:
            errs.append("job.status")
    gu_ids = []
    for u in state.get("generation_units") or []:
        if not isinstance(u, dict) or not re.fullmatch(r"gu\d+", str(u.get("id", ""))):
            errs.append("generation_unit.id")
            continue
        if u["id"] in gu_ids:
            errs.append(f"duplicate unit {u['id']}")
        gu_ids.append(u["id"])
        cm = u.get("conditioning_mode")
        if cm is not None and cm not in CONDITIONING:
            errs.append(f"{u['id']} conditioning_mode")
        for sid in u.get("shot_ids") or []:
            if sid not in set(shot_ids):
                errs.append(f"{u.get('id')} missing shot {sid}")
    return errs


def valid_state(state):
    """宽松结构校验：枚举字段必须合法，未知字段放行。"""
    if not isinstance(state, dict) or state.get("stage") not in STAGES:
        return False
    ver = state.get("schema_version", 1)
    if ver not in (1, 2):
        return False
    if ver == 2:
        if state_errors(state):
            return False
    for sc in state.get("scenes") or []:
        if not isinstance(sc, dict) or not re.fullmatch(r"sc\d+", str(sc.get("id", ""))):
            return False
        for part in ("start", "end", "video", "storyboard", "dialogue"):
            if not _status_ok(sc.get(part)):
                return False
    for a in state.get("assets") or []:
        if not isinstance(a, dict):
            return False
        if a.get("status") is not None and a.get("status") not in STATUSES:
            return False
        if a.get("type") is not None and a.get("type") not in ASSET_TYPES:
            return False
        voice = a.get("voice")
        if isinstance(voice, dict) and voice.get("status") not in STATUSES:
            return False
    return True
