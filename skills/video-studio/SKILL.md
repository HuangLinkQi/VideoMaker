---
name: video-studio
description: 剧本→资产（人物/场景/道具三视图）→场景首尾帧→逐场视频 的页面化审核流水线。用户在本地审核页面（studio/server.py，端口 8931）上传剧本、点击「通过/重新生成/移除」等按钮，按钮写入事件队列；用户在对话里说「处理一下」时由 agent 消费事件并执行生成。用户提到「视频工作室」「审核页面」「studio」「处理一下（页面事件）」或要走剧本视频流水线时使用。
---

# video-studio：页面审核 + agent 执行 的视频流水线

核心原则：**生成与判断都在 agent 对话里做，页面只做展示和审核按钮**。server 只是文件读写的薄层，不要往 server 里加生成逻辑。

制作主表是 **场次 sequences + 镜头 shots**，不是扁平 scXX。「一句一条」和「两条 8 秒拼接」都已禁用。H3 单条长度是生成单元上限，不是场次时长。

`execution.mode=paused` 时禁止提交 GPU/TTS；遗留 `generating` 登记为 `recovery_required`，不得自动重提。写 state 必须带 `revision`，冲突 409。事件按 `event_id` ack，禁止清空整个队列。

## 启动

```bash
cd /Users/huangrq25/Desktop/其他/龙
python3 studio/server.py &          # 端口 8931
open http://localhost:8931
```

## 目录约定

```
projects/<项目名>/
  script.txt       剧本原文
  state.json       状态机（唯一事实来源，agent 用 PUT 更新）
  events.jsonl     页面按钮事件队列
  characters/      人物三视图（<id>.png=带比例尺展示版, <id>_clean.png=生成参考原图, char_<id>_front.png=正面单视图crop）
  scenes/          场景设定图（loc_*.png，四视角：平视/反打/左侧/右侧 + 比例尺）
  props/           道具设定图（prop_*.png，三视图 + 比例尺）
  storyboards/     sb_<scene>.png（导演工作台分镜板，每场一张）
  keyframes/       kf_<scene>_start.png / kf_<scene>_end.png
  videos/          <scene>.mp4
  dialogues/       <scene>.wav（本场 TTS 台词轨，路线 B）
  characters/<id>_voice.wav  人物声线锚点（6–12 秒干声）
  生成参数记录.md   每轮生成追加（沿用根目录同名文件的格式）
```

## API 速查（server 是薄层，直接读写文件也可以）

- `GET  /api/project/<name>/state` / `PUT` 同路径 → 读/写 state.json（v2 必须带 `revision`，冲突 409）
- `PATCH /api/project/<name>/state` → `{revision, shots:[], utterances:[]}` 局部更新
- `GET  /api/project/<name>/events` → 未 ack 事件
- `POST /api/project/<name>/events/ack` → `{"ids":[event_id, ...]}`，禁止整队列清空
- `POST /api/project/<name>/execution` → `{"mode":"paused"|"running"}`
- `GET  /api/project/<name>/planning` / `jobs` / `compile`
- `POST /api/project/<name>/archive` → `{"path": "keyframes/xxx.png"}` 把项目内文件移入同级 `versions/` 子目录（文件名加 `__YYYYMMDD-HHMMSS` 时间戳），返回 `{"archived": 新相对路径}`；重新生成覆盖旧文件前必须先归档
- `POST /api/project/<name>/upload` → `{"path":"characters/<id>_voice.wav","data":"<base64>"}` 写音频（仅 `characters/` 或 `dialogues/`，≤15MB）
- `GET  /api/knowledge` → 知识库文档清单；`GET /knowledge/<相对路径>` → 文档原文

## 提示词知识库（2026-09-03 嵌入，位于 `studio/knowledge/`）

Gaven 提示词方法论已随服务内置，页面第 5 个页签「📚 知识库」可在线阅读。**写提示词前先读对应文档，不要凭记忆套用**：

