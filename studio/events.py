"""事件队列：events.jsonl 追加；ack 只确认指定 event_id，禁止整文件清空。"""
from __future__ import annotations

import json
import os
import time
import uuid

from state_store import atomic_write, project_lock, read_json

ACK_NAME = "events_ack.json"
LOG_NAME = "events.jsonl"


def _paths(project_dir):
    return (
        os.path.join(project_dir, LOG_NAME),
        os.path.join(project_dir, ACK_NAME),
    )


def new_event(ev_type, target="", note="", target_revision=None, frame=None):
    ev = {
        "event_id": uuid.uuid4().hex,
        "ts": round(time.time(), 3),
        "type": str(ev_type),
        "target": str(target or ""),
        "note": str(note or ""),
        "target_revision": target_revision,
    }
    if frame:
        ev["frame"] = str(frame)
    return ev


def append_event(project_dir, event):
    log_path, _ = _paths(project_dir)
    os.makedirs(project_dir, exist_ok=True)
    line = json.dumps(event, ensure_ascii=False) + "\n"
    with project_lock(project_dir):
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
    return event


def _acked_set(project_dir):
    _, ack_path = _paths(project_dir)
    data = read_json(ack_path, {"ids": []})
    return set(data.get("ids") or [])


def load_all(project_dir):
    log_path, _ = _paths(project_dir)
    out = []
    if not os.path.exists(log_path):
        return out
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def pending(project_dir):
    acked = _acked_set(project_dir)
    return [e for e in load_all(project_dir) if e.get("event_id") and e["event_id"] not in acked]


def ack_ids(project_dir, ids):
    """只确认给定 id。未知 id 忽略。返回实际确认的 id 列表。"""
    want = [str(i) for i in (ids or []) if i]
    if not want:
        return []
    _, ack_path = _paths(project_dir)
    with project_lock(project_dir):
        data = read_json(ack_path, {"ids": []})
        have = list(data.get("ids") or [])
        have_set = set(have)
        live = {e.get("event_id") for e in load_all(project_dir)}
        newly = []
        for i in want:
            if i in live and i not in have_set:
                have.append(i)
                have_set.add(i)
                newly.append(i)
        atomic_write(ack_path, json.dumps({"ids": have}, ensure_ascii=False, indent=2) + "\n")
    return newly
