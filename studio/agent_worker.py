#!/usr/bin/env python3
"""视频工作室自动执行 worker：Claude Agent SDK 常驻会话 + 事件监听。

页面按钮写入 events.jsonl 后无需在对话里喊「处理一下」，本 worker 检测到
新事件即向常驻 Claude 会话注入任务消息，立即消费。会话跨点击保持上下文。

每次任务注入的内容（用户要求的三件套）：
  1. 系统提示词（session 级，稳定规则）+ 项目 .claude/skills 自动加载的标准 skill
  2. 动态任务上下文头（stage / 事件清单 / 约束 / 重试政策 / 执行模型，实时生成）
  3. 配置记忆：studio/config/global.json + projects/<name>/config.json（生成前必读）

模型切换：页面 POST /api/worker/model 写 studio/desired_model.json，本进程
巡检发现与当前会话不一致时干净退出本会话，supervise 用新模型重建（boot 重载协议）。

依赖: studio/.venv 里的 claude-agent-sdk（claude CLI 需已登录）
启动: nohup studio/.venv/bin/python studio/agent_worker.py >> studio/worker.log 2>&1 &
状态: studio/worker_status.json（server GET /api/worker 读取；心跳 30s 内算在线）
降级: worker 不在线时照旧——用户在对话里喊「处理一下」，协议不变。
"""
import asyncio
import json
import os
import shutil
import time
import traceback

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, ResultMessage

from model_info import display as model_display, resolve_model_info
import events as eventlog  # noqa: E402
from worker_schedule import (  # noqa: E402
    filter_ready, finish_dispatch, load_attempts, prune_attempts,
    prune_dispatched, record_attempt, save_attempts,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECTS = os.path.join(ROOT, "projects")
STATUS_PATH = os.path.join(ROOT, "studio", "worker_status.json")
DESIRED_MODEL_PATH = os.path.join(ROOT, "studio", "desired_model.json")
SKILL = os.path.expanduser("~/.agents/skills/video-studio/SKILL.md")
GLOBAL_CFG = os.path.join(ROOT, "studio", "config", "global.json")

POLL_SEC = 1.0          # 事件文件 mtime 轮询间隔
QUIET_SEC = 2.0         # 去抖：事件文件静默这么久才触发，合并一波连点
HEARTBEAT_SEC = 10.0
MAX_CRASHES = 5         # 连续崩溃次数上限


def write_status(**kw):
    st = {}
    try:
        st.update(json.load(open(STATUS_PATH)) if os.path.exists(STATUS_PATH) else {})
    except (OSError, ValueError):
        pass
    st.update({"pid": os.getpid(), "now": time.time()})  # 心跳时间以本次为准
    st.update(kw)
    tmp = STATUS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)
    os.replace(tmp, STATUS_PATH)


def read_json(path, default):
    try:
        return json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return default


def scan_events():
    """返回 {项目名: 最新未确认事件 ts}，只含未 ack 队列。"""
    out = {}
    if not os.path.isdir(PROJECTS):
        return out
    for name in os.listdir(PROJECTS):
        try:
            queue = eventlog.pending(os.path.join(PROJECTS, name))
        except OSError:
            continue
        if queue:
            out[name] = max(e.get("ts") or 0 for e in queue)
    return out


def read_queue(name):
    return eventlog.pending(os.path.join(PROJECTS, name))


def read_desired_model():
    """页面写入的目标模型；空 = 跟随中转/CLI 默认（不显式指定）。"""
    d = read_json(DESIRED_MODEL_PATH, {})
    return str(d.get("model", "")).strip() or None


