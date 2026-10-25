"use strict";
const $ = (id) => document.getElementById(id);
const DEF = { font: "Tahoma", size_pct: 5.5, color: "#ffffff", outline_color: "#000000", outline_w: 3, bg_on: false,
  bg_color: "#000000", bg_opacity: 0.6, position: "bottom", margin_pct: 6, bold: true, emphasis_color: "#ffd400" };
const QUICK = ["😀", "😂", "😍", "👍", "🔥", "❤️", "🎉", "🙏", "😮", "⭐", "✅", "⚠️"];
const EMO = { خنده: "😂", خندید: "😂", شاد: "😄", عشق: "❤️", دوست: "❤️", قلب: "❤️", غم: "😢", گریه: "😭", ناراحت: "😔",
  آتش: "🔥", داغ: "🔥", عالی: "👌", خوب: "👍", ممنون: "🙏", تشکر: "🙏", سلام: "👋", خداحافظ: "👋", پول: "💰", پیتزا: "🍕",
  غذا: "🍽️", چای: "🍵", قهوه: "☕", ماشین: "🚗", خانه: "🏠", مدرسه: "🏫", کتاب: "📚", موسیقی: "🎵", آهنگ: "🎶",
  تولد: "🎂", جشن: "🎉", سفر: "✈️", دریا: "🌊", خورشید: "☀️", باران: "🌧️", برف: "❄️", ستاره: "⭐", گربه: "🐱",
  سگ: "🐶", تلفن: "📞", ساعت: "⏰", مهم: "❗", سؤال: "❓", چرا: "❓", وای: "😮", ترس: "😨", خواب: "😴", کار: "💼",
  فوتبال: "⚽", ورزش: "⚽", دوربین: "📷", عکس: "📷", ویدیو: "🎬" };
const EFFECTS = [["none", "بدون افکت"], ["fadein", "ظاهر شدن تدریجی"], ["fadeout", "محو شدن"], ["fadeboth", "ظاهر و محو"], ["pop", "بزرگ‌شدن کوتاه"]];
const ACTIVE = ["queued", "transcribing", "render_queued", "rendering"];
const S = { jobs: [], cur: null, segs: [], dirty: false, lastStatus: {}, style: { ...DEF }, fxOn: true, uploading: 0, notes: [], lastTA: null };
try { Object.assign(S.style, JSON.parse(localStorage.getItem("style") || "{}")); } catch (e) { /* مرورگر ذخیره‌سازی ندارد */ }

async function api(path, opt = {}) {
  const r = await fetch(path, opt);
  if (!r.ok) {
    let msg = "خطای ناشناخته (" + r.status + ")";
    try { const j = await r.json(); msg = typeof j.detail === "string" ? j.detail : "ورودی نامعتبر است."; } catch (e) { /* بدنهٔ غیر JSON */ }
    throw new Error(msg);
  }
  return r.json();
}
const jbody = (o, method = "POST") => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(o) });
const curJob = () => S.jobs.find((j) => j.id === S.cur);
const el = (tag, props = {}, ...kids) => { const e = Object.assign(document.createElement(tag), props); kids.forEach((k) => e.append(k)); return e; };

/* ---------- وضعیت موتور ---------- */
async function loadStatus() {
  const s = await api("/api/status");
  const b = $("banner");
  const msgs = [];
  if (!s.ffmpeg) msgs.push(s.ffmpeg_help);
  if (!s.whisper_installed) msgs.push("کتابخانهٔ faster-whisper نصب نیست. start.bat را دوباره اجرا کنید.");
  if (s.whisper_installed && !s.model_cached) msgs.push("اولین تبدیل گفتار، مدل فارسی (حدود ۳ گیگابایت) را یک‌بار در پوشهٔ models دانلود می‌کند و حدود ۳ گیگابایت RAM مصرف می‌کند؛ بعد از آن همه‌چیز محلی است.");
  b.hidden = !msgs.length; b.textContent = msgs.join(" ");
  b.className = "banner" + (!s.ffmpeg || !s.whisper_installed ? " err" : "");
  $("engine").textContent = s.ffmpeg ? s.ffmpeg_version.split(" ").slice(0, 3).join(" ") : "FFmpeg یافت نشد";
}

