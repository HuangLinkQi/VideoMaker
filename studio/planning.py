"""镜头预算、对白粗估、覆盖检查、依赖失效。不把 4 字/秒当全员硬规则。"""
from __future__ import annotations

import hashlib
import json
import os
import re

from schema import FPS, TARGET_FRAMES, TARGET_SECONDS

_COUNTABLE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]")


def countable_chars(text):
    return len(_COUNTABLE.findall(text or ""))


def estimate_line_seconds(text, cps=4.0, pause=0.8):
    n = countable_chars(text)
    if n <= 0:
        return 0.0
    return n / float(cps) + float(pause)


def seconds_to_frames(seconds):
    return int(round(float(seconds) * FPS))


def frames_to_seconds(frames):
    return int(frames) / float(FPS)


def interval_len(in_frame, out_frame):
    """[in, out) 长度。"""
    return max(0, int(out_frame) - int(in_frame))


def take_window(actual_frames, target_frames, source_in=0):
    """从实际素材取用 target 帧。实际不足则失败。"""
    actual = int(actual_frames)
    target = int(target_frames)
    src_in = int(source_in)
    src_out = src_in + target
    if actual < src_out:
        return {
            "ok": False,
            "reason": f"actual_frames={actual} < needed {src_out}",
            "source_in_frame": src_in,
            "source_out_frame": src_out,
        }
    return {
        "ok": True,
        "source_in_frame": src_in,
        "source_out_frame": src_out,
        "used_frames": target,
        "leftover": actual - src_out,
    }


def _by_id(items):
    return {x["id"]: x for x in (items or []) if isinstance(x, dict) and x.get("id")}


SAMPLES_PER_FRAME = 2000  # 48kHz / 24fps
HEAD_SEQ_IDS = ("seq01", "seq02")  # 前 37 秒按场次成员统计，不按旧 888 帧边界


def utterance_duration(ut, cps=4.0, pause=0.8):
    measured = ut.get("measured_seconds")
    if measured is not None:
        return float(measured), "measured"
    return estimate_line_seconds(ut.get("text") or "", cps=cps, pause=pause), "estimate"


def _overlap_seconds(ut, shot, seq=None):
    """镜头只取对白在本镜时间轴上的相交区间。采样点按场次起点换算，不用全片帧号直接相乘。"""
    dur, source = utterance_duration(ut)
    shot_in = shot.get("timeline_in_frame")
    shot_out = shot.get("timeline_out_frame")
    in_s = ut.get("sequence_in_sample")
    out_s = ut.get("sequence_out_sample")
    if in_s is not None and out_s is not None and shot_in is not None and shot_out is not None:
        origin = int((seq or {}).get("timeline_in_frame") or 0)
        local_in = int(shot_in) - origin
        local_out = int(shot_out) - origin
        shot_in_s = local_in * SAMPLES_PER_FRAME
        shot_out_s = local_out * SAMPLES_PER_FRAME
        lo = max(int(in_s), shot_in_s)
        hi = min(int(out_s), shot_out_s)
        sec = max(0, hi - lo) / (FPS * SAMPLES_PER_FRAME)
        return round(sec, 3), "measured" if ut.get("measured_seconds") is not None else source
    vis = None
    for item in shot.get("utterance_visible") or []:
        if item.get("utterance_id") == ut.get("id"):
            vis = item
            break
    if vis and vis.get("seconds") is not None:
        return float(vis["seconds"]), source
    return dur, source


def shot_speech_seconds(shot, utterances, cps=4.0, pause=0.8, first_occurrence=None, seq=None):
    """镜头对白占用。同一句若已被本场先前镜头计过整句，本镜不再整句重计。"""
    ut_map = _by_id(utterances)
    seen = first_occurrence if first_occurrence is not None else set()
    total = 0.0
    details = []
    for uid in shot.get("utterance_ids") or []:
        ut = ut_map.get(uid)
        if not ut:
            continue
        if uid in seen:
            sec, source = _overlap_seconds(ut, shot, seq)
            if ut.get("sequence_in_sample") is None and not shot.get("utterance_visible"):
                sec = 0.0
                source = "shared"
        else:
            seen.add(uid)
            if ut.get("sequence_in_sample") is not None or shot.get("utterance_visible"):
                sec, source = _overlap_seconds(ut, shot, seq)
            else:
                sec, source = utterance_duration(ut, cps=cps, pause=pause)
        total += sec
        details.append({"utterance_id": uid, "seconds": round(sec, 3), "source": source,
                        "text": ut.get("text") or ""})
    return round(total, 3), details


