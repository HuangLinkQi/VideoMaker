"""项目 state.json 的带锁写入。revision CAS，原子替换。

生成脚本禁止读旧整包后直接覆盖：必须经本模块，并传入 expected_revision。
"""
from __future__ import annotations

import json
import os
import time

try:
    import fcntl
except ImportError:  # Windows 不在本项目目标环境
    fcntl = None

from schema import SCHEMA_VERSION, state_errors, valid_state

LOCK_NAME = "state.json.lock"


class ConflictError(Exception):
    def __init__(self, current_revision, message="revision conflict"):
        super().__init__(message)
        self.current_revision = current_revision


class ValidationError(Exception):
    pass


def _lock_path(project_dir):
    return os.path.join(project_dir, LOCK_NAME)


def _state_path(project_dir):
    return os.path.join(project_dir, "state.json")


def atomic_write(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


class _ProjectLock:
    def __init__(self, project_dir):
        self.path = _lock_path(project_dir)
        self.fd = None

    def __enter__(self):
        self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o644)
        if fcntl is not None:
            fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        if self.fd is not None:
            if fcntl is not None:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
            self.fd = None


def project_lock(project_dir):
    return _ProjectLock(project_dir)


def load_unlocked(project_dir, default=None):
    return read_json(_state_path(project_dir), default if default is not None else {})


def dump_state(state):
    return json.dumps(state, ensure_ascii=False, indent=2) + "\n"


def _bump(state):
    state["revision"] = int(state.get("revision") or 0) + 1
    if state.get("schema_version") is None:
        state["schema_version"] = SCHEMA_VERSION
    state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    return state


def save_cas(project_dir, new_state, expected_revision=None):
    """在写锁下检查 revision，通过后 bump 并原子替换。返回写入后的 state。"""
    if not valid_state(new_state):
        raise ValidationError("; ".join(state_errors(new_state) or ["invalid state"]))
    with project_lock(project_dir):
        current = load_unlocked(project_dir, {})
        cur_rev = current.get("revision")
        if expected_revision is not None and cur_rev != expected_revision:
            raise ConflictError(cur_rev)
        written = json.loads(json.dumps(new_state, ensure_ascii=False))
        _bump(written)
        atomic_write(_state_path(project_dir), dump_state(written))
        return written


def patch_state(project_dir, mutator, expected_revision):
    """读-改-写：mutator(state) 原地修改，CAS 成功后返回新 state。"""
    with project_lock(project_dir):
        current = load_unlocked(project_dir, {})
        cur_rev = current.get("revision")
        if cur_rev != expected_revision:
            raise ConflictError(cur_rev)
        mutator(current)
        if not valid_state(current):
            raise ValidationError("invalid state after patch")
        _bump(current)
        atomic_write(_state_path(project_dir), dump_state(current))
        return current