/* ---------- بارگذاری و صف ---------- */
function addFiles(files) {
  [...files].forEach((f) => uploadQ.push(f));
  pumpUploads();
}
const uploadQ = []; let uploading = false;
async function pumpUploads() {
  if (uploading) return; uploading = true;
  while (uploadQ.length) {
    const f = uploadQ.shift();
    try { await uploadOne(f); } catch (e) { S.notes.push(f.name + ": " + e.message); renderNotes(); }
  }
  $("uploadMsg").textContent = ""; uploading = false;
}
function uploadOne(f) {
  return new Promise((res, rej) => {
    const fd = new FormData(); fd.append("file", f); fd.append("language", $("lang").value);
    const x = new XMLHttpRequest(); x.open("POST", "/api/jobs");
    x.upload.onprogress = (e) => { if (e.lengthComputable) $("uploadMsg").textContent = `بارگذاری ${f.name}: ${Math.round(e.loaded / e.total * 100)}٪`; };
    x.onload = async () => {
      let j = {}; try { j = JSON.parse(x.responseText); } catch (e) { /* ignore */ }
      if (x.status >= 200 && x.status < 300) { await refresh(); if (!S.cur) selectJob(j.id); res(); }
      else rej(new Error(typeof j.detail === "string" ? j.detail : "بارگذاری ناموفق بود."));
    };
    x.onerror = () => rej(new Error("ارتباط با برنامه قطع شد."));
    x.send(fd);
  });
}
function renderNotes() {
  const ul = $("notes"); ul.replaceChildren(...S.notes.map((n, i) => el("li", { title: "برای بستن بزنید", onclick: () => { S.notes.splice(i, 1); renderNotes(); } }, n)));
}
const STAGE_FA = { queued: "در صف", transcribing: "", ready: "آمادهٔ ویرایش", render_queued: "در صف ساخت خروجی", rendering: "", done: "خروجی ساخته شد", error: "خطا" };
function renderJobs() {
  $("jobs").replaceChildren(...S.jobs.map((j) => {
    const pct = j.progress == null ? "" : ` ${Math.round(j.progress)}٪`;
    const st = el("div", { className: "st" + (j.status === "error" ? " error" : "") }, (j.stage || STAGE_FA[j.status]) + pct);
    const top = el("div", { className: "top" }, el("span", { className: "nm", textContent: j.name }),
      el("span", { className: "muted", textContent: `${Math.round(j.duration)} ثانیه` }));
    if (j.status === "error") top.append(el("button", { textContent: "تلاش دوباره", onclick: async (e) => { e.stopPropagation(); try { await api(`/api/jobs/${j.id}/retry`, { method: "POST" }); } catch (er) { S.notes.push(er.message); renderNotes(); } refresh(); } }));
    top.append(el("button", { textContent: "حذف", disabled: j.status === "transcribing" || j.status === "rendering", onclick: async (e) => { e.stopPropagation(); await delJob(j.id); } }));
    const li = el("li", { className: j.id === S.cur ? "sel" : "", onclick: () => selectJob(j.id) }, top, st);
    if (ACTIVE.includes(j.status)) { const b = el("div", { className: "bar" + (j.progress == null ? " indet" : "") }, el("i")); b.firstChild.style.width = (j.progress || 0) + "%"; li.append(b); }
    return li;
  }));
}
async function delJob(id) {
  try { await api(`/api/jobs/${id}`, { method: "DELETE" }); } catch (e) { S.notes.push(e.message); renderNotes(); return; }
  if (S.cur === id) { S.cur = null; S.segs = []; S.dirty = false; $("vid").removeAttribute("src"); $("vid").load(); renderAll(); }
  refresh();
}

/* ---------- انتخاب ویدیو و بارگذاری بخش‌ها ---------- */
async function selectJob(id) {
  if (S.cur === id) return;
  if (S.dirty && S.cur) await saveSegs(true);
  S.cur = id; S.segs = []; S.dirty = false;
  await loadCur(true);
}
async function loadCur(force) {
  const j = S.cur && (await api(`/api/jobs/${S.cur}`).catch(() => null));
  if (!j) { S.cur = null; renderAll(); return; }
  if (force || !S.dirty) S.segs = j.segments || [];
  const v = $("vid"), url = `/api/jobs/${j.id}/preview`;
  if (j.has_preview && !v.src.endsWith(url)) v.src = url;
  renderAll();
}
async function refresh() {
  try { S.jobs = await api("/api/jobs"); } catch (e) { return; }
  let reload = false;
  for (const j of S.jobs) {
    const prev = S.lastStatus[j.id]; S.lastStatus[j.id] = j.status;
    if (j.id === S.cur && prev && prev !== j.status && ["ready", "done", "error"].includes(j.status)) reload = true;
  }
  renderJobs(); renderOut();
  if (reload || (S.cur && curJob() && curJob().has_preview && !$("vid").getAttribute("src"))) await loadCur(S.segs.length === 0);
}