def recompute(state, cps=4.0, pause=0.8):
    """按镜头重算场次规划帧、对白粗估、超额。写入 sequences[].budget 和 state.planning。"""
    shots = _by_id(state.get("shots"))
    utterances = state.get("utterances") or []
    report = {"sequences": [], "over_budget": [], "uncovered_utterances": [],
              "target_seconds": TARGET_SECONDS, "target_frames": TARGET_FRAMES}
    planned_frames = 0
    covered = set()
    head_frames = 0
    any_measured = False
    all_measured = True
    for seq in state.get("sequences") or []:
        s_frames = 0
        seen_ut = set()
        seq_speech = 0.0
        seq_measured = True
        shot_rows = []
        for sid in seq.get("shot_ids") or []:
            sh = shots.get(sid) or {}
            tf = int(sh.get("target_frames") or 0)
            s_frames += tf
            speech, details = shot_speech_seconds(
                sh, utterances, cps=cps, pause=pause, first_occurrence=seen_ut, seq=seq)
            for d in details:
                covered.add(d["utterance_id"])
                if d["source"] == "measured":
                    any_measured = True
                elif d["source"] not in ("shared",):
                    seq_measured = False
            window_s = frames_to_seconds(tf)
            row = {
                "id": sid,
                "target_frames": tf,
                "target_seconds": round(window_s, 3),
                "speech_seconds": speech,
                "speech_details": details,
                "over_by": round(speech - window_s, 3) if speech > window_s + 1e-6 else 0,
            }
            shot_rows.append(row)
            if row["over_by"] > 0:
                report["over_budget"].append({
                    "sequence_id": seq.get("id"), "shot_id": sid,
                    "over_by_seconds": row["over_by"],
                    "hint": "台词粗估超出镜头窗口；先实测 WAV，禁止截句尾或擅自改词",
                })
        ut_map = _by_id(utterances)
        for uid in seen_ut:
            ut = ut_map.get(uid) or {}
            sec, src = utterance_duration(ut, cps=cps, pause=pause)
            seq_speech += sec
            if src != "measured":
                seq_measured = False
        if not seen_ut:
            seq_measured = False
        all_measured = all_measured and seq_measured
        target_f = int(seq.get("target_frames") or 0)
        budget = {
            "target_frames": target_f,
            "target_seconds": round(frames_to_seconds(target_f), 3),
            "planned_frames": s_frames,
            "planned_seconds": round(frames_to_seconds(s_frames), 3),
            "speech_seconds": round(seq_speech, 3),
            "speech_measured": bool(seen_ut) and seq_measured,
            "delta_frames": s_frames - target_f,
        }
        seq["budget"] = budget
        planned_frames += s_frames
        if seq.get("id") in HEAD_SEQ_IDS:
            head_frames += s_frames
        report["sequences"].append({"id": seq.get("id"), "title": seq.get("title"),
                                    "budget": budget, "shots": shot_rows})
    report["planned_frames"] = planned_frames
    report["planned_seconds"] = round(frames_to_seconds(planned_frames), 3)
    report["head37_frames"] = head_frames
    report["head37_seconds"] = round(frames_to_seconds(head_frames), 3)
    report["speech_measured"] = bool(any_measured) and all_measured
    for ut in utterances:
        uid = ut.get("id")
        if uid and uid not in covered:
            report["uncovered_utterances"].append({"id": uid, "text": ut.get("text")})
    state["planning"] = report
    return report


def store_planning(state):
    return recompute(state)


UTTERANCE_CONTENT = {"text", "speaker"}
UTTERANCE_RESULT = {
    "wav", "measured_seconds", "sequence_in_sample", "sequence_out_sample",
    "voice_revision_id",
}
UTTERANCE_REVIEW = {"status", "validity"}
SHOT_CONTENT = {
    "target_frames", "target_seconds", "utterance_ids", "cast", "location_id",
    "location_variant_id", "conditioning_mode", "action", "entry_state",
    "exit_state", "shot_size", "camera", "cut_reason", "props", "era_id",
    "blocking", "facing", "expression", "shooting_mode", "unit_id",
    "utterance_visible",
}
SHOT_REVIEW = {"status", "validity"}


