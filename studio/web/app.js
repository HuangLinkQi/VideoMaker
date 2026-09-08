/* 视频工作室前端：数据驱动自 state.json，按钮只负责发事件。 */
const $ = s => document.querySelector(s);
const main = $("#main");
let project = null, state = null, view = "script";

const api = async (path, opts) => {
  const r = await fetch(path, opts);
  let j = {};
  try { j = await r.json(); } catch (e) { j = {error: r.statusText}; }
  if (j && typeof j === "object") j._http = r.status;
  return j;
};
const paused = () => ((state && state.execution) || {}).mode === "paused";
function frameKeyOf(type) {
  if (/^(approve|regenerate|remove|restore)_start$/.test(type)) return "start";
  if (/^(approve|regenerate|remove|restore)_end$/.test(type)) return "end";
  return "";
}
function resourceRevision(type, target) {
  if (type === "approve_asset" || type === "remove_asset" || type === "regenerate_asset") {
    const a = findAsset(target) || {};
    return a.revision_id || a.file_sha256 || a.image || "";
  }
  if (type === "approve_voice" || type === "regenerate_voice") {
    const v = ((findAsset(target) || {}).voice) || {};
    return v.revision_id || v.file_sha256 || "";
  }
  if (type === "approve_shot" || type === "regenerate_shot") {
    const sh = findShot(target) || {};
    return sh.revision_id || ((sh.start || {}).image) || sh.id || "";
  }
  const fk = frameKeyOf(type);
  const owner = (fk && findShot(target)) || findScene(target);
  if (owner && fk) {
    const f = owner[fk] || {};
    return f.revision_id || f.file_sha256 || f.image || target;
  }
  const sc = findScene(target);
  if (sc) {
    if (type.indexOf("storyboard") >= 0)
      return (sc.storyboard || {}).image || (sc.storyboard || {}).status || target;
    if (type.indexOf("dialogue") >= 0)
      return (sc.dialogue || {}).file || (sc.dialogue || {}).status || target;
    if (type.indexOf("video") >= 0)
      return (sc.video || {}).file || (sc.video || {}).status || target;
    if (/approve_keyframes|regenerate_scene/.test(type)) {
      const start = ((sc.start || {}).image) || "";
      const end = ((sc.end || {}).image) || "";
      return (start || end) ? start + "|" + end : target;
    }
  }
  return state && state.revision;
}
const postEvent = (type, target, note) => {
  const body = {
    type, target, note: note || "",
    target_revision: resourceRevision(type, target),
  };
  const fk = frameKeyOf(type);
  if (fk) body.frame = fk;
  return api(`/api/project/${encodeURIComponent(project)}/event`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body),
  });
};

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.style.opacity = 1;
  setTimeout(() => (t.style.opacity = 0), 2500);
}

/* 通用按钮处理器：发事件 + toast 提示（worker 在线则自动执行，否则去 agent 对话） */
let workerOnline = false;
$("#workerBadge").onclick = () => {   // 点徽标直达配置页切模型
  view = "config";
  refresh(true);
};
async function fire(type, target, needNote) {
  let note = "";
  if (needNote) {
    note = prompt("备注（会带给 agent，可留空）：") || "";
  }
  const r = await postEvent(type, target, note);
  if (r && r._http === 423) {
    toast("生成已暂停，不能提交生成任务");
    return;
  }
  if (r && r._http === 409) {
    toast("审核对象已变化，请刷新后再审");
    lastStateJson = "";
    refresh(true);
    return;
  }
  if (r && r.error) { toast("✗ " + r.error); return; }
  toast(workerOnline
    ? "已记录 ✔ 自动执行 agent 接手中，稍后页面自动更新"
    : "已记录 ✔ 请到 agent 对话里说一声「处理一下」");
}

const imgSrc = rel =>
  rel ? `/projects/${encodeURIComponent(project)}/${rel}?v=${stateVersion}` : "";

const badge = st => `<span class="badge ${st}">${{
  pending:"待生成", generating:"生成中", review:"待审核",
  approved:"已通过", regenerate:"待重生成", removed:"已取消"}[st] || st}</span>`;

/* ---------- 视图 ---------- */
function renderScript() {
  const diff = (state && state.script_diff) || {};
  const only = diff.script_only || [];
  const route = (state && state.gaps && state.gaps.route) || diff.route || {};
  const gaps = ((state && state.gaps && state.gaps.missing_assets) || []);
  const diffHtml = only.length ? `
    <div class="card">
      <h3>剧本文件 vs 页面台词</h3>
      <p class="small">script.txt 仍是旧稿；当前制作以 state 台词为整合候选，不覆盖剧本文件。</p>
      <table class="diffTable">
        <tr><th>位置</th><th>剧本文件</th><th>页面 / 整合候选</th></tr>
        ${only.map(d => `<tr>
          <td class="small">${esc(d.where || "")}</td>
          <td class="old">${esc(d.text || "")}</td>
          <td class="new">${esc(d.note || "")}</td>
        </tr>`).join("")}
      </table>
      <p class="small">路线差异：剧本写「${esc(route.script_sign || "")}」；
        已审纸条「${esc(route.approved_note_image || "")}」；
        村长口述「${esc(route.cunzhang_spoken || "")}」。
        候选「${esc(route.candidate || "")}」——需剧本审核，迁移未改对白或资产。</p>
      ${gaps.length ? `<p class="small">缺资产：</p><ul class="gapList">${
        gaps.map(g => `<li>${esc(g.id)} · ${esc(g.need)}</li>`).join("")}</ul>` : ""}
    </div>` : "";
  main.innerHTML = `
    ${diffHtml}
    <div class="card">
      <h3>上传剧本</h3>
      <p class="small">粘贴剧本全文。确认剧本在生成暂停期间会被拒绝。</p>
      <textarea id="scriptText" placeholder="在此粘贴剧本..."></textarea>
      <div class="row">
        <button id="saveScript">保存剧本</button>
        <button id="confirmScript" ${paused() ? "disabled" : ""}>✔ 确认剧本，开始抽象人物</button>
      </div>
    </div>`;
  fetch(`/projects/${encodeURIComponent(project)}/script.txt`)
    .then(r => (r.ok ? r.text() : ""))
    .then(t => { $("#scriptText").value = t; });
  $("#saveScript").onclick = async () => {
    await api(`/api/project/${encodeURIComponent(project)}/script`, {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({text: $("#scriptText").value}),
    });
    toast("剧本已保存");
  };
  $("#confirmScript").onclick = async () => {
    await api(`/api/project/${encodeURIComponent(project)}/script`, {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({text: $("#scriptText").value}),
    });
    await fire("script_confirmed", "");
  };
}

/* ---------- 资产页：人物/场景/道具 分组 ---------- */
const TYPE_NAMES = {character: "人物", scene: "场景", prop: "道具"};
const TYPE_ORDER = ["character", "scene", "prop"];

function assetCard(c) {
  return `
    <div class="card">
      <h3>${c.name} ${badge(c.status)}</h3>
      <p class="small">${c.desc || ""}${c.height_cm ? ` · ${c.height_cm}cm` : ""}</p>
      ${c.image && c.status !== "pending"
        ? `<img class="frame" src="${imgSrc(c.image)}">`
        : `<p class="hint">（尚无设定图）</p>`}
      ${"" /* 面部特写已并入五格设定图，页面不再单独展示 c.face（文件保留供首尾帧参考） */}
      ${assetViewThumbs(c)}
      ${voiceBlock(c)}
      <div class="row">
        <button data-a="approve_asset" data-t="${c.id}">✔ 通过</button>
        <button data-a="regenerate_asset" data-t="${c.id}" data-n="1">↻ 重新生成</button>
        <button data-a="remove_asset" data-t="${c.id}">✖ 移除</button>
      </div>
    </div>`;
}

function assetViewThumbs(c) {
  const vs = c.views || {};
  const names = {
    face_front: "正脸", face_side: "侧脸",
    body_front: "全身正", body_side: "全身侧", body_back: "全身背",
    eye: "平视", reverse: "反打", left: "左侧", right: "右侧",
    front: "正面", side: "侧面", threeq: "3/4", detail: "特写",
  };
  const items = Object.entries(vs).filter(([, f]) => f);
  if (!items.length) return "";
  return `<div class="usedRow"><p class="small">分视图原图：</p><div class="row usedImgs">${
    items.map(([k, f]) => `<figure class="usedFig"><img src="${imgSrc(f)}" title="${esc(f)}">
      <figcaption class="small">${esc(names[k] || k)}</figcaption></figure>`).join("")}</div></div>`;
}

