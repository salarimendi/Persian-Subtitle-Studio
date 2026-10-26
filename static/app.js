"use strict";
const $ = (id) => document.getElementById(id);
const el = (tag, props = {}, ...kids) => { const e = Object.assign(document.createElement(tag), props); kids.forEach((k) => e.append(k)); return e; };
const DEF_STYLE = { font: "Tahoma", size_pct: 5.5, bold: true, color: "#ffffff", outline_color: "#000000", outline_w: 3, shadow_w: 0, shadow_color: "#000000",
  shadow_opacity: 0.6, line_spacing: 1.2, position: "bottom", margin_pct: 6, bg_on: false, bg_color: "#000000", bg_opacity: 0.6, bg_radius: 0.3, bg_pad_x: 0.45,
  bg_pad_y: 0.12, bg_border_w: 0, bg_border_color: "#ffffff", emphasis_color: "#ffd400", word_mode: "none", word_color: "#ffd400", effect_all: "none",
  max_chars: 32, max_words: 0, min_dur: 0.8, gap: 0.08, digits: "keep", fix_punct: true };
const DEF_EXP = { reframe: "original", cover_duration: 0, cover_fit: "fill", embed_artwork: true, formats: ["mp4", "srt", "ass"], language: "fa" };
const QUICK = ["😀", "😂", "😍", "👍", "🔥", "❤️", "🎉", "🙏", "😮", "⭐", "✅", "⚠️"];
const EMO = { خنده: "😂", شاد: "😄", عشق: "❤️", دوست: "❤️", قلب: "❤️", غم: "😢", گریه: "😭", آتش: "🔥", عالی: "👌", خوب: "👍", ممنون: "🙏",
  تشکر: "🙏", سلام: "👋", پول: "💰", غذا: "🍽️", چای: "🍵", قهوه: "☕", ماشین: "🚗", خانه: "🏠", کتاب: "📚", موسیقی: "🎵", تولد: "🎂", جشن: "🎉",
  سفر: "✈️", دریا: "🌊", خورشید: "☀️", باران: "🌧️", برف: "❄️", ستاره: "⭐", گربه: "🐱", سگ: "🐶", مهم: "❗", چرا: "❓", وای: "😮", ویدیو: "🎬" };
const EFFECTS = [["none", "بدون افکت"], ["fadein", "ظاهر شدن تدریجی"], ["fadeout", "محو شدن"], ["fadeboth", "ظاهر و محو"], ["pop", "بزرگ‌شدن کوتاه"]];
const ACTIVE = ["queued", "transcribing", "render_queued", "rendering"];
const STAGE_FA = { queued: "در صف", transcribing: "", ready: "آمادهٔ ویرایش", render_queued: "در صف ساخت خروجی", rendering: "", done: "خروجی ساخته شد", error: "خطا" };
const S = { jobs: [], cur: null, segs: [], dirty: false, lastStatus: {}, style: { ...DEF_STYLE }, exp: { ...DEF_EXP }, notes: [], lastTA: null, layout: null, presets: [] };
try { Object.assign(S.style, JSON.parse(localStorage.getItem("style2") || "{}")); Object.assign(S.exp, JSON.parse(localStorage.getItem("exp2") || "{}")); } catch (e) { /* بدون ذخیره‌سازی */ }

async function api(path, opt = {}) {
  const r = await fetch(path, opt);
  if (!r.ok) {
    let msg = "خطای ناشناخته (" + r.status + ")";
    try { const j = await r.json(); msg = typeof j.detail === "string" ? j.detail : "ورودی نامعتبر است."; } catch (e) { /* غیر JSON */ }
    throw new Error(msg);
  }
  return r;
}
const jget = async (p) => (await api(p)).json();
const jsend = (o, method = "POST") => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(o) });
const curJob = () => S.jobs.find((j) => j.id === S.cur);
const note = (m) => { S.notes.push(m); renderNotes(); };
const body = (extra = {}) => ({ segments: S.segs, style: S.style, export: S.exp, ...extra });
const persist = () => { try { localStorage.setItem("style2", JSON.stringify(S.style)); localStorage.setItem("exp2", JSON.stringify(S.exp)); } catch (e) { /* ignore */ } };

