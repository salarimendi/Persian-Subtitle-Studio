"""موتور برنامه: بررسی ویدیو، Whisper، ساخت SRT/ASS، FFmpeg و صف پردازش.
این فایل به FastAPI وابسته نیست تا جدا از وب هم قابل‌آزمایش باشد."""
from __future__ import annotations

import atexit
import json
import queue
import re
import secrets
import shutil
import subprocess
import sys
import threading
import traceback
from dataclasses import dataclass, field
from pathlib import Path

import asr

MAX_SECONDS = 300
IS_WIN = sys.platform.startswith("win")
EMOJI_FONT = "Segoe UI Emoji" if IS_WIN else "Noto Color Emoji"
FFMPEG_HELP = (
    "FFmpeg پیدا نشد. در PowerShell این دستور را اجرا کنید: winget install Gyan.FFmpeg "
    "سپس پنجرهٔ start.bat را ببندید و دوباره اجرا کنید. "
    "راه دیگر: از ffmpeg.org نسخهٔ ویندوز را بگیرید و پوشهٔ bin را به PATH اضافه کنید، "
    "یا ffmpeg.exe و ffprobe.exe را کنار main.py بگذارید."
)


class UserError(Exception):
    """خطایی که پیامش مستقیم به کاربر نشان داده می‌شود."""


# ---------------------------------------------------------------- ابزارها
def find_tool(name: str, app_dir: Path) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for p in (app_dir / f"{name}.exe", app_dir / "ffmpeg" / "bin" / f"{name}.exe"):
        if p.exists():
            return str(p)
    return None


_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def safe_stem(filename: str) -> str:
    stem = Path(filename.replace("\\", "/").split("/")[-1]).stem
    stem = re.sub(r"[^\w\-. ()]", "_", stem, flags=re.UNICODE).strip(" ._") or "video"
    stem = stem[:80].strip(" ._") or "video"
    return stem + "_" if stem.upper() in _RESERVED else stem


def safe_ext(filename: str) -> str:
    ext = Path(filename.replace("\\", "/").split("/")[-1]).suffix.lower()
    return ext if re.fullmatch(r"\.[a-z0-9]{1,5}", ext) else ".bin"


def read_range(path: Path, header: str | None) -> tuple[int, bytes, dict]:
    """پاسخ HTTP Range برای پیش‌نمایش (لازم برای جلو/عقب‌کردن ویدیو در مرورگر)."""
    size = path.stat().st_size
    m = re.fullmatch(r"bytes=(\d*)-(\d*)", (header or "").strip())
    if not m or not (m[1] or m[2]):
        return 200, path.read_bytes(), {"Accept-Ranges": "bytes"}
    if m[1]:
        a, b = int(m[1]), int(m[2]) if m[2] else size - 1
    else:
        a, b = max(0, size - int(m[2])), size - 1
    b = min(b, size - 1)
    if a > b:
        return 416, b"", {"Content-Range": f"bytes */{size}"}
    with open(path, "rb") as f:
        f.seek(a)
        data = f.read(b - a + 1)
    return 206, data, {"Content-Range": f"bytes {a}-{b}/{size}", "Accept-Ranges": "bytes"}


# ---------------------------------------------------------------- SRT
def srt_time(t: float) -> str:
    ms = max(0, int(round(t * 1000)))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def make_srt(segments: list[dict]) -> str:
    out, n = [], 0
    for s in sorted(segments, key=lambda x: x["start"]):
        text = s["text"].strip()
        if not text:
            continue
        n += 1
        out.append(f"{n}\n{srt_time(s['start'])} --> {srt_time(s['end'])}\n{text}\n")
    return "\n".join(out)


# ---------------------------------------------------------------- ASS
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\u2300-\u23FF\uFE0F\u200D]+")


def _hex_bgr(color: str) -> str:
    c = color.lstrip("#")
    return f"{c[4:6]}{c[2:4]}{c[0:2]}".upper()


def _esc(text: str, font: str) -> str:
    text = text.replace("\\", "/").replace("{", "\\{").replace("}", "\\}")
    text = EMOJI_RE.sub(lambda m: "{\\fn%s}%s{\\fn%s}" % (EMOJI_FONT, m.group(), font), text)
    return text.replace("\r", "").replace("\n", "\\N")