/* 人物声线锚点：与设定图分开审核。场景/道具没有此项。 */
function voiceBlock(c) {
  if ((c.type || "character") !== "character") return "";
  const v = c.voice || {};
  const st = v.status || "pending";
  const player = v.file
    ? `<audio class="voicePlayer" controls preload="metadata" src="${imgSrc(v.file)}"></audio>`
    : `<p class="hint">（尚无声线。上传 6–12 秒干声，或点重新生成）</p>`;
  return `<div class="voiceBlock">
    <p class="small">🎙 声线 ${badge(st)}
      <span class="small">${esc(v.desc || "")}</span></p>
    ${player}
    <div class="row">
      <button data-a="approve_voice" data-t="${c.id}">✔ 通过声线</button>
      <button data-a="regenerate_voice" data-t="${c.id}" data-n="1">↻ 重新生成声线</button>
      <label class="uploadBtn">⬆ 上传音频
        <input type="file" accept="audio/*" data-voice-up="${c.id}" hidden>
      </label>
    </div>
  </div>`;
}

function renderAssets() {
  const list = state.assets || [];
  if (!list.length) {
    main.innerHTML = `<p class="hint">暂无资产。先到「剧本」页确认剧本。</p>`;
    return;
  }
  main.innerHTML = TYPE_ORDER.map(t => {
    const items = list.filter(c => (c.type || "character") === t);
    if (!items.length) return "";
    return `<h2 class="groupTitle">${TYPE_NAMES[t]}（${items.length}）</h2>
      <div class="grid">${items.map(assetCard).join("")}</div>`;
  }).join("");
  bindButtons();
}

/* 单帧块：正常显示图 + 移除/选现有帧按钮；removed 显示置灰图 + 恢复按钮 + 单帧模式提示 */
function frameBlock(s, key, label) {
  const f = s[key] || {};
  const removed = f.status === "removed";
  const other = key === "start" ? "尾帧" : "首帧";
  const media = removed
    ? (f.image ? `<img class="frame removed" src="${imgSrc(f.image)}">` : "")
    : (f.image ? `<img class="frame" src="${imgSrc(f.image)}">`
               : `<p class="hint">（待生成）</p>`);
  const hint = removed
    ? `<p class="hint">已移除——视频将仅用${other}引导${key === "end" ? "（标准 i2v）" : "（尾帧模式）"}</p>`
    : "";
  const isShot = /^sh\d+$/.test(s.id || "");
  const editable = (state.schema_version || 1) < 2 || isShot;
  const approveType = key === "start" ? "approve_start" : "approve_end";
  const regenType = key === "start" ? "regenerate_start" : "regenerate_end";
  const val = f.validity === "stale" ? `<span class="staleTag">stale</span>` : "";
  const btn = !editable ? "" : (removed
    ? `<button data-restore="${key}" data-t="${s.id}">↩ 恢复${label}</button>`
    : `<button data-remove="${key}" data-t="${s.id}">✂ 移除${label}</button>`);
  const ops = !editable ? "" : `<div class="row">
    ${!removed && isShot
      ? `<button data-a="${approveType}" data-t="${s.id}">✔ 通过${label}</button>
         <button data-a="${regenType}" data-t="${s.id}" data-n="1">↻ 重生成${label}</button>` : ""}
    ${btn}
    <button data-pick="${key}" data-t="${s.id}">🖼 选现有帧</button></div>`;
  return `<div><p class="small">${label} ${badge(f.status || "pending")} ${val}</p>
    ${media}${hint}${ops}
    ${editable ? versionBar(s, key) : ""}
    ${editable ? promptEditor(s.id, key, f.prompt_zh || f.prompt || "", label + "提示词") : ""}</div>`;
}

/* 版本切换条：versions[] 按时间顺序，当前生效的（image/file）高亮置顶语义 */
function versionBar(s, key) {
  const f = s[key] || {};
  const vs = f.versions || [];
  if (vs.length < 2) return "";
  const cur = (key === "video" || key === "dialogue") ? f.file : f.image;
  return `<div class="row verBar"><span class="small">历史版本</span>${vs.map((v, i) =>
    `<button class="verBtn${v.file === cur ? " cur" : ""}" data-ver="${s.id}:${key}:${i}"
      title="${esc(v.ts || "")}">v${i + 1}${v.file === cur ? " ✓当前" : ""}</button>`).join("")}
    <span class="small">点按切换生效版本</span></div>`;
}

/* 视频生成所用的首/尾帧与参考图（可追溯） */
function usedFrames(v) {
  const u = (v && v.used) || {};
  const items = [];
  if (u.start) items.push(["首帧", u.start]);
  if (u.end) items.push(["尾帧", u.end]);
  (u.refs || []).forEach((r, i) => items.push(["参考" + (i + 1), r]));
  const dlg = u.dialogue ? `<p class="small">台词轨：<audio class="voicePlayer" controls preload="metadata"
    src="${imgSrc(u.dialogue)}"></audio></p>` : "";
  if (!items.length && !dlg) return "";
  const figs = items.length ? `<div class="row usedImgs">${
    items.map(([lb, f]) => `<figure class="usedFig"><img src="${imgSrc(f)}"
      title="${esc(f)}"><figcaption class="small">${lb}</figcaption></figure>`).join("")}</div>` : "";
  return `<div class="usedRow"><p class="small">本次视频生成使用：</p>${figs}${dlg}</div>`;
}

/* 提示词入口：一行紧凑展示（标签 + 按钮 + 摘要），点开弹窗查看/编辑，
   保存直接写 state（重生成时 agent 以此为准） */
function promptEditor(sid, key, text, label) {
  const pv = (text || "").replace(/\s+/g, " ").trim();
  const preview = pv ? (pv.length > 60 ? pv.slice(0, 60) + "…" : pv) : "（未设置）";
  return `<div class="promptRow">
    <button data-prompt="${sid}:${key}" title="${esc(pv)}">📝 ${label}</button>
    <span class="promptPreview">${esc(preview)}</span>
  </div>`;
}

function openPromptModal(sid, key) {
  const owner = findShot(sid) || findScene(sid);
  if (!owner) return;
  const label = key === "video" ? "视频提示词"
              : (key === "start" ? "首帧提示词" : "尾帧提示词");
  const isVideo = key === "video";
  const zh = isVideo ? (owner.prompt || "") : ((owner[key] || {}).prompt_zh || "");
  const en = isVideo ? "" : ((owner[key] || {}).prompt || "");
  const ov = document.createElement("div");
  ov.className = "pickerOverlay";
  ov.innerHTML = `<div class="picker promptCard">
    <div class="row"><b>${esc(sid)} · ${label}</b>
      <span class="small">保存后，下次重新生成以此为准</span>
      <button id="pmSave">💾 保存提示词</button>
      <button id="pmClose">✖ 关闭</button></div>
    <p class="small pmLabel">中文（阅读/编辑用）</p>
    <textarea id="pmZh" spellcheck="false" class="pmZh">${esc(zh)}</textarea>
    ${isVideo ? "" : `<p class="small pmLabel">English（实际喂给生成模型，可编辑）</p>
    <textarea id="pmEn" spellcheck="false" class="pmEn">${esc(en)}</textarea>`}
  </div>`;
  document.body.appendChild(ov);
  ov.querySelector("#pmClose").onclick = () => ov.remove();
  ov.onclick = e => { if (e.target === ov) ov.remove(); };
  ov.querySelector("#pmSave").onclick = async () => {
    const zhVal = ov.querySelector("#pmZh").value;
    const enVal = isVideo ? "" : ov.querySelector("#pmEn").value;
    if ((state.schema_version || 1) >= 2 && /^sh\d+$/.test(sid)) {
      const fields = isVideo ? {prompt: zhVal} : {prompt_zh: zhVal, prompt: enVal};
      const payload = isVideo ? {id: sid, prompt: zhVal} : {id: sid, [key]: fields};
      if (await patchBoard({shots: [payload]})) { toast("提示词已保存 ✔"); ov.remove(); }
      return;
    }
    if (isVideo) owner.prompt = zhVal;
    else {
      owner[key] = owner[key] || {};
      owner[key].prompt_zh = zhVal;
      owner[key].prompt = enVal;
    }
    if (await saveState()) { toast("提示词已保存 ✔"); ov.remove(); }
  };
}

function sceneAssets(s) {
  return `<div class="pair">${frameBlock(s, "start", "首帧")}${frameBlock(s, "end", "尾帧")}</div>`;
}