/* ---------- وضعیت ---------- */
async function loadStatus() {
  const s = await jget("/api/status"), msgs = [];
  if (!s.ffmpeg) msgs.push(s.ffmpeg_help);
  if (!s.whisper_installed) msgs.push("کتابخانهٔ faster-whisper نصب نیست؛ وابستگی‌ها را نصب کنید.");
  if (s.whisper_installed && !s.model_cached) msgs.push("اولین تبدیل گفتار، مدل فارسی (حدود ۳ گیگابایت) را یک‌بار در پوشهٔ models دانلود می‌کند و حدود ۳ گیگابایت RAM مصرف می‌کند.");
  $("banner").hidden = !msgs.length; $("banner").textContent = msgs.join(" ");
  $("banner").className = "banner" + (!s.ffmpeg || !s.whisper_installed ? " err" : "");
  $("engine").textContent = s.ffmpeg ? s.ffmpeg_version.split(" ").slice(0, 3).join(" ") : "FFmpeg یافت نشد";
}
async function loadProjects() {
  const l = await jget("/api/projects"), sel = $("openSaved");
  sel.replaceChildren(el("option", { value: "", textContent: "— انتخاب نشده —" }), ...l.map((p) => el("option", { value: p.file, textContent: `${p.video_name} (${p.segments} بخش، ${p.saved_at})` })));
}

/* ---------- بارگذاری و صف ---------- */
const uploadQ = []; let uploading = false;
function addFiles(files) { [...files].forEach((f) => uploadQ.push(f)); pumpUploads(); }
async function pumpUploads() {
  if (uploading) return; uploading = true;
  while (uploadQ.length) { const f = uploadQ.shift(); try { await xhrPost("/api/jobs", (fd) => { fd.append("file", f); fd.append("language", $("lang").value); }, f.name); } catch (e) { note(f.name + ": " + e.message); } }
  $("uploadMsg").textContent = ""; uploading = false;
}
function xhrPost(url, fill, label) {
  return new Promise((res, rej) => {
    const fd = new FormData(); fill(fd);
    const x = new XMLHttpRequest(); x.open("POST", url);
    x.upload.onprogress = (e) => { if (e.lengthComputable) $("uploadMsg").textContent = `بارگذاری ${label}: ${Math.round(e.loaded / e.total * 100)}٪`; };
    x.onload = async () => {
      let j = {}; try { j = JSON.parse(x.responseText); } catch (e) { /* ignore */ }
      if (x.status >= 200 && x.status < 300) { await refresh(); if (!S.cur || url === "/api/open") selectJob(j.id, true); res(j); }
      else rej(new Error(typeof j.detail === "string" ? j.detail : "درخواست ناموفق بود."));
    };
    x.onerror = () => rej(new Error("ارتباط با برنامه قطع شد."));
    x.send(fd);
  });
}
function renderNotes() { $("notes").replaceChildren(...S.notes.map((n, i) => el("li", { title: "برای بستن بزنید", onclick: () => { S.notes.splice(i, 1); renderNotes(); } }, n))); }
function renderJobs() {
  $("jobs").replaceChildren(...S.jobs.map((j) => {
    const pct = j.progress == null ? "" : ` ${Math.round(j.progress)}٪`;
    const st = el("div", { className: "st" + (j.status === "error" ? " error" : "") }, (j.stage || STAGE_FA[j.status]) + pct);
    const top = el("div", { className: "top" }, el("span", { className: "nm", textContent: j.name }), el("span", { className: "muted", textContent: `${Math.round(j.duration)} ثانیه` }));
    if (j.status === "error") top.append(el("button", { textContent: "تلاش دوباره", onclick: async (e) => { e.stopPropagation(); try { await api(`/api/jobs/${j.id}/retry`, { method: "POST" }); } catch (er) { note(er.message); } refresh(); } }));
    top.append(el("button", { textContent: "حذف", disabled: j.status === "transcribing" || j.status === "rendering", onclick: async (e) => { e.stopPropagation(); await delJob(j.id); } }));
    const li = el("li", { className: j.id === S.cur ? "sel" : "", onclick: () => selectJob(j.id) }, top, st);
    if (ACTIVE.includes(j.status)) { const b = el("div", { className: "bar" + (j.progress == null ? " indet" : "") }, el("i")); b.firstChild.style.width = (j.progress || 0) + "%"; li.append(b); }
    return li;
  }));
}
async function delJob(id) {
  try { await api(`/api/jobs/${id}`, { method: "DELETE" }); } catch (e) { note(e.message); return; }
  if (S.cur === id) { S.cur = null; S.segs = []; S.dirty = false; $("vid").removeAttribute("src"); $("vid").load(); renderAll(); }
  refresh();
}