# ---------- 每次任务注入的动态上下文头 ----------
def build_task_message(name, ready=None):
    state = read_json(os.path.join(PROJECTS, name, "state.json"), {})
    gcfg = read_json(GLOBAL_CFG, {})
    pcfg = read_json(os.path.join(PROJECTS, name, "config.json"), {})
    queue = list(ready) if ready is not None else read_queue(name)
    exe = (state.get("execution") or {})
    mode = exe.get("mode") or "running"
    line_strs = [
        f"  - {e.get('event_id', '?')} | {e.get('type', '?')} -> {e.get('target') or '-'}"
        + (f"（备注: {e['note']}）" if e.get("note") else "")
        for e in queue
    ] or ["  （读取时为空——可能已被并发消费）"]
    ids = [e.get("event_id") for e in queue if e.get("event_id")]
    rules = gcfg.get("rules", {})
    retry = {**gcfg.get("retry_defaults", {}), **pcfg.get("retry", {})}
    pause_line = (
        "生成已暂停：只处理审核类事件（approve/remove/restore）；"
        "不要提交 GPU/TTS 任务，不要把遗留 generating 当完成或自动重提。"
        if mode != "running" else "执行模式 running。"
    )
    return "\n".join([
        "【任务上下文（worker 自动注入，实时快照）】",
        f"执行模型: {model_display()}（本轮参数记录须写明此模型）",
        f"项目: {name} | stage: {state.get('stage', '?')}"
        f" | schema: {state.get('schema_version', 1)}"
        f" | revision: {state.get('revision', '?')}"
        f" | execution.mode: {mode}"
        f" | 镜头: {len(state.get('shots') or [])}"
        f" | 场次: {len(state.get('sequences') or [])}"
        f" | 资产: {len(state.get('assets') or [])}"
        f" | 队列: {len(queue)} 条",
        pause_line,
        "待处理事件:",
        *line_strs,
        f"约束: 每批 {rules.get('batch_size', 5)} 场禁全量；"
        f"自动重试上限 {retry.get('max_auto_retries', 2)} 次"
        f"（{retry.get('seed_rule', '见 SKILL.md')}）；"
        f"耗尽后置 regenerate 汇报用户",
        f"配置记忆（生成前必读）: studio/config/global.json + "
        f"projects/{name}/config.json；历史教训: "
        f"projects/{name}/生成参数记录.md",
        "",
        "处理一下：按 SKILL.md 协议逐条消费上述事件，先读配置与参数记录再动手；"
        "写 state 必须带 revision，冲突返回 409 则重新 GET；"
        f"处理成功且产物落盘后 POST /api/project/{name}/events/ack "
        f"{{\"ids\": {json.dumps(ids)}}}，只 ack 对应 event_id，禁止清空整个队列；"
        "只处理上方列出的 event_id；未列出的（含 blocked/退避中）不要并入本轮；"
        "新到达事件留给 worker 下一轮调度过滤后再派发；"
        "最后用两三句话汇报，不要贴长输出。",
    ])


SYSTEM_APPEND = """你是「视频工作室」流水线的常驻执行 agent（页面按钮 → 事件队列 → 你消费）。
稳定规则：
- 工作流标准是 video-studio skill（本会话已加载）；动手前遵守其全部协议与红线。
- 每次任务的动态上下文头由 worker 注入，其中事件清单是唯一待办来源，不要凭记忆处理。
- 生成前必读 studio/config/global.json（全局模型参数/红线）与 projects/<项目>/config.json（项目风格/重试政策）；两者冲突时项目配置优先。
- 失败处理：生成报错/超时按重试政策自动换 seed 重试；重试耗尽置 regenerate 并在汇报里说明；翻车配置写进 projects/<项目>/生成参数记录.md 并标禁用。
- 任何情况下不要绕过事件队列自行决定生成内容；页面审核结果是唯一质量事实来源。
- execution.mode=paused 时禁止提交 GPU/TTS 生成；遗留 generating 是 recovery_required，不得自动重提。
- ack 必须带 event_id 列表；禁止 POST events/ack 清空整个队列。
- 写 state 用 PATCH 或 PUT 并携带 revision；409 表示冲突，重新 GET 再写。
- 审批只能落地事件里的 target_revision；对象版本已变则冲突，不得改批最新版。"""


async def heartbeat():
    """独立心跳任务：主循环阻塞在等 agent 回复时也能持续刷新在线状态。

    write_status 是同步的读-合-写，事件循环内不会被抢占，与主循环无竞态。"""
    while True:
        await asyncio.sleep(HEARTBEAT_SEC)
        write_status(alive=True)