| 时机 | 必读 | 要点 |
|---|---|---|
| **视频阶段逐场写视频提示词**（写入 `state.scenes[].prompt`） | `22剧本转视频提示词/Gaven剧本转视频提示词_SKILL_单文件完整版.md`；细化运镜/表演/连续性读其 `references/{camera-motion,performance,continuity-qc}.md` | 按【素材状态】【视觉风格】【镜头】【表演】【声音】【关键锁定】格式；每段≤30秒且各镜头时长之和=总时长；抽象情绪转译成可见行为；运镜写清幅度/方向/停止点；接触动作「触发—反应—结果—释放」；结尾写清离开状态供下场衔接；不增删改原对白 |
| 首尾帧/资产图的英文 prompt（光线、焦段、色调词汇） | `19第十九期AI电影导演Skill/Gaven图像提示词导演.md` + `references/{director-styles,photo-styles,capture-films,print-films}.md` | 按「景别→构图→主体→动作→环境层次→光影色彩→美学材质」顺序组织；抽象词用光线/尺度/留白具体化 |
| 翻车返修 | 22 套文末「样片返修」一节 | 只改导致问题的片段，不因一次失败重写全部视觉设定，不用堆禁止项掩盖动作结构问题 |

知识库只提供提示词方法论；**生成技术栈与参数仍按下方「生成执行要点」执行，勿改**。

## 提示词与版本管理（2026-09-05 加入）

- **中英双提示词**：首尾帧英文 prompt 存在 `scenes[].start/end.prompt`（实际喂模型），页面同时展示 `prompt_zh` 中文译文供用户阅读/编辑。用户改中文后，重生成前 agent 要把中文改动同步回英文 `prompt`（英文仍是唯一喂给模型的版本）；用户在英文栏的改动直接生效。
- **版本管理**：首/尾帧（`start/end.versions`）和视频（`video.versions`）都维护 `[{file, ts}]` 时间升序列表。重新生成覆盖文件前：先 `POST /archive` 归档旧文件 → 新文件落回原路径 → `versions` 追加新条目 → `image`/`file` 指向最新版。页面有 v1/v2… 切换按钮，切换即改 `image`/`file` 并置 `review` 待用户重新确认。**永不删除历史版本**。
- **视频输入追溯**：每次生成视频时在 `scenes[].video.used` 记录实际输入：`{start: 首帧路径, end: 尾帧路径或null, refs: [参考图...], dialogue: 台词轨路径或省略}`——仅首帧就只写 start，仅尾帧就只写 end，有额外参考图全列进 refs；路线 B 混音后必须写 `dialogue`。页面视频卡片会展示这些缩略图和台词播放器。

## 状态机

`state.stage`: `script → assets → keyframes → videos → done`

状态值：`pending(待生成) generating review(待审核) approved regenerate(待重生成) removed`

## 资产体系（人物/场景/道具，一视同仁）

- `state.assets[]`：每项 `{id, name, desc, type: character|scene|prop, height_cm, status, image, note, prompt}`；人物另有 `voice`（见下「声音」）
- **参考锚定编辑管线（2026-09-06 下午起，替代「一次采样出整图」）**：`studio/batch_assets.py`（gen/finalize 两步）+ GPU `gen_edit.py`/`run_batch.py`/`pack_batch.py`。Qwen-Image-Edit-2511 fp8mixed + Lightning 4steps + multiple-angles LoRA + ModelSamplingAuraFlow(shift 3.1) + CFGNorm，锚定单视角参考图逐视角编辑，本地 `sheet_collage.py` 拼贴。旧法（Krea-2 一次采样三联 / 分视单出拼接）均禁用
- **场景锚点必须单视角**：三格设定图直接作锚会继承分栏布局；用 clean 图左 1/3 裁剪（`scenes/<id>_wide.png`）。乱码场景走两遍法：先对锚点做一次「去字」编辑再锚新锚出视角
- **人物锚点**：`char_<id>_front.png`（正面全身 crop）；先出正面清洁版，再链式出侧/背与正/侧特写（五格）
- **所有资产设定图必须带比例尺**：生成后跑 `python3 studio/ruler.py <in> <out> <height_cm> <标签>`；原图保留为 `*_clean.png`
- 人物五格 = 特写正 | 特写侧 | [比例尺] | 全身正 | 全身侧 | 全身背；场景四格 = [比例尺] | 平视 | 反打 | 左侧 | 右侧；道具三格 = [比例尺] | 正 | 侧 | 3/4——面板全部来自锚定编辑，拼贴是纯贴图不引入不一致
- 首尾帧/视频引用参考图时，人物用 `characters/char_<id>_front.png`（正面单视图 crop，**不要用整张三视图**，会出复制体）

## 声音（路线 B，2026-09-06 加入）

锁定策略：**TTS 先锁人物声线和本场台词，FL2VA 仍出无声画面，再把审过的台词轨混进成片。** 不要走 H3 原生 `<Audio N>` 克隆——FL2VA 没有参考音频槽，跨场会漂。