/* ---------- انتخاب ویدیو ---------- */
async function selectJob(id, force) {
  if (S.cur === id && !force) return;
  if (S.dirty && S.cur && S.cur !== id) await saveProject(true);
  S.cur = id; S.segs = []; S.dirty = false; S.layout = null;
  await loadCur(true);
}
async function loadCur(force) {
  const j = S.cur && (await jget(`/api/jobs/${S.cur}`).catch(() => null));
  if (!j) { S.cur = null; renderAll(); return; }
  if (force || !S.dirty) S.segs = j.segments || [];
  if (force && j.style) Object.assign(S.style, DEF_STYLE, j.style);
  if (force && j.export) Object.assign(S.exp, DEF_EXP, j.export);
  const v = $("vid"), url = `/api/jobs/${j.id}/preview`;
  if (j.has_preview && !v.src.endsWith(url)) v.src = url;
  syncControls(); renderAll(); wantPreview();
}
async function refresh() {
  try { S.jobs = await jget("/api/jobs"); } catch (e) { return; }
  let reload = false;
  for (const j of S.jobs) {
    const prev = S.lastStatus[j.id]; S.lastStatus[j.id] = j.status;
    if (j.id === S.cur && prev && prev !== j.status && ["ready", "done", "error"].includes(j.status)) reload = true;
  }
  renderJobs(); renderOut();
  const cj = curJob();
  if (reload || (cj && cj.has_preview && !$("vid").getAttribute("src"))) await loadCur(S.segs.length === 0);
}

/* ---------- ویرایش بخش‌ها ---------- */
const mark = () => { S.dirty = true; wantPreview(); };
function suggest(text) {
  const t = text.replace(/ي/g, "ی").replace(/ك/g, "ک"), out = [];
  for (const [k, e] of Object.entries(EMO)) if (t.includes(k) && !out.includes(e)) out.push(e);
  return out.length ? out.slice(0, 6) : ["😀", "👍", "🔥", "❤️", "🎉"];
}
function insertAt(ta, s, text) {
  const p = ta.selectionStart ?? ta.value.length;
  ta.value = ta.value.slice(0, p) + text + ta.value.slice(ta.selectionEnd ?? p);
  ta.selectionStart = ta.selectionEnd = p + text.length; ta.focus(); s.text = ta.value; mark();
}
function renderSegs() {
  $("segs").replaceChildren(...S.segs.map((s, i) => {
    const num = (k) => el("input", { type: "number", step: 0.1, min: 0, value: s[k], title: k === "start" ? "شروع (ثانیه)" : "پایان (ثانیه)", oninput: (e) => { s[k] = parseFloat(e.target.value) || 0; mark(); } });
    const ta = el("textarea", { value: s.text, rows: 2, dir: "rtl", oninput: (e) => { s.text = e.target.value; mark(); }, onfocus: () => { S.lastTA = { ta, s }; } });
    const fx = el("select", { onchange: (e) => { s.effect = e.target.value; mark(); } }, ...EFFECTS.map(([v, t]) => el("option", { value: v, textContent: t, selected: s.effect === v })));
    const em = el("input", { type: "text", placeholder: "کلمهٔ تأکیدی", value: s.emphasis || "", oninput: (e) => { s.emphasis = e.target.value; mark(); } });
    const sg = el("div", { className: "sugg", hidden: true });
    const sBtn = el("button", { type: "button", textContent: "پیشنهاد ایموجی", onclick: () => { sg.hidden = !sg.hidden; if (!sg.hidden) sg.replaceChildren(...suggest(ta.value).map((e) => el("button", { type: "button", textContent: e, onclick: () => insertAt(ta, s, " " + e) }))); } });
    const play = el("button", { type: "button", textContent: "پخش", onclick: () => { const v = $("vid"); v.currentTime = s.start; v.play(); } });
    const add = el("button", { type: "button", textContent: "بخش بعدی", onclick: () => { S.segs.splice(i + 1, 0, { start: s.end, end: Math.min(s.end + 2, curJob().duration), text: "", effect: "none", emphasis: "" }); mark(); renderSegs(); } });
    const del = el("button", { type: "button", textContent: "حذف", onclick: () => { S.segs.splice(i, 1); mark(); renderSegs(); } });
    const li = el("li", {}, el("div", { className: "times" }, num("start"), num("end"), play), ta, el("div", { className: "meta" }, fx, em, sBtn, el("span", { className: "sp" }), add, del), sg);
    li.dataset.i = i; return li;
  }));
}
async function saveProject(silent) {
  if (!S.cur) return;
  try { await api(`/api/jobs/${S.cur}/segments`, jsend(body(), "PUT")); S.dirty = false; if (!silent) { $("uploadMsg").textContent = "پروژه ذخیره شد."; setTimeout(() => ($("uploadMsg").textContent = ""), 2500); } loadProjects(); }
  catch (e) { note(e.message); }
  await refresh();
}