function renderKeyframes() {
  const v2 = (state.schema_version || 1) >= 2;
  const list = state.scenes || [];
  const seqs = state.sequences || [];
  const shots = state.shots || [];
  if (v2) {
    if (!seqs.length && !shots.length) {
      main.innerHTML = `<p class="hint">暂无镜头。</p>`;
      return;
    }
    let html = `<div class="card"><h3>镜头首尾帧</h3>
      <p class="small">按场次展示镜头。暂停期间仍可审核、选帧、改提示词和切换版本；重生成需恢复执行。</p></div>`;
    const listed = new Set();
    seqs.forEach(seq => {
      const seqShots = (seq.shot_ids || []).map(findShot).filter(Boolean);
      seqShots.forEach(sh => listed.add(sh.id));
      html += `<h2 class="groupTitle">${seq.id} · ${esc(seq.title || "")}
        <span class="small">${esc(seq.script_range || "")}</span></h2>`;
      seqShots.forEach(sh => {
        const stale = sh.validity === "stale"
          ? `<span class="staleTag">stale${sh.stale_reason ? " · " + esc(sh.stale_reason) : ""}</span>` : "";
        html += `<div class="card">
          <h3>${sh.id} · ${esc(sh.title || "")} ${badge(sh.status || "pending")}
            <span class="small">${esc(sh.conditioning_mode || "first_only")}</span> ${stale}</h3>
          ${sceneAssets(sh)}
          <div class="row">
            <button data-a="regenerate_shot" data-t="${sh.id}" data-n="1">↻ 整镜重生成</button>
          </div>
        </div>`;
      });
    });
    shots.filter(sh => !listed.has(sh.id)).forEach(sh => {
      html += `<div class="card">
        <h3>${sh.id} · ${esc(sh.title || "")} ${badge(sh.status || "pending")}</h3>
        ${sceneAssets(sh)}</div>`;
    });
    if (list.length) {
      html += `<div class="card"><h3>旧场景卡（只读）</h3>
        <p class="small">历史 scXX 仅供查阅，不能改时长/台词/选帧或审批。</p></div>`;
      list.forEach(s => {
        html += `<div class="card">
          <h3>${s.id} · ${s.title || ""} <span class="small">${s.duration || ""}s</span></h3>
          ${sceneAssets(s)}
        </div>`;
      });
    }
    main.innerHTML = html;
    bindButtons();
    return;
  }
  if (!list.length) {
    main.innerHTML = `<p class="hint">暂无场景。</p>`;
    return;
  }
  let html = `<div class="card"><h3>旧场景卡（维护）</h3>
    <p class="small">时长预算与切点以「分镜」工作台为准。</p></div>`;
  list.forEach((s, i) => {
    if (i > 0) html += `<div class="insertZone">
      <button class="insertBtn" data-ins="${list[i - 1].id}"
        title="在 ${list[i - 1].id} 与 ${s.id} 之间插入新场景">＋</button></div>`;
    html += `
    <div class="card">
      <h3>${s.id} · ${s.title || ""}
        <input class="durInput" type="number" min="1" max="120" step="1"
          value="${s.duration || ""}" data-dur="${s.id}" title="时长（秒），改完自动保存"> s</h3>
      <div class="sbBlock">
        <p class="small">🎬 分镜图 ${badge(s.storyboard?.status || "pending")}</p>
        ${s.storyboard?.image
          ? `<img class="frame" src="${imgSrc(s.storyboard.image)}">`
          : `<p class="hint">（无分镜图）</p>`}
        <div class="row">
          <button data-a="approve_storyboard" data-t="${s.id}">✔ 通过</button>
          <button data-a="regenerate_storyboard" data-t="${s.id}" data-n="1">↻ 重新生成分镜</button>
        </div>
      </div>
      ${sceneAssets(s)}
      <div class="row">
        <button data-a="approve_keyframes" data-t="${s.id}">✔ 通过</button>
        ${s.start?.status !== "removed"
          ? `<button data-a="regenerate_start" data-t="${s.id}" data-n="1">↻ 重生成首帧</button>` : ""}
        ${s.end?.status !== "removed"
          ? `<button data-a="regenerate_end" data-t="${s.id}" data-n="1">↻ 重生成尾帧</button>` : ""}
        <button data-a="regenerate_scene" data-t="${s.id}" data-n="1">↻ 整场重生成</button>
      </div>
    </div>`;
  });
  main.innerHTML = html;
  bindButtons();
}

/* 插入场景弹窗：输入给 agent 的场景描述，提交为 insert_scene 事件 */
function openInsertModal(afterSid) {
  const ov = document.createElement("div");
  ov.className = "pickerOverlay";
  ov.innerHTML = `<div class="picker promptCard">
    <div class="row"><b>在 ${esc(afterSid)} 之后插入新场景</b>
      <button id="imSubmit">✔ 交给 agent 执行</button>
      <button id="imClose">✖ 关闭</button></div>
    <p class="small pmLabel">描述这场戏的内容、时长、涉及人物/场景/道具，将作为任务交给 agent（新场景走分镜→首尾帧流程）</p>
    <textarea id="imText" class="pmZh" spellcheck="false"
      placeholder="例如：林川挂掉电话后起身去茶水间冲咖啡，5 秒，人物：林川，场景：办公室茶水间"></textarea>
  </div>`;
  document.body.appendChild(ov);
  const ta = ov.querySelector("#imText");
  ta.focus();
  ov.querySelector("#imClose").onclick = () => ov.remove();
  ov.onclick = e => { if (e.target === ov) ov.remove(); };
  ov.querySelector("#imSubmit").onclick = async () => {
    await postEvent("insert_scene", afterSid, ta.value.trim());
    ov.remove();
    toast(workerOnline
      ? "已记录 ✔ 自动执行 agent 接手中，稍后页面自动更新"
      : "已记录 ✔ 请到 agent 对话里说一声「处理一下」");
  };
}

function charName(id) {
  const a = (state.assets || []).find(x => x.id === id);
  return a ? a.name : id;
}

function dialogueBlock(s) {
  const d = s.dialogue || {};
  const lines = d.lines || [];
  const st = d.status || (lines.length ? "pending" : "approved");
  const lineHtml = lines.length
    ? `<ul class="dlgLines">${lines.map(l =>
        `<li><b>${esc(charName(l.speaker))}</b>${l.offscreen
          ? ` <span class="small">画外</span>` : ""} 「${esc(l.text || "")}」${
          l.emotion ? ` <span class="small">${esc(l.emotion)}</span>` : ""}</li>`
      ).join("")}</ul>`
    : `<p class="hint">本场无对白，跳过台词轨</p>`;
  const player = d.file
    ? `<audio class="voicePlayer" controls preload="metadata" src="${imgSrc(d.file)}"></audio>`
    : (lines.length ? `<p class="hint">（台词轨待生成——开口角色声线通过后即可生成）</p>` : "");
  const ro = (state.schema_version || 1) >= 2;
  const btns = ro ? "" : `<div class="row">
      <button data-a="approve_dialogue" data-t="${s.id}">✔ 通过台词</button>
      ${lines.length
        ? `<button data-a="regenerate_dialogue" data-t="${s.id}" data-n="1">↻ 重新生成台词</button>` : ""}
      <button data-dlg="${s.id}">📝 编辑台词</button>
      <label class="uploadBtn">⬆ 上传台词轨
        <input type="file" accept="audio/*" data-dlg-up="${s.id}" hidden>
      </label>
    </div>`;
  return `<div class="dlgBlock">
    <p class="small">🎙 台词轨 ${badge(st)}</p>
    ${lineHtml}${player}
    ${ro ? "" : versionBar(s, "dialogue")}
    ${btns}
  </div>`;
}

function renderVideos() {
  const list = state.scenes || [];
  const ro = (state.schema_version || 1) >= 2;
  const notice = ro ? `<div class="card"><h3>旧视频卡（只读）</h3>
    <p class="small">成片审批与台词以分镜工作台的镜头/对白为准。这里只查阅历史 scXX 文件。</p></div>` : "";
  main.innerHTML = notice + (list.length ? list.map(s => `
    <div class="card">
      <h3>${s.id} · ${s.title || ""} 视频 ${badge(s.video?.status || "pending")}</h3>
      ${s.storyboard?.image
        ? `<img class="frame sbThumb" src="${imgSrc(s.storyboard.image)}"
             title="分镜图">` : ""}
      ${dialogueBlock(s)}
      ${s.video?.file
        ? `<video class="frame" controls src="${imgSrc(s.video.file)}"></video>`
        : `<p class="hint">（待生成）</p>`}
      ${ro ? "" : versionBar(s, "video")}
      ${usedFrames(s.video)}
      ${ro ? "" : promptEditor(s.id, "video", s.prompt || "", "视频提示词")}
      ${ro ? "" : `<div class="row">
        <button data-a="approve_video" data-t="${s.id}">✔ 通过</button>
        <button data-a="regenerate_video" data-t="${s.id}" data-n="1">↻ 重新生成</button>
      </div>`}
    </div>`).join("")
    : `<p class="hint">暂无场景。</p>`);
  bindButtons();
}

function findShot(id) {
  return (state.shots || []).find(x => x.id === id);
}
function findUtterance(id) {
  return (state.utterances || []).find(x => x.id === id);
}
function framesSec(f) { return (Number(f) || 0) / 24; }
function shotUtterances(sh) {
  return (sh.utterance_ids || []).map(findUtterance).filter(Boolean);
}

