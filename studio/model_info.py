#!/usr/bin/env python3
"""执行模型解析模块：读 claude CLI 的 settings.json 链，解析当前生效的 agent 模型。

模型是影响生成质量的因素之一，worker / server / 页面 / 参数记录都用它统一口径。

解析层级（settings.json 本身的优先级：local > project > user）：
  1. CLAUDE_MODEL 环境变量（worker 启动时转成 CLI --model，最高优先）
  2. ANTHROPIC_MODEL（shell 环境或 settings 的 env 块）
  3. settings 的 "model" 字段（local > project > user）
  4. 兜底：有 ANTHROPIC_BASE_URL 即走中转默认（未显式指定）；否则 CLI 内置默认

另提供「实测模型」：读 ~/.claude/projects/<cwd编码>/ 最新会话记录里 API 实际
返回的 model 字段——中转服务改默认模型时，实测值比静态解析更可信。

仅标准库；不返回任何 token/密钥。
用法: python3 studio/model_info.py   （命令行直接打印 JSON）
"""
import glob
import json
import os
import re
import ssl
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLAUDE_DIR = os.path.expanduser("~/.claude")

SETTINGS_FILES = [  # 低优先在前
    os.path.join(CLAUDE_DIR, "settings.json"),          # user 层
    os.path.join(ROOT, ".claude", "settings.json"),     # 项目共享层
    os.path.join(ROOT, ".claude", "settings.local.json"),  # 项目本地层（最高）
]


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def read_settings():
    """合并三层 settings，返回 (merged_env, model_chain)。model_chain 供展示来源。"""
    env, chain = {}, []
    for path in SETTINGS_FILES:
        d = _read_json(path)
        if not d:
            continue
        if isinstance(d.get("env"), dict):
            env.update(d["env"])
        if d.get("model"):
            chain.append({"source": os.path.relpath(path, ROOT)
                          if path.startswith(ROOT) else os.path.basename(path),
                          "model": str(d["model"])})
    return env, chain


def transcript_dir(cwd=ROOT):
    """claude CLI 会话记录目录：路径中非字母数字字符替换为 '-'。"""
    enc = re.sub(r"[^A-Za-z0-9]", "-", cwd)
    return os.path.join(CLAUDE_DIR, "projects", enc)


def observe_last_model(cwd=ROOT, max_lines=400):
    """从该 cwd 最新的会话记录里取最近一条 assistant 回复的 model 字段。"""
    tdir = transcript_dir(cwd)
    files = sorted(glob.glob(os.path.join(tdir, "*.jsonl")), key=os.path.getmtime)
    if not files:
        return None
    model = None
    try:
        with open(files[-1], encoding="utf-8", errors="replace") as f:
            lines = f.readlines()[-max_lines:]
    except OSError:
        return None
    for line in reversed(lines):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("type") == "assistant":
            m = d.get("message", {}).get("model")
            if m:
                model = m
                break
    return {"model": model,
            "file": os.path.basename(files[-1]),
            "mtime": os.path.getmtime(files[-1])}


def resolve_model_info():
    """汇总静态解析与实测，返回统一视图（不含密钥）。"""
    env, chain = read_settings()
    explicit = os.environ.get("CLAUDE_MODEL") \
        or os.environ.get("ANTHROPIC_MODEL") \
        or env.get("ANTHROPIC_MODEL") \
        or (chain[-1]["model"] if chain else None)
    base_url = os.environ.get("ANTHROPIC_BASE_URL") or env.get("ANTHROPIC_BASE_URL")
    if explicit:
        effective, source = explicit, (
            "CLAUDE_MODEL env" if os.environ.get("CLAUDE_MODEL")
            else "ANTHROPIC_MODEL" if (os.environ.get("ANTHROPIC_MODEL")
                                       or env.get("ANTHROPIC_MODEL"))
            else f"settings model ({chain[-1]['source']})" if chain else "?")
    elif base_url:
        effective, source = None, "中转默认（settings env.ANTHROPIC_BASE_URL，未显式指定）"
    else:
        effective, source = None, "CLI 内置默认（无显式配置）"
    obs = observe_last_model()
    return {
        "effective": effective,          # 静态解析出的显式模型（可能为 None=走默认）
        "source": source,                # 生效来源说明
        "observed": obs and obs["model"],  # 会话记录实测的 API 实际模型（最可信）
        "observed_at": obs and obs["mtime"],
        "relay": bool(base_url),
        "base_url": base_url,
        "settings_model_chain": chain,   # 三层 settings 里的 model 字段（低→高）
        "resolved_at": time.time(),
    }


def display(info=None):
    """给人看的一句话：优先实测值，其次静态值。"""
    info = info or resolve_model_info()
    return info.get("observed") or info.get("effective") or "未知（走中转/CLI 默认）"


# ---------- 可用模型清单（页面下拉框数据源） ----------
_models_cache = {"ts": 0.0, "models": None, "source": ""}


def list_models(max_age=300, timeout=8):
    """从 settings 的中转地址拉取可用模型（GET /v1/models，缓存 5 分钟）。

    中转为企业自签证书，此处跳过校验（本机工具、token 不出本机）；
    拉取失败时兜底用 global.json 的 agent_models 候选清单。"""
    now = time.time()
    if _models_cache["models"] is not None and now - _models_cache["ts"] < max_age:
        return dict(_models_cache)
    env, _ = read_settings()
    base = (env.get("ANTHROPIC_BASE_URL") or "").rstrip("/")
    token = env.get("ANTHROPIC_AUTH_TOKEN") or env.get("ANTHROPIC_API_KEY") or ""
    models, source = None, ""
    if base and token:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(base + "/v1/models", headers={
            "x-api-key": token, "Authorization": "Bearer " + token,
            "anthropic-version": "2023-06-01"})
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                d = json.loads(r.read().decode("utf-8", "replace"))
            ids = sorted({m.get("id") for m in d.get("data", []) if m.get("id")})
            if ids:
                models, source = ids, "relay /v1/models"
        except (OSError, ValueError):
            pass
    if models is None:
        g = _read_json(os.path.join(ROOT, "studio", "config", "global.json"), {})
        models, source = [m for m in g.get("agent_models", []) if m], "global.json 兜底"
    _models_cache.update(ts=now, models=models, source=source)
    return dict(_models_cache)


if __name__ == "__main__":
    print(json.dumps(resolve_model_info(), ensure_ascii=False, indent=2))
    print("=> 生效模型:", display())