def ass_text(text: str, emphasis: str, st: dict, plain: bool = False) -> str:
    """plain=True برای لایهٔ کادر پس‌زمینه: بدون تأکید و بدون تعویض فونت ایموجی."""
    text = text.strip()
    if plain:
        return text.replace("\\", "/").replace("{", "\\{").replace("}", "\\}").replace("\r", "").replace("\n", "\\N")
    pre, mid, post = (text.partition(emphasis) if emphasis and emphasis in text else (text, "", ""))
    font = st["font"]
    body = _esc(pre, font)
    if mid:
        prim, bold = _hex_bgr(st["color"]), 1 if st["bold"] else 0
        body += "{\\1c&H%s&\\b1}%s{\\1c&H%s&\\b%d}" % (_hex_bgr(st["emphasis_color"]), _esc(mid, font), prim, bold)
        body += _esc(post, font)
    return body


def ass_time(t: float) -> str:
    cs = max(0, int(round(t * 100)))
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02}:{s:02}.{cs:02}"


def effect_tags(effect: str, dur: float) -> str:
    f = int(min(300, dur * 500))
    return {
        "fadein": f"\\fad({f},0)",
        "fadeout": f"\\fad(0,{f})",
        "fadeboth": f"\\fad({f},{f})",
        "pop": "\\fscx70\\fscy70\\t(0,180,\\fscx115\\fscy115)\\t(180,320,\\fscx100\\fscy100)",
    }.get(effect, "")


def build_ass(segments: list[dict], st: dict, w: int, h: int, effects_on: bool) -> str:
    size = round(h * st["size_pct"] / 100)
    outline = round(st["outline_w"] * h / 1080, 1)
    align = {"bottom": 2, "middle": 5, "top": 8}[st["position"]]
    mv, mh = round(h * st["margin_pct"] / 100), round(w * 0.05)
    bold = -1 if st["bold"] else 0
    box_alpha = "%02X" % round((1 - st["bg_opacity"]) * 255)
    fmt = ("Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,"
           "StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding")
    head = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {w}", f"PlayResY: {h}", "WrapStyle: 0",
        "ScaledBorderAndShadow: yes", "", "[V4+ Styles]", f"Format: {fmt}",
        f"Style: Main,{st['font']},{size},&H00{_hex_bgr(st['color'])},&H00{_hex_bgr(st['color'])},"
        f"&H00{_hex_bgr(st['outline_color'])},&H00000000,{bold},0,0,0,100,100,0,0,1,{outline},0,{align},{mh},{mh},{mv},-1",
        # لایهٔ جعبهٔ پس‌زمینه: متن نامرئی، فقط کادر (رنگ کادر از OutlineColour) دیده می‌شود
        f"Style: Box,{st['font']},{size},&HFF{_hex_bgr(st['color'])},&HFF{_hex_bgr(st['color'])},"
        f"&H{box_alpha}{_hex_bgr(st['bg_color'])},&H00000000,{bold},0,0,0,100,100,0,0,3,{round(size * 0.18)},0,{align},{mh},{mh},{mv},-1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    lines = []
    for s in sorted(segments, key=lambda x: x["start"]):
        if not s["text"].strip() or s["end"] <= s["start"]:
            continue
        tags = effect_tags(s.get("effect", "none"), s["end"] - s["start"]) if effects_on else ""
        emph = s.get("emphasis", "") if effects_on else ""
        pre = "{" + tags + "}" if tags else ""
        body = pre + ass_text(s["text"], emph, st)
        a, b = ass_time(s["start"]), ass_time(s["end"])
        if st["bg_on"]:
            lines.append(f"Dialogue: 0,{a},{b},Box,,0,0,0,,{pre}{ass_text(s['text'], '', st, plain=True)}")
        lines.append(f"Dialogue: 1,{a},{b},Main,,0,0,0,,{body}")
    return "\n".join(head + lines) + "\n"


# ---------------------------------------------------------------- Job
@dataclass
class Job:
    id: str
    name: str
    ext: str
    language: str
    dir: Path
    duration: float = 0
    width: int = 0
    height: int = 0
    status: str = "queued"  # queued|transcribing|ready|render_queued|rendering|done|error
    stage: str = "در صف"
    progress: float | None = None
    error: str = ""
    warnings: list = field(default_factory=list)
    segments: list = field(default_factory=list)
    has_preview: bool = False
    base: str = ""
    outputs: dict = field(default_factory=dict)
    render_req: dict | None = None

    def public(self, with_segments=False) -> dict:
        d = {k: getattr(self, k) for k in ("id", "name", "duration", "width", "height", "status", "stage", "progress",
                                           "error", "warnings", "has_preview", "outputs")}
        if with_segments:
            d["segments"] = self.segments
        return d


