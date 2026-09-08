"""分镜拍摄方案：生成单元、三级人审、结构编辑、视图。GET 无写入副作用。"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import time
import uuid

from planning import (
    FPS, _by_id, _reflow_timelines, character_ref_file, file_sha256,
    location_ref_file, mark_stale, prop_ref_file, seconds_to_frames, store_planning,
)


REVIEW_STATUSES = {"draft", "review", "approved", "changes_requested"}
SHOOTING_MODES = {"one_take", "cut"}
SCOPES = {"shot", "sequence", "project"}
DECISIONS = {"approved", "changes_requested"}
EDIT_OPS = {"split", "merge", "reorder", "add", "remove", "restore"}
DEFAULT_CAPABILITY = {
    "max_frames": 192,
    "max_seconds": 8,
    "fps": FPS,
    "modes_verified": ["first_only", "first_last"],
    "last_only_verified": False,
    "simulate_jobs": False,
}


def _dump(x):
    return json.dumps(x, sort_keys=True, ensure_ascii=False, default=str)


def content_hash(obj):
    return hashlib.sha256(_dump(obj).encode("utf-8")).hexdigest()


def empty_review():
    return {"status": "draft", "validity": "current", "content_hash": "",
            "ts": "", "source": "", "note": ""}


def next_numeric_id(prefix, existing):
    n = 1
    have = set(existing)
    while f"{prefix}{n:03d}" in have:
        n += 1
    return f"{prefix}{n:03d}"


def capability(state):
    cap = dict(DEFAULT_CAPABILITY)
    cap.update(state.get("capability") or {})
    return cap


def shot_plan_fields(sh):
    return {
        "id": sh.get("id"),
        "title": sh.get("title"),
        "action": sh.get("action"),
        "entry_state": sh.get("entry_state"),
        "exit_state": sh.get("exit_state"),
        "shot_size": sh.get("shot_size"),
        "camera": sh.get("camera"),
        "cut_reason": sh.get("cut_reason"),
        "blocking": sh.get("blocking"),
        "facing": sh.get("facing"),
        "expression": sh.get("expression"),
        "cast": sh.get("cast"),
        "props": sh.get("props"),
        "location_id": sh.get("location_id"),
        "location_variant_id": sh.get("location_variant_id"),
        "era_id": sh.get("era_id"),
        "target_frames": sh.get("target_frames"),
        "utterance_ids": sh.get("utterance_ids"),
        "utterance_visible": sh.get("utterance_visible"),
        "unit_id": sh.get("unit_id"),
        "shooting_mode": sh.get("shooting_mode"),
        "conditioning_mode": sh.get("conditioning_mode"),
    }


def unit_plan_fields(u):
    return {
        "id": u.get("id"),
        "shot_ids": u.get("shot_ids"),
        "conditioning_mode": u.get("conditioning_mode"),
        "target_frames": u.get("target_frames"),
        "internal_cuts": u.get("internal_cuts"),
        "prompt_revision_id": ((u.get("prompt") or {}).get("revision_id")),
        "start_ident": (u.get("start") or {}).get("revision_id") or (u.get("start") or {}).get("image"),
        "end_ident": (u.get("end") or {}).get("revision_id") or (u.get("end") or {}).get("image"),
    }


def computed_units(state):
    """内存中的 1:1 单元，不写盘。"""
    existing = list(state.get("generation_units") or [])
    by_shot = {}
    for u in existing:
        for sid in u.get("shot_ids") or []:
            by_shot[sid] = u
    out = list(existing)
    used = {u.get("id") for u in out if u.get("id")}
    for sh in state.get("shots") or []:
        sid = sh.get("id")
        if not sid or sid in by_shot:
            continue
        uid = sh.get("unit_id") or ("gu" + re.sub(r"^sh", "", sid))
        if uid in used:
            uid = next_numeric_id("gu", used)
        unit = {
            "id": uid,
            "shot_ids": [sid],
            "conditioning_mode": sh.get("conditioning_mode") or "first_only",
            "target_frames": sh.get("target_frames") or 0,
            "requested_frames": None,
            "internal_cuts": [],
            "start": copy.deepcopy(sh.get("start") or {}),
            "end": copy.deepcopy(sh.get("end") or {}),
            "prompt": copy.deepcopy(sh.get("prompt_pack") or {"status": "draft"}),
        }
        out.append(unit)
        used.add(uid)
        by_shot[sid] = unit
    return out


def ensure_units(state, persist=True):
    units = computed_units(state)
    if persist:
        state["generation_units"] = units
        umap = {u["id"]: u for u in units if u.get("id")}
        for sh in state.get("shots") or []:
            uid = next((u["id"] for u in units if sh.get("id") in (u.get("shot_ids") or [])), None)
            if uid:
                sh["unit_id"] = uid
                u = umap.get(uid) or {}
                sh["conditioning_mode"] = u.get("conditioning_mode") or sh.get("conditioning_mode")
    return units


def project_units(state, units=None):
    units = units if units is not None else computed_units(state)
    umap = {u["id"]: u for u in units if u.get("id")}
    for sh in state.get("shots") or []:
        u = umap.get(sh.get("unit_id") or "")
        if not u:
            continue
        sh["conditioning_mode"] = u.get("conditioning_mode") or sh.get("conditioning_mode")
        if len(u.get("shot_ids") or []) == 1:
            sh["start"] = copy.deepcopy(u.get("start") or sh.get("start") or {})
            sh["end"] = copy.deepcopy(u.get("end") or sh.get("end") or {})
    return state


def migrate_dry_run(state):
    shots = state.get("shots") or []
    units = computed_units(state)
    uts = state.get("utterances") or []
    start_n = sum(1 for sh in shots if (sh.get("start") or {}).get("image"))
    video_n = sum(1 for sh in shots if (sh.get("video") or {}).get("file"))
    return {
        "dry_run": True,
        "schema_version": state.get("schema_version"),
        "revision": state.get("revision"),
        "execution": (state.get("execution") or {}).get("mode"),
        "sequences": len(state.get("sequences") or []),
        "shots": len(shots),
        "utterances": len(uts),
        "utterance_ids": [u.get("id") for u in uts],
        "generation_units": len(units),
        "unit_map": [{"shot_id": sh.get("id"), "unit_id": next(
            (u["id"] for u in units if sh.get("id") in (u.get("shot_ids") or [])), None)}
            for sh in shots],
        "start_image_paths": start_n,
        "video_paths": video_n,
        "reviews_after": "draft",
        "writes": False,
        "gaps": [g for g in (
            "removed 尾帧保持原状，不自动恢复",
            "first_only 保持原状，待用户核对",
            "新增三级审核全部 draft，不继承旧分镜图批准",
        )],
    }


def migrate_apply(state):
    ensure_units(state, persist=True)
    if "reviews" not in state or not isinstance(state.get("reviews"), list):
        state["reviews"] = []
    sb = state.get("storyboard")
    if not isinstance(sb, dict):
        sb = empty_review()
        state["storyboard"] = sb
    if sb.get("status") not in REVIEW_STATUSES:
        sb.update(empty_review())
    for seq in state.get("sequences") or []:
        rv = seq.get("storyboard_review")
        if not isinstance(rv, dict) or rv.get("status") not in REVIEW_STATUSES:
            seq["storyboard_review"] = empty_review()
        if not seq.get("shooting_mode"):
            seq["shooting_mode"] = "cut" if len(seq.get("shot_ids") or []) > 1 else "one_take"
            seq["shooting_mode_confirmed"] = False
    for sh in state.get("shots") or []:
        rv = sh.get("storyboard_review")
        if not isinstance(rv, dict) or rv.get("status") not in REVIEW_STATUSES:
            sh["storyboard_review"] = empty_review()
        if not sh.get("shooting_mode"):
            sh["shooting_mode"] = "one_take"
    if not state.get("capability"):
        state["capability"] = dict(DEFAULT_CAPABILITY)
    store_planning(state)
    return state


def _set_review(node, status="draft", validity="stale", reason=""):
    rv = node.get("storyboard_review") if "storyboard_review" in node or node.get("id") else node
    if node is not rv and "status" in (node.get("storyboard") or {}):
        rv = node["storyboard"]
    if not isinstance(rv, dict):
        rv = empty_review()
    rv["status"] = status
    rv["validity"] = validity
    if reason:
        rv["stale_reason"] = reason
    return rv


def invalidate_plan(state, shot_ids=None, reason="plan change", also_project=True):
    shot_ids = set(shot_ids or [])
    units = _by_id(state.get("generation_units") or computed_units(state))
    seq_hit = set()
    for sh in state.get("shots") or []:
        if shot_ids and sh.get("id") not in shot_ids:
            continue
        sh["storyboard_review"] = _set_review(
            {"storyboard_review": sh.get("storyboard_review") or empty_review()},
            "draft", "stale", reason) if False else dict(sh.get("storyboard_review") or empty_review())
        sh["storyboard_review"]["status"] = "draft"
        sh["storyboard_review"]["validity"] = "stale"
        sh["storyboard_review"]["stale_reason"] = reason
        uid = sh.get("unit_id")
        u = units.get(uid) if uid else None
        if u:
            prompt = u.get("prompt") or {}
            if prompt.get("status") in ("approved", "review"):
                prompt["status"] = "draft"
                prompt["validity"] = "stale"
                u["prompt"] = prompt
        for seq in state.get("sequences") or []:
            if sh.get("id") in (seq.get("shot_ids") or []):
                seq_hit.add(seq.get("id"))
    for seq in state.get("sequences") or []:
        if seq.get("id") in seq_hit:
            seq["storyboard_review"] = dict(seq.get("storyboard_review") or empty_review())
            seq["storyboard_review"]["status"] = "draft"
            seq["storyboard_review"]["validity"] = "stale"
            seq["storyboard_review"]["stale_reason"] = reason
    if also_project:
        sb = dict(state.get("storyboard") or empty_review())
        sb["status"] = "draft"
        sb["validity"] = "stale"
        sb["stale_reason"] = reason
        state["storyboard"] = sb
    mark_stale(state, reason, shot_ids=list(shot_ids) if shot_ids else [
        sh.get("id") for sh in state.get("shots") or []], nodes=("video", "take", "start", "end"))
    return state


def plan_hash_shot(state, sh):
    return content_hash(shot_plan_fields(sh))


def sequence_plan_hash(state, seq):
    smap = _by_id(state.get("shots"))
    return content_hash({
        "id": seq.get("id"), "shot_ids": seq.get("shot_ids"),
        "shooting_mode": seq.get("shooting_mode"),
        "target_frames": seq.get("target_frames"),
        "shots": [plan_hash_shot(state, smap.get(i) or {})
                  for i in (seq.get("shot_ids") or [])],
    })


def project_plan_hash(state):
    return content_hash({
        "sequences": [s.get("id") for s in (state.get("sequences") or [])],
        "target_frames": (state.get("planning") or {}).get("target_frames"),
        "seq_reviews": [(s.get("id"), (s.get("storyboard_review") or {}).get("status"))
                        for s in (state.get("sequences") or [])],
    })


def current_review_record(state, scope, target):
    for r in reversed(state.get("reviews") or []):
        if r.get("scope") == scope and r.get("target") == target and r.get("validity") == "current":
            return r
    return None


def _shot_complete(sh):
    if not sh:
        return False, "shot not found"
    missing = [k for k in ("action", "entry_state", "exit_state") if not (sh.get(k) or "").strip()]
    if missing:
        return False, f"shot {sh.get('id')} missing {','.join(missing)}"
    return True, ""


def _child_reviews_ok(state, scope, target):
    if scope == "sequence":
        seq = next((s for s in (state.get("sequences") or []) if s.get("id") == target), None)
        if not seq:
            return False, "sequence not found"
        for sid in seq.get("shot_ids") or []:
            sh = _by_id(state.get("shots")).get(sid) or {}
            rv = sh.get("storyboard_review") or {}
            if rv.get("status") != "approved" or rv.get("validity") == "stale":
                return False, f"shot {sid} not approved/current"
        return True, ""
    if scope == "project":
        for seq in state.get("sequences") or []:
            rv = seq.get("storyboard_review") or {}
            if rv.get("status") != "approved" or rv.get("validity") == "stale":
                return False, f"sequence {seq.get('id')} not approved/current"
        return True, ""
    return False, "bad scope"


def apply_review(state, scope, target, decision, content_hash_client, note="",
                 source="human", request_id=None):
    if scope not in SCOPES:
        raise ValueError("illegal scope")
    if decision not in DECISIONS:
        raise ValueError("illegal decision")
    if not content_hash_client:
        raise ValueError("content_hash required")
    if source != "human":
        raise ValueError("review source must be human")
    if scope == "shot":
        node = _by_id(state.get("shots")).get(target)
        if not node:
            raise KeyError(target)
        current = plan_hash_shot(state, node)
        holder = "storyboard_review"
    elif scope == "sequence":
        node = next((s for s in (state.get("sequences") or []) if s.get("id") == target), None)
        if not node:
            raise KeyError(target)
        current = sequence_plan_hash(state, node)
        holder = "storyboard_review"
    else:
        node = state
        current = project_plan_hash(state)
        holder = "storyboard"
        target = state.get("name") or "project"
    if str(content_hash_client) != str(current):
        raise PermissionError("content_hash mismatch")
    if decision == "approved":
        if scope == "shot":
            ok, reason = _shot_complete(node)
            if not ok:
                raise PermissionError(reason)
        elif scope in ("sequence", "project"):
            ok, reason = _child_reviews_ok(state, scope, target if scope != "project" else "")
            if not ok:
                raise PermissionError(reason)
    rec = {
        "review_id": "rv_" + uuid.uuid4().hex[:12],
        "scope": scope,
        "target": target,
        "decision": decision,
        "content_hash": current,
        "revision": state.get("revision"),
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": "human",
        "note": note or "",
        "validity": "current",
        "request_id": request_id or "",
    }
    for old in state.setdefault("reviews", []):
        if old.get("scope") == scope and old.get("target") == target and old.get("validity") == "current":
            old["validity"] = "stale"
    state["reviews"].append(rec)
    slot = empty_review()
    slot["status"] = "approved" if decision == "approved" else "changes_requested"
    slot["validity"] = "current"
    slot["content_hash"] = current
    slot["ts"] = rec["ts"]
    slot["source"] = "human"
    slot["note"] = note or ""
    if holder == "storyboard":
        state["storyboard"] = slot
    else:
        node[holder] = slot
    return rec


def reject_forged_review_fields(fields):
    rv = fields.get("storyboard_review")
    if isinstance(rv, dict) and rv.get("status") in ("approved", "changes_requested"):
        raise ValueError("storyboard_review can only be set via /storyboard/review")
    if fields.get("status") == "approved" and "storyboard_review" in fields:
        raise ValueError("storyboard_review can only be set via /storyboard/review")


def apply_unit_patch(state, unit_id, fields, reason="unit edit"):
    ensure_units(state, persist=True)
    units = _by_id(state.get("generation_units"))
    u = units.get(unit_id)
    if not u:
        raise KeyError(unit_id)
    fields = dict(fields)
    old_mode = u.get("conditioning_mode")
    affected = list(u.get("shot_ids") or [])
    for k, v in fields.items():
        if k in ("id",):
            continue
        if k in ("start", "end") and isinstance(v, dict):
            merged = dict(u.get(k) or {})
            merged.update(v)
            u[k] = merged
        else:
            u[k] = v
    if "target_seconds" in fields and "target_frames" not in fields:
        u["target_frames"] = seconds_to_frames(fields["target_seconds"])
    project_units(state)
    if fields.get("conditioning_mode") and fields.get("conditioning_mode") != old_mode:
        reason = "conditioning_mode change"
    if any(k in fields for k in ("conditioning_mode", "shot_ids", "internal_cuts",
                                 "target_frames", "start", "end", "prompt")):
        invalidate_plan(state, shot_ids=affected, reason=reason)
        if "start" in fields or "end" in fields:
            mark_stale(state, reason, shot_ids=affected, nodes=("video", "take"),
                       stale_shot=False)
    store_planning(state)
    return u


def _frames_of(sh):
    return int(sh.get("target_frames") or 0)


def edit_preview(state, op, body):
    if op not in EDIT_OPS:
        raise ValueError("unknown edit op")
    return apply_edit(copy.deepcopy(state), op, body, commit=False)


def apply_edit(state, op, body, commit=True):
    if op not in EDIT_OPS:
        raise ValueError("unknown edit op")
    ensure_units(state, persist=True)
    shots = state.setdefault("shots", [])
    smap = _by_id(shots)
    if op == "split":
        sid = body.get("shot_id")
        sh = smap.get(sid)
        if not sh:
            raise KeyError(sid)
        split_s = float(body.get("split_seconds") or 0)
        split_f = seconds_to_frames(split_s) if body.get("split_seconds") is not None else int(body.get("split_frames") or 0)
        total = _frames_of(sh)
        if split_f <= 0 or split_f >= total:
            raise ValueError("split point must be inside the shot")
        nid = next_numeric_id("sh", smap)
        new = copy.deepcopy(sh)
        new["id"] = nid
        new["target_frames"] = total - split_f
        new["title"] = (sh.get("title") or sid) + " (后)"
        new["storyboard_review"] = empty_review()
        new["unit_id"] = None
        sh["target_frames"] = split_f
        sh["storyboard_review"] = empty_review()
        shots.insert(shots.index(sh) + 1, new)
        for seq in state.get("sequences") or []:
            ids = list(seq.get("shot_ids") or [])
            if sid in ids:
                i = ids.index(sid)
                ids.insert(i + 1, nid)
                seq["shot_ids"] = ids
                new["sequence_id"] = seq.get("id")
        new["utterance_ids"] = list(sh.get("utterance_ids") or [])
        ensure_units(state, persist=True)
        _reflow_timelines(state)
        invalidate_plan(state, shot_ids=[sid, nid], reason="split")
        store_planning(state)
        return {"created": nid, "left": sid, "frames": [split_f, total - split_f]}
    if op == "merge":
        a, b = body.get("shot_ids") or [None, None][:2]
        if isinstance(body.get("shot_ids"), list) and len(body["shot_ids"]) == 2:
            a, b = body["shot_ids"]
        sa, sb = smap.get(a), smap.get(b)
        if not sa or not sb:
            raise KeyError("merge shots")
        if sa.get("sequence_id") != sb.get("sequence_id"):
            raise ValueError("cannot merge across sequences")
        seq = next(s for s in state.get("sequences") or [] if s.get("id") == sa.get("sequence_id"))
        ids = list(seq.get("shot_ids") or [])
        if abs(ids.index(a) - ids.index(b)) != 1:
            raise ValueError("cannot merge non-adjacent shots")
        keep, drop = (sa, sb) if ids.index(a) < ids.index(b) else (sb, sa)
        keep["target_frames"] = _frames_of(sa) + _frames_of(sb)
        uts = []
        for uid in (keep.get("utterance_ids") or []) + (drop.get("utterance_ids") or []):
            if uid not in uts:
                uts.append(uid)
        keep["utterance_ids"] = uts
        seq["shot_ids"] = [x for x in ids if x != drop["id"]]
        state["shots"] = [s for s in shots if s.get("id") != drop["id"]]
        state["generation_units"] = [
            u for u in (state.get("generation_units") or [])
            if drop["id"] not in (u.get("shot_ids") or [])
        ]
        keep["unit_id"] = None
        ensure_units(state, persist=True)
        _reflow_timelines(state)
        invalidate_plan(state, shot_ids=[keep["id"]], reason="merge")
        store_planning(state)
        return {"kept": keep["id"], "removed": drop["id"]}
    if op == "reorder":
        seq_id = body.get("sequence_id")
        order = body.get("shot_ids") or []
        seq = next((s for s in state.get("sequences") or [] if s.get("id") == seq_id), None)
        if not seq:
            raise KeyError(seq_id)
        cur = list(seq.get("shot_ids") or [])
        if sorted(order) != sorted(cur):
            raise ValueError("reorder must include the same shot ids")
        if len(order) != len(set(order)):
            raise ValueError("duplicate shot_ids")
        seq["shot_ids"] = list(order)
        _reflow_timelines(state)
        invalidate_plan(state, shot_ids=order, reason="reorder")
        store_planning(state)
        return {"shot_ids": order}
    if op == "add":
        seq_id = body.get("sequence_id")
        seq = next((s for s in state.get("sequences") or [] if s.get("id") == seq_id), None)
        if not seq:
            raise KeyError(seq_id)
        nid = next_numeric_id("sh", smap)
        after = body.get("after")
        sh = {
            "id": nid, "sequence_id": seq_id, "title": body.get("title") or "新子分镜",
            "target_frames": int(body.get("target_frames") or seconds_to_frames(body.get("target_seconds") or 3)),
            "action": body.get("action") or "",
            "entry_state": body.get("entry_state") or "",
            "exit_state": body.get("exit_state") or "",
            "conditioning_mode": "first_only",
            "shooting_mode": "one_take",
            "utterance_ids": [],
            "cast": body.get("cast") or [],
            "props": body.get("props") or [],
            "location_id": body.get("location_id") or "",
            "storyboard_review": empty_review(),
            "start": {"status": "pending", "image": ""},
            "end": {"status": "removed", "image": ""},
            "validity": "current",
            "status": "draft",
        }
        shots.append(sh)
        ids = list(seq.get("shot_ids") or [])
        if after and after in ids:
            ids.insert(ids.index(after) + 1, nid)
        else:
            ids.append(nid)
        seq["shot_ids"] = ids
        ensure_units(state, persist=True)
        _reflow_timelines(state)
        invalidate_plan(state, shot_ids=[nid], reason="add shot")
        store_planning(state)
        return {"created": nid}
    if op == "remove":
        sid = body.get("shot_id")
        sh = smap.get(sid)
        if not sh:
            raise KeyError(sid)
        sh["removed"] = True
        sh["storyboard_review"] = empty_review()
        for seq in state.get("sequences") or []:
            if sid in (seq.get("shot_ids") or []):
                seq["shot_ids"] = [x for x in seq["shot_ids"] if x != sid]
                seq.setdefault("removed_shot_ids", [])
                if sid not in seq["removed_shot_ids"]:
                    seq["removed_shot_ids"].append(sid)
        invalidate_plan(state, shot_ids=[sid], reason="remove shot")
        store_planning(state)
        return {"removed": sid}
    if op == "restore":
        sid = body.get("shot_id")
        sh = smap.get(sid)
        if not sh:
            raise KeyError(sid)
        sh["removed"] = False
        seq = next((s for s in state.get("sequences") or [] if s.get("id") == sh.get("sequence_id")), None)
        if seq:
            ids = list(seq.get("shot_ids") or [])
            if sid not in ids:
                ids.append(sid)
                seq["shot_ids"] = ids
            rem = seq.get("removed_shot_ids") or []
            seq["removed_shot_ids"] = [x for x in rem if x != sid]
        sh["storyboard_review"] = empty_review()
        _reflow_timelines(state)
        invalidate_plan(state, shot_ids=[sid], reason="restore shot")
        store_planning(state)
        return {"restored": sid}
    raise ValueError("unknown edit op")


def over_capability(state, frames, shooting_mode="one_take"):
    cap = capability(state)
    max_f = int(cap.get("max_frames") or 192)
    if shooting_mode == "one_take" and int(frames or 0) > max_f:
        return True, f"one_take {frames} exceeds capability {max_f} frames"
    return False, ""


def blockers(state, project_dir=None):
    out = []
    plan = state.get("planning") or {}
    if plan.get("over_budget"):
        out.extend({"kind": "budget", **row} for row in plan["over_budget"])
    for sh in state.get("shots") or []:
        if sh.get("removed"):
            continue
        over, msg = over_capability(state, sh.get("target_frames"), sh.get("shooting_mode") or "one_take")
        if over:
            out.append({"kind": "capability", "shot_id": sh.get("id"), "error": msg})
        loc = location_ref_file(
            _by_id(state.get("assets")).get(sh.get("location_id")),
            sh.get("location_variant_id"), project_dir)
        if sh.get("location_id") and not loc.get("ok"):
            out.append({"kind": "location", "shot_id": sh.get("id"), "error": loc.get("error")})
    gaps = (state.get("gaps") or {}).get("route") or (state.get("script_diff") or {}).get("route") or {}
    if gaps:
        out.append({"kind": "story_conflict", "route": gaps})
    return out


def asset_catalog(state, project_dir=None):
    items = []
    for a in state.get("assets") or []:
        files = []
        for key in ("image", "face", "ref_file"):
            if a.get(key):
                files.append({"role": key, "file": a[key],
                              "sha256": file_sha256(project_dir, a[key]) if project_dir else a.get("file_sha256") or ""})
        for k, f in (a.get("views") or {}).items():
            if f:
                files.append({"role": "view:" + k, "file": f,
                              "sha256": file_sha256(project_dir, f) if project_dir else ""})
        for v in a.get("variants") or []:
            for key in ("file", "ref_file", "wide"):
                if v.get(key):
                    files.append({"role": f"variant:{v.get('id')}:{key}", "file": v[key],
                                  "status": v.get("status"),
                                  "sha256": file_sha256(project_dir, v[key]) if project_dir else v.get("file_sha256") or ""})
        items.append({
            "id": a.get("id"), "name": a.get("name"), "type": a.get("type"),
            "status": a.get("status"), "files": files,
        })
    return items


def build_view(state, project_dir=None):
    """只读视图：不修改传入 state。"""
    st = copy.deepcopy(state)
    units = computed_units(st)
    umap = {u["id"]: u for u in units}
    seqs = []
    for seq in st.get("sequences") or []:
        shots = []
        for sid in seq.get("shot_ids") or []:
            sh = _by_id(st.get("shots")).get(sid)
            if not sh:
                continue
            u = umap.get(sh.get("unit_id") or "")
            if not u:
                u = next((x for x in units if sid in (x.get("shot_ids") or [])), {})
            loc = location_ref_file(
                _by_id(st.get("assets")).get(sh.get("location_id")),
                sh.get("location_variant_id"), project_dir)
            shots.append({
                **{k: sh.get(k) for k in (
                    "id", "title", "action", "entry_state", "exit_state", "shot_size",
                    "camera", "cut_reason", "blocking", "facing", "expression",
                    "cast", "props", "location_id", "location_variant_id", "era_id",
                    "target_frames", "timeline_in_frame", "timeline_out_frame",
                    "utterance_ids", "utterance_visible", "shooting_mode",
                    "conditioning_mode", "unit_id", "validity", "start", "end", "video")},
                "plan_hash": plan_hash_shot(st, sh),
                "storyboard_review": sh.get("storyboard_review") or empty_review(),
                "unit": {"id": u.get("id"), "shot_ids": u.get("shot_ids"),
                         "conditioning_mode": u.get("conditioning_mode"),
                         "internal_cuts": u.get("internal_cuts") or [],
                         "target_frames": u.get("target_frames"),
                         "start": u.get("start"), "end": u.get("end"),
                         "prompt": u.get("prompt")},
                "location_ref": loc,
            })
        seqs.append({
            "id": seq.get("id"), "title": seq.get("title"),
            "script_range": seq.get("script_range"),
            "target_frames": seq.get("target_frames"),
            "budget": seq.get("budget"),
            "shooting_mode": seq.get("shooting_mode"),
            "shooting_mode_confirmed": seq.get("shooting_mode_confirmed"),
            "continuity_note": seq.get("continuity_note") or "",
            "storyboard_review": seq.get("storyboard_review") or empty_review(),
            "plan_hash": sequence_plan_hash(st, seq),
            "shots": shots,
        })
    return {
        "revision": st.get("revision"),
        "plan_hash": project_plan_hash(st),
        "execution": st.get("execution"),
        "capability": capability(st),
        "planning": st.get("planning"),
        "storyboard": st.get("storyboard") or empty_review(),
        "sequences": seqs,
        "generation_units": units,
        "assets": asset_catalog(st, project_dir),
        "blockers": blockers(st, project_dir),
        "reviews": st.get("reviews") or [],
        "gaps": st.get("gaps") or st.get("script_diff") or {},
    }


def validate_prompt_result(state, body, project_dir=None):
    """结构/原文/来源错误 → ValueError；创作推断 → pending_inferences。"""
    skill = body.get("skill") or {}
    if not skill.get("name") or not skill.get("hash"):
        raise ValueError("skill source required")
    if skill.get("name") != "minimax-h3-prompt-standardizer":
        raise ValueError("unexpected skill")
    event_id = body.get("event_id")
    input_hash = body.get("input_hash")
    if not event_id or not input_hash:
        raise ValueError("event_id and input_hash required")
    target = body.get("target")
    sh = _by_id(state.get("shots")).get(target)
    if not sh:
        raise KeyError(target)
    want = body.get("expected_input_hash") or input_hash
    live = content_hash(shot_plan_fields(sh))
    if want != input_hash:
        raise PermissionError("stale prompt result")
    if input_hash != live:
        raise PermissionError("stale prompt result")
    text = body.get("prompt_zh") or body.get("text") or ""
    ut_map = _by_id(state.get("utterances"))
    for uid in sh.get("utterance_ids") or []:
        ut = ut_map.get(uid) or {}
        orig = ut.get("text") or ""
        if orig and orig not in text:
            raise ValueError(f"dialogue missing or altered: {uid}")
        sp = ut.get("speaker") or ""
        name = (_by_id(state.get("assets")).get(sp) or {}).get("name") or sp
        if orig and name and name not in text and sp not in text:
            raise ValueError(f"speaker missing: {uid}")
    timeline = body.get("timeline") or []
    if timeline:
        covered = 0
        prev_out = None
        for seg in timeline:
            inn, out = int(seg.get("in") or 0), int(seg.get("out") or 0)
            if out < inn:
                raise ValueError("timeline reversed")
            if prev_out is not None and inn < prev_out:
                raise ValueError("timeline overlap")
            covered += out - inn
            prev_out = out
        tf = int(sh.get("target_frames") or 0)
        if tf and covered != tf:
            raise ValueError("timeline does not cover shot")
    inferences = body.get("inferences") or []
    pending = [i for i in inferences if not i.get("confirmed")]
    order = body.get("shot_order")
    if order and order != [target]:
        raise ValueError("shot order changed")
    return {"ok": True, "pending_inferences": pending, "live_hash": live}


def accept_prompt_result(state, body):
    check = validate_prompt_result(state, body)
    ensure_units(state, persist=True)
    sh = _by_id(state.get("shots")).get(body.get("target"))
    uid = sh.get("unit_id")
    u = _by_id(state.get("generation_units")).get(uid) if uid else None
    pack = {
        "revision_id": "pr_" + uuid.uuid4().hex[:10],
        "status": "review" if not check["pending_inferences"] else "draft",
        "validity": "current",
        "prompt_zh": body.get("prompt_zh") or body.get("text") or "",
        "timeline": body.get("timeline") or [],
        "skill": body.get("skill"),
        "agent_model": body.get("agent_model") or "",
        "input_hash": body.get("input_hash"),
        "event_id": body.get("event_id"),
        "pending_inferences": check["pending_inferences"],
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": "skill",
    }
    if u is not None:
        hist = list((u.get("prompt") or {}).get("versions") or [])
        old = u.get("prompt") or {}
        if old.get("revision_id"):
            hist.append({k: old.get(k) for k in ("revision_id", "prompt_zh", "ts", "status")})
        pack["versions"] = hist
        u["prompt"] = pack
    sh["prompt_pack"] = pack
    rv = dict(sh.get("storyboard_review") or empty_review())
    if rv.get("status") == "approved":
        rv["status"] = "draft"
        rv["validity"] = "stale"
        rv["stale_reason"] = "prompt result"
        sh["storyboard_review"] = rv
    return pack


def skill_file_info(root):
    path = os.path.join(root, ".claude", "skills", "minimax-h3-prompt-standardizer", "SKILL.md")
    if not os.path.isfile(path):
        return {"path": path, "exists": False, "hash": ""}
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return {"path": path, "exists": True, "hash": h.hexdigest(),
            "name": "minimax-h3-prompt-standardizer"}


def request_store_path(project_dir):
    return os.path.join(project_dir, "request_index.json")


def lookup_request(project_dir, request_id):
    if not request_id:
        return None
    from state_store import read_json
    data = read_json(request_store_path(project_dir), {"items": {}})
    return (data.get("items") or {}).get(request_id)


def remember_request(project_dir, request_id, body_hash, result):
    if not request_id:
        return None
    from state_store import atomic_write, project_lock, read_json
    path = request_store_path(project_dir)
    with project_lock(project_dir):
        data = read_json(path, {"items": {}})
        items = data.setdefault("items", {})
        prev = items.get(request_id)
        if prev:
            if prev.get("body_hash") != body_hash:
                return {"conflict": True, "stored": prev}
            return {"conflict": False, "replay": True, "stored": prev}
        items[request_id] = {"body_hash": body_hash, "result": result,
                             "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
        atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        return {"conflict": False, "replay": False, "stored": items[request_id]}
