"""从已锁镜头表/资产版本编译提示词。禁止硬编码角色表或 SCENE_MAP。"""
from __future__ import annotations

from planning import (
    _by_id, character_ref_file, file_sha256, frames_to_seconds, location_ref_file,
    prop_ref_file,
)

MAX_KREA_REFS = 3


def compile_shot(state, shot_id, project_dir=None):
    shots = _by_id(state.get("shots"))
    assets = _by_id(state.get("assets"))
    ut_map = _by_id(state.get("utterances"))
    sh = shots.get(shot_id)
    if not sh:
        raise KeyError(shot_id)
    warnings = []
    blocked = []
    candidates = []
    for c in sh.get("cast") or []:
        if not c.get("on_screen", True):
            continue
        aid = c.get("asset_id")
        a = assets.get(aid)
        if a is None:
            blocked.append(f"missing character {aid}")
            continue
        if a.get("status") == "removed":
            warnings.append(f"removed character {aid}")
            continue
        path = character_ref_file(a, aid, project_dir)
        candidates.append({
            "slot": "character", "asset_id": aid,
            "file": path,
            "sha256": file_sha256(project_dir, path) if path else "",
            "status": a.get("status"),
            "on_screen": True,
        })
    loc_id = sh.get("location_id")
    if loc_id:
        loc = assets.get(loc_id)
        resolved = location_ref_file(loc, sh.get("location_variant_id"), project_dir)
        if not resolved["ok"]:
            blocked.append(resolved["error"])
            warnings.append(resolved["error"])
        else:
            loc_file = resolved["file"]
            candidates.append({
                "slot": "location", "asset_id": loc_id,
                "file": loc_file,
                "variant": sh.get("location_variant_id"),
                "sha256": file_sha256(project_dir, loc_file) if loc_file else "",
                "status": resolved.get("status"),
            })
    for p in sh.get("props") or []:
        aid = p.get("asset_id") if isinstance(p, dict) else p
        a = assets.get(aid)
        if a is None:
            blocked.append(f"missing prop {aid}")
            continue
        if a.get("status") == "removed":
            warnings.append(f"removed prop {aid} — do not restore")
            continue
        path = prop_ref_file(a)
        candidates.append({
            "slot": "prop", "asset_id": aid,
            "file": path,
            "state": (p or {}).get("state") if isinstance(p, dict) else "",
            "sha256": file_sha256(project_dir, path) if path else "",
            "status": a.get("status"),
        })
    passed = candidates[:MAX_KREA_REFS]
    overflow = candidates[MAX_KREA_REFS:]
    if overflow:
        warnings.append(
            f"Krea-2 最多 {MAX_KREA_REFS} 槽；实际传入 {[r.get('asset_id') for r in passed]}；"
            f"其余需局部编辑加入: {[r.get('asset_id') for r in overflow]}"
        )
    lines = []
    for uid in sh.get("utterance_ids") or []:
        ut = ut_map.get(uid) or {}
        sp = ut.get("speaker") or ""
        name = (assets.get(sp) or {}).get("name") or sp
        lines.append(f"{name}：{ut.get('text') or ''}")
    seconds = frames_to_seconds(sh.get("target_frames") or 0)
    prompt_zh = "\n".join([
        f"【镜头 {sh.get('id')}｜{seconds:g}秒｜{sh.get('shot_size') or ''}】",
        f"地点：{loc_id} / {sh.get('location_variant_id') or ''} / era={sh.get('era_id') or ''}",
        f"动作：{sh.get('action') or ''}",
        f"入口：{sh.get('entry_state') or ''}",
        f"出口：{sh.get('exit_state') or ''}",
        f"条件模式：{sh.get('conditioning_mode') or 'first_only'}",
        ("对白：\n" + "\n".join(lines)) if lines else "对白：无",
    ])
    return {
        "shot_id": shot_id,
        "source_revision": state.get("revision"),
        "prompt_zh": prompt_zh,
        "prompt_en": "",
        "prompt_en_validity": "stale",
        "conditioning_mode": sh.get("conditioning_mode") or "first_only",
        "refs_passed": passed,
        "refs_overflow": overflow,
        "warnings": warnings,
        "blocked": blocked,
        "target_frames": sh.get("target_frames"),
    }


def compile_all(state, project_dir=None):
    return [compile_shot(state, sh["id"], project_dir=project_dir)
            for sh in state.get("shots") or []]


def compile_unit(state, unit_id, project_dir=None):
    """生成单元实际送模清单。三种模式互斥；不创作新内容。"""
    from storyboard import computed_units, capability
    units = {u["id"]: u for u in computed_units(state) if u.get("id")}
    u = units.get(unit_id)
    if not u:
        raise KeyError(unit_id)
    shot_ids = u.get("shot_ids") or []
    if not shot_ids:
        raise ValueError("empty unit")
    items = [compile_shot(state, sid, project_dir=project_dir) for sid in shot_ids]
    mode = u.get("conditioning_mode") or "first_only"
    if mode not in ("first_only", "first_last", "last_only"):
        raise ValueError("illegal mode")
    start = u.get("start") or {}
    end = u.get("end") or {}
    frames = {}
    if mode in ("first_only", "first_last"):
        frames["first_frame"] = {
            "file": start.get("image") or "",
            "sha256": start.get("file_sha256") or "",
            "revision_id": start.get("revision_id") or "",
            "status": start.get("status"),
        }
    if mode in ("last_only", "first_last"):
        frames["last_frame"] = {
            "file": end.get("image") or "",
            "sha256": end.get("file_sha256") or "",
            "revision_id": end.get("revision_id") or "",
            "status": end.get("status"),
        }
    prompt = u.get("prompt") or {}
    cap = capability(state)
    return {
        "unit_id": unit_id,
        "shot_ids": shot_ids,
        "source_revision": state.get("revision"),
        "conditioning_mode": mode,
        "frames": frames,
        "has_first_frame": "first_frame" in frames,
        "has_last_frame": "last_frame" in frames,
        "omni_reference": False,
        "native_audio": False,
        "prompt_zh": prompt.get("prompt_zh") or "",
        "prompt_revision_id": prompt.get("revision_id") or "",
        "prompt_status": prompt.get("status") or "draft",
        "skill": prompt.get("skill") or {},
        "shots": items,
        "blocked": [b for it in items for b in (it.get("blocked") or [])],
        "warnings": [w for it in items for w in (it.get("warnings") or [])],
        "target_frames": u.get("target_frames") or sum(it.get("target_frames") or 0 for it in items),
        "capability": cap,
        "internal_cuts": u.get("internal_cuts") or [],
    }