/* ---------- ویرایش بخش‌ها ---------- */
const mark = () => { S.dirty = true; };
function suggest(text) {
  const t = text.replace(/ي/g, "ی").replace(/ك/g, "ک"), out = [];
  for (const [k, e] of Object.entries(EMO)) if (t.includes(k) && !out.includes(e)) out.push(e);
  return out.length ? out.slice(0, 6) : ["😀", "👍", "🔥", "❤️", "🎉"];
}
function insertAt(ta, s, text) {
  const p = ta.selectionStart ?? ta.value.length;
  ta.value = ta.value.slice(0, p) + text + ta.value.slice(ta.selectionEnd ?? p);
  ta.selectionStart = ta.selectionEnd = p + text.length; ta.focus();
  s.text = ta.value; mark();
}
function renderSegs() {
  const ol = $("segs");
  ol.replaceChildren(...S.segs.map((s, i) => {
    const num = (k) => el("input", { type: "number", step: 0.1, min: 0, value: s[k], title: k === "start" ? "شروع (ثانیه)" : "پایان (ثانیه)",
      oninput: (e) => { s[k] = parseFloat(e.target.value) || 0; mark(); } });
    const ta = el("textarea", { value: s.text, rows: 2, dir: "rtl", oninput: (e) => { s.text = e.target.value; mark(); }, onfocus: () => { S.lastTA = { ta, s }; } });
    const fx = el("select", { onchange: (e) => { s.effect = e.target.value; mark(); } }, ...EFFECTS.map(([v, t]) => el("option", { value: v, textContent: t, selected: s.effect === v })));
    const em = el("input", { type: "text", placeholder: "کلمهٔ تأکیدی", value: s.emphasis || "", oninput: (e) => { s.emphasis = e.target.value; mark(); } });
    const sg = el("div", { className: "sugg", hidden: true });
    const sBtn = el("button", { type: "button", textContent: "پیشنهاد ایموجی", onclick: () => {
      sg.hidden = !sg.hidden; if (sg.hidden) return;
      sg.replaceChildren(...suggest(ta.value).map((e) => el("button", { type: "button", textContent: e, onclick: () => insertAt(ta, s, " " + e) })));
    } });
    const play = el("button", { type: "button", textContent: "پخش", onclick: () => { const v = $("vid"); v.currentTime = s.start; v.play(); } });
    const add = el("button", { type: "button", textContent: "بخش بعدی", onclick: () => { const n = { start: s.end, end: Math.min(s.end + 2, curJob().duration), text: "", effect: "none", emphasis: "" }; S.segs.splice(i + 1, 0, n); mark(); renderSegs(); } });
    const del = el("button", { type: "button", textContent: "حذف", onclick: () => { S.segs.splice(i, 1); mark(); renderSegs(); } });
    const li = el("li", {}, el("div", { className: "times" }, num("start"), num("end"), play), ta,
      el("div", { className: "meta" }, fx, em, sBtn, el("span", { className: "sp" }), add, del), sg);
    li.dataset.i = i;
    return li;
  }));
}
async function saveSegs(silent) {
  if (!S.cur) return;
  try { await api(`/api/jobs/${S.cur}/segments`, jbody({ segments: S.segs }, "PUT")); S.dirty = false; if (!silent) { S.notes = []; renderNotes(); $("uploadMsg").textContent = "ذخیره شد."; setTimeout(() => ($("uploadMsg").textContent = ""), 2000); } }
  catch (e) { S.notes.push(e.message); renderNotes(); }
  await refresh();
}

/* ---------- خروجی ---------- */
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
  const names = { mp4: "ویدیوی زیرنویس‌دار (MP4)", srt: "زیرنویس ساده (SRT)", ass: "زیرنویس با افکت (ASS)" };
  $("links").replaceChildren(...Object.keys(names).filter((k) => j.outputs && j.outputs[k]).map((k) => el("a", { href: `/api/jobs/${j.id}/download/${k}`, textContent: names[k], download: "" })));
}
async function doRender() {
  const j = curJob(); if (!j) return;
  for (const s of S.segs) if (s.text.trim() && s.end <= s.start) { S.notes.push("در یکی از بخش‌ها زمان پایان باید بعد از شروع باشد."); renderNotes(); return; }
  try { await api(`/api/jobs/${j.id}/render`, jbody({ style: S.style, segments: S.segs, effects_on: S.fxOn })); S.dirty = false; S.notes = []; renderNotes(); }
  catch (e) { S.notes.push(e.message); renderNotes(); }
  refresh();
}