def mark_stale(state, reason, shot_ids=None, utterance_ids=None, clear_audio=False,
               nodes=None, stale_shot=True):
    """台词/时长/资产变更后标记依赖失效。不重跑 H3，只标 stale。

    clear_audio 仅在对白原文或声线版本变化时清空 WAV；审核和结果落库不得反向清空。
    nodes 默认只让视频/take 过期；视觉输入变化时传入 start/end。
    stale_shot=False 时只让指定节点过期（换帧不把镜头规划一并打 stale）。
    """
    shot_ids = set(shot_ids or [])
    utterance_ids = set(utterance_ids or [])
    nodes = tuple(nodes) if nodes is not None else ("video", "take")
    ut_map = _by_id(state.get("utterances"))
    for sh in state.get("shots") or []:
        hit = sh.get("id") in shot_ids
        if not hit:
            for uid in sh.get("utterance_ids") or []:
                if uid in utterance_ids:
                    hit = True
                    break
        if not hit:
            continue
        if stale_shot:
            sh["validity"] = "stale"
            sh["stale_reason"] = reason
        for key in nodes:
            node = sh.get(key)
            if not isinstance(node, dict):
                continue
            if node.get("status") in ("approved", "review", "generating"):
                node["validity"] = "stale"
                sh[key] = node
    if clear_audio:
        for uid in utterance_ids:
            ut = ut_map.get(uid)
            if ut:
                ut["status"] = "pending"
                ut["wav"] = None
                ut["measured_seconds"] = None
                ut["sequence_in_sample"] = None
                ut["sequence_out_sample"] = None
    return state


def _is_int(n):
    return isinstance(n, int) and not isinstance(n, bool)


def _shot_patch_kind(sh, fields):
    content = result = frame = False
    for k, v in fields.items():
        if k == "id":
            continue
        if k in SHOT_CONTENT:
            if sh.get(k) != v:
                content = True
        elif k in ("start", "end"):
            old, new = sh.get(k) or {}, v if isinstance(v, dict) else {}
            merged = dict(old)
            merged.update(new)
            if _node_ident(old) != _node_ident(merged):
                frame = True
            else:
                old_st, new_st = old.get("status"), merged.get("status")
                if old_st != new_st and ("removed" in (old_st, new_st)):
                    frame = True
        elif k in ("video", "take"):
            old, new = sh.get(k) or {}, v if isinstance(v, dict) else {}
            if new.get("file") not in (None, old.get("file")):
                result = True
            elif new.get("image") not in (None, old.get("image")):
                content = True
        elif k in SHOT_REVIEW:
            pass
        elif sh.get(k) != v:
            content = True
    if content:
        return "content"
    if frame:
        return "frame"
    if result:
        return "result"
    return "review"


def _utterance_patch_kind(ut, fields):
    content = result = False
    for k, v in fields.items():
        if k == "id":
            continue
        if k in UTTERANCE_CONTENT:
            if ut.get(k) != v:
                content = True
        elif k in UTTERANCE_RESULT:
            result = True
        elif k in UTTERANCE_REVIEW:
            pass
        elif ut.get(k) != v:
            content = True
    if content:
        return "content"
    if result:
        return "result"
    return "review"


def _merge_node(old, new):
    if not isinstance(new, dict):
        return new
    out = dict(old or {})
    out.update(new)
    return out


def _restamp_frame_node(old, new, project_dir):
    merged = _merge_node(old, new)
    if merged.get("image") == (old or {}).get("image"):
        return merged
    if "file_sha256" not in new:
        merged["file_sha256"] = file_sha256(project_dir, merged.get("image") or "") if project_dir else ""
    if "revision_id" not in new:
        h = merged.get("file_sha256") or ""
        merged["revision_id"] = h[:16] if h else ""
    if "validity" not in new:
        merged["validity"] = "current"
    if "status" not in new:
        merged["status"] = "review"
    return merged


def apply_shot_patch(state, shot_id, fields, reason="shot edit", project_dir=None):
    shots = _by_id(state.get("shots"))
    sh = shots.get(shot_id)
    if not sh:
        raise KeyError(shot_id)
    fields = dict(fields)
    if "target_seconds" in fields and "target_frames" not in fields:
        fields["target_frames"] = seconds_to_frames(fields["target_seconds"])
    if "target_frames" in fields:
        tf = fields["target_frames"]
        if not _is_int(tf) or tf < 0:
            raise ValueError("target_frames must be a non-negative integer")
    kind = _shot_patch_kind(sh, fields)
    for k, v in fields.items():
        if k == "id":
            continue
        if k in ("start", "end") and isinstance(v, dict):
            sh[k] = _restamp_frame_node(sh.get(k), v, project_dir)
        elif k in ("video", "take") and isinstance(v, dict):
            sh[k] = _merge_node(sh.get(k), v)
        else:
            sh[k] = v
    if kind == "content":
        _reflow_timelines(state)
        mark_stale(state, reason, shot_ids=[shot_id], clear_audio=False)
    elif kind == "frame":
        mark_stale(state, reason, shot_ids=[shot_id], clear_audio=False,
                   stale_shot=False)
    store_planning(state)
    return sh