async function patchBoard(payload) {
  const r = await api(`/api/project/${encodeURIComponent(project)}/state`, {
    method: "PATCH",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({revision: state.revision, ...payload}),
  });
  if (r._http === 409) {
    toast("版本冲突，已重新加载（未提交的修改请再试一次）");
    lastStateJson = "";
    refresh(true);
    return false;
  }
  if (r.error) { toast("✗ " + r.error); return false; }
  if (r.state) state = r.state;
  lastStateJson = "";
  refresh(true);
  return true;
}

function budgetBar(target, planned, extra) {
  const t = Number(target) || 0;
  const p = Number(planned) || 0;
  const pct = t ? Math.min(100, (p / t) * 100) : 0;
  const over = p - t > 0.05;
  return `<div class="budgetRow">
    <div class="budgetBar${over ? " over" : ""}"><i style="width:${pct}%"></i></div>
    <span class="small">${extra}</span>
  </div>`;
}

function locationRef(sh) {
  const loc = findAsset(sh.location_id) || {};
  const vid = sh.location_variant_id;
  const hit = (loc.variants || []).find(v => v.id === vid);
  const sheet = loc.image || "";
  const variantExplicit = hit && (hit.ref_file || hit.wide);
  if (variantExplicit) return variantExplicit;
  const variantFile = (hit && hit.file) || "";
  if (variantFile && variantFile !== sheet) return variantFile;
  const parentExplicit = loc.ref_file || (loc.views && loc.views.eye);
  if (parentExplicit) return parentExplicit;
  const path = variantFile || sheet;
  if (path && path !== sheet) return path;
  return sh.location_id ? `scenes/${sh.location_id}_wide.png` : path;
}

function shotRefs(sh) {
  const files = [];
  if (sh.start && sh.start.image) files.push(["首", sh.start.image]);
  (sh.cast || []).forEach(c => {
    if (c.on_screen === false) return;
    files.push(["人", `characters/char_${c.asset_id}_front.png`]);
  });
  if (sh.location_id) files.push(["地", locationRef(sh)]);
  const shown = files.slice(0, 3);
  return `<div class="refThumbs">${shown.map(([lb, f]) =>
    `<img src="${imgSrc(f)}" title="${esc(lb + " " + f)}"
      onerror="this.style.display='none'">`).join("")}</div>`;
}

const boardFold = {};
const shotOpen = {};
const shotTab = {};
function reviewLabel(rv) {
  rv = rv || {};
  const st = rv.status || "draft";
  const val = rv.validity === "stale" ? " stale" : "";
  return `${badge(st)}${val ? `<span class="staleTag">${val}</span>` : ""}`;
}
function unitOf(sh) {
  const units = (state && state.generation_units) || [];
  const hit = units.find(u => (u.shot_ids || []).includes(sh.id) || u.id === sh.unit_id);
  if (hit) return hit;
  return {id: sh.unit_id || ("gu" + String(sh.id || "").replace(/^sh/, "")),
          shot_ids: [sh.id], conditioning_mode: sh.conditioning_mode || "first_only",
          start: sh.start, end: sh.end, internal_cuts: [], target_frames: sh.target_frames};
}
function renderBoard() {
  const seqs = state.sequences || [];
  const shots = state.shots || [];
  if (!seqs.length) {
    main.innerHTML = `<p class="hint">尚无镜头表。请先跑 schema v2 迁移。</p>`;
    return;
  }
  const plan = state.planning || {};
  const jobs = state.jobs || [];
  const recov = jobs.filter(j => j.status === "recovery_required");
  const headIds = new Set();
  (seqs || []).forEach(s => {
    if (s.id === "seq01" || s.id === "seq02") (s.shot_ids || []).forEach(id => headIds.add(id));
  });
  const planned37 = shots.filter(s => headIds.has(s.id)).reduce((n, s) => n + (s.target_frames || 0), 0);
  const audioLabel = plan.speech_measured ? "音频实测" : "音频待测";
  const filmRv = state.storyboard || {};
  const seqApproved = seqs.filter(s => (s.storyboard_review || {}).status === "approved"
    && (s.storyboard_review || {}).validity !== "stale").length;
  const shotApproved = shots.filter(s => (s.storyboard_review || {}).status === "approved"
    && (s.storyboard_review || {}).validity !== "stale").length;
  const route = ((state.gaps || {}).route) || ((state.script_diff || {}).route) || {};
  let html = `<div class="card">
    <h3>导演工作台 ${reviewLabel(filmRv)}</h3>
    <p class="small">拍摄方案可编辑、可审核。已有资产和真实首尾帧是依据，不另生成分镜拼图。
      生成${paused() ? "仍暂停" : "进行中"}。
      全片目标 ${plan.target_frames || 4536} 帧 / ${plan.target_seconds || 189}s；
      已规划 ${plan.planned_seconds || "?"}s。
      前 37 秒目标 888 帧，镜头合计 ${planned37} 帧。
      ${audioLabel}。
      审核进度：子分镜 ${shotApproved}/${shots.length} · 场次 ${seqApproved}/${seqs.length} · 全片 ${filmRv.status || "draft"}。</p>
    ${budgetBar(plan.target_seconds || 189, plan.planned_seconds,
      `目标 ${plan.target_seconds || 189}s · 镜头 ${plan.planned_seconds || "?"}s · ${audioLabel}`)}
    ${route.script_sign ? `<p class="small staleTag">路线文案未裁定：剧本「${esc(route.script_sign || "")}」／纸条「${esc(route.approved_note_image || "")}」／口述「${esc(route.cunzhang_spoken || "")}」</p>` : ""}
    ${recov.length ? `<p class="small staleTag">遗留任务 ${recov.length} 条为「运行状态未知」，
      无 prompt_id，不能自动认领，也不会因重启重提。</p>` : ""}
    <div class="row">
      <button data-sb-review="project" data-t="${esc(state.name || "")}">✔ 确认全片分镜</button>
      <button data-sb-std="project" data-t="${esc(state.name || "")}">整理全片文字方案</button>
    </div>
  </div>`;
  seqs.forEach((seq, idx) => {
    const key = seq.id;
    if (boardFold[key] === undefined) boardFold[key] = idx > 1;
    const folded = boardFold[key];
    const b = seq.budget || {};
    const seqShots = (seq.shot_ids || []).map(findShot).filter(Boolean);
    const speech = (b.speech_seconds != null) ? Number(b.speech_seconds) : 0;
    const speechTag = b.speech_measured ? "音频实测" : "音频粗估";
    html += `<div class="card" data-seq="${seq.id}">
      <div class="seqHead" data-fold="${seq.id}">
        <h3>${seq.id} · ${esc(seq.title || "")}
          <span class="small">${esc(seq.script_range || "")}</span></h3>
        <span class="small">目标 ${b.target_seconds || seq.target_seconds}s /
          镜头 ${b.planned_seconds ?? framesSec(seqShots.reduce((n, s) => n + (s.target_frames || 0), 0)).toFixed(1)}s /
          ${speechTag} ${speech.toFixed(1)}s ${folded ? "▸" : "▾"}</span>
      </div>
      ${budgetBar(b.target_seconds || seq.target_seconds, b.planned_seconds,
        `Δ ${(b.delta_frames || 0)} 帧`)}
      <div class="row">
        <label class="small">拍摄方式
          <select data-seq-shoot="${seq.id}">
            <option value="one_take"${seq.shooting_mode === "one_take" ? " selected" : ""}>一镜到底</option>
            <option value="cut"${seq.shooting_mode !== "one_take" ? " selected" : ""}>切镜</option>
          </select>
        </label>
        <span class="small">本场审核 ${reviewLabel(seq.storyboard_review)}</span>
        <button data-sb-review="sequence" data-t="${seq.id}">✔ 确认本场分镜</button>
        <button data-sb-changes="sequence" data-t="${seq.id}">↩ 退回修改</button>
        <button data-sb-add="${seq.id}">＋ 子分镜</button>
      </div>`;
    if (!folded) {
      html += `<table class="shotTable">
        <tr><th>镜头</th><th>时间</th><th>秒</th><th>景别</th><th>地点</th>
          <th>人物</th><th>对白</th><th>条件</th><th>引用</th><th>入口 / 出口</th></tr>`;
      seqShots.forEach(sh => {
        const t0 = framesSec(sh.timeline_in_frame);
        const t1 = framesSec(sh.timeline_out_frame);
        const lines = shotUtterances(sh);
        const cast = (sh.cast || []).map(c => {
          const n = charName(c.asset_id);
          return c.on_screen === false ? n + "(画外)" : n;
        }).join("、");
        const stale = sh.validity === "stale" ? `<div class="staleTag">stale${sh.stale_reason ? " · " + esc(sh.stale_reason) : ""}</div>` : "";
        html += `<tr>
          <td><button class="linkish" data-shot-open="${sh.id}"><b>${sh.id}</b></button>
            <div class="small">${esc(sh.title || "")}</div>
            ${reviewLabel(sh.storyboard_review)}${stale}</td>
          <td class="small">${t0.toFixed(1)}–${t1.toFixed(1)}s
            <div>${sh.timeline_in_frame}–${sh.timeline_out_frame}f</div></td>
          <td><input class="durInput" type="number" min="0.5" max="30" step="0.5"
            value="${framesSec(sh.target_frames)}" data-shot-dur="${sh.id}"></td>
          <td>${esc(sh.shot_size || "")}</td>
          <td class="small">${esc(sh.location_id || "")}<div>${esc(sh.location_variant_id || "")}</div></td>
          <td class="small">${esc(cast)}</td>
          <td>${lines.length ? `<ul class="dlgLines">${lines.map(u =>
            `<li><b>${esc(charName(u.speaker))}</b> 「${esc(u.text || "")}」
              <button data-ut="${u.id}">改</button></li>`).join("")}</ul>`
            : `<span class="small">无对白</span>`}</td>
          <td class="small">${esc(sh.conditioning_mode || "")}</td>
          <td>${shotRefs(sh)}</td>
          <td class="small">${esc(sh.entry_state || "")}<hr>
            ${esc(sh.exit_state || "")}</td>
        </tr>`;
        if (shotOpen[sh.id]) {
          html += `<tr class="shotEditor"><td colspan="10">${shotEditor(sh)}</td></tr>`;
        }
      });
      html += `</table>`;
    }
    html += `</div>`;
  });
  main.innerHTML = html;
  main.querySelectorAll("[data-fold]").forEach(el => {
    el.onclick = () => { boardFold[el.dataset.fold] = !boardFold[el.dataset.fold]; renderBoard(); };
  });
  main.querySelectorAll("input[data-shot-dur]").forEach(inp => {
    inp.onchange = async () => {
      const v = parseFloat(inp.value);
      if (!v || v <= 0) return;
      const ok = await patchBoard({shots: [{id: inp.dataset.shotDur, target_seconds: v}]});
      if (ok) toast(`${inp.dataset.shotDur} 已改为 ${v}s，依赖已标 stale`);
    };
  });
  main.querySelectorAll("button[data-ut]").forEach(b => {
    b.onclick = () => openUtteranceModal(b.dataset.ut);
  });
  main.querySelectorAll(".refThumbs img").forEach(im => {
    im.onclick = () => openLightbox(im.src);
  });
  if (typeof bindStoryboard === "function") bindStoryboard();
}