**两层音频，互不替代：**

1. **人物声线锚点** `assets[].voice`（仅 `type=character`）
   `{file, transcript, desc, status, versions:[{file,ts}]}`
   - 文件：`characters/<id>_voice.wav`，6–12 秒单人干声，24kHz mono，无 BGM/混响
   - 来源：用户在资产卡上传，或 `regenerate_voice` 用 TTS 按 `desc` 生成校准句
   - 校准句默认「今天天气不错，我们慢慢说。」（不要用剧本草稿当校准句）
   - 年轻王桂芬可从王桂芬锚点派生（同一人、更亮更急）；年幼林川必须独立童声
   - 与设定图分开审核：`approve_asset` 只过图，`approve_voice` 只过声。图过了即可进首尾帧，声线未过不能出台词轨

2. **本场台词轨** `scenes[].dialogue`
   `{status, file, lines:[{speaker, text, start, emotion, offscreen}], versions}`
   - 文件：`dialogues/<scene>.wav`（本场混好的台词，可能多人拼接）
   - 无对白：`lines=[]`，status 直接 `approved`，跳过 TTS
   - 生成前：该场开口角色的 `voice.status` 必须 `approved`
   - 用户可在视频页编辑 lines（格式 `speaker|text|emotion|offscreen(0/1)`）后点重新生成

**成片混音：**
- FL2VA 继续静音出画面（`gen_videos.py` 不接 audio VAE）
- 台词 `approved` 且有 `file` 时：`ffmpeg` 把 `dialogues/<scene>.wav` 混进 `videos/<scene>.mp4`，对齐 `lines[].start`（缺省从开口动作开始）
- `video.used.dialogue` 记本场台词轨路径
- 人声只来自 TTS；环境音（键盘、虫鸣、忙音）与人声分离，不要让 TTS 带环境
- 口型：提示词仍写「嘴唇开合与台词逐字对应」。口型明显不对时，`regenerate_video` 的 note 写「对口型」，agent 用 LatentSync/MuseTalk 以台词 wav 为时钟后处理，**不重跑 H3、不换 checkpoint**
- 无台词轨或台词未通过：成片保持静音（与改声音之前行为一致）

**TTS 栈（ComfyUI，生成前读 `studio/config/global.json` 的 `voice_stack`）：**
- 默认 CosyVoice 2 或 IndexTTS-2 零样本克隆（中文）
- 输入：该角色 `voice.file` + 本场 `text` + `emotion`
- GPU 没装 TTS 节点：停止并汇报，让用户上传成品 wav；不要改走 H3 原生克隆
- 覆盖旧 wav 前必须 `POST /archive`，versions 里旧条目改指向归档路径，再追加新条目

## 流水线步骤

### 1. 剧本 → 资产清单 + 场景拆分（事件 `script_confirmed`）

读 `projects/<name>/script.txt`：
1. 抽象人物：姓名/年龄/身高/外貌/服装，写入 assets（type=character, status=pending）
2. 抽象场景（地点）和关键道具，写入 assets（type=scene/prop）
3. 拆分：按剧本场次 `sequences`（如 0:00–0:21）建镜头表 `shots`。只在换景别/机位/地点/人物进出时切开；同镜头允许多句对白。禁止一句一条，禁止把场次切成两条 8 秒再拼接。对白写入 `utterances[]`，镜头只引用 `utterance_ids`。旧 `scenes[]` 仅作 legacy 只读。
4. 人物资产写入 `voice: {file:"", transcript:校准句, desc:音色描述, status:pending, versions:[]}`
5. stage 设为 `assets`，在对话里汇报后开始生成资产设定图

### 2. 资产设定图（assets 阶段）

- 人物/场景/道具一律走参考锚定编辑（见上）；人物产物另存 `char_<id>_face.png`（特写正）供首尾帧多参考
- GPU 脚本 `studio/gen_assets.py`（Krea-2 Turbo，一条工作流、一次采样）。拉回后 `ruler.py` 加比例尺，裁正面 `char_<id>_front.png`，status=review
- **禁用** `gen_asset_views.py` / `compose_sheet.py` 分视拼接
- 人物声线不自动生成：等用户点「重新生成声线」或上传 wav

### 3. 首尾帧（keyframes 阶段）