def apply_utterance_patch(state, ut_id, fields, reason="utterance edit"):
    ut_map = _by_id(state.get("utterances"))
    ut = ut_map.get(ut_id)
    if not ut:
        raise KeyError(ut_id)
    fields = dict(fields)
    kind = _utterance_patch_kind(ut, fields)
    if kind == "content":
        fields["status"] = "pending"
        fields["wav"] = None
        fields["measured_seconds"] = None
        fields["sequence_in_sample"] = None
        fields["sequence_out_sample"] = None
    prev_result = {k: ut.get(k) for k in UTTERANCE_RESULT}
    for k, v in fields.items():
        if k == "id":
            continue
        ut[k] = v
    affected = [sh["id"] for sh in state.get("shots") or []
                if ut_id in (sh.get("utterance_ids") or [])]
    if kind == "content":
        mark_stale(state, reason, shot_ids=affected, utterance_ids=[ut_id],
                   clear_audio=True)
    elif kind == "result":
        changed = any(ut.get(k) != prev_result.get(k) for k in UTTERANCE_RESULT)
        if changed:
            mark_stale(state, reason, shot_ids=affected, utterance_ids=[ut_id],
                       clear_audio=False)
    store_planning(state)
    return ut


def media_identity(node):
    """内容身份：哈希/revision，不用可覆盖路径。"""
    if not isinstance(node, dict):
        return ""
    return str(node.get("revision_id") or node.get("file_sha256") or "")


def _node_ident(node):
    if not isinstance(node, dict):
        return ""
    return _dump({
        "image": node.get("image"),
        "file": node.get("file"),
        "revision_id": node.get("revision_id"),
        "file_sha256": node.get("file_sha256"),
    })


def file_sha256(project_dir, rel):
    if not project_dir or not rel:
        return ""
    path = os.path.join(project_dir, rel)
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


_FRAME_EVENT = {
    "approve_start": "start", "approve_end": "end",
    "regenerate_start": "start", "regenerate_end": "end",
    "remove_start": "start", "remove_end": "end",
    "restore_start": "start", "restore_end": "end",
}


def frame_event_key(ev_type):
    return _FRAME_EVENT.get(ev_type)


def _frame_version(node, project_dir=None):
    if not isinstance(node, dict):
        return ""
    ident = media_identity(node)
    if ident:
        return ident
    img = node.get("image") or node.get("file") or ""
    if project_dir and img:
        h = file_sha256(project_dir, img)
        if h:
            return h
    return img


def resource_version(state, ev_type, target, project_dir=None):
    """用户正在审核的对象版本，不是整包 state.revision。"""
    assets = _by_id(state.get("assets"))
    shots = _by_id(state.get("shots"))
    if ev_type in ("approve_asset", "remove_asset", "regenerate_asset"):
        a = assets.get(target) or {}
        return media_identity(a) or (file_sha256(project_dir, a.get("image") or "") if project_dir else "") or a.get("image") or ""
    if ev_type in ("approve_voice", "regenerate_voice"):
        v = (assets.get(target) or {}).get("voice") or {}
        ident = media_identity(v)
        if ident:
            return ident
        if project_dir and v.get("file"):
            return file_sha256(project_dir, v["file"])
        return ""
    if ev_type in ("approve_shot", "regenerate_shot"):
        sh = shots.get(target) or {}
        return sh.get("revision_id") or (sh.get("start") or {}).get("image") or sh.get("id") or ""
    frame_key = _FRAME_EVENT.get(ev_type)
    if frame_key:
        owner = shots.get(target)
        if owner is None:
            owner = next((x for x in (state.get("scenes") or []) if x.get("id") == target), None)
        if owner is not None:
            return _frame_version(owner.get(frame_key) or {}, project_dir) or target
        return str(state.get("revision", ""))
    sc = next((x for x in (state.get("scenes") or []) if x.get("id") == target), None)
    if sc is None:
        return str(state.get("revision", ""))
    if "storyboard" in ev_type:
        sb = sc.get("storyboard") or {}
        return sb.get("image") or sb.get("status") or target
    if "dialogue" in ev_type:
        d = sc.get("dialogue") or {}
        return d.get("file") or d.get("status") or target
    if "video" in ev_type:
        v = sc.get("video") or {}
        return v.get("file") or v.get("status") or target
    if ev_type in ("approve_keyframes", "regenerate_scene"):
        start = (sc.get("start") or {}).get("image") or ""
        end = (sc.get("end") or {}).get("image") or ""
        return start + "|" + end if (start or end) else target
    return str(state.get("revision", ""))


def _dump(x):
    return json.dumps(x, sort_keys=True, ensure_ascii=False, default=str)


def scenes_were_edited(old, new):
    return _dump(old.get("scenes") or []) != _dump(new.get("scenes") or [])


