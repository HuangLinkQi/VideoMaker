#!/usr/bin/env python3
"""schema v1 → v2 迁移：快照、dry-run、映射、报告、回滚。不凭空创建 approved。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from board_draft import (  # noqa: E402
    MISSING_ASSETS, PERSON_LINKS, ROUTE_DIFF, SCRIPT_ONLY_LINES, attach_to_state,
)
from jobs import leftover_generating_jobs, register  # noqa: E402
from schema import SCHEMA_VERSION, valid_state  # noqa: E402
from state_store import atomic_write, dump_state, load_unlocked, project_lock  # noqa: E402

PAUSE_REASON = "审视分镜模块期间正式暂停；禁止自动恢复生成"


def sha256_text(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def snapshot(project_dir, tag):
    ts = time.strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(project_dir, "snapshots", f"{ts}-{tag}")
    os.makedirs(dest, exist_ok=True)
    copied = []
    for name in ("state.json", "script.txt", "config.json", "events.jsonl",
                 "events_ack.json", "jobs_index.json", "jobs.jsonl",
                 "migration_pointer.json"):
        src = os.path.join(project_dir, name)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(dest, name))
            copied.append(name)
    gsrc = os.path.join(ROOT, "studio", "config", "global.json")
    if os.path.isfile(gsrc):
        shutil.copy2(gsrc, os.path.join(dest, "global.json"))
        copied.append("global.json")
    rollback = {
        "snapshot_dir": dest,
        "copied": copied,
        "created_at": ts,
        "project_dir": project_dir,
    }
    with open(os.path.join(dest, "rollback.json"), "w", encoding="utf-8") as f:
        json.dump(rollback, f, ensure_ascii=False, indent=2)
    return dest


def _asset_revision(project_dir, asset):
    rel = asset.get("image") or ""
    digest = sha256_file(os.path.join(project_dir, rel)) if rel else ""
    return {
        "asset_id": asset.get("id"),
        "revision_id": (digest[:16] if digest else "nohash"),
        "file": rel,
        "sha256": digest,
    }


def _script_diff(script_text, utterances):
    state_lines = [{"id": u["id"], "speaker": u.get("speaker"), "text": u.get("text")}
                   for u in utterances]
    return {
        "script_hash": sha256_text(script_text),
        "state_utterances": state_lines,
        "script_only": SCRIPT_ONLY_LINES,
        "route": ROUTE_DIFF,
        "note": "电话段与工位主管对白已与剧本对齐；路线差异仍待审",
    }


def build_v2(old, project_dir, script_text, freeze_tag):
    state = json.loads(json.dumps(old, ensure_ascii=False))
    state["schema_version"] = SCHEMA_VERSION
    state["revision"] = int(old.get("revision") or 0)
    state["execution"] = {
        "mode": "paused",
        "reason": PAUSE_REASON,
        "paused_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    state["script_revision_id"] = "scr_file_" + sha256_text(script_text)[:12]
    state["active_edit_revision_id"] = ""
    state["legacy_scene_ids"] = [sc.get("id") for sc in (old.get("scenes") or [])]
    attach_to_state(state)

    for a in state.get("assets") or []:
        ref = _asset_revision(project_dir, a)
        a["revision_id"] = ref["revision_id"]
        a["file_sha256"] = ref["sha256"]
        aid = a.get("id")
        if aid == "wangguifen":
            a["person_id"] = "wangguifen"
            a["age_variant"] = "elder"
        elif aid == "wangguifen_young":
            a["person_id"] = "wangguifen"
            a["age_variant"] = "young"
        elif aid == "linchuan":
            a["person_id"] = "linchuan"
            a["age_variant"] = "adult"
        elif aid == "linchuan_child":
            a["person_id"] = "linchuan"
            a["age_variant"] = "child"
        if a.get("type") == "scene" and aid == "loc_home":
            a["variants"] = [
                {"id": "loc_home_night", "tod": "night", "file": a.get("image"),
                 "status": a.get("status")},
                {"id": "loc_home_morning", "tod": "morning", "file": "",
                 "status": "pending"},
            ]
        if a.get("type") == "scene" and aid == "loc_hospital":
            a["variants"] = [
                {"id": "loc_hospital_wake", "tod": "wake", "file": a.get("image"),
                 "status": a.get("status")},
                {"id": "loc_hospital_moon", "tod": "moon", "file": "",
                 "status": "pending"},
            ]
        if aid == "prop_bus_sign":
            a["status"] = "removed"

    for sc in state.get("scenes") or []:
        vid = sc.get("video") or {}
        if vid.get("status") == "generating":
            vid["status"] = "blocked"
            vid["note"] = "P0 遗留任务，运行状态未知，需恢复核对"
            sc["video"] = vid

    jobs = leftover_generating_jobs(old.get("scenes") or [], freeze_tag)
    state["jobs"] = jobs
    state["script_diff"] = _script_diff(script_text, state["utterances"])
    state["gaps"] = {
        "missing_assets": MISSING_ASSETS,
        "person_links": PERSON_LINKS,
        "route": ROUTE_DIFF,
    }
    state["media_readonly_legacy"] = True
    state["source_revision"] = old.get("revision")
    state["source_hash"] = sha256_text(json.dumps(old, sort_keys=True, ensure_ascii=False))
    return state


def report_for(old, new, snapshot_dir):
    old_ids = [sc.get("id") for sc in (old.get("scenes") or [])]
    shot_map = []
    for sh in new.get("shots") or []:
        shot_map.append({
            "shot_id": sh.get("id"),
            "legacy_scene_ids": sh.get("legacy_scene_ids") or [],
            "target_frames": sh.get("target_frames"),
            "status": sh.get("status"),
            "start_status": (sh.get("start") or {}).get("status"),
        })
    approved_created = []
    for sh in new.get("shots") or []:
        for key in ("start", "end", "video"):
            if (sh.get(key) or {}).get("status") == "approved":
                approved_created.append(f"{sh['id']}.{key}")
    return {
        "snapshot_dir": snapshot_dir,
        "old_scene_count": len(old_ids),
        "legacy_scene_ids": old_ids,
        "sequence_count": len(new.get("sequences") or []),
        "shot_count": len(new.get("shots") or []),
        "utterance_count": len(new.get("utterances") or []),
        "job_count": len(new.get("jobs") or []),
        "execution": new.get("execution"),
        "planned_seconds": (new.get("planning") or {}).get("planned_seconds"),
        "target_seconds": (new.get("planning") or {}).get("target_seconds"),
        "shot_map": shot_map,
        "script_diff": new.get("script_diff"),
        "gaps": new.get("gaps"),
        "removed_preserved": [
            a.get("id") for a in (new.get("assets") or []) if a.get("status") == "removed"
        ],
        "illegal_approved_on_new_shots": approved_created,
        "valid": valid_state(new),
    }


class MigrationConflict(Exception):
    def __init__(self, message, current_revision=None):
        super().__init__(message)
        self.current_revision = current_revision


def already_migrated(state):
    return int(state.get("schema_version") or 1) >= 2 and bool(state.get("sequences"))


def apply(project_dir, new_state, snapshot_dir, expected_revision=None,
          expected_hash=None, force=False):
    path = os.path.join(project_dir, "state.json")
    with project_lock(project_dir):
        current = load_unlocked(project_dir, {})
        if already_migrated(current) and not force:
            raise MigrationConflict("already migrated to v2; refuse re-apply of draft",
                                    current.get("revision"))
        cur_rev = current.get("revision")
        want_rev = expected_revision if expected_revision is not None else new_state.get("source_revision")
        if want_rev is not None and cur_rev != want_rev:
            raise MigrationConflict(
                f"source revision {want_rev} != current {cur_rev}; rebuild candidate",
                cur_rev)
        if expected_hash:
            actual = sha256_text(json.dumps(current, sort_keys=True, ensure_ascii=False))
            if actual != expected_hash:
                raise MigrationConflict("source hash mismatch; rebuild candidate", cur_rev)
        if not valid_state(new_state):
            raise MigrationConflict("candidate failed validation", cur_rev)
        new_state["revision"] = int(current.get("revision") or 0) + 1
        new_state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        atomic_write(path, dump_state(new_state))
    for job in new_state.get("jobs") or []:
        register(project_dir, job, expected_key=job.get("idempotency_key"))
    pointer = {
        "active_snapshot": snapshot_dir,
        "applied_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "schema_version": SCHEMA_VERSION,
        "source_revision": new_state.get("source_revision"),
    }
    atomic_write(os.path.join(project_dir, "migration_pointer.json"),
                 json.dumps(pointer, ensure_ascii=False, indent=2) + "\n")
    return new_state


def rollback(project_dir, snapshot_dir):
    src = os.path.join(snapshot_dir, "state.json")
    if not os.path.isfile(src):
        raise FileNotFoundError(src)
    names = ("state.json", "script.txt", "config.json", "events.jsonl",
             "events_ack.json", "jobs_index.json", "jobs.jsonl",
             "migration_pointer.json")
    trash = os.path.expanduser("~/trash")
    os.makedirs(trash, exist_ok=True)
    with project_lock(project_dir):
        for name in names:
            s = os.path.join(snapshot_dir, name)
            d = os.path.join(project_dir, name)
            if os.path.isfile(s):
                shutil.copy2(s, d)
            elif os.path.isfile(d):
                ts = time.strftime("%Y%m%d-%H%M%S")
                shutil.move(d, os.path.join(trash, f"{name}__rollback__{ts}"))
    return os.path.join(project_dir, "state.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="测试")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--rollback", metavar="SNAPSHOT_DIR")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    project_dir = os.path.join(ROOT, "projects", args.project)
    if args.rollback:
        path = rollback(project_dir, args.rollback)
        print("rolled back to", path)
        return 0
    old = load_unlocked(project_dir, {})
    if already_migrated(old) and args.apply and not args.force:
        print(json.dumps({"error": "already v2; pass --force to rebuild",
                          "revision": old.get("revision")}, ensure_ascii=False))
        return 1
    script_path = os.path.join(project_dir, "script.txt")
    script_text = ""
    if os.path.isfile(script_path):
        with open(script_path, encoding="utf-8") as f:
            script_text = f.read()
    tag = "P0"
    snap = snapshot(project_dir, tag)
    freeze = os.path.basename(snap)
    new = build_v2(old, project_dir, script_text, freeze)
    report = report_for(old, new, snap)
    report_path = os.path.join(snap, "migration_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    cand = os.path.join(snap, "state.v2.candidate.json")
    with open(cand, "w", encoding="utf-8") as f:
        f.write(dump_state(new))
    print(json.dumps({
        "snapshot": snap,
        "report": report_path,
        "candidate": cand,
        "valid": report["valid"],
        "planned_seconds": report["planned_seconds"],
        "jobs": report["job_count"],
        "illegal_approved": report["illegal_approved_on_new_shots"],
        "applied": False,
    }, ensure_ascii=False, indent=2))
    if args.apply and not args.dry_run:
        apply(project_dir, new, snap,
              expected_revision=new.get("source_revision"),
              expected_hash=new.get("source_hash"),
              force=args.force)
        print("applied revision", new.get("revision"))
    elif not args.apply:
        print("dry-run only; pass --apply to switch")
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
