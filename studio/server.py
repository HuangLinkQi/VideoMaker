#!/usr/bin/env python3
"""视频工作室本地服务：项目状态 + 审核事件队列 + 知识库 + 静态页面。

仅标准库。用法: python3 studio/server.py [port]   (默认 8931)
数据全部落在 projects/<name>/ 下:
  script.txt / state.json / events.jsonl / characters/ / scenes/ / props/
  keyframes/ / videos/ / dialogues/
提示词知识库在 studio/knowledge/，页面「知识库」页签与 /knowledge/ 路由只读服务。
"""
import base64
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import events as eventlog  # noqa: E402
import jobs as joblog  # noqa: E402
import model_info  # noqa: E402
import planning  # noqa: E402
import prompt_compiler  # noqa: E402
import storyboard as sboard  # noqa: E402
from schema import (  # noqa: E402
    EMPTY_STATE, EVENT_TYPES, GENERATE_EVENT_TYPES, STAGES, STATUSES,
    valid_state,
)
from state_store import (  # noqa: E402
    ConflictError, ValidationError, atomic_write, load_unlocked, read_json,
    save_cas,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 工作目录
WEB = os.path.join(ROOT, "studio", "web")
PROJECTS = os.path.join(ROOT, "projects")
KNOWLEDGE = os.path.join(ROOT, "studio", "knowledge")

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".mp4": "video/mp4", ".webm": "video/webm",
    ".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4",
    ".ogg": "audio/ogg", ".flac": "audio/flac", ".aac": "audio/aac",
    ".json": "application/json; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
}
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".ogg", ".flac", ".aac"}
UPLOAD_PREFIXES = ("characters/", "dialogues/")
MAX_UPLOAD = 15 * 1024 * 1024

def load_config(path):
    return read_json(path, {})


def safe_name(name):
    if not name or not re.fullmatch(r"[\w一-鿿\- ]{1,64}", name):
        return None
    return name.strip()


def pdir(name):
    return os.path.join(PROJECTS, name)


def safe_join(base, rel):
    """base + rel 的 realpath；越出 base 一律拒绝（防路径穿越）。"""
    base = os.path.realpath(base)
    full = os.path.realpath(os.path.join(base, rel))
    return full if (full == base or full.startswith(base + os.sep)) else None


def legacy_scene_dialogue_rel(rel):
    """v2 旧场次台词轨 dialogues/scNN*.wav，不是逐句 utNN。"""
    rel = str(rel or "").replace("\\", "/")
    if not rel.startswith("dialogues/"):
        return False
    return bool(re.match(r"sc\d+", os.path.basename(rel), re.I))