def stamp_media_identities(state, project_dir):
    """用磁盘内容哈希补全 revision_id / file_sha256。不单独判定变更。"""
    if not project_dir:
        return state
    for a in state.get("assets") or []:
        img = a.get("image") or ""
        if img:
            h = file_sha256(project_dir, img)
            if h:
                a.setdefault("file_sha256", h)
                if not a.get("revision_id"):
                    a["revision_id"] = a.get("file_sha256") or h[:16]
        voice = a.get("voice")
        if isinstance(voice, dict) and voice.get("file"):
            h = file_sha256(project_dir, voice["file"])
            if h:
                voice.setdefault("file_sha256", h)
                if not voice.get("revision_id"):
                    voice["revision_id"] = voice.get("file_sha256") or h
        for v in a.get("variants") or []:
            if v.get("file"):
                h = file_sha256(project_dir, v["file"])
                if h:
                    v.setdefault("file_sha256", h)
    return state


def carry_media_identities(stamped_old, new):
    """路径未变且新对象缺身份时，带上已从磁盘算出的哈希，避免把初始化当成换文件。"""
    old_assets = _by_id(stamped_old.get("assets"))
    for na in new.get("assets") or []:
        oa = old_assets.get(na.get("id")) or {}
        if na.get("image") == oa.get("image") and not media_identity(na) and media_identity(oa):
            na["file_sha256"] = oa.get("file_sha256")
            na["revision_id"] = oa.get("revision_id")
        nv, ov = na.get("voice") or {}, oa.get("voice") or {}
        if isinstance(nv, dict) and nv.get("file") == ov.get("file"):
            if not media_identity(nv) and media_identity(ov):
                nv["file_sha256"] = ov.get("file_sha256")
                nv["revision_id"] = ov.get("revision_id")
        old_vars = {v.get("id"): v for v in (oa.get("variants") or []) if v.get("id")}
        for v in na.get("variants") or []:
            ov = old_vars.get(v.get("id")) or {}
            if v.get("file") == ov.get("file") and not media_identity(v) and media_identity(ov):
                v["file_sha256"] = ov.get("file_sha256")
    return new


def _looks_like_sheet(rel):
    base = os.path.basename(rel or "")
    return bool(base) and not any(tok in base for tok in ("_wide", "_front", "_plate", "_view"))


def location_ref_file(asset, variant_id=None, project_dir=None):
    """生成用单机位图。选中变体优先用自身参考，不把父资产其它时段图盖上去。"""
    resolved = resolve_location_variant(asset, variant_id)
    if not resolved.get("ok"):
        return resolved
    views = (asset or {}).get("views") or {}
    sheet = (asset or {}).get("image") or ""
    hit = None
    if variant_id:
        hit = next((v for v in (asset.get("variants") or []) if v.get("id") == variant_id), None)
    variant_explicit = (hit.get("ref_file") or hit.get("wide") or "") if hit else ""
    variant_file = (hit.get("file") or "") if hit else ""
    parent_explicit = (asset or {}).get("ref_file") or views.get("eye") or ""
    if hit is not None:
        if variant_explicit:
            path = variant_explicit
        elif variant_file and variant_file != sheet:
            path = variant_file
        elif "morning" in str(variant_id or "").lower() and (
                not variant_file or variant_file == sheet):
            return {"ok": False,
                    "error": f"variant {variant_id} has only setting sheet, no morning single-view ref",
                    "file": variant_file, "status": (hit.get("status") if hit else "")}
        else:
            path = parent_explicit or variant_file or ""
    else:
        path = parent_explicit or resolved.get("file") or ""

    def wide_sib(p):
        if not p:
            return ""
        if p.endswith("_wide.png"):
            return p
        root, ext = os.path.splitext(p)
        return root + "_wide.png"

    if path and path != sheet and not _looks_like_sheet(path):
        return {"ok": True, "file": path, "status": resolved.get("status"),
                "variant_id": variant_id, "error": ""}
    if path and path != sheet:
        return {"ok": True, "file": path, "status": resolved.get("status"),
                "variant_id": variant_id, "error": ""}
    wide = wide_sib(sheet or path)
    if wide and wide != path:
        if not project_dir or _file_ok(project_dir, wide):
            return {"ok": True, "file": wide, "status": resolved.get("status"),
                    "variant_id": variant_id, "error": ""}
        return {"ok": False, "error": "need single-view location ref, not setting sheet",
                "file": path, "status": resolved.get("status")}
    if path == sheet and _looks_like_sheet(path):
        return {"ok": False, "error": "need single-view location ref, not setting sheet",
                "file": path, "status": resolved.get("status")}
    return {"ok": True, "file": path, "status": resolved.get("status"),
            "variant_id": variant_id, "error": ""}


