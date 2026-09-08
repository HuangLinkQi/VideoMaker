"""worker 事件调度：按未确认且未 dispatched 的 event_id，而不是项目最大时间戳。"""
from __future__ import annotations

import json
import os

from state_store import atomic_write, read_json

MAX_EVENT_TRIES = 3
BACKOFF_SEC = (2, 10, 40)
ATTEMPTS_NAME = "event_attempts.json"


def actionable_events(queue, dispatched_ids):
    dispatched = set(dispatched_ids or [])
    return [e for e in (queue or []) if e.get("event_id") and e["event_id"] not in dispatched]


def mark_dispatched(dispatched_ids, sent_ids):
    return set(dispatched_ids or []) | {i for i in (sent_ids or []) if i}


def prune_dispatched(dispatched_ids, pending_ids):
    """ack 后的 id 离开 pending，从 dispatched 去掉；未 ack 的仍占着，避免忙等重推。"""
    return set(dispatched_ids or []) & set(pending_ids or [])


def finish_dispatch(held, sent_ids, still_pending, succeeded):
    """成功才把 sent 放入 dispatched；SDK 失败必须允许同一未 ack 事件再次成为 ready。"""
    if succeeded:
        return prune_dispatched(mark_dispatched(held, sent_ids), still_pending)
    return prune_dispatched(held, still_pending)


def load_attempts(project_dir):
    return read_json(os.path.join(project_dir, ATTEMPTS_NAME), {})


def save_attempts(project_dir, attempts):
    atomic_write(os.path.join(project_dir, ATTEMPTS_NAME),
                 json.dumps(attempts, ensure_ascii=False, indent=2) + "\n")


def prune_attempts(attempts, pending_ids):
    keep = set(pending_ids or [])
    return {k: v for k, v in (attempts or {}).items() if k in keep}


def filter_ready(queue, dispatched_ids, attempts, now):
    """跳过已阻塞、未到退避时间的事件。"""
    ready = []
    for e in actionable_events(queue, dispatched_ids):
        eid = e.get("event_id")
        rec = (attempts or {}).get(eid) or {}
        if rec.get("blocked"):
            continue
        if float(rec.get("next_ok") or 0) > now:
            continue
        ready.append(e)
    return ready


def record_attempt(attempts, event_ids, now, succeeded, max_tries=MAX_EVENT_TRIES):
    """成功则清计数；失败则 tries+1，到上限 blocked。"""
    out = dict(attempts or {})
    for eid in event_ids or []:
        if not eid:
            continue
        if succeeded:
            out.pop(eid, None)
            continue
        rec = dict(out.get(eid) or {})
        tries = int(rec.get("tries") or 0) + 1
        rec["tries"] = tries
        rec["last_error_at"] = now
        if tries >= max_tries:
            rec["blocked"] = True
            rec["next_ok"] = None
        else:
            delay = BACKOFF_SEC[min(tries - 1, len(BACKOFF_SEC) - 1)]
            rec["blocked"] = False
            rec["next_ok"] = now + delay
        out[eid] = rec
    return out