/* ---------- خروجی و کاور ---------- */
function renderOut() {
  const j = curJob(), st = $("state"), bw = $("barWrap");
  $("render").disabled = !j || !["ready", "done", "error"].includes(j.status) || (j.status === "error" && !S.segs.length);
  st.className = "state"; $("links").replaceChildren(); $("warns").replaceChildren();
  if (!j) { st.textContent = "ویدیویی انتخاب نشده است."; bw.hidden = true; return; }
  const pct = j.progress == null ? "" : ` ${Math.round(j.progress)}٪`;
  st.textContent = (j.stage || STAGE_FA[j.status]) + pct;
  bw.hidden = !ACTIVE.includes(j.status); bw.className = "bar" + (j.progress == null ? " indet" : ""); $("bar").style.width = (j.progress || 0) + "%";
  if (j.status === "error") { st.className = "state error"; st.textContent = j.error; }
  $("warns").replaceChildren(...(j.warnings || []).map((w) => el("li", { textContent: w })));
  const names = { mp4: "ویدیوی زیرنویس‌دار (MP4)", srt: "زیرنویس SRT", ass: "زیرنویس ASS با استایل", thumb: "تامنیل (JPG)", project: "پروژه (JSON)", txt: "رونویسی (TXT)" };
  $("links").replaceChildren(...Object.keys(names).filter((k) => j.outputs && j.outputs[k]).map((k) => el("a", { href: `/api/jobs/${j.id}/download/${k}`, textContent: names[k], download: "" })));
  const ci = $("coverImg"); ci.hidden = $("coverClear").hidden = !j.has_cover;
  if (j.has_cover && !ci.dataset.for) { ci.src = `/api/jobs/${j.id}/cover?ts=${Date.now()}`; ci.dataset.for = j.id; }
  if (!j.has_cover) delete ci.dataset.for;
}
async function doRender() {
  const j = curJob(); if (!j) return;
  for (const s of S.segs) if (s.text.trim() && s.end <= s.start) return note("در یکی از بخش‌ها زمان پایان باید بعد از شروع باشد.");
  if (!S.exp.formats.length) return note("حداقل یک فرمت خروجی را انتخاب کنید.");
  try { await api(`/api/jobs/${j.id}/render`, jsend(body())); S.dirty = false; S.notes = []; renderNotes(); loadProjects(); } catch (e) { note(e.message); }
  refresh();
}
async function uploadCover(f) {
  if (!S.cur || !f) return;
  const fd = new FormData(); fd.append("file", f);
  try { await api(`/api/jobs/${S.cur}/cover`, { method: "POST", body: fd }); const ci = $("coverImg"); delete ci.dataset.for; if (!S.exp.cover_duration) { S.exp.cover_duration = 1.5; syncControls(); persist(); } } catch (e) { note(e.message); }
  refresh();
}

/* ---------- کنترل‌ها ---------- */
function syncControls() {
  document.querySelectorAll("[data-k]").forEach((c) => { const v = S.style[c.dataset.k]; if (c.type === "checkbox") c.checked = !!v; else c.value = v; });
  $("reframe").value = S.exp.reframe; $("coverDur").value = S.exp.cover_duration; $("coverFit").value = S.exp.cover_fit; $("embedArt").checked = S.exp.embed_artwork;
  document.querySelectorAll(".fmt").forEach((c) => { c.checked = S.exp.formats.includes(c.value); });
  const j = curJob(); let ar = j ? j.width / j.height : 16 / 9;
  if (S.exp.reframe !== "original") { const [a, b] = S.exp.reframe.split(":"); ar = a / b; }
  $("stage").style.aspectRatio = ar; $("stage").style.maxWidth = `calc(68vh * ${ar})`;
}
function bindControls() {
  document.querySelectorAll("[data-k]").forEach((c) => c.addEventListener("input", () => {
    const k = c.dataset.k;
    S.style[k] = c.type === "checkbox" ? c.checked : c.type === "range" || c.type === "number" ? parseFloat(c.value) || 0 : c.value;
    persist(); S.dirty = !!S.cur; wantPreview();
  }));
  const ex = (id, k, f = (c) => c.value) => $(id).addEventListener("input", () => { S.exp[k] = f($(id)); persist(); syncControls(); S.dirty = !!S.cur; wantPreview(); });
  ex("reframe", "reframe"); ex("coverDur", "cover_duration", (c) => parseFloat(c.value) || 0); ex("coverFit", "cover_fit"); ex("embedArt", "embed_artwork", (c) => c.checked);
  document.querySelectorAll(".fmt").forEach((c) => c.addEventListener("input", () => { S.exp.formats = [...document.querySelectorAll(".fmt")].filter((x) => x.checked).map((x) => x.value); persist(); }));
}