def _cast_input(c, assets, project_dir=None):
    aid = c.get("asset_id") if isinstance(c, dict) else c
    a = assets.get(aid) or {}
    on_screen = True if not isinstance(c, dict) else c.get("on_screen", True)
    return {
        "id": aid,
        "on_screen": on_screen,
        "ident": media_identity(a),
        "image": a.get("image"),
        "ref": character_ref_file(a, aid, project_dir),
        "views": a.get("views") or {},
        "face": a.get("face"),
    }


def _prop_input(p, assets):
    aid = p.get("asset_id") if isinstance(p, dict) else p
    a = assets.get(aid) or {}
    return {
        "id": aid,
        "state": (p or {}).get("state") if isinstance(p, dict) else "",
        "ident": media_identity(a),
        "image": a.get("image"),
        "ref": prop_ref_file(a),
        "views": a.get("views") or {},
    }


def _location_input(state, sh, project_dir=None):
    assets = _by_id(state.get("assets"))
    loc = assets.get(sh.get("location_id")) or {}
    loc_res = location_ref_file(loc, sh.get("location_variant_id"), project_dir)
    variant = None
    if sh.get("location_variant_id"):
        variant = next((v for v in (loc.get("variants") or [])
                        if v.get("id") == sh.get("location_variant_id")), None)
    return {
        "id": sh.get("location_id"),
        "variant_id": sh.get("location_variant_id"),
        "file": loc_res.get("file"),
        "ident": media_identity(variant or loc),
        "variant_file": (variant or {}).get("file"),
        "variant_hash": (variant or {}).get("file_sha256"),
        "ref_file": loc.get("ref_file"),
        "views": loc.get("views") or {},
    }


def shot_visual_input(state, sh, project_dir=None):
    """首尾帧依赖的画面输入：出镜、道具状态、实际参考裁图。"""
    assets = _by_id(state.get("assets"))
    return _dump({
        "cast": [_cast_input(c, assets, project_dir) for c in sh.get("cast") or []],
        "props": [_prop_input(p, assets) for p in sh.get("props") or []],
        "location": _location_input(state, sh, project_dir),
    })


def shot_generation_input(state, sh, project_dir=None):
    """镜头实际生成输入摘要：引用资源及其子资源版本，PUT/PATCH 共用。"""
    assets = _by_id(state.get("assets"))
    ut_map = _by_id(state.get("utterances"))
    uts = []
    for uid in sh.get("utterance_ids") or []:
        ut = ut_map.get(uid) or {}
        uts.append({
            "id": uid,
            "text": ut.get("text"),
            "speaker": ut.get("speaker"),
            "wav": ut.get("wav"),
            "measured_seconds": ut.get("measured_seconds"),
            "sequence_in_sample": ut.get("sequence_in_sample"),
            "sequence_out_sample": ut.get("sequence_out_sample"),
            "voice": media_identity(((assets.get(ut.get("speaker")) or {}).get("voice") or {})),
        })
    return _dump({
        "target_frames": sh.get("target_frames"),
        "action": sh.get("action"),
        "camera": sh.get("camera"),
        "entry_state": sh.get("entry_state"),
        "exit_state": sh.get("exit_state"),
        "shot_size": sh.get("shot_size"),
        "era_id": sh.get("era_id"),
        "conditioning_mode": sh.get("conditioning_mode"),
        "utterance_ids": sh.get("utterance_ids"),
        "utterances": uts,
        "cast": [_cast_input(c, assets, project_dir) for c in sh.get("cast") or []],
        "props": [_prop_input(p, assets) for p in sh.get("props") or []],
        "location": _location_input(state, sh, project_dir),
        "start": _node_ident(sh.get("start") or {}),
        "end": _node_ident(sh.get("end") or {}),
    })


def _voice_replaced(ov, nv):
    """内容替换：路径变了，或两边都有身份且不同。缺身份补全不算更换。"""
    old_vid, new_vid = media_identity(ov), media_identity(nv)
    if (nv.get("file") or "") != (ov.get("file") or ""):
        return True
    if old_vid and new_vid and old_vid != new_vid:
        return True
    return False