- **前置环节：导演工作台分镜板（storyboard，2026-09-04 加入）**——用户要求在已有人物三视图后、生成首尾帧前，先为每场生成一张多格分镜板，直观呈现镜头布置与参考：
  1. 按知识库 22 套方法论拆该场镜头（景别/运镜/时长），拆成 ≤5 个 panel，每 panel 写英文画面描述（含时间段如 0-3s；**对白不画进图**——中文会乱码，对白在页面任务上下文与视频提示词里体现）
  2. 写 stdin JSON（scene/chars/refs/panels），在 GPU 服务器跑 `studio/gen_storyboard.py`：`cat /tmp/panels.json | python3 gen_storyboard.py [retry]`，产物 `sb_<scene>_*.png` 拉回 `storyboards/sb_<scene>.png`
  3. 置 `scenes[].storyboard.status=review`，让用户在页面「首尾帧」页签审（每场卡片顶部）
  4. **新场景的首尾帧在该场 storyboard approved 后再生成**（批次 1 已有首尾帧的历史场景补生成工作台，不回退既有帧）；用户可 `approve_storyboard` / `regenerate_storyboard`（带 note 修改分镜设计）
- 前提：该场景引用的资产全部 approved
- 参考图通过 `TextEncodeKrea2Ref` 喂入（image1-3，≤3 张）：人物正面 crop + 场景图 + 道具图，并在 prompt 里写明各资产尺寸关系（如 "a 158cm elderly woman next to a 176cm young man"）
- prompt 加人数硬约束（"Exactly one/two person(s) in the frame, no duplicates"）
- 输出 `keyframes/kf_<scene>_start.png` / `_end.png`，完成置 review
- **分批生成（默认 5 场一批）**，每批入库后让用户审，不要一次全跑
- **单帧场景**：某帧 status=removed 时，生成与入库都跳过该帧（不覆盖、不重置其状态）；页面在该帧位置显示移除提示

### 4. 视频（videos 阶段）

- 前提：该场景**所有未移除的帧**都 approved
- **台词轨（路线 B）**：该场 `dialogue.lines` 非空时，开口角色 `voice` 必须已 approved，再 TTS 出 `dialogues/<scene>.wav` 置 review；用户通过后再混音。无对白则跳过
- **帧模式由 state 决定**：双帧在场 → first+last 双引导；仅首帧（尾帧 removed）→ 标准 i2v 只喂 first；仅尾帧（首帧 removed）→ 只喂 last（尾帧模式）。H3 节点参数按 `run_h3_fl2va.py` 模板减掉不存在的输入
- **先用知识库 22 套格式为该场写视频提示词**（见上「提示词知识库」表），写入 `state.scenes[].prompt` 并 PUT state，再提交生成；**该场的 storyboard 分镜板就是【镜头】表的可视化底稿**，两者镜头顺序/景别必须一致
- 用 approved 首尾帧生成**无声**视频，输出 `videos/<scene>.mp4`；若该场台词已 approved 且有 file，混入后再置 video.status=review
- 全部场景 video approved 后 stage=done，并询问是否拼接成片

## 事件消费协议（用户说「处理一下」时执行）

1. `GET /api/project/<name>/events`，逐条处理（按 ts 顺序）：