function shotEditor(sh) {
  const tab = shotTab[sh.id] || "plan";
  const u = unitOf(sh);
  const tabs = [["plan", "拍摄方案"], ["assets", "资产"], ["frames", "首尾帧"], ["prompt", "模型提示词"]];
  const tabBar = `<div class="tabBar">${tabs.map(([id, lb]) =>
    `<button class="${tab === id ? "active" : ""}" data-shot-tab="${sh.id}:${id}">${lb}</button>`).join("")}</div>`;
  let body = "";
  if (tab === "plan") {
    body = `<div class="shotForm">
      <label>核心事件<textarea data-sf="${sh.id}:action">${esc(sh.action || "")}</textarea></label>
      <label>人物位置 / 朝向<input data-sf="${sh.id}:blocking" value="${esc(sh.blocking || "")}"></label>
      <label>表情 / 视线<input data-sf="${sh.id}:expression" value="${esc(sh.expression || "")}"></label>
      <label>入口状态<textarea data-sf="${sh.id}:entry_state">${esc(sh.entry_state || "")}</textarea></label>
      <label>出口状态<textarea data-sf="${sh.id}:exit_state">${esc(sh.exit_state || "")}</textarea></label>
      <label>景别 / 机位<input data-sf="${sh.id}:camera" value="${esc((sh.shot_size || "") + " " + (sh.camera || "")).trim()}"></label>
      <label>切点原因<input data-sf="${sh.id}:cut_reason" value="${esc(sh.cut_reason || "")}"></label>
      <div class="row">
        <button data-sf-save="${sh.id}">💾 保存方案</button>
        <button data-sb-review="shot" data-t="${sh.id}">✔ 确认子分镜</button>
        <button data-sb-changes="shot" data-t="${sh.id}">↩ 退回修改</button>
        <button data-sb-std="shot" data-t="${sh.id}">整理提示词</button>
        <button data-sb-split="${sh.id}">拆分</button>
      </div>
    </div>`;
  } else if (tab === "assets") {
    const assets = state.assets || [];
    body = `<div class="assetBrowse">${["character", "scene", "prop"].map(tp => {
      const items = assets.filter(a => (a.type || "character") === tp);
      return `<h4>${TYPE_NAMES[tp] || tp}（${items.length}）</h4>
        <div class="usedImgs">${items.map(a => `<figure class="usedFig">
          ${a.image ? `<img src="${imgSrc(a.image)}" title="${esc(a.id)}">` : `<p class="hint">无图</p>`}
          <figcaption class="small">${esc(a.name || a.id)} ${badge(a.status)}</figcaption>
        </figure>`).join("")}</div>`;
    }).join("")}</div>
    <p class="small">浏览全部已有图。模型输入槽与浏览数量分开；超过 3 槽需分步制作真实首尾帧。</p>`;
  } else if (tab === "frames") {
    const mode = u.conditioning_mode || "first_only";
    body = `<div class="row">
      <span class="small">生成单元 ${esc(u.id)}（${(u.shot_ids || []).join("、")}）</span>
      <label class="small">帧模式
        <select data-unit-mode="${u.id}">
          <option value="first_only"${mode === "first_only" ? " selected" : ""}>仅首帧</option>
          <option value="first_last"${mode === "first_last" ? " selected" : ""}>首尾帧</option>
          <option value="last_only"${mode === "last_only" ? " selected" : ""}>仅尾帧</option>
        </select>
      </label>
    </div>
    ${sceneAssets({id: sh.id, start: u.start || sh.start, end: u.end || sh.end})}`;
  } else {
    const p = u.prompt || sh.prompt_pack || {};
    body = `${promptEditor(sh.id, "start", (sh.start || {}).prompt_zh || (sh.start || {}).prompt || "", "首帧提示词")}
      ${promptEditor(sh.id, "end", (sh.end || {}).prompt_zh || (sh.end || {}).prompt || "", "尾帧提示词")}
      <p class="small">规范结果 ${badge(p.status || "draft")}
        ${p.skill ? `skill ${esc(p.skill.name || "")}` : "尚未整理"}</p>
      <textarea readonly class="pmZh">${esc(p.prompt_zh || "")}</textarea>`;
  }
  return tabBar + body;
}

async function storyboardPost(tail, body) {
  const r = await api(`/api/project/${encodeURIComponent(project)}/${tail}`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({revision: state.revision, ...body}),
  });
  if (r._http === 409) {
    toast("版本或审核依赖冲突：" + (r.error || ""));
    lastStateJson = "";
    refresh(true);
    return r;
  }
  if (r.error) { toast("✗ " + r.error); return r; }
  if (r.state) state = r.state;
  lastStateJson = "";
  refresh(true);
  return r;
}

function bindStoryboard() {
  main.querySelectorAll("[data-shot-open]").forEach(b => {
    b.onclick = e => { e.stopPropagation(); shotOpen[b.dataset.shotOpen] = !shotOpen[b.dataset.shotOpen]; renderBoard(); };
  });
  main.querySelectorAll("[data-shot-tab]").forEach(b => {
    b.onclick = () => {
      const [id, tab] = b.dataset.shotTab.split(":");
      shotTab[id] = tab; renderBoard();
    };
  });
  main.querySelectorAll("select[data-seq-shoot]").forEach(sel => {
    sel.onchange = () => patchBoard({sequences: [{id: sel.dataset.seqShoot, shooting_mode: sel.value}]});
  });
  main.querySelectorAll("select[data-unit-mode]").forEach(sel => {
    sel.onchange = () => patchBoard({generation_units: [{id: sel.dataset.unitMode, conditioning_mode: sel.value}]});
  });
  main.querySelectorAll("[data-sf-save]").forEach(b => {
    b.onclick = async () => {
      const id = b.dataset.sfSave;
      const fields = {id};
      main.querySelectorAll(`[data-sf^="${id}:"]`).forEach(inp => {
        const key = inp.dataset.sf.split(":")[1];
        fields[key] = inp.value;
      });
      if (fields.camera && !fields.shot_size) {
        const parts = String(fields.camera).trim().split(/\s+/);
        if (parts[0]) fields.shot_size = parts[0];
      }
      const ok = await patchBoard({shots: [fields]});
      if (ok) toast(id + " 方案已保存，相关审核已失效");
    };
  });
  main.querySelectorAll("[data-sb-review]").forEach(b => {
    b.onclick = async () => {
      const scope = b.dataset.sbReview;
      const target = b.dataset.t;
      const r = await api(`/api/project/${encodeURIComponent(project)}/storyboard`);
      const hash = await fetchHash(scope, target, r);
      await storyboardPost("storyboard/review", {
        scope, target, decision: "approved", content_hash: hash, source: "human",
      });
    };
  });
  main.querySelectorAll("[data-sb-changes]").forEach(b => {
    b.onclick = async () => {
      const note = prompt("退回备注：") || "";
      const r = await api(`/api/project/${encodeURIComponent(project)}/storyboard`);
      const hash = await fetchHash(b.dataset.sbChanges, b.dataset.t, r);
      await storyboardPost("storyboard/review", {
        scope: b.dataset.sbChanges, target: b.dataset.t,
        decision: "changes_requested", content_hash: hash, note, source: "human",
      });
    };
  });
  main.querySelectorAll("[data-sb-std]").forEach(b => {
    b.onclick = () => fire("standardize_storyboard", b.dataset.t);
  });
  main.querySelectorAll("[data-sb-add]").forEach(b => {
    b.onclick = () => storyboardPost("storyboard/edit", {op: "add", sequence_id: b.dataset.sbAdd});
  });
  main.querySelectorAll("[data-sb-split]").forEach(b => {
    b.onclick = async () => {
      const sec = parseFloat(prompt("在第几秒拆分？") || "");
      if (!sec) return;
      await storyboardPost("storyboard/edit", {op: "split", shot_id: b.dataset.sbSplit, split_seconds: sec});
    };
  });
  bindButtons();
}