class Handler(BaseHTTPRequestHandler):
    server_version = "VideoStudio/1.0"

    def log_message(self, fmt, *args):  # 精简日志
        sys.stderr.write("[studio] %s\n" % (fmt % args))

    # ---------- helpers ----------
    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False),
                   "application/json; charset=utf-8")

    def _body(self):
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n)
        return json.loads(raw.decode("utf-8")) if raw.strip() else {}

    def _project_from_path(self, prefix):
        """'/api/project/<name>/rest' -> (name, rest)"""
        rest = self.path[len(prefix):]
        parts = rest.lstrip("/").split("/", 1)
        name = urllib.parse.unquote(parts[0])
        tail = parts[1] if len(parts) > 1 else ""
        name = safe_name(name)
        if not name or not os.path.isdir(pdir(name)):
            return None, None
        return name, tail

    # ---------- GET ----------
    def do_GET(self):
        path = urllib.parse.unquote(urllib.parse.urlparse(self.path).path)
        if path == "/" or path == "/index.html":
            return self._serve_file(os.path.join(WEB, "index.html"))
        if path.startswith("/web/"):
            return self._serve_file(os.path.join(WEB, path[5:]))
        if path == "/api/projects":
            names = sorted(d for d in os.listdir(PROJECTS)
                           if os.path.isdir(pdir(d))) if os.path.isdir(PROJECTS) else []
            return self._json({"projects": names})
        m = re.fullmatch(r"/api/project/([^/]+)/state", path)
        if m:
            name = safe_name(urllib.parse.unquote(m.group(1)))
            if not name:
                return self._json({"error": "bad name"}, 400)
            st = read_json(os.path.join(pdir(name), "state.json"),
                           {**EMPTY_STATE, "name": name})
            planning.stamp_media_identities(st, pdir(name))
            return self._json(st)
        m = re.fullmatch(r"/api/project/([^/]+)/events", path)
        if m:
            name = safe_name(urllib.parse.unquote(m.group(1)))
            if not name:
                return self._json({"error": "bad name"}, 400)
            pending = eventlog.pending(pdir(name))
            return self._json({"events": pending})
        m = re.fullmatch(r"/api/project/([^/]+)/(planning|jobs|compile)", path)
        if m:
            name = safe_name(urllib.parse.unquote(m.group(1)))
            kind = m.group(2)
            if not name or not os.path.isdir(pdir(name)):
                return self._json({"error": "project not found"}, 404)
            state = load_unlocked(pdir(name), {**EMPTY_STATE, "name": name})
            if kind == "planning":
                return self._json(planning.recompute(state))
            if kind == "jobs":
                return self._json(joblog.load_index(pdir(name)))
            items = prompt_compiler.compile_all(state, project_dir=pdir(name))
            units = []
            for u in sboard.computed_units(state):
                try:
                    units.append(prompt_compiler.compile_unit(
                        state, u["id"], project_dir=pdir(name)))
                except (KeyError, ValueError) as e:
                    units.append({"unit_id": u.get("id"), "error": str(e)})
            return self._json({"items": items, "units": units})
        m = re.fullmatch(r"/api/project/([^/]+)/storyboard", path)
        if m:
            name = safe_name(urllib.parse.unquote(m.group(1)))
            if not name or not os.path.isdir(pdir(name)):
                return self._json({"error": "project not found"}, 404)
            state = load_unlocked(pdir(name), {**EMPTY_STATE, "name": name})
            return self._json(sboard.build_view(state, project_dir=pdir(name)))
        m = re.fullmatch(r"/api/project/([^/]+)/frames", path)
        if m:  # 可复用帧清单：keyframes/ 下所有图片，供页面「选现有帧」直接指派
            name = safe_name(urllib.parse.unquote(m.group(1)))
            if not name or not os.path.isdir(pdir(name)):
                return self._json({"error": "project not found"}, 404)
            kd = os.path.join(pdir(name), "keyframes")
            frames = []
            if os.path.isdir(kd):
                for fn in os.listdir(kd):
                    if os.path.splitext(fn)[1].lower() in (".png", ".jpg", ".jpeg"):
                        full = os.path.join(kd, fn)
                        frames.append({"file": "keyframes/" + fn,
                                       "mtime": os.path.getmtime(full)})
            frames.sort(key=lambda x: -x["mtime"])  # 最新生成的排最前
            return self._json({"frames": frames})
        if path == "/api/knowledge":  # 知识库文档清单
            docs = []
            if os.path.isdir(KNOWLEDGE):
                for root, dirs, files in os.walk(KNOWLEDGE):
                    dirs.sort()
                    for fn in sorted(files):
                        if os.path.splitext(fn)[1].lower() in (".md", ".txt"):
                            full = os.path.join(root, fn)
                            docs.append({"path": os.path.relpath(full, KNOWLEDGE),
                                         "size": os.path.getsize(full)})
            return self._json({"docs": docs})
        if path == "/api/worker":  # 自动执行 worker 状态（agent_worker.py 心跳）
            st = read_json(os.path.join(ROOT, "studio", "worker_status.json"), {})
            online = bool(st.get("alive")) and time.time() - st.get("now", 0) < 30
            desired = read_json(os.path.join(ROOT, "studio", "desired_model.json"), {})
            return self._json({"online": online, "busy": bool(st.get("busy")),
                               "project": st.get("project", ""),
                               "last_push": st.get("last_push", 0),
                               "last_result": st.get("last_result", ""),
                               "model": st.get("model", ""),
                               "desired_model": str(desired.get("model", ""))})
        if path == "/api/model":  # 执行模型解析（settings 链 + 会话实测 + 可用清单）
            info = model_info.resolve_model_info()
            info["available"] = model_info.list_models()
            return self._json(info)
        if path == "/api/config/global":  # 全局配置（记忆）
            return self._json(load_config(
                os.path.join(ROOT, "studio", "config", "global.json")))
        m = re.fullmatch(r"/api/project/([^/]+)/config", path)
        if m:  # 项目配置（记忆）
            name = safe_name(urllib.parse.unquote(m.group(1)))
            if not name or not os.path.isdir(pdir(name)):
                return self._json({"error": "project not found"}, 404)
            return self._json(load_config(os.path.join(pdir(name), "config.json")))
        if path.startswith("/knowledge/"):  # 知识库文档（只读）
            full = safe_join(KNOWLEDGE, path[len("/knowledge/"):])
            if full:
                return self._serve_file(full)
            return self._json({"error": "forbidden"}, 403)
        if path.startswith("/projects/"):
            full = safe_join(PROJECTS, path[len("/projects/"):])
            if full:
                return self._serve_file(full)
            return self._json({"error": "forbidden"}, 403)
        return self._json({"error": "not found"}, 404)

    def _serve_file(self, full):
        if not os.path.isfile(full):
            return self._json({"error": "not found"}, 404)
        ext = os.path.splitext(full)[1].lower()
        ctype = MIME.get(ext, "application/octet-stream")
        size = os.path.getsize(full)
        # Range 支持：视频阶段 mp4 拖动进度条必需
        rng = self.headers.get("Range", "")
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", rng.strip()) if rng else None
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start, end = int(m.group(1)), min(int(m.group(2) or size - 1), size - 1)
            else:  # 后缀 Range: bytes=-N
                start, end = max(0, size - int(m.group(2))), size - 1
            if start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            self.send_response(206)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with open(full, "rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = f.read(min(65536, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            return
        with open(full, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # ---------- POST / PUT ----------
    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path

        if path == "/api/project":  # 新建项目
            name = safe_name(str(self._body().get("name", "")))
            if not name:
                return self._json({"error": "bad name"}, 400)
            d = pdir(name)
            for sub in ("characters", "scenes", "props", "storyboards",
                        "keyframes", "videos", "dialogues"):
                os.makedirs(os.path.join(d, sub), exist_ok=True)
            st = os.path.join(d, "state.json")
            if not os.path.exists(st):
                with open(st, "w", encoding="utf-8") as f:
                    json.dump({**EMPTY_STATE, "name": name}, f,
                              ensure_ascii=False, indent=2)
            open(os.path.join(d, "events.jsonl"), "a").close()
            return self._json({"ok": True, "name": name})

        if path == "/api/worker/model":  # 页面切换执行模型 → worker 巡检自动重建会话
            body = self._body()
            m = str(body.get("model", "")).strip()
            if m and not re.fullmatch(r"[A-Za-z0-9._/\-:]{1,100}", m):
                return self._json({"error": "bad model name"}, 400)
            atomic_write(os.path.join(ROOT, "studio", "desired_model.json"),
                         json.dumps({"model": m, "ts": time.time()},
                                    ensure_ascii=False))
            return self._json({"ok": True, "model": m})

        name, tail = self._project_from_path("/api/project/")
        if not name:
            return self._json({"error": "project not found"}, 404)

        if tail == "script":  # 上传剧本 {"text": ...}
            text = str(self._body().get("text", ""))
            if not text.strip():
                return self._json({"error": "empty script"}, 400)
            with open(os.path.join(pdir(name), "script.txt"), "w",
                      encoding="utf-8") as f:
                f.write(text)
            return self._json({"ok": True})

        if tail == "event":  # 页面按钮事件（类型白名单校验）
            body = self._body()
            ev_type = str(body.get("type", ""))
            need_target = EVENT_TYPES.get(ev_type)
            if need_target is None:
                return self._json(
                    {"error": "unknown type", "allowed": sorted(EVENT_TYPES)}, 400)
            if need_target and not str(body.get("target", "")):
                return self._json({"error": "target required for " + ev_type}, 400)
            state = load_unlocked(pdir(name), {})
            mode = (state.get("execution") or {}).get("mode", "running")
            if ev_type in GENERATE_EVENT_TYPES and mode != "running":
                return self._json({"error": "execution paused", "mode": mode}, 423)
            target = str(body.get("target", ""))
            client_rev = body.get("target_revision")
            current_ver = planning.resource_version(state, ev_type, target,
                                                     project_dir=pdir(name))
            if ev_type.startswith("approve_"):
                if client_rev is None or client_rev == "":
                    return self._json({"error": "target_revision required for approve",
                                       "current": current_ver}, 400)
                if str(client_rev) != str(current_ver):
                    return self._json({"error": "target_revision conflict",
                                       "current": current_ver,
                                       "submitted": client_rev}, 409)
                stored_rev = client_rev
            else:
                stored_rev = client_rev if client_rev is not None else state.get("revision")
            ev = eventlog.new_event(
                ev_type, target, body.get("note", ""),
                target_revision=stored_rev,
                frame=planning.frame_event_key(ev_type) or body.get("frame"))
            if body.get("scope"):
                ev["scope"] = str(body.get("scope"))
            if body.get("input_hash"):
                ev["input_hash"] = str(body.get("input_hash"))
            if body.get("request_id"):
                ev["request_id"] = str(body.get("request_id"))
            eventlog.append_event(pdir(name), ev)
            code = 202 if ev_type == "standardize_storyboard" else 200
            return self._json({"ok": True, "event_id": ev["event_id"],
                               "target_revision": stored_rev}, code)

        if tail == "archive":  # 版本归档：{"path": "keyframes/xxx.png"} → 移入 versions/ 加时间戳
            rel = str(self._body().get("path", "")).replace("\\", "/")
            st = load_unlocked(pdir(name), {})
            if int(st.get("schema_version") or 1) >= 2 and legacy_scene_dialogue_rel(rel):
                return self._json({
                    "error": "legacy dialogues are read-only; upload utterance wav via shots",
                }, 400)
            full = safe_join(pdir(name), rel)
            if not full or not os.path.isfile(full):
                return self._json({"error": "file not found"}, 404)
            base, ext = os.path.splitext(os.path.basename(full))
            vdir = os.path.join(os.path.dirname(full), "versions")
            os.makedirs(vdir, exist_ok=True)
            ts = time.strftime("%Y%m%d-%H%M%S")
            dst = os.path.join(vdir, f"{base}__{ts}{ext}")
            n = 1
            while os.path.exists(dst):  # 同一秒内多次归档加序号
                dst = os.path.join(vdir, f"{base}__{ts}_{n}{ext}")
                n += 1
            os.rename(full, dst)
            rel_dst = os.path.relpath(dst, pdir(name))
            return self._json({"ok": True, "archived": rel_dst})

        if tail == "events/ack":  # 按 event_id 确认；禁止整文件清空
            body = self._body()
            ids = body.get("ids") or body.get("event_ids") or []
            if body.get("event_id"):
                ids = list(ids) + [body["event_id"]]
            if not ids:
                return self._json({"error": "ids required; whole-queue ack is disabled"}, 400)
            acked = eventlog.ack_ids(pdir(name), ids)
            return self._json({"ok": True, "acked": acked})

        if tail == "execution":  # 正式暂停 / 恢复
            body = self._body()
            mode = str(body.get("mode", ""))
            if mode not in ("paused", "running"):
                return self._json({"error": "mode must be paused or running"}, 400)
            expected = body.get("revision")
            state = load_unlocked(pdir(name), {})
            if expected is None:
                expected = state.get("revision")
            state["execution"] = {
                "mode": mode,
                "reason": str(body.get("reason", "")),
                "paused_at": time.strftime("%Y-%m-%dT%H:%M:%S") if mode == "paused" else "",
            }
            try:
                written = save_cas(pdir(name), state, expected_revision=expected)
            except ConflictError as e:
                return self._json({"error": "revision conflict",
                                   "current_revision": e.current_revision}, 409)
            except ValidationError as e:
                return self._json({"error": str(e)}, 400)
            return self._json({"ok": True, "revision": written["revision"],
                               "execution": written.get("execution")})

        if tail in ("storyboard/edit", "storyboard/review", "storyboard/prompt-result",
                    "storyboard/migrate"):
            return self._storyboard_post(name, tail, self._body())

        if tail == "jobs/submit":
            return self._jobs_submit(name, self._body())

        if tail == "upload":  # 音频上传 {"path":"characters/x_voice.wav","data":"<base64>"}
            body = self._body()
            rel = str(body.get("path", "")).replace("\\", "/")
            if rel.startswith("/") or ".." in rel.split("/"):
                return self._json({"error": "bad path"}, 400)
            if not rel.startswith(UPLOAD_PREFIXES):
                return self._json({"error": "path must be under characters/ or dialogues/"}, 400)
            st = load_unlocked(pdir(name), {})
            if int(st.get("schema_version") or 1) >= 2 and rel.startswith("dialogues/"):
                return self._json({
                    "error": "legacy dialogues are read-only; upload utterance wav via shots",
                }, 400)
            ext = os.path.splitext(rel)[1].lower()
            if ext not in AUDIO_EXT:
                return self._json({"error": "unsupported audio type",
                                   "allowed": sorted(AUDIO_EXT)}, 400)
            try:
                b64 = str(body.get("data", "")).strip()
                b64 += "=" * ((-len(b64)) % 4)
                raw = base64.b64decode(b64, validate=False)
            except (ValueError, TypeError):
                return self._json({"error": "invalid base64"}, 400)
            if not raw or len(raw) > MAX_UPLOAD:
                return self._json({"error": "empty or too large (max 15MB)"}, 400)
            digest = hashlib.sha256(raw).hexdigest()
            folder = os.path.dirname(rel) or "characters"
            stem = os.path.splitext(os.path.basename(rel))[0]
            stem = re.sub(r"__[0-9a-f]{8,64}$", "", stem, flags=re.I)
            out_rel = f"{folder}/{stem}__{digest[:16]}{ext}"
            full = safe_join(pdir(name), out_rel)
            if not full:
                return self._json({"error": "bad path"}, 400)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "wb") as f:
                f.write(raw)
            return self._json({"ok": True, "path": out_rel, "bytes": len(raw),
                               "sha256": digest, "revision_id": digest})

        return self._json({"error": "not found"}, 404)

    def do_PUT(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/config/global":  # 全局配置（记忆）
            body = self._body()
            if not isinstance(body, dict):
                return self._json({"error": "config must be a JSON object"}, 400)
            atomic_write(os.path.join(ROOT, "studio", "config", "global.json"),
                         json.dumps(body, ensure_ascii=False, indent=2))
            return self._json({"ok": True})
        m = re.fullmatch(r"/api/project/([^/]+)/config", path)
        if m:  # 项目配置（记忆）
            name = safe_name(urllib.parse.unquote(m.group(1)))
            if not name or not os.path.isdir(pdir(name)):
                return self._json({"error": "project not found"}, 404)
            body = self._body()
            if not isinstance(body, dict):
                return self._json({"error": "config must be a JSON object"}, 400)
            atomic_write(os.path.join(pdir(name), "config.json"),
                         json.dumps(body, ensure_ascii=False, indent=2))
            return self._json({"ok": True})
        m = re.fullmatch(r"/api/project/([^/]+)/state", path)
        if not m:
            return self._json({"error": "not found"}, 404)
        name = safe_name(urllib.parse.unquote(m.group(1)))
        if not name or not os.path.isdir(pdir(name)):
            return self._json({"error": "project not found"}, 404)
        state = self._body()
        if not valid_state(state):
            return self._json({"error": "invalid state: stage/status/type/id 校验未通过",
                               "allowed": {"stages": sorted(STAGES),
                                           "statuses": sorted(STATUSES)}}, 400)
        state["name"] = name
        expected = state.get("revision")
        current = load_unlocked(pdir(name), {})
        if current.get("schema_version") == 2 and expected is None:
            return self._json({"error": "revision required",
                               "current_revision": current.get("revision")}, 409)
        if current.get("schema_version") == 2:
            if planning.scenes_were_edited(current, state):
                return self._json({
                    "error": "legacy scenes are read-only; edit shots/utterances on the board",
                    "current_revision": current.get("revision"),
                }, 400)
            planning.stamp_media_identities(current, pdir(name))
            planning.carry_media_identities(current, state)
            planning.propagate_put(current, state)
        try:
            written = save_cas(pdir(name), state, expected_revision=expected)
        except ConflictError as e:
            return self._json({"error": "revision conflict",
                               "current_revision": e.current_revision}, 409)
        except ValidationError as e:
            return self._json({"error": str(e)}, 400)
        return self._json({"ok": True, "revision": written.get("revision")})

    def do_PATCH(self):
        path = urllib.parse.urlparse(self.path).path
        m = re.fullmatch(r"/api/project/([^/]+)/state", path)
        if not m:
            return self._json({"error": "not found"}, 404)
        name = safe_name(urllib.parse.unquote(m.group(1)))
        if not name or not os.path.isdir(pdir(name)):
            return self._json({"error": "project not found"}, 404)
        body = self._body()
        expected = body.get("revision")
        current = load_unlocked(pdir(name), {})
        if expected is None:
            return self._json({"error": "revision required",
                               "current_revision": current.get("revision")}, 409)
        if current.get("revision") != expected:
            return self._json({"error": "revision conflict",
                               "current_revision": current.get("revision")}, 409)
        try:
            plan_changed = []
            for item in body.get("shots") or []:
                sboard.reject_forged_review_fields(item)
                sh0 = next((s for s in (current.get("shots") or []) if s.get("id") == item["id"]), {})
                before = sboard.plan_hash_shot(current, sh0)
                planning.apply_shot_patch(current, item["id"], item,
                                          project_dir=pdir(name))
                sh1 = next((s for s in (current.get("shots") or []) if s.get("id") == item["id"]), {})
                if sboard.plan_hash_shot(current, sh1) != before:
                    plan_changed.append(item["id"])
            for item in body.get("utterances") or []:
                planning.apply_utterance_patch(current, item["id"], item)
            for item in body.get("generation_units") or []:
                sboard.apply_unit_patch(current, item["id"], item)
                plan_changed.extend(item.get("shot_ids") or [])
            for item in body.get("sequences") or []:
                seq = next((s for s in (current.get("sequences") or [])
                            if s.get("id") == item.get("id")), None)
                if not seq:
                    raise KeyError(item.get("id"))
                for k, v in item.items():
                    if k in ("id", "storyboard_review"):
                        continue
                    seq[k] = v
                plan_changed.extend(seq.get("shot_ids") or [])
            if plan_changed:
                sboard.invalidate_plan(current, shot_ids=plan_changed, reason="shot edit")
            if body.get("execution"):
                mode = body["execution"].get("mode")
                if mode in ("paused", "running"):
                    current["execution"] = {
                        **(current.get("execution") or {}),
                        **body["execution"],
                    }
            written = save_cas(pdir(name), current, expected_revision=expected)
        except KeyError as e:
            return self._json({"error": f"unknown id {e}"}, 400)
        except ValueError as e:
            return self._json({"error": str(e)}, 400)
        except ConflictError as e:
            return self._json({"error": "revision conflict",
                               "current_revision": e.current_revision}, 409)
        except ValidationError as e:
            return self._json({"error": str(e)}, 400)
        return self._json({"ok": True, "revision": written.get("revision"),
                           "state": written})

    def _storyboard_post(self, name, tail, body):
        expected = body.get("revision")
        current = load_unlocked(pdir(name), {})
        if expected is None:
            return self._json({"error": "revision required",
                               "current_revision": current.get("revision")}, 409)
        rid = body.get("request_id")
        body_hash = sboard.content_hash({k: body.get(k) for k in body if k != "request_id"})
        if rid:
            prev = sboard.lookup_request(pdir(name), rid)
            if prev:
                if prev.get("body_hash") != body_hash:
                    return self._json({"error": "request_id conflict",
                                       "request_id": rid}, 409)
                if prev.get("result"):
                    return self._json(prev["result"], int(prev["result"].get("_http") or 200))
        if tail == "storyboard/migrate":
            if body.get("dry_run", True):
                return self._json(sboard.migrate_dry_run(current))
            if current.get("revision") != expected:
                return self._json({"error": "revision conflict",
                                   "current_revision": current.get("revision")}, 409)
            sboard.migrate_apply(current)
            try:
                written = save_cas(pdir(name), current, expected_revision=expected)
            except ConflictError as e:
                return self._json({"error": "revision conflict",
                                   "current_revision": e.current_revision}, 409)
            except ValidationError as e:
                return self._json({"error": str(e)}, 400)
            return self._json({"ok": True, "revision": written.get("revision"),
                               "dry_run": False})
        if current.get("revision") != expected:
            return self._json({"error": "revision conflict",
                               "current_revision": current.get("revision")}, 409)
        try:
            if tail == "storyboard/edit":
                op = str(body.get("op") or "")
                if body.get("preview"):
                    preview_state = json.loads(json.dumps(current))
                    result = sboard.apply_edit(preview_state, op, body)
                    return self._json({"ok": True, "preview": True, "result": result,
                                       "planning": preview_state.get("planning")})
                result = sboard.apply_edit(current, op, body)
            elif tail == "storyboard/review":
                rec = sboard.apply_review(
                    current, str(body.get("scope") or ""),
                    str(body.get("target") or ""),
                    str(body.get("decision") or ""),
                    str(body.get("content_hash") or ""),
                    note=str(body.get("note") or ""),
                    source=str(body.get("source") or "human"),
                    request_id=rid)
                result = {"review": rec}
            elif tail == "storyboard/prompt-result":
                pack = sboard.accept_prompt_result(current, body)
                result = {"prompt": pack}
            else:
                return self._json({"error": "not found"}, 404)
            written = save_cas(pdir(name), current, expected_revision=expected)
        except KeyError as e:
            return self._json({"error": f"unknown id {e}"}, 404)
        except ValueError as e:
            return self._json({"error": str(e)}, 400)
        except PermissionError as e:
            return self._json({"error": str(e)}, 409)
        except ConflictError as e:
            return self._json({"error": "revision conflict",
                               "current_revision": e.current_revision}, 409)
        except ValidationError as e:
            return self._json({"error": str(e)}, 400)
        out = {"ok": True, "revision": written.get("revision"), **result,
               "state": written}
        if rid:
            sboard.remember_request(pdir(name), rid, body_hash,
                                    {k: out[k] for k in out if k != "state"})
        return self._json(out)

    def _jobs_submit(self, name, body):
        state = load_unlocked(pdir(name), {})
        mode = (state.get("execution") or {}).get("mode", "running")
        if mode != "running":
            return self._json({"error": "execution paused", "mode": mode}, 423)
        expected = body.get("revision")
        if expected is None:
            return self._json({"error": "revision required",
                               "current_revision": state.get("revision")}, 409)
        if expected != state.get("revision"):
            return self._json({"error": "revision conflict",
                               "current_revision": state.get("revision")}, 409)
        unit_id = str(body.get("unit_id") or "")
        shot_id = str(body.get("shot_id") or "")
        if not unit_id and shot_id:
            units = [u for u in sboard.computed_units(state)
                     if shot_id in (u.get("shot_ids") or [])]
            if len(units) != 1:
                return self._json({"error": "ambiguous unit mapping", "shot_id": shot_id}, 400)
            unit_id = units[0]["id"]
        if not unit_id:
            return self._json({"error": "unit_id required"}, 400)
        try:
            manifest = prompt_compiler.compile_unit(state, unit_id, project_dir=pdir(name))
        except KeyError:
            return self._json({"error": "unit not found"}, 404)
        except ValueError as e:
            return self._json({"error": str(e)}, 400)
        cap = sboard.capability(state)
        cm = manifest.get("conditioning_mode")
        if cm == "last_only" and not cap.get("last_only_verified"):
            return self._json({"error": "last_only not verified", "unit_id": unit_id}, 409)
        if cm not in ("first_only", "first_last", "last_only"):
            return self._json({"error": "illegal mode"}, 400)
        sb = state.get("storyboard") or {}
        if sb.get("status") != "approved" or sb.get("validity") == "stale":
            return self._json({"error": "project storyboard not approved/current"}, 409)
        input_hash = str(body.get("input_hash") or sboard.content_hash(manifest))
        live_hash = sboard.content_hash({
            "unit": unit_id, "revision": state.get("revision"),
            "frames": manifest.get("frames"),
            "prompt": manifest.get("prompt_revision_id"),
        })
        if body.get("input_hash") and body.get("input_hash") != live_hash and body.get("input_hash") != input_hash:
            return self._json({"error": "stale input_hash"}, 409)
        rid = body.get("request_id")
        key = joblog.idempotency_key(name, str(state.get("revision")), "render:" + unit_id, input_hash)
        job = joblog.new_job(
            "render", unit_id, status="queued",
            idempotency_key=key, unit_id=unit_id,
            input_hash=input_hash, request_id=rid or "",
            manifest=manifest, revision=state.get("revision"))
        stored, created = joblog.register(pdir(name), job, expected_key=key)
        if cap.get("simulate_jobs"):
            joblog.set_status(pdir(name), stored["job_id"], "running")
            joblog.set_status(
                pdir(name), stored["job_id"], "done",
                outputs=[{"kind": "simulated", "file": "", "status": "review"}],
                simulated=True)
            stored = next((j for j in joblog.load_index(pdir(name)).get("jobs") or []
                           if j.get("job_id") == stored["job_id"]), stored)
        return self._json({"ok": True, "job_id": stored["job_id"],
                           "status": stored.get("status"),
                           "input_hash": input_hash,
                           "unit_id": unit_id,
                           "created": created,
                           "manifest": manifest}, 202)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8931
    os.makedirs(PROJECTS, exist_ok=True)
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"视频工作室已启动: http://localhost:{port}  (Ctrl+C 停止)")
    srv.serve_forever()


if __name__ == "__main__":
    main()