| 事件 type | target | 动作 |
|---|---|---|
| `script_confirmed` | — | 走上面「步骤 1」 |
| `approve_asset` | asset id | status=approved；该阶段资产全部 approved 后 stage→keyframes |
| `regenerate_asset` | asset id | status=regenerate→generating，按 note 改 prompt，**一条工作流**重新生成完整设定图 + 重加比例尺，完成置 review |
| `remove_asset` | asset id | status=removed；后续首尾帧不再引用 |
| `approve_keyframes` | scene id（仅 v1） | start+end 中**非 removed 的帧**置 approved（两帧都 removed 是非法状态，向用户提示）；全部场景通过后 stage→videos |
| `approve_start` / `approve_end` | shot id（v2）或 scene id（v1） | 只把目标帧置 approved。`target_revision` 必须等于该帧当前版本（revision_id / file_sha256 / image）；对不上则 409，不得改批新版本。事件带 `frame=start\|end` |
| `regenerate_start` / `regenerate_end` | shot id（v2）或 scene id（v1） | 按 note 重生对应帧（保留另一帧），完成置 review。paused 时 423，不得提交 GPU |
| `regenerate_scene` | scene id（仅 v1） | 整场未移除的帧重生成 |
| `regenerate_shot` | shot id（v2） | 整镜未移除的帧重生成 |
| `remove_start` / `remove_end` | shot / scene id | 对应帧 status=removed（**图片文件保留在原处**，不删除）。v2 页面用 PATCH shots，只改目标帧，不得恢复已移除的另一帧，也不得改成 last_only |
| `restore_start` / `restore_end` | shot / scene id | 恢复被移除的帧：有图置 review 重新审核，无图置 pending |
| `approve_storyboard` | scene id | storyboard.status=approved；该场可进入首尾帧生成 |
| `regenerate_storyboard` | scene id | 按 note 调整分镜设计（镜头顺序/景别/格数），重生成工作台置 review |
| `approve_video` | scene id | video.status=approved |
| `regenerate_video` | scene id | 按 note 改视频 prompt 重新生成；note 含「对口型」时只跑口型后处理，不重跑 H3 |
| `insert_scene` | scene id | 在 target 场景**之后**插入新场景：id 取未占用的最小序号（数组顺序即展示顺序，不要求 id 连续），按 note 描述抽象 title/duration/引用资产，新场景 start/end/storyboard/video/dialogue 均 pending（无对白则 dialogue.status=approved、lines=[]），stage 保持 keyframes；完成后提醒用户走分镜→首尾帧流程 |
| `approve_voice` | asset id | `assets[].voice.status=approved`（只过声线，不影响设定图） |
| `regenerate_voice` | asset id | 按 note/desc 用 TTS 生成 `characters/<id>_voice.wav`（先 archive），完成置 review |
| `approve_dialogue` | scene id | `scenes[].dialogue.status=approved` |
| `regenerate_dialogue` | scene id | 按 lines + 已通过的人物声线 TTS 本场台词轨（先 archive），完成置 review |
| `standardize_storyboard` | shot / sequence / 项目名 | 读取 `$minimax-h3-prompt-standardizer`，按当前方案整理文字候选；paused 可执行。结果 POST `/storyboard/prompt-result`，不得自动批准、不得提交 GPU/TTS |

（旧事件名 approve_character / regenerate_character / remove_character 视同对应 _asset 处理。）

2. 处理成功且产物落盘后 `POST /api/project/<name>/events/ack` `{"ids":[...本批 event_id]}`。禁止清空整个 `events.jsonl`。
3. 在对话里简要汇报处理结果；页面每 3 秒自动刷新，用户无需手动刷新
4. **ack 前复查未确认队列**：若处理期间又有新事件写入，一并处理到空为止
5. `execution.mode=paused` 时不提交任何生成；审核类事件（approve/remove/restore）仍可处理
6. 写 state 先 GET，PUT/PATCH 带当前 `revision`；409 则重新 GET，不要用旧快照覆盖
7. 审批事件必须带所审对象的 `target_revision`（资产 revision_id / 声线 file / **帧 revision_id** 等）；与当前不符则 409，不得改批最新版。v2 首尾帧页按 sequences 展示 shots；提示词/选帧/版本/移除/恢复走带 revision 的 PATCH shots，禁止 PUT 改 legacy scenes（会 400）

## 自动执行 worker（2026-09-04 加入：点击即触发，无需喊「处理一下」）

- 常驻进程 `studio/agent_worker.py`：用 claude-agent-sdk 的 **Streaming Input 模式**维持一个常驻 Claude 会话，1 秒轮询 `projects/*/events.jsonl`（2 秒去抖合并连点），发现新事件即注入「处理一下」任务消息，agent 立即消费
- 启动：`cd /Users/huangrq25/Desktop/其他/龙 && nohup studio/.venv/bin/python studio/agent_worker.py >> studio/worker.log 2>&1 &`（依赖 `studio/.venv` 里的 claude-agent-sdk 和已登录的 claude CLI）
- 状态：心跳写 `studio/worker_status.json`，页面头部徽标实时显示「在线/执行中/离线」（`GET /api/worker`）
- 会话跨点击保留上下文（批次进度、翻车原因都在）；上下文过长可重启 worker，或在页面「⚙️ 配置」切换执行模型（写 `studio/desired_model.json`，worker 巡检到后自动重建会话，无需手动重启）
- worker 离线时自动退回「用户喊一声」模式，两条通道协议完全相同

## 约束与校验（2026-09-04 加入，server 强制执行）