async function fetchHash(scope, target, view) {
  if (scope === "project") return view.plan_hash || "";
  if (scope === "sequence") {
    const seq = (view.sequences || []).find(s => s.id === target);
    return (seq && seq.plan_hash) || "";
  }
  for (const seq of view.sequences || []) {
    const sh = (seq.shots || []).find(x => x.id === target);
    if (sh) return sh.plan_hash || "";
  }
  return "";
}

function openUtteranceModal(uid) {
  const ut = findUtterance(uid);
  if (!ut) return;
  const ov = document.createElement("div");
  ov.className = "pickerOverlay";
  ov.innerHTML = `<div class="picker promptCard">
    <div class="row"><b>${esc(uid)} · ${esc(charName(ut.speaker))}</b>
      <button id="umSave">💾 保存</button>
      <button id="umClose">✖ 关闭</button></div>
    <p class="small">改台词会使该句 WAV、场次音频、口型和成片失效；原始画面是否复用需再检查。</p>
    <textarea id="umText" class="pmZh">${esc(ut.text || "")}</textarea>
  </div>`;
  document.body.appendChild(ov);
  ov.querySelector("#umClose").onclick = () => ov.remove();
  ov.onclick = e => { if (e.target === ov) ov.remove(); };
  ov.querySelector("#umSave").onclick = async () => {
    const text = ov.querySelector("#umText").value.trim();
    if (!text) return;
    const ok = await patchBoard({utterances: [{id: uid, text}]});
    if (ok) { toast("台词已更新，依赖已标 stale"); ov.remove(); }
  };
}

async function saveFrameNode(id, key, fields) {
  if ((state.schema_version || 1) >= 2 && /^sh\d+$/.test(id || "")) {
    return patchBoard({shots: [{id, [key]: fields}]});
  }
  const sc = findScene(id);
  if (!sc) return false;
  sc[key] = Object.assign(sc[key] || {}, fields);
  return saveState();
}

/* 直接改写 state（移除/恢复/选现有帧）：本地操作无需 agent，PUT 带 revision */
async function saveState() {
  const r = await api(`/api/project/${encodeURIComponent(project)}/state`, {
    method: "PUT", headers: {"Content-Type": "application/json"},
    body: JSON.stringify(state),
  });
  if (r._http === 409) {
    toast("版本冲突，已重新加载（未提交的修改请再试一次）");
    lastStateJson = "";
    refresh(true);
    return false;
  }
  if (r.error) { toast("✗ 保存失败: " + r.error); return false; }
  lastStateJson = "";
  refresh(true);
  return true;
}

function findScene(sid) {
  return (state.scenes || []).find(x => x.id === sid);
}

function findAsset(id) {
  return (state.assets || []).find(x => x.id === id);
}

async function uploadAudio(relPath, file) {
  const dataUrl = await new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(r.result);
    r.onerror = () => reject(r.error);
    r.readAsDataURL(file);
  });
  const data = String(dataUrl).split(",")[1] || "";
  return api(`/api/project/${encodeURIComponent(project)}/upload`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({path: relPath, data}),
  });
}

function audioExt(file) {
  const ext = (file.name.split(".").pop() || "wav").toLowerCase();
  return ["wav", "mp3", "m4a", "ogg", "flac", "aac"].includes(ext) ? ext : "wav";
}

/* 编辑本场台词：每行 speaker|text|emotion|offscreen(0/1) */
function openDialogueModal(sid) {
  const sc = findScene(sid);
  if (!sc) return;
  const d = sc.dialogue || {};
  const lines = d.lines || [];
  const text = lines.map(l =>
    [l.speaker || "", l.text || "", l.emotion || "", l.offscreen ? "1" : "0"].join("|")
  ).join("\n");
  const ov = document.createElement("div");
  ov.className = "pickerOverlay";
  ov.innerHTML = `<div class="picker promptCard">
    <div class="row"><b>${esc(sid)} · 编辑台词</b>
      <button id="dmSave">💾 保存</button>
      <button id="dmClose">✖ 关闭</button></div>
    <p class="small pmLabel">每行：角色id|台词|情绪|画外(0/1)。空行忽略。保存后需重新生成台词轨。</p>
    <textarea id="dmText" class="pmZh" spellcheck="false">${esc(text)}</textarea>
  </div>`;
  document.body.appendChild(ov);
  ov.querySelector("#dmClose").onclick = () => ov.remove();
  ov.onclick = e => { if (e.target === ov) ov.remove(); };
  ov.querySelector("#dmSave").onclick = async () => {
    const parsed = ov.querySelector("#dmText").value.split("\n").map(row => {
      const t = row.trim();
      if (!t) return null;
      const [speaker, txt, emotion, off] = t.split("|");
      if (!speaker || !txt) return null;
      return {
        speaker: speaker.trim(),
        text: (txt || "").trim(),
        emotion: (emotion || "").trim(),
        offscreen: (off || "").trim() === "1",
      };
    }).filter(Boolean);
    sc.dialogue = sc.dialogue || {status: "pending", file: "", versions: []};
    sc.dialogue.lines = parsed;
    sc.dialogue.status = parsed.length ? "pending" : "approved";
    if (await saveState()) { toast("台词已保存 ✔"); ov.remove(); }
  };
}