def propagate_put(old, new):
    """整包 PUT 的失效传播。身份初始化（缺哈希→补哈希）不算换声线。"""
    old_assets = _by_id(old.get("assets"))
    new_assets = _by_id(new.get("assets"))
    voice_changed = set()
    image_changed = set()
    for aid, na in new_assets.items():
        oa = old_assets.get(aid) or {}
        if _voice_replaced(oa.get("voice") or {}, na.get("voice") or {}):
            voice_changed.add(aid)
        old_iid, new_iid = media_identity(oa), media_identity(na)
        if na.get("image") != oa.get("image"):
            image_changed.add(aid)
        elif old_iid and new_iid and old_iid != new_iid:
            image_changed.add(aid)
    stale_uts = []
    for ut in new.get("utterances") or []:
        old_ut = _by_id(old.get("utterances")).get(ut.get("id")) or {}
        speaker = ut.get("speaker")
        if speaker in voice_changed:
            stale_uts.append(ut["id"])
            ut["status"] = "pending"
            ut["wav"] = None
            ut["measured_seconds"] = None
        elif ut.get("text") != old_ut.get("text") or ut.get("speaker") != old_ut.get("speaker"):
            stale_uts.append(ut["id"])
            ut["status"] = "pending"
            ut["wav"] = None
            ut["measured_seconds"] = None
        elif (ut.get("wav") != old_ut.get("wav") or
              ut.get("measured_seconds") != old_ut.get("measured_seconds") or
              ut.get("sequence_in_sample") != old_ut.get("sequence_in_sample") or
              ut.get("sequence_out_sample") != old_ut.get("sequence_out_sample")):
            stale_uts.append(ut["id"])
    visual_shots, video_shots = [], []
    for sh in new.get("shots") or []:
        old_sh = _by_id(old.get("shots")).get(sh.get("id")) or {}
        video = shot_generation_input(new, sh) != shot_generation_input(old, old_sh)
        visual = (
            _node_ident(sh.get("start") or {}) != _node_ident(old_sh.get("start") or {})
            or _node_ident(sh.get("end") or {}) != _node_ident(old_sh.get("end") or {})
            or shot_visual_input(new, sh) != shot_visual_input(old, old_sh)
        )
        for c in (sh.get("cast") or []) + (sh.get("props") or []):
            aid = c.get("asset_id") if isinstance(c, dict) else c
            if aid in image_changed:
                visual = True
                video = True
            if aid in voice_changed:
                video = True
        loc = sh.get("location_id")
        if loc in image_changed:
            visual = True
            video = True
        old_loc = old_assets.get(loc) or {}
        new_loc = new_assets.get(loc) or {}
        vid = sh.get("location_variant_id")
        old_var = next((v for v in (old_loc.get("variants") or []) if v.get("id") == vid), None) or {}
        new_var = next((v for v in (new_loc.get("variants") or []) if v.get("id") == vid), None) or {}
        if (new_var.get("file") or "") != (old_var.get("file") or ""):
            visual = True
            video = True
        elif (media_identity(old_var) and media_identity(new_var)
              and media_identity(old_var) != media_identity(new_var)):
            visual = True
            video = True
        if video:
            video_shots.append(sh.get("id"))
        if visual:
            visual_shots.append(sh.get("id"))
    if video_shots or stale_uts:
        mark_stale(new, "put content change", shot_ids=video_shots,
                   utterance_ids=stale_uts, clear_audio=False)
    if visual_shots:
        mark_stale(new, "put visual change", shot_ids=visual_shots,
                   clear_audio=False, nodes=("start", "end", "video", "take"))
    _reflow_timelines(new)
    store_planning(new)
    return new


def _reflow_timelines(state):
    shots = _by_id(state.get("shots"))
    t = 0
    for seq in state.get("sequences") or []:
        seq_start = t
        for sid in seq.get("shot_ids") or []:
            sh = shots.get(sid)
            if not sh:
                continue
            tf = int(sh.get("target_frames") or 0)
            sh["timeline_in_frame"] = t
            sh["timeline_out_frame"] = t + tf
            t += tf
        seq["timeline_in_frame"] = seq_start
        seq["timeline_out_frame"] = t
        planned = t - seq_start
        if seq.get("target_frames") is None:
            seq["target_frames"] = planned


def coverage_ok(report):
    return not report.get("uncovered_utterances")


def resolve_location_variant(asset, variant_id):
    """按 location_variant_id 解析已批准单机位文件。缺失变体不得回退到其它时段。"""
    if not asset:
        return {"ok": False, "error": "location asset missing", "file": "", "status": ""}
    variants = asset.get("variants") or []
    if variant_id and variants:
        hit = next((v for v in variants if v.get("id") == variant_id), None)
        if hit is None:
            return {"ok": False, "error": f"variant not registered: {variant_id}",
                    "file": "", "status": ""}
        st = hit.get("status") or "pending"
        path = hit.get("file") or ""
        if st != "approved" or not path:
            return {"ok": False,
                    "error": f"variant {variant_id} status={st} file={path or 'empty'}",
                    "file": path, "status": st}
        return {"ok": True, "file": path, "status": st, "variant_id": variant_id,
                "error": ""}
    path = asset.get("image") or ""
    if asset.get("status") == "removed":
        return {"ok": False, "error": "location removed", "file": path, "status": "removed"}
    if asset.get("status") != "approved" or not path:
        return {"ok": False, "error": "location not approved", "file": path,
                "status": asset.get("status") or ""}
    return {"ok": True, "file": path, "status": asset.get("status"), "error": ""}


