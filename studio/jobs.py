"""任务账本：幂等键、claim、心跳、遗留 generating 登记。详细日志 jobs.jsonl。"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid

from state_store import atomic_write, project_lock, read_json

LOG_NAME = "jobs.jsonl"
INDEX_NAME = "jobs_index.json"

TERMINAL = {"done", "failed", "cancelled"}
REPLAYABLE = {"queued", "claimed", "running", "done", "blocked", "recovery_required"}


def idempotency_key(project, target_revision, op, input_hash):
    raw = f"{project}|{target_revision}|{op}|{input_hash}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _log_path(project_dir):
    return os.path.join(project_dir, LOG_NAME)


def _index_path(project_dir):
    return os.path.join(project_dir, INDEX_NAME)


def _append_log(project_dir, record):
    path = _log_path(project_dir)
    line = json.dumps(record, ensure_ascii=False) + "\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())


def load_index(project_dir):
    return read_json(_index_path(project_dir), {"jobs": []})


def _write_index(project_dir, index):
    atomic_write(_index_path(project_dir), json.dumps(index, ensure_ascii=False, indent=2) + "\n")


def find_by_key(jobs, key):
    """成功或进行中的相同幂等键复用原任务；failed/cancelled 允许新 attempt。"""
    for j in jobs:
        if j.get("idempotency_key") == key and j.get("status") in REPLAYABLE:
            return j
    return None


def new_job(kind, target, status="queued", **extra):
    job = {
        "job_id": extra.pop("job_id", None) or ("job_" + uuid.uuid4().hex[:12]),
        "kind": kind,
        "target": target,
        "status": status,
        "idempotency_key": extra.pop("idempotency_key", ""),
        "prompt_id": extra.pop("prompt_id", None),
        "claimed_by": extra.pop("claimed_by", ""),
        "heartbeat_at": extra.pop("heartbeat_at", 0),
        "error": extra.pop("error", ""),
        "outputs": extra.pop("outputs", []),
        "created_at": extra.pop("created_at", time.strftime("%Y-%m-%dT%H:%M:%S")),
    }
    job.update(extra)
    return job


def register(project_dir, job, expected_key=None):
    """同类未完成任务只允许一个有效 claim。相同幂等键返回已有记录。"""
    key = expected_key or job.get("idempotency_key")
    with project_lock(project_dir):
        index = load_index(project_dir)
        jobs = index.setdefault("jobs", [])
        if key:
            existing = find_by_key(jobs, key)
            if existing:
                return existing, False
        jobs.append(job)
        _write_index(project_dir, index)
        _append_log(project_dir, {"op": "register", "job": job, "ts": time.time()})
    return job, True


def claim(project_dir, job_id, worker_id):
    with project_lock(project_dir):
        index = load_index(project_dir)
        now = time.time()
        for j in index.get("jobs") or []:
            if j.get("job_id") != job_id:
                continue
            owner = j.get("claimed_by") or ""
            if j.get("status") in TERMINAL:
                return None, "terminal"
            if owner and owner != worker_id and j.get("status") in ("claimed", "running"):
                return j, "busy"
            j["status"] = "claimed"
            j["claimed_by"] = worker_id
            j["heartbeat_at"] = now
            _write_index(project_dir, index)
            _append_log(project_dir, {"op": "claim", "job_id": job_id,
                                      "worker_id": worker_id, "ts": now})
            return j, "ok"
        return None, "missing"


def heartbeat(project_dir, job_id, worker_id):
    with project_lock(project_dir):
        index = load_index(project_dir)
        for j in index.get("jobs") or []:
            if j.get("job_id") == job_id and j.get("claimed_by") == worker_id:
                j["heartbeat_at"] = time.time()
                if j.get("status") == "claimed":
                    j["status"] = "running"
                _write_index(project_dir, index)
                return j
        return None


def set_status(project_dir, job_id, status, **fields):
    with project_lock(project_dir):
        index = load_index(project_dir)
        for j in index.get("jobs") or []:
            if j.get("job_id") == job_id:
                j["status"] = status
                j.update(fields)
                _write_index(project_dir, index)
                _append_log(project_dir, {"op": "status", "job_id": job_id,
                                          "status": status, "ts": time.time()})
                return j
        return None


def leftover_generating_jobs(scenes, freeze_tag):
    """把 video.status=generating 登记为无法自动认领的遗留任务。"""
    out = []
    for sc in scenes or []:
        vid = sc.get("video") or {}
        if vid.get("status") != "generating":
            continue
        sid = sc.get("id")
        key = idempotency_key("legacy", sid, "video_generate", freeze_tag)
        out.append(new_job(
            "video", sid, status="recovery_required",
            job_id=f"job_legacy_{sid}",
            idempotency_key=key,
            prompt_id=None,
            error="运行状态未知，需恢复核对；原始 prompt_id 缺失，无法自动认领",
            created_from="leftover generating at P0 freeze",
            legacy_scene_id=sid,
        ))
    return out