/* 帧选择器：列出 keyframes/ 目录全部图片（最新的在前），点选即指派为首/尾帧 */
async function openFramePicker(sid, key) {
  const label = key === "start" ? "首帧" : "尾帧";
  const r = await api(`/api/project/${encodeURIComponent(project)}/frames`);
  const frames = r.frames || [];
  const ov = document.createElement("div");
  ov.className = "pickerOverlay";
  ov.innerHTML = `<div class="picker">
    <div class="row"><b>选择 ${esc(sid)} 的${label}</b>
      <span class="small">共 ${frames.length} 张，点击即指派</span>
      <button id="pickerClose">✖ 关闭</button></div>
    <div class="pickerGrid">${frames.map(f => `
      <div class="pickerItem" data-f="${esc(f.file)}">
        <img src="/projects/${encodeURIComponent(project)}/${esc(f.file)}?v=${stateVersion}">
        <p class="small">${esc(f.file.replace(/^keyframes\//, ""))}</p>
      </div>`).join("") || `<p class="hint">keyframes/ 目录暂无图片</p>`}</div>
  </div>`;
  document.body.appendChild(ov);
  ov.querySelector("#pickerClose").onclick = () => ov.remove();
  ov.onclick = e => { if (e.target === ov) ov.remove(); };
  ov.querySelectorAll(".pickerItem").forEach(it => {
    it.onclick = async () => {
      const file = it.dataset.f;
      const owner = findShot(sid) || findScene(sid);
      if (!owner) { ov.remove(); return; }
      const f = owner[key] || {};
      const vs = (f.versions || []).slice();
      if (file && !vs.some(v => v.file === file)) {
        vs.push({file, ts: new Date().toISOString().slice(0, 16).replace("T", " ")});
      }
      const ok = await saveFrameNode(sid, key, {
        image: file, status: "review", validity: "current", versions: vs,
      });
      if (ok) toast(`已指派 ${file} 为 ${sid} ${label}（待审核）`);
      ov.remove();
    };
  });
}

/* 图片灯箱：点击任意帧图/参考图放大查看，点任意处或按 Esc 关闭 */
function openLightbox(src) {
  const ov = document.createElement("div");
  ov.className = "lightbox";
  ov.innerHTML = `<img src="${src}">`;
  ov.onclick = () => ov.remove();
  const onKey = e => { if (e.key === "Escape") { ov.remove(); document.removeEventListener("keydown", onKey); } };
  document.addEventListener("keydown", onKey);
  document.body.appendChild(ov);
}

function bindButtons() {
  main.querySelectorAll("button[data-a]").forEach(b => {
    const t = b.dataset.a || "";
    if (paused() && /^(regenerate_|script_confirmed|insert_scene)/.test(t)) {
      b.disabled = true;
      b.title = "生成已暂停";
    }
    b.onclick = () => fire(b.dataset.a, b.dataset.t, b.dataset.n === "1");
  });
  main.querySelectorAll("button[data-remove]").forEach(b => {
    b.onclick = () => saveFrameNode(b.dataset.t, b.dataset.remove, {status: "removed"});
  });
  main.querySelectorAll("button[data-restore]").forEach(b => {
    b.onclick = () => {
      const owner = findShot(b.dataset.t) || findScene(b.dataset.t) || {};
      const f = owner[b.dataset.restore] || {};
      saveFrameNode(b.dataset.t, b.dataset.restore, {
        status: f.image ? "review" : "pending",
      });
    };
  });
  main.querySelectorAll("button[data-pick]").forEach(b => {
    b.onclick = () => openFramePicker(b.dataset.t, b.dataset.pick);
  });
  main.querySelectorAll("img.frame, .usedFig img").forEach(im => {
    im.onclick = () => openLightbox(im.src);
  });
  main.querySelectorAll("button[data-prompt]").forEach(b => {
    b.onclick = () => {
      const [sid, key] = b.dataset.prompt.split(":");
      openPromptModal(sid, key);
    };
  });
  main.querySelectorAll("button[data-ver]").forEach(b => {
    b.onclick = async () => {
      const [sid, key, idx] = b.dataset.ver.split(":");
      const owner = findShot(sid) || findScene(sid);
      const f = owner && owner[key];
      const v = f && (f.versions || [])[+idx];
      if (!v) return;
      const fields = (key === "video" || key === "dialogue")
        ? {file: v.file, status: "review"}
        : {image: v.file, status: "review", validity: "current"};
      if (await saveFrameNode(sid, key, fields)) toast(`已切换到 v${+idx + 1}（待审核）`);
    };
  });
  main.querySelectorAll("button[data-ins]").forEach(b => {
    b.onclick = () => openInsertModal(b.dataset.ins);
  });
  main.querySelectorAll("input[data-dur]").forEach(inp => {
    inp.onchange = async () => {
      const sc = findScene(inp.dataset.dur);
      const v = parseInt(inp.value, 10);
      if (!sc || !v || v < 1) { inp.value = sc ? sc.duration || "" : ""; return; }
      sc.duration = v;
      if (await saveState()) toast(`${sc.id} 时长已改为 ${v}s ✔`);
    };
  });
  main.querySelectorAll("button[data-dlg]").forEach(b => {
    b.onclick = () => openDialogueModal(b.dataset.dlg);
  });
  main.querySelectorAll("input[data-voice-up]").forEach(inp => {
    inp.onchange = async () => {
      const file = inp.files && inp.files[0];
      inp.value = "";
      if (!file) return;
      const a = findAsset(inp.dataset.voiceUp);
      if (!a) return;
      const rel = `characters/${a.id}_voice.${audioExt(file)}`;
      a.voice = a.voice || {transcript: "", desc: "", versions: []};
      a.voice.versions = a.voice.versions || [];
      const r = await uploadAudio(rel, file);
      if (r.error) { toast("✗ 上传失败: " + r.error); return; }
      const ts = new Date().toISOString().slice(0, 16).replace("T", " ");
      const live = r.path || rel;
      a.voice.file = live;
      a.voice.file_sha256 = r.sha256 || "";
      a.voice.revision_id = r.revision_id || r.sha256 || "";
      a.voice.status = "review";
      a.voice.versions.push({file: live, ts, sha256: r.sha256 || ""});
      if (await saveState()) toast(`${a.name} 声线已上传，待审核`);
      else toast("声线文件已另存，状态未切换（冲突时原审核文件不变）");
    };
  });
  main.querySelectorAll("input[data-dlg-up]").forEach(inp => {
    inp.onchange = async () => {
      const file = inp.files && inp.files[0];
      inp.value = "";
      if (!file) return;
      const sc = findScene(inp.dataset.dlgUp);
      if (!sc) return;
      const rel = `dialogues/${sc.id}.${audioExt(file)}`;
      sc.dialogue = sc.dialogue || {lines: [], versions: []};
      sc.dialogue.versions = sc.dialogue.versions || [];
      if (sc.dialogue.file) {
        const ar = await api(`/api/project/${encodeURIComponent(project)}/archive`, {
          method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({path: sc.dialogue.file}),
        });
        if (ar.archived) {
          const last = sc.dialogue.versions[sc.dialogue.versions.length - 1];
          if (last && last.file === sc.dialogue.file) last.file = ar.archived;
          else sc.dialogue.versions.push({file: ar.archived, ts: last && last.ts || ""});
        }
      }
      const r = await uploadAudio(rel, file);
      if (r.error) { toast("✗ 上传失败: " + r.error); return; }
      const ts = new Date().toISOString().slice(0, 16).replace("T", " ");
      const live = r.path || rel;
      sc.dialogue.file = live;
      sc.dialogue.file_sha256 = r.sha256 || "";
      sc.dialogue.revision_id = r.revision_id || r.sha256 || "";
      sc.dialogue.status = "review";
      sc.dialogue.versions.push({file: live, ts, sha256: r.sha256 || ""});
      if (await saveState()) toast(`${sc.id} 台词轨已上传，待审核`);
    };
  });
}

const views = {script: renderScript, assets: renderAssets, board: renderBoard,
               keyframes: renderKeyframes, videos: renderVideos,
               knowledge: renderKnowledge, config: renderConfig};

/* ---------- 配置页：全局/项目 记忆（JSON 编辑 + 保存） ---------- */
let cfgRenderedFor = null;
async function renderConfig() {
  if ($("#cfgRoot") && cfgRenderedFor === (project || "")) return;  // 已在显示
  cfgRenderedFor = project || "";
  const enc = encodeURIComponent(project || "");
  const [g, p, mi, wk] = await Promise.all([
    api("/api/config/global"),
    project ? api(`/api/project/${enc}/config`) : Promise.resolve(null),
    api("/api/model"),
    api("/api/worker"),
  ]);
  const active = mi.observed || mi.effective || "";
  const desired = wk.desired_model || "";
  /* 下拉框选项：中转清单 + 当前实测/目标模型兜底（清单可能不含实际路由的模型） */
  const opts = [{"id": "", "label": "自动（中转默认）"},
    ...mi.available.models.map(m => ({"id": m, "label": m}))];
  for (const extra of [active, desired]) {
    if (extra && !opts.some(o => o.id === extra))
      opts.push({"id": extra, "label": extra + (extra === active ? "（当前实测）" : "")});
  }
  const switcher = `
    <div class="row">
      <select id="modelSelect" style="flex:1;min-width:220px">${
        opts.map(o => `<option value="${esc(o.id)}"${o.id === (desired || "") ? " selected" : ""}>${esc(o.label)}</option>`).join("")
      }</select>
      <button id="applyModelBtn">确认切换</button>
    </div>
    <p class="small" id="modelSwitchMsg">${desired && desired !== active
      ? `⏳ 已请求切换到 ${esc(desired)}，worker 重建会话中（约 30–60 秒）…`
      : "选择模型后点「确认切换」：写入目标 → worker 自动重建会话生效；正在执行的任务完成后才切换。"}${mi.available.source !== "relay /v1/models"
      ? `<br>⚠ 模型清单来源: ${esc(mi.available.source)}` : ""}</p>`;
  const modelCard = `<div class="card">
    <h3>🤖 执行模型 <span class="small">studio/model_info.py</span></h3>
    <p class="small">影响生成质量的因素之一；每轮参数记录须写明此模型。模型经
      settings.json 三层链解析（local &gt; project &gt; user），并以最新会话记录实测校准。</p>
    <p>当前生效: <b>${esc(active || "未知（走默认）")}</b>
      <span class="small">（${esc(mi.source)}）</span></p>
    ${mi.relay ? `<p class="small">API 中转: ${esc(mi.base_url || "")}</p>` : ""}
    ${mi.settings_model_chain.length ? `<p class="small">settings 显式模型（低→高）: ${
      esc(mi.settings_model_chain.map(c => `${c.source}=${c.model}`).join(" → "))}</p>` : ""}
    ${switcher}
  </div>`;
  const block = (title, path, obj, endpoint) => `
    <div class="card">
      <h3>${title} <span class="small">${path}</span></h3>
      <p class="small">生成前 agent 必读的记忆层；JSON 格式，保存后立即生效。</p>
      <textarea id="${endpoint}" style="min-height:260px" spellcheck="false">${
        esc(JSON.stringify(obj, null, 2))}</textarea>
      <div class="row"><button data-cfg="${endpoint}">保存</button>
        <span class="small" id="${endpoint}Msg"></span></div>
    </div>`;
  main.innerHTML = `<div id="cfgRoot">
    ${modelCard}
    <div class="card"><h3>⚙️ 配置与记忆</h3>
      <p class="small">全局配置对所有项目生效；项目配置覆盖全局同名项。
        事件白名单与 state 校验由 server 强制执行，无需在此配置。</p></div>
    ${block("全局配置", "studio/config/global.json", g, "cfgGlobal")}
    ${project ? block("项目配置（当前项目）", `projects/${esc(project)}/config.json`,
                      p || {}, "cfgProject")
              : `<p class="hint">请先选择项目以编辑项目配置。</p>`}
  </div>`;
  main.querySelectorAll("button[data-cfg]").forEach(b => {
    b.onclick = async () => {
      const ta = $("#" + b.dataset.cfg), msg = $("#" + b.dataset.cfg + "Msg");
      let obj;
      try { obj = JSON.parse(ta.value); }
      catch (e) { msg.textContent = "✗ JSON 解析失败: " + e.message; return; }
      const r = await api(b.dataset.cfg === "cfgGlobal"
        ? "/api/config/global"
        : `/api/project/${encodeURIComponent(project)}/config`,
        {method: "PUT", headers: {"Content-Type": "application/json"},
         body: JSON.stringify(obj)});
      msg.textContent = r.ok ? "✔ 已保存" : "✗ " + (r.error || "保存失败");
    };
  });
  /* 模型切换：下拉选择 + 确认 → 写 desired_model.json，worker 巡检后自动重建会话 */
  const switchModel = async m => {
    const r = await api("/api/worker/model", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({model: m})});
    if (r.error) { toast("✗ 切换失败: " + r.error); return; }
    toast(wk.online
      ? `已请求切换到 ${m || "自动"}，worker 重建会话中…`
      : `已保存目标模型 ${m || "自动"}，worker 启动时生效`);
    cfgRenderedFor = null;   // 强制重渲染配置页，刷新高亮与提示
    refresh(true);
  };
  $("#applyModelBtn").onclick = () => {
    const v = $("#modelSelect").value;
    if (v === desired) { toast("与当前目标一致，无需切换"); return; }
    switchModel(v);
  };
}

/* ---------- 知识库：Gaven 提示词方法论（服务端 studio/knowledge/ 只读） ---------- */
let kbDocs = null, kbSel = "";
const esc = s => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

function mdRender(t) {
  const blocks = [];
  t = t.replace(/```[\s\S]*?(?:```|$)/g, m => {
    blocks.push(m.replace(/^```\w*\n?/, "").replace(/```\s*$/, ""));
    return `\u0000${blocks.length - 1}\u0000`;
  });
  let html = esc(t).split("\n").map(line => {
    const s = line.trim();
    if (/^\u0000\d+\u0000$/.test(s)) return s;
    const h = s.match(/^(#{1,4})\s+(.*)/);
    if (h) return `<h${h[1].length + 1}>${h[2]}</h${h[1].length + 1}>`;
    if (/^\s*([-*]|\d+\.)\s+/.test(line)) return `<li>${line.replace(/^\s*([-*]|\d+\.)\s+/, "")}</li>`;
    if (/^\s*\|/.test(s)) return `<div class="mdRow">${s}</div>`;
    if (!s) return "";
    return `<p>${s}</p>`;
  }).join("");
  html = html
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/(?:<li>[\s\S]*?<\/li>)+/g, m => `<ul>${m}</ul>`);
  return html.replace(/\u0000(\d+)\u0000/g, (_, i) => `<pre>${esc(blocks[i])}</pre>`);
}

async function renderKnowledge() {
  if (kbSel) {  // 阅读视图
    const doc = $("#mdDoc");
    if (doc && doc.dataset.sel === kbSel) return;  // 已在显示，不打断阅读
    main.innerHTML = `<p class="hint">加载中…</p>`;
    const r = await fetch(`/knowledge/${kbSel.split("/").map(encodeURIComponent).join("/")}`);
    const text = await r.text();
    main.innerHTML = `
      <div class="row kbBar"><button id="kbBack">← 返回列表</button>
        <span class="small">studio/knowledge/${esc(kbSel)}</span></div>
      <div class="card mdDoc" id="mdDoc" data-sel="${esc(kbSel)}">${mdRender(text)}</div>`;
    $("#kbBack").onclick = () => { kbSel = ""; renderKnowledge(); };
    window.scrollTo(0, 0);
    return;
  }
  if (kbDocs && $("#kbRoot")) return;  // 列表已在显示
  const r = await api("/api/knowledge");
  kbDocs = r.docs || [];
  const groups = {};
  kbDocs.forEach(d => {
    const top = d.path.split("/")[0];
    (groups[top] = groups[top] || []).push(d);
  });
  main.innerHTML = `<div id="kbRoot">
    <div class="card"><h3>📖 提示词知识库</h3>
      <p class="small">Gaven 方法论已嵌入本服务：视频阶段逐场提示词按「22剧本转视频提示词」格式写；
        首尾帧与资产图的英 prompt 光线/焦段/色调词汇参考「19第十九期AI电影导演Skill」。
        文件实体在 <code>studio/knowledge/</code>。</p></div>
    ${Object.entries(groups).map(([g, docs]) => `
      <h2 class="groupTitle">${esc(g)}</h2>
      <div class="kbList">${docs.map(d => {
        const rel = esc(d.path.slice(g.length + 1));
        const kb = Math.max(1, Math.round(d.size / 1024));
        return `<button class="kbItem" data-path="${esc(d.path)}">${rel}
          <span class="small">${kb} KB</span></button>`;
      }).join("")}</div>`).join("")}</div>`;
  main.querySelectorAll(".kbItem").forEach(b => {
    b.onclick = () => { kbSel = b.dataset.path; renderKnowledge(); };
  });
}

let stateVersion = 0, lastStateJson = "";

async function refresh(force) {
  if (!project) return;
  const [newState, worker] = await Promise.all([
    api(`/api/project/${encodeURIComponent(project)}/state`),
    api("/api/worker"),
  ]);
  state = newState;
  workerOnline = !!worker.online;
  const pauseEl = $("#pauseBadge");
  if (pauseEl) {
    const on = paused();
    pauseEl.hidden = !on;
    pauseEl.textContent = on
      ? `⏸ 生成已暂停${(state.execution && state.execution.reason) ? " · " + state.execution.reason : ""}`
      : "";
  }
  const badge = $("#workerBadge");
  if (badge) {
    badge.textContent = workerOnline
      ? (worker.busy ? `🤖 agent 执行中…` : `🤖 自动执行 在线`)
      : `🤖 自动执行 离线`;
    badge.className = "workerBadge " + (workerOnline ? (worker.busy ? "busy" : "on") : "off");
    badge.title = `执行模型: ${worker.model || "未解析"}`
      + (worker.last_result ? `\n上次执行: ${worker.last_result}` : "");
  }
  const json = JSON.stringify(state);
  if (!force && json === lastStateJson) return;   // 状态没变不重渲染，避免图片闪烁
  lastStateJson = json;
  stateVersion++;
  document.querySelectorAll("#stageNav button").forEach(b => {
    b.classList.toggle("active", b.dataset.stage === view);
    b.style.opacity = 1;
  });
  (views[view] || renderScript)();
}

/* ---------- 项目选择 ---------- */
async function loadProjects() {
  const {projects} = await api("/api/projects");
  const sel = $("#projectSelect");
  sel.innerHTML = `<option value="">选择项目…</option>` +
    projects.map(p => `<option ${p === project ? "selected" : ""}>${p}</option>`).join("");
}
$("#projectSelect").onchange = e => { project = e.target.value || null; lastStateJson = ""; refresh(true); };
$("#newProjectBtn").onclick = async () => {
  const name = prompt("项目名：");
  if (!name) return;
  const r = await api("/api/project", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({name}),
  });
  if (r.error) return toast("创建失败：" + r.error);
  project = r.name;
  lastStateJson = "";
  await loadProjects();
  refresh(true);
};
document.querySelectorAll("#stageNav button").forEach(b => {
  b.onclick = () => { view = b.dataset.stage; refresh(true); };
});
loadProjects();

/* 每 3 秒轮询 state：agent 更新后页面自动出现新图；
   用户正在输入（剧本框/备注框）时跳过本次，避免打断 */
setInterval(() => {
  const ae = document.activeElement;
  if (ae && (ae.tagName === "TEXTAREA" || ae.tagName === "INPUT")) return;
  if (document.querySelector(".pickerOverlay")) return;
  refresh();
}, 3000);