def _file_ok(project_dir, rel):
    if not rel:
        return False
    if not project_dir:
        return True
    return os.path.isfile(os.path.join(project_dir, rel))


def character_ref_file(asset, aid, project_dir=None):
    views = (asset or {}).get("views") or {}
    cands = [views.get("body_front")]
    if aid:
        cands.append(f"characters/char_{aid}_front.png")
    cands.append((asset or {}).get("face"))
    if aid:
        cands.append(f"characters/char_{aid}_face.png")
    existing = []
    for p in cands:
        if not p:
            continue
        if project_dir:
            if os.path.isfile(os.path.join(project_dir, p)):
                return p
            existing.append(p)
        else:
            return p
    return existing[0] if existing else (cands[1] if aid else "")


def prop_ref_file(asset):
    if not asset:
        return ""
    views = asset.get("views") or {}
    return views.get("front") or asset.get("image") or ""


def _frame_ready(node, label, project_dir):
    if not node or node.get("status") == "removed":
        return False, f"{label} removed"
    if node.get("status") != "approved":
        return False, f"{label} {node.get('status') or 'missing'}"
    if node.get("validity") == "stale":
        return False, f"{label} is stale"
    if not node.get("image"):
        return False, f"{label} missing"
    if project_dir and not _file_ok(project_dir, node.get("image")):
        return False, f"{label} file missing"
    return True, ""


def can_submit_render(state, shot_id, project_dir=None):
    """正式生成前置：镜头 approved+current；按 conditioning_mode 检查帧；道具须已审。"""
    exe = (state.get("execution") or {}).get("mode")
    if exe != "running":
        return False, "execution paused"
    shots = _by_id(state.get("shots"))
    sh = shots.get(shot_id)
    if not sh:
        return False, "shot not found"
    if sh.get("status") != "approved":
        return False, f"shot is {sh.get('status') or 'unreviewed'}"
    if sh.get("validity") == "stale":
        return False, "shot is stale"
    assets = _by_id(state.get("assets"))
    for ref in (sh.get("cast") or []):
        if isinstance(ref, dict) and ref.get("on_screen") is False:
            continue
        aid = ref.get("asset_id") if isinstance(ref, dict) else ref
        if aid not in assets:
            return False, f"missing asset {aid}"
        a = assets[aid]
        if a.get("status") == "removed":
            return False, f"removed asset {aid}"
        if a.get("status") != "approved":
            return False, f"asset {aid} not approved"
        cref = character_ref_file(a, aid, project_dir)
        if project_dir and cref and not _file_ok(project_dir, cref):
            return False, f"character file missing: {cref}"
    for ref in sh.get("props") or []:
        aid = ref.get("asset_id") if isinstance(ref, dict) else ref
        if not aid:
            continue
        a = assets.get(aid)
        if a is None:
            return False, f"missing prop {aid}"
        if a.get("status") == "removed":
            return False, f"removed asset {aid}"
        if a.get("status") != "approved":
            return False, f"prop {aid} not approved"
        pfile = prop_ref_file(a)
        if not pfile:
            return False, f"prop {aid} missing image"
        if project_dir and not _file_ok(project_dir, pfile):
            return False, f"prop file missing: {pfile}"
    loc_id = sh.get("location_id")
    if loc_id:
        loc = assets.get(loc_id)
        if loc is None:
            return False, f"missing location {loc_id}"
        resolved = location_ref_file(loc, sh.get("location_variant_id"), project_dir)
        if not resolved["ok"]:
            return False, resolved["error"]
        loc_file = resolved.get("file") or ""
        if project_dir and loc_file and not _file_ok(project_dir, loc_file):
            return False, f"location file missing: {loc_file}"
    start, end = sh.get("start") or {}, sh.get("end") or {}
    cm = sh.get("conditioning_mode") or "first_only"
    if cm == "last_only":
        return False, "last_only not verified"
    if start.get("status") == "removed" and end.get("status") == "removed":
        return False, "both frames removed"
    if cm in ("first_only", "first_last"):
        ok, reason = _frame_ready(start, "start frame", project_dir)
        if not ok:
            return False, reason
    if cm == "first_last":
        ok, reason = _frame_ready(end, "end frame", project_dir)
        if not ok:
            return False, reason
    return True, ""