/* ---------- ظاهر و پیش‌نمایش ---------- */
function bindStyle() {
  document.querySelectorAll("[data-k]").forEach((c) => {
    const k = c.dataset.k;
    if (c.type === "checkbox") c.checked = !!S.style[k]; else c.value = S.style[k];
    c.addEventListener("input", () => {
      S.style[k] = c.type === "checkbox" ? c.checked : c.type === "range" ? parseFloat(c.value) : c.value;
      try { localStorage.setItem("style", JSON.stringify(S.style)); } catch (e) { /* ignore */ }
    });
  });
}
let lastKey = "";
function tick() {
  const v = $("vid"), ov = $("ov"), t = v.currentTime || 0;
  const idx = S.segs.findIndex((s) => s.text.trim() && t >= s.start && t < s.end);
  document.querySelectorAll("#segs li").forEach((li) => li.classList.toggle("now", +li.dataset.i === idx));
  if (idx < 0 || !v.clientHeight) { ov.hidden = true; requestAnimationFrame(tick); return; }
  const s = S.segs[idx], st = S.style, H = v.clientHeight, k = H / 1080, span = $("ovText");
  const key = [idx, s.text, s.emphasis, S.fxOn, JSON.stringify(st), H].join("|");
  if (key !== lastKey) {
    lastKey = key; ov.hidden = false;
    ov.style.cssText = `font-size:${H * st.size_pct / 100}px;line-height:1.35;font-family:"${st.font}",Tahoma,"Segoe UI",sans-serif;` +
      (st.position === "middle" ? "top:50%;" : st.position === "top" ? `top:${st.margin_pct}%;` : `bottom:${st.margin_pct}%;`);
    span.style.cssText = `color:${st.color};font-weight:${st.bold ? 700 : 400};-webkit-text-stroke:${2 * st.outline_w * k}px ${st.outline_color};` +
      (st.bg_on ? `background:${hexA(st.bg_color, st.bg_opacity)};padding:.18em;` : "");
    span.replaceChildren();
    const em = S.fxOn && s.emphasis && s.text.includes(s.emphasis) ? s.text.indexOf(s.emphasis) : -1;
    if (em < 0) span.append(s.text);
    else { span.append(s.text.slice(0, em)); span.append(el("b", { textContent: s.emphasis, style: `color:${st.emphasis_color};-webkit-text-stroke:inherit` })); span.append(s.text.slice(em + s.emphasis.length)); }
  }
  let op = 1, sc = 1; const dur = s.end - s.start, f = Math.min(0.3, dur / 2), a = t - s.start, b = s.end - t;
  if (S.fxOn) {
    if (s.effect === "fadein" || s.effect === "fadeboth") op = Math.min(op, a / f);
    if (s.effect === "fadeout" || s.effect === "fadeboth") op = Math.min(op, b / f);
    if (s.effect === "pop") sc = a < 0.18 ? 0.7 + 0.45 * a / 0.18 : a < 0.32 ? 1.15 - 0.15 * (a - 0.18) / 0.14 : 1;
  }
  ov.style.opacity = Math.max(0, Math.min(1, op));
  ov.style.transform = (st.position === "middle" ? "translateY(-50%) " : "") + `scale(${sc})`;
  requestAnimationFrame(tick);
}
function hexA(h, a) { const n = parseInt(h.slice(1), 16); return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${a})`; }

/* ---------- نمایش کلی ---------- */
function renderAll() {
  renderJobs(); renderSegs(); renderOut();
  $("stageEmpty").hidden = !!(curJob() && curJob().has_preview);
  $("addSeg").disabled = $("saveSegs").disabled = !S.cur;
}

/* ---------- راه‌اندازی ---------- */
$("file").onchange = (e) => { addFiles(e.target.files); e.target.value = ""; };
const drop = $("drop");
["dragenter", "dragover"].forEach((n) => drop.addEventListener(n, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((n) => drop.addEventListener(n, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => addFiles(e.dataTransfer.files));
drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); $("file").click(); } });
$("fxOn").onchange = (e) => { S.fxOn = e.target.checked; };
$("fxApply").onclick = () => { S.segs.forEach((s) => (s.effect = $("fxAll").value)); mark(); renderSegs(); };
$("addSeg").onclick = () => {
  const v = $("vid"), t = Math.min(v.currentTime || 0, Math.max(0, (curJob()?.duration || 1) - 0.5));
  S.segs.push({ start: +t.toFixed(1), end: +Math.min(t + 2, curJob().duration).toFixed(1), text: "", effect: "none", emphasis: "" }); S.segs.sort((a, b) => a.start - b.start); mark(); renderSegs();
};
$("saveSegs").onclick = () => saveSegs(false);
$("render").onclick = doRender;
$("openOut").onclick = async () => { try { await api("/api/open-output", { method: "POST" }); } catch (e) { S.notes.push(e.message); renderNotes(); } };
$("emojiBar").replaceChildren(...QUICK.map((e) => el("button", { type: "button", textContent: e, title: "درج در متن بخش انتخاب‌شده",
  onmousedown: (ev) => ev.preventDefault(), onclick: () => { if (S.lastTA) insertAt(S.lastTA.ta, S.lastTA.s, e); } })));
window.addEventListener("beforeunload", (e) => { if (S.dirty) { e.preventDefault(); e.returnValue = ""; } });
bindStyle(); renderAll(); loadStatus().catch(() => {}); refresh(); setInterval(refresh, 1200); requestAnimationFrame(tick);