async def run():
    desired = read_desired_model()   # 会话定型：boot 前把目标模型写进环境
    if desired:
        os.environ["CLAUDE_MODEL"] = desired
    else:
        os.environ.pop("CLAUDE_MODEL", None)
    opts = ClaudeAgentOptions(
        cwd=ROOT,
        permission_mode="bypassPermissions",  # 本地可信流水线，无人值守必须免确认
        model=os.environ.get("CLAUDE_MODEL") or None,
        cli_path=shutil.which("claude"),      # nohup/cron 环境下 PATH 可能不全
        setting_sources=["user", "project"],  # user 层带登录凭据；project 层加载 .claude/skills
                                              # （只给 project 会 Not logged in，已实测）
        system_prompt={"type": "preset", "preset": "claude_code",
                       "append": SYSTEM_APPEND},
    )
    write_status(alive=True, busy=False, started_at=time.time(),
                 last_push=0, last_done=0, pushes=0, last_result="",
                 model=model_display(), model_info=resolve_model_info())
    beat = asyncio.create_task(heartbeat())  # 独立心跳，覆盖 boot/消费全程
    try:
        async with ClaudeSDKClient(opts) as client:
            # 首条：把协议与配置装进上下文（一次性成本），然后待命
            await client.query(
                f"你是「视频工作室」流水线的常驻执行 agent。协议文档在 {SKILL}"
                "（已作为本会话 skill 加载）。现在只读一遍该文档熟悉事件协议与红线，"
                "再读 studio/config/global.json 与 projects/测试/config.json 熟悉参数记忆，"
                "不要执行任何生成，回复「待命」即可。之后每次「处理一下」消息头部"
                "带有实时任务上下文，以它为准。")
            async for msg in client.receive_response():
                if isinstance(msg, ResultMessage):
                    print(f"[boot] 协议已载入 (turns={msg.num_turns}, "
                          f"cost=${msg.total_cost_usd or 0:.4f})", flush=True)
            write_status(last_done=time.time())
            # boot 后当前会话记录已生成，重解析拿实测模型（比静态解析准）
            write_status(model=model_display(), model_info=resolve_model_info())

            dispatched = {}   # 项目 -> 已交给 agent 尚未 ack 的 event_id；启动为空以便恢复
            quiet_since = {}
            pushes = 0
            while True:
                await asyncio.sleep(POLL_SEC)
                now = time.time()
                want = read_desired_model()
                if want != desired:  # 页面切换了模型 → 干净退出，supervise 用新模型重建
                    print(f"[model-switch] {desired or '(默认)'} -> "
                          f"{want or '(默认)'}，重建会话中…", flush=True)
                    return
                if not os.path.isdir(PROJECTS):
                    continue
                for name in sorted(os.listdir(PROJECTS)):
                    proj = os.path.join(PROJECTS, name)
                    if not os.path.isdir(proj):
                        continue
                    queue = eventlog.pending(proj)
                    pending_ids = {e.get("event_id") for e in queue if e.get("event_id")}
                    held = prune_dispatched(dispatched.get(name, set()), pending_ids)
                    dispatched[name] = held
                    attempts = prune_attempts(load_attempts(proj), pending_ids)
                    ready = filter_ready(queue, held, attempts, now)
                    if not ready:
                        quiet_since.pop(name, None)
                        continue
                    quiet_since.setdefault(name, now)
                    if now - quiet_since[name] < QUIET_SEC:
                        continue
                    sent_ids = [e.get("event_id") for e in ready]
                    pushes += 1
                    print(f"[push] {name} events -> agent", flush=True)
                    write_status(busy=True, last_push=now, project=name,
                                 pushes=pushes)
                    t0 = time.time()
                    await client.query(build_task_message(name, ready))
                    summary, cost = "", 0.0
                    succeeded = True
                    async for msg in client.receive_response():
                        if isinstance(msg, ResultMessage):
                            summary = (msg.result or "")[:300]
                            cost = msg.total_cost_usd or 0.0
                            if getattr(msg, "is_error", False):
                                succeeded = False
                    tag = "done" if succeeded else "error"
                    print(f"[{tag}] {name} ({time.time()-t0:.0f}s, "
                          f"${cost:.4f}) {summary[:120]}", flush=True)
                    write_status(busy=False, last_done=time.time(),
                                 last_result=("error: " + summary) if not succeeded else summary,
                                 last_cost_usd=cost)
                    still = {e.get("event_id") for e in eventlog.pending(proj) if e.get("event_id")}
                    dispatched[name] = finish_dispatch(held, sent_ids, still, succeeded)
                    attempts = record_attempt(attempts, sent_ids, now, succeeded)
                    save_attempts(proj, attempts)
                    quiet_since.pop(name, None)
    finally:
        beat.cancel()


async def supervise():
    """崩溃自动重启（会话重建 = 重新 boot 载入协议），连续崩溃超限则退出。

    run() 正常返回也是重建信号（页面切换了模型），同样进下一轮。"""
    fails = 0
    while fails < MAX_CRASHES:
        try:
            await run()
            fails = 0  # 正常返回 = 模型切换重建，重置崩溃计数
            continue
        except KeyboardInterrupt:
            raise
        except Exception:
            fails += 1
            err = traceback.format_exc(limit=3)[-400:]
            print(f"[crash #{fails}] {err}", flush=True)
            write_status(alive=False, last_error=err[-200:])
            if fails < MAX_CRASHES:
                await asyncio.sleep(min(300, 10 * 2 ** fails))
    print("[exit] 连续崩溃超限，worker 停止（事件仍在队列，人工通道可用）", flush=True)


if __name__ == "__main__":
    try:
        asyncio.run(supervise())
    except KeyboardInterrupt:
        pass
    finally:
        write_status(alive=False)
        print("[exit] worker stopped", flush=True)