- **事件白名单**：`POST /event` 只接受协议表中的 type，未知类型或缺 target 的写入会被 server 400 拒绝（页面正常操作不受影响）
- **状态机校验**：`PUT /state` 校验 `stage ∈ {script, assets, keyframes, videos, done}`、`status ∈ {pending, generating, review, approved, regenerate, removed}`、`assets[].type ∈ {character, scene, prop}`、`scenes[].id` 为 `sc<数字>`；校验不过整包拒绝——**agent 写 state 前先 GET 最新 state、改完只回写，不要用旧快照覆盖**（避免丢掉并发的审核结果）
- 未知字段会被保留（向前兼容），但枚举字段写错值会 400；收到 400 不要硬重试，先修正数据

## 配置与记忆（生成前必读，冲突时项目配置优先）

| 层 | 路径 | 内容 | API |
|---|---|---|---|
| 全局 | `studio/config/global.json` | 已固化的 Krea-2 / H3 模型参数、红线（批量、人数约束、正面crop、禁用清单等）、重试默认政策 | `GET/PUT /api/config/global` |
| 项目 | `projects/<name>/config.json` | 风格母版、回忆段调色、角色备注、进行中的注意事项、项目级重试政策 | `GET/PUT /api/project/<name>/config` |
| 运行记忆 | `projects/<name>/生成参数记录.md` | 每轮 seed/耗时/翻车记录（追加，翻车标禁用） | 直接读写文件 |

- worker 每次任务注入的上下文头会带 stage 快照、事件清单与重试政策摘要；**生成动作前仍要读两份 config 全文**（摘要只是提醒）
- 用户在对话里调整偏好（如换风格、改批量）时：更新对应 config.json 并在汇报里说明已写入

## 重试机制

- **生成失败/超时**：按 config `retry` 政策自动重试（默认上限 2 次），seed 按 `50000 + batch*1000 + retry*100` 递增；每次尝试都追加参数记录
- **质量打回**（用户 regenerate 事件）：视作一次 retry，同样递增 seed，并**按事件 note 修改 prompt**——不带修改的原样重试没有意义
- **重试耗尽**：停止，置 `regenerate` 状态，向用户汇报已尝试的组合与失败原因，参数记录标「禁用此配置」
- **worker 崩溃**：自动重启会话（最多 5 次、指数退避）；事件未 ack 就还在队列里，重启后自然续跑——ack 只在动作成功落盘后执行

## 生成执行要点（复用《别晃》验证过的模式，详见根目录 `交接-2026-09-03.md` 与 `生成参数记录.md`）

- ComfyUI API `127.0.0.1:8188`（SSH 隧道到沉鱼云 RTX 5090，用完提醒用户关机省钱；**SSH 端口和密码会变，以用户最近一次给的为准**，skill 里不写死）
- **图片默认用 Krea-2 Turbo**（2026-09-03 验证，效果远好于 SDXL/Juggernaut）：
  - UNET `Krea-2/krea2_turbo_fp8_scaled.safetensors` + CLIPLoader type=`krea2`（`qwen3vl/qwen3vl_4b_fp8_scaled.safetensors`）+ VAE `qwen_image_vae.safetensors`
  - steps=8, cfg=1.0, euler, simple；负面用 ConditioningZeroOut
  - 参考图：`TextEncodeKrea2Ref`（image1-3）；现成脚本 `studio/gen_keyframes.py`（首尾帧，分批）、`studio/gen_assets.py`（资产设定图，**一条工作流一次出完整图**）、`studio/gen_storyboard.py`（导演工作台分镜板）
  - 教训：**整张三视图直接作参考会导致画面出现人物复制体**；必须用正面单视图 crop + 人数约束
  - 教训：**分视单出再拼接一致性差**（2026-09-06 整批作废），资产必须同一工作流一次完成
- SDXL/Juggernaut 路线（IPAdapter FaceID Plus v2）仅作备选；**prompt 里不要写中文文字**（渲染会乱码）
- 视频：MiniMax H3 fl2va，1344×768，steps=12，res_multistep（参数模板见根目录 `run_h3_fl2va.py`）；**成片默认无声**，台词按「声音（路线 B）」混入
- 声音：CosyVoice 2 / IndexTTS-2 零样本克隆，参数见 `voice_stack`；不要接 H3 audio VAE 来「发明」人物声
- 每轮参数（**执行 agent 模型** + 生成模型/seed/cfg/耗时）追加到项目内 `生成参数记录.md`；agent 模型从任务上下文头或 `GET /api/model` 获取（`studio/model_info.py` 解析 settings.json 链 + 会话实测）
- 被用户判定翻车的批次要在参数记录里标「禁用」，不得复用其配置