/* ---------- پیش‌نمایش: فریم دقیق (libass) هنگام توقف، نمایش تقریبی هنگام پخش ---------- */
let pvTimer = null, pvSeq = 0, pvUrl = null, lastKey = "";
function wantPreview() { clearTimeout(pvTimer); pvTimer = setTimeout(() => { fetchLayout(); fetchFrame(); }, 300); }
async function fetchFrame() {
  const j = curJob(), v = $("vid"), img = $("frameImg");
  if (!j || !j.has_preview || (!v.paused && !v.ended)) return;
  const seq = ++pvSeq;
  try {
    const r = await api(`/api/jobs/${j.id}/frame`, jsend(body({ t: v.currentTime || 0 }))), b = await r.blob();
    if (seq !== pvSeq) return;
    if (pvUrl) URL.revokeObjectURL(pvUrl);
    pvUrl = URL.createObjectURL(b); img.src = pvUrl; img.hidden = false; $("ov").hidden = true;
  } catch (e) { if (seq === pvSeq) $("pvNote").textContent = "پیش‌نمایش دقیق ساخته نشد: " + e.message; }
}
async function fetchLayout() {
  const j = curJob(); if (!j || !j.has_preview) return;
  try { S.layout = await (await api(`/api/jobs/${j.id}/layout`, jsend(body()))).json(); lastKey = ""; } catch (e) { /* فقط برای نمایش تقریبی هنگام پخش */ }
}
function tick() {
  const v = $("vid"), ov = $("ov"), L = S.layout;
  if (v.paused || !L) { requestAnimationFrame(tick); return; }
  $("frameImg").hidden = true;
  const t = v.currentTime, ev = L.events.find((e) => t >= e.start && t < e.end), st = S.style, stage = $("stage"), H = stage.clientHeight;
  if (!ev || !H) { ov.hidden = true; requestAnimationFrame(tick); return; }
  const size = L.rel_size * H, k = H / 1080, key = JSON.stringify([ev.start, ev.lines, st, H]);
  if (key !== lastKey) {
    lastKey = key; ov.hidden = false;
    const box = el("div", { className: "box" });
    box.style.cssText = `font:${st.bold ? 700 : 400} ${size}px/${st.line_spacing} "${st.font}",Tahoma,sans-serif;gap:${Math.max(0, st.line_spacing - 1.2) * size}px;` +
      (st.position === "middle" ? "top:50%;transform:translateY(-50%);" : st.position === "top" ? `top:${st.margin_pct}%;` : `bottom:${st.margin_pct}%;`);
    ev.lines.forEach((tx) => {
      const ln = el("span", { className: "ln", textContent: tx });
      const sh = st.shadow_w ? `${st.shadow_w * k}px ${st.shadow_w * k}px 0 ${hexA(st.shadow_color, st.shadow_opacity)}` : "none";
      ln.style.cssText = `color:${st.color};-webkit-text-stroke:${2 * st.outline_w * k}px ${st.outline_color};text-shadow:${sh};` +
        (st.bg_on ? `background:${hexA(st.bg_color, st.bg_opacity)};padding:${st.bg_pad_y * size}px ${st.bg_pad_x * size}px;border-radius:${st.bg_radius * size}px;` +
          (st.bg_border_w ? `border:${st.bg_border_w * k}px solid ${st.bg_border_color};` : "") : "");
      box.append(ln);
    });
    ov.replaceChildren(box);
  }
  const a = t - ev.start, b = ev.end - t, f = Math.min(0.3, (ev.end - ev.start) / 2);
  let op = 1, sc = 1;
  if (ev.effect === "fadein" || ev.effect === "fadeboth") op = Math.min(op, a / f);
  if (ev.effect === "fadeout" || ev.effect === "fadeboth") op = Math.min(op, b / f);
  if (ev.effect === "pop") sc = a < 0.18 ? 0.7 + 0.45 * a / 0.18 : a < 0.32 ? 1.15 - 0.15 * (a - 0.18) / 0.14 : 1;
  ov.style.opacity = Math.max(0, Math.min(1, op)); ov.style.transform = `scale(${sc})`;
  requestAnimationFrame(tick);
}
function hexA(h, a) { const n = parseInt(h.slice(1), 16); return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${a})`; }

/* ---------- نمایش کلی و راه‌اندازی ---------- */
function renderAll() {
  renderJobs(); renderSegs(); renderOut();
  $("stageEmpty").hidden = !!(curJob() && curJob().has_preview);
  $("addSeg").disabled = $("saveSegs").disabled = !S.cur;
}
async function openProject() {
  const video = $("openVideo").files[0], sub = $("openSub").files[0], saved = $("openSaved").value;
  if (!video) return note("ویدیوی مربوط به پروژه را انتخاب کنید.");
  if (!sub && !saved) return note("یک پروژهٔ ذخیره‌شده یا فایل زیرنویس انتخاب کنید.");
  if ($("openOver").checked && !confirm("فایل‌های قبلی پروژه با همین نام بازنویسی شوند؟")) return;
  try {
    await xhrPost("/api/open", (fd) => { fd.append("video", video); if (sub && !saved) fd.append("sub", sub); fd.append("saved", saved); fd.append("shift", $("openShift").value || "0"); fd.append("overwrite", $("openOver").checked); }, video.name);
    $("uploadMsg").textContent = "";
  } catch (e) { note(e.message); }
}
$("file").onchange = (e) => { addFiles(e.target.files); e.target.value = ""; };
const drop = $("drop");
["dragenter", "dragover"].forEach((n) => drop.addEventListener(n, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((n) => drop.addEventListener(n, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => addFiles(e.dataTransfer.files));
drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); $("file").click(); } });
$("fxApply").onclick = () => { S.segs.forEach((s) => (s.effect = $("fxAll").value)); mark(); renderSegs(); };
$("addSeg").onclick = () => {
  const v = $("vid"), t = Math.min(v.currentTime || 0, Math.max(0, (curJob()?.duration || 1) - 0.5));
  S.segs.push({ start: +t.toFixed(1), end: +Math.min(t + 2, curJob().duration).toFixed(1), text: "", effect: "none", emphasis: "" }); S.segs.sort((a, b) => a.start - b.start); mark(); renderSegs();
};
$("saveSegs").onclick = () => saveProject(false);
$("render").onclick = doRender;
$("openBtn").onclick = openProject;
$("openOut").onclick = async () => { try { await api("/api/open-output", { method: "POST" }); } catch (e) { note(e.message); } };
$("coverFile").onchange = (e) => { uploadCover(e.target.files[0]); e.target.value = ""; };
$("coverClear").onclick = async () => { if (!S.cur) return; await api(`/api/jobs/${S.cur}/cover`, { method: "DELETE" }); S.exp.cover_duration = 0; syncControls(); persist(); refresh(); };
$("emojiBar").replaceChildren(...QUICK.map((e) => el("button", { type: "button", textContent: e, title: "درج در متن بخش انتخاب‌شده", onmousedown: (ev) => ev.preventDefault(), onclick: () => { if (S.lastTA) insertAt(S.lastTA.ta, S.lastTA.s, e); } })));
const vid = $("vid");
["pause", "seeked", "loadeddata", "ended"].forEach((n) => vid.addEventListener(n, () => { wantPreview(); }));
vid.addEventListener("play", () => { $("frameImg").hidden = true; });
window.addEventListener("beforeunload", (e) => { if (S.dirty) { e.preventDefault(); e.returnValue = ""; } });
(async () => {
  try {
    const p = await jget("/api/presets"); S.presets = p.presets;
    $("presets").replaceChildren(...p.presets.map((pr) => el("button", { type: "button", textContent: pr.name, onclick: () => { Object.assign(S.style, pr.style); persist(); syncControls(); S.dirty = !!S.cur; wantPreview(); } })));
    const f = await jget("/api/fonts"); $("fontlist").replaceChildren(...[...f.builtin, ...f.custom].map((n) => el("option", { value: n })));
  } catch (e) { /* مقادیر پیش‌فرض محلی */ }
  bindControls(); syncControls(); renderAll(); loadStatus().catch(() => {}); loadProjects().catch(() => {}); refresh(); setInterval(refresh, 1200); requestAnimationFrame(tick);
})();