class Manager:
    def __init__(self, app_dir: Path):
        self.app = app_dir
        self.work, self.output = app_dir / "work", app_dir / "output"
        self.models, self.fonts = app_dir / "models", app_dir / "fonts"
        self.jobs: dict[str, Job] = {}
        self.lock = threading.RLock()
        self.q: queue.Queue = queue.Queue()
        self.ffmpeg = find_tool("ffmpeg", app_dir)
        self.ffprobe = find_tool("ffprobe", app_dir)
        self._model = None
        self._started = False

    # --- چرخهٔ عمر
    def start(self):
        if self._started:
            return
        self._started = True
        shutil.rmtree(self.work, ignore_errors=True)  # بقایای اجرای قبلی
        self.work.mkdir(exist_ok=True)
        self.output.mkdir(exist_ok=True)
        atexit.register(lambda: shutil.rmtree(self.work, ignore_errors=True))
        threading.Thread(target=self._worker, daemon=True, name="worker").start()

    def tools_ok(self) -> bool:
        return bool(self.ffmpeg and self.ffprobe)

    def status(self) -> dict:
        import importlib.util
        ver = ""
        if self.ffmpeg:
            try:
                ver = subprocess.run([self.ffmpeg, "-version"], capture_output=True, text=True, timeout=10).stdout.splitlines()[0]
            except Exception:
                ver = "نامشخص"
        return {
            "ffmpeg": self.tools_ok(), "ffmpeg_version": ver, "ffmpeg_help": "" if self.tools_ok() else FFMPEG_HELP,
            "whisper_installed": importlib.util.find_spec("faster_whisper") is not None,
            "model": asr.MODEL_REPO, "model_cached": asr.is_complete(asr.model_dir(self.models)),
            "max_seconds": MAX_SECONDS, "output_dir": str(self.output),
        }

    # --- بررسی ویدیو
    def probe(self, path: Path) -> dict:
        if not self.tools_ok():
            raise UserError(FFMPEG_HELP)
        r = subprocess.run([self.ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        if r.returncode != 0:
            raise UserError("این فایل ویدیوی معتبر نیست یا FFmpeg نمی‌تواند آن را بخواند.")
        info = json.loads(r.stdout or "{}")
        streams = info.get("streams", [])
        v = next((s for s in streams if s.get("codec_type") == "video" and s.get("width")), None)
        if not v:
            raise UserError("در این فایل تصویر ویدیو پیدا نشد.")
        if not any(s.get("codec_type") == "audio" for s in streams):
            raise UserError("این ویدیو صدا ندارد، پس چیزی برای تبدیل به زیرنویس نیست.")
        try:
            dur = float(info.get("format", {}).get("duration") or v.get("duration") or 0)
        except ValueError:
            dur = 0
        if dur <= 0:
            raise UserError("مدت ویدیو قابل تشخیص نیست.")
        if dur > MAX_SECONDS + 0.5:
            raise UserError(f"مدت ویدیو {dur / 60:.1f} دقیقه است؛ حداکثر مجاز {MAX_SECONDS // 60} دقیقه است.")
        w, h = int(v["width"]), int(v["height"])
        rot = v.get("tags", {}).get("rotate")
        for sd in v.get("side_data_list", []):
            rot = sd.get("rotation", rot)
        try:
            if rot is not None and abs(int(float(rot))) % 180 == 90:
                w, h = h, w
        except ValueError:
            pass
        return {"duration": dur, "width": w // 2 * 2, "height": h // 2 * 2}

    # --- مدیریت کارها
    def new_dir(self) -> tuple[str, Path]:
        jid = secrets.token_hex(4)
        d = self.work / jid
        d.mkdir(parents=True)
        return jid, d

    def register(self, jid: str, display_name: str, ext: str, language: str) -> Job:
        d = self.work / jid
        info = self.probe(d / f"input{ext}")
        job = Job(jid, display_name, ext, "fa" if language != "auto" else "auto", d, **info)
        with self.lock:
            self.jobs[jid] = job
        self.q.put(("transcribe", jid))
        return job

    def get(self, jid: str) -> Job:
        job = self.jobs.get(jid)
        if not job:
            raise UserError("این مورد پیدا نشد.")
        return job

    def list(self) -> list[dict]:
        with self.lock:
            return [j.public() for j in self.jobs.values()]

    def delete(self, jid: str):
        job = self.get(jid)
        if job.status in ("transcribing", "rendering"):
            raise UserError("این ویدیو در حال پردازش است؛ پس از پایان آن را حذف کنید.")
        with self.lock:
            self.jobs.pop(jid, None)
        shutil.rmtree(job.dir, ignore_errors=True)

    def retry(self, jid: str):
        job = self.get(jid)
        if job.status != "error" or job.segments:
            raise UserError("فقط ویدیویی که تبدیل گفتارش خطا داده قابل تلاش مجدد است.")
        job.status, job.stage, job.error, job.progress = "queued", "در صف", "", None
        self.q.put(("transcribe", jid))

    def save_segments(self, jid: str, segments: list[dict]):
        job = self.get(jid)
        job.segments = self._clean(segments, job.duration)
        if job.base:
            (self.output / f"{job.base}.srt").write_text(make_srt(job.segments), encoding="utf-8-sig")
            job.outputs["srt"] = f"{job.base}.srt"

    def render(self, jid: str, style: dict, segments: list[dict], effects_on: bool):
        job = self.get(jid)
        if job.status not in ("ready", "done", "error") or (job.status == "error" and not job.segments):
            raise UserError("این ویدیو هنوز آمادهٔ ساخت خروجی نیست.")
        job.segments = self._clean(segments, job.duration)
        if not any(s["text"].strip() for s in job.segments):
            raise UserError("هیچ متن زیرنویسی برای ساخت خروجی وجود ندارد.")
        job.render_req = {"style": style, "effects_on": effects_on}
        job.status, job.stage, job.progress, job.error = "render_queued", "در صف ساخت خروجی", None, ""
        self.q.put(("render", jid))

    @staticmethod
    def _clean(segments, duration):
        out = []
        for s in segments:
            a = min(max(0.0, float(s["start"])), duration)
            b = min(max(a, float(s["end"])), duration)
            out.append({"start": round(a, 2), "end": round(b, 2), "text": str(s["text"]),
                        "effect": s.get("effect", "none"), "emphasis": s.get("emphasis", "")})
        return sorted(out, key=lambda x: x["start"])

    def _alloc_base(self, job: Job) -> str:
        if job.base:
            return job.base
        stem = safe_stem(job.name)
        with self.lock:
            taken = {j.base for j in self.jobs.values() if j.base}
            n = 1
            while True:
                base = stem if n == 1 else f"{stem}_{n}"
                exists = any((self.output / f).exists() for f in (f"{base}.srt", f"{base}.ass", f"{base}_sub.mp4"))
                if base not in taken and not exists:
                    job.base = base
                    return base
                n += 1

    # --- کارگر (فقط یک پردازش سنگین در هر لحظه)
    def _worker(self):
        while True:
            kind, jid = self.q.get()
            job = self.jobs.get(jid)
            if not job:
                continue
            try:
                self._transcribe(job) if kind == "transcribe" else self._render(job)
            except UserError as e:
                job.status, job.error, job.progress = "error", str(e), None
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                job.status, job.error, job.progress = "error", f"خطای غیرمنتظره: {e}", None

    def run_ffmpeg(self, args: list[str], cwd: Path, total: float, on_progress):
        cmd = [self.ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-nostats", "-progress", "pipe:1", *args]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        log = cwd / "ffmpeg.log"
        with open(log, "wb") as lf:
            p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=lf, text=True, encoding="utf-8",
                                 errors="replace", creationflags=flags)
            for line in p.stdout:
                k, _, v = line.strip().partition("=")
                if k in ("out_time_us", "out_time_ms") and v.isdigit() and total > 0:
                    on_progress(min(100.0, int(v) / 1e6 / total * 100))
            p.stdout.close()
            rc = p.wait()
        if rc != 0:
            tail = log.read_text(encoding="utf-8", errors="replace").strip()[-600:]
            raise UserError(f"FFmpeg با خطا متوقف شد: {tail or 'کد ' + str(rc)}")
        log.unlink(missing_ok=True)

    def _get_model(self, job: Job):
        if not self._model:
            job.progress = None
            def status(msg):
                job.stage = msg
            try:
                self._model = asr.load_model(self.models, on_status=status)
            except asr.ASRError as e:
                raise UserError(str(e))
        return self._model

    def _transcribe(self, job: Job):
        job.status = "transcribing"
        if not job.has_preview:
            job.stage, job.progress = "ساخت نسخهٔ سبک برای پیش‌نمایش", 0
            self.run_ffmpeg(["-i", f"input{job.ext}", "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn",
                             "-vf", "scale=-2:480", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
                             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", "preview.mp4"],
                            job.dir, job.duration, lambda p: setattr(job, "progress", p))
            job.has_preview = True
        model = self._get_model(job)
        job.stage, job.progress = "تبدیل گفتار به متن", 0
        try:
            segs = asr.transcribe(model, str(job.dir / f"input{job.ext}"), job.duration, job.language,
                                  lambda p: setattr(job, "progress", p))
        except asr.ASRError as e:
            raise UserError(str(e))
        segs = [dict(s, effect="none", emphasis="") for s in segs]
        if not segs:
            job.warnings.append("گفتاری تشخیص داده نشد. می‌توانید بخش‌ها را دستی اضافه کنید.")
        loops = sum(asr.has_loop(s["text"]) for s in segs)
        if loops:
            job.warnings.append(f"{loops} بخش تکرار غیرعادی یک کلمه دارد (نشانهٔ خطای مدل)؛ متن را بررسی کنید.")
        job.segments = self._clean(segs, job.duration)
        self._alloc_base(job)
        (self.output / f"{job.base}.srt").write_text(make_srt(job.segments), encoding="utf-8-sig")
        job.outputs["srt"] = f"{job.base}.srt"
        job.status, job.stage, job.progress = "ready", "آمادهٔ ویرایش", None

    def _render(self, job: Job):
        job.status, job.stage, job.progress, job.warnings = "rendering", "ساخت فایل زیرنویس", 0, []
        st = job.render_req["style"]
        base = self._alloc_base(job)
        if any(EMOJI_RE.search(s["text"]) for s in job.segments):
            if not self._emoji_font_ok():
                job.warnings.append(f"فونت ایموجی «{EMOJI_FONT}» روی این سیستم پیدا نشد؛ ایموجی‌ها ممکن است به‌صورت مربع نمایش داده شوند.")
            else:
                job.warnings.append("ایموجی‌ها با فونت سیستم رندر می‌شوند و ممکن است تک‌رنگ دیده شوند (محدودیت libass).")
        if st["font"].lower().startswith("vazir") and not any(self.fonts.glob("*azir*")):
            job.warnings.append("فونت Vazirmatn در پوشهٔ fonts پیدا نشد؛ فونت جایگزین استفاده می‌شود.")
        ass = build_ass(job.segments, st, job.width, job.height, job.render_req["effects_on"])
        (job.dir / "subs.ass").write_text(ass, encoding="utf-8-sig")
        (self.output / f"{base}.srt").write_text(make_srt(job.segments), encoding="utf-8-sig")
        (self.output / f"{base}.ass").write_text(ass, encoding="utf-8-sig")
        vf = "scale=trunc(iw/2)*2:trunc(ih/2)*2,ass=subs.ass"
        fonts_copy = job.dir / "fonts"
        if self.fonts.exists() and any(self.fonts.iterdir()):
            shutil.copytree(self.fonts, fonts_copy, dirs_exist_ok=True)
            vf += ":fontsdir=fonts"
        job.stage = "چسباندن زیرنویس به تصویر"
        try:
            self.run_ffmpeg(["-i", f"input{job.ext}", "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn", "-vf", vf,
                             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                             "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "out.mp4"],
                            job.dir, job.duration, lambda p: setattr(job, "progress", p))
            final = self.output / f"{base}_sub.mp4"
            shutil.move(str(job.dir / "out.mp4"), str(final))
        finally:  # پاک‌سازی فایل‌های موقت رندر
            for t in ("subs.ass", "out.mp4"):
                (job.dir / t).unlink(missing_ok=True)
            shutil.rmtree(fonts_copy, ignore_errors=True)
        job.outputs = {"mp4": final.name, "srt": f"{base}.srt", "ass": f"{base}.ass"}
        job.status, job.stage, job.progress = "done", "تمام شد", None

    @staticmethod
    def _emoji_font_ok() -> bool:
        if IS_WIN:
            return (Path(__import__("os").environ.get("WINDIR", "C:/Windows")) / "Fonts" / "seguiemj.ttf").exists()
        try:
            return EMOJI_FONT in subprocess.run(["fc-list"], capture_output=True, text=True).stdout
        except Exception:  # noqa: BLE001
            return False
