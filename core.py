"""موتور برنامه: بررسی ویدیو، صف پردازش، FFmpeg (رندر، کاور، فریم پیش‌نمایش) و پروژه‌ها.
منطق نمایش زیرنویس در subs.py و تشخیص گفتار در asr.py است. به FastAPI وابسته نیست."""
from __future__ import annotations

import atexit
import base64
import json
import queue
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

import asr
import subs

MAX_SECONDS = 300
IS_WIN = sys.platform.startswith("win")
EMOJI_FONT = subs.EMOJI_FONT_WIN if IS_WIN else subs.EMOJI_FONT_OTHER
FFMPEG_HELP = (
    "FFmpeg پیدا نشد. در PowerShell این دستور را اجرا کنید: winget install Gyan.FFmpeg "
    "سپس پنجرهٔ برنامه را ببندید و دوباره اجرا کنید. "
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
    fps: str = "30"
    status: str = "queued"  # queued|transcribing|ready|render_queued|rendering|done|error
    stage: str = "در صف"
    progress: float | None = None
    error: str = ""
    warnings: list = field(default_factory=list)
    segments: list = field(default_factory=list)
    has_preview: bool = False
    has_cover: bool = False
    base: str = ""
    outputs: dict = field(default_factory=dict)
    style: dict | None = None
    export: dict | None = None
    pw: int = 0
    ph: int = 0
    mode: str = "transcribe"  # transcribe | open

    def public(self, with_segments=False) -> dict:
        d = {k: getattr(self, k) for k in ("id", "name", "duration", "width", "height", "status", "stage", "progress",
                                           "error", "warnings", "has_preview", "has_cover", "outputs")}
        if with_segments:
            d.update(segments=self.segments, style=self.style, export=self.export)
        return d


def parse_upload(filename: str, data: bytes, duration: float) -> tuple[dict, list[str]]:
    """فایل زیرنویس/رونویسی/پروژه را می‌خواند. خروجی: (دادهٔ پروژه، هشدارها)."""
    text, ext, warns = subs.decode_bytes(data), Path(filename).suffix.lower(), []
    try:
        if ext == ".json":
            return subs.parse_project(text), warns
        if ext == ".srt":
            return {"segments": subs.parse_srt(text)}, warns
        if ext in (".ass", ".ssa"):
            return subs.parse_ass(text), warns
        if ext == ".txt":
            segs, timed = subs.parse_txt(text, duration)
            if not timed:
                warns.append("فایل متنی زمان نداشت؛ متن‌ها به‌صورت تقریبی روی مدت ویدیو پخش شدند و باید زمان‌ها را اصلاح کنید.")
            return {"segments": segs}, warns
    except (ValueError, json.JSONDecodeError) as e:
        raise UserError(f"خواندن فایل ناموفق بود: {e}")
    raise UserError("فرمت فایل پشتیبانی نمی‌شود. از .json (پروژه)، .srt، .ass یا .txt استفاده کنید.")


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
        self._wcache: dict = {}

    # --- چرخهٔ عمر
    def start(self):
        if self._started:
            return
        self._started = True
        shutil.rmtree(self.work, ignore_errors=True)
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
            except Exception:  # noqa: BLE001
                ver = "نامشخص"
        return {
            "ffmpeg": self.tools_ok(), "ffmpeg_version": ver, "ffmpeg_help": "" if self.tools_ok() else FFMPEG_HELP,
            "whisper_installed": importlib.util.find_spec("faster_whisper") is not None,
            "model": asr.MODEL_REPO, "model_cached": asr.is_complete(asr.model_dir(self.models)),
            "max_seconds": MAX_SECONDS, "output_dir": str(self.output),
        }

    def fonts_list(self) -> list[str]:
        out = []
        for p in sorted(self.fonts.glob("*")) if self.fonts.exists() else []:
            if p.suffix.lower() in (".ttf", ".otf"):
                fam = subs.font_family(p)
                if fam and fam not in out:
                    out.append(fam)
        return out

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
        fps = v.get("avg_frame_rate", "30/1")
        m = re.fullmatch(r"(\d+)/(\d+)", fps)
        fps = fps if m and int(m[2]) and 10 <= int(m[1]) / int(m[2]) <= 60 else "30"
        return {"duration": dur, "width": w // 2 * 2, "height": h // 2 * 2, "fps": fps}

    # --- مدیریت کارها
    def new_dir(self) -> tuple[str, Path]:
        jid = secrets.token_hex(4)
        d = self.work / jid
        d.mkdir(parents=True)
        return jid, d

    def _make_job(self, jid, display_name, ext, language, mode) -> Job:
        d = self.work / jid
        info = self.probe(d / f"input{ext}")
        job = Job(jid, display_name, ext, "fa" if language != "auto" else "auto", d, mode=mode, **info)
        if job.width >= job.height:
            job.ph = min(720, job.height) // 2 * 2
            job.pw = round(job.width * job.ph / job.height / 2) * 2
        else:
            job.pw = min(720, job.width) // 2 * 2
            job.ph = round(job.height * job.pw / job.width / 2) * 2
        return job

    def register(self, jid: str, display_name: str, ext: str, language: str) -> Job:
        job = self._make_job(jid, display_name, ext, language, "transcribe")
        with self.lock:
            self.jobs[jid] = job
        self.q.put(("transcribe", jid))
        return job

    def open_project(self, jid: str, display_name: str, ext: str, sub_name: str | None, sub_data: bytes | None,
                     saved: str | None, shift: float, overwrite: bool) -> Job:
        """ویدیو + (پروژه | SRT | ASS | TXT) را بدون رونویسی دوباره باز می‌کند."""
        job = self._make_job(jid, display_name, ext, "fa", "open")
        data, warns = {}, []
        if saved:
            p = next((x for x in self.output.glob("*.project.json") if x.name == saved), None)
            if not p:
                raise UserError("پروژهٔ ذخیره‌شده پیدا نشد.")
            data, warns = parse_upload(p.name, p.read_bytes(), job.duration)
        elif sub_data is not None:
            data, warns = parse_upload(sub_name or "", sub_data, job.duration)
        else:
            raise UserError("یک فایل پروژه، SRT، ASS یا TXT انتخاب کنید.")
        if not data.get("segments"):
            raise UserError("در فایل انتخاب‌شده زیرنویسی پیدا نشد.")
        segs = data["segments"]
        if shift and "style" not in data:
            segs = [dict(s, start=s["start"] - shift, end=s["end"] - shift) for s in segs if s["end"] - shift > 0]
            for s in segs:
                s["start"] = max(0.0, s["start"])
        job.segments = self._clean(segs, job.duration)
        job.style = subs.merge_style(data["style"]) if data.get("style") else None
        job.export = subs.merge_export(data["export"]) if data.get("export") else None
        if data.get("cover_b64"):
            try:
                (job.dir / "cover.jpg").write_bytes(base64.b64decode(data["cover_b64"]))
                job.has_cover = True
            except Exception:  # noqa: BLE001
                warns.append("تصویر کاور ذخیره‌شده در پروژه خوانده نشد.")
        old = safe_stem(str(data.get("base", ""))) if data.get("base") else ""
        if overwrite and old and old == data.get("base"):
            job.base = old
        job.warnings = warns + ([f"{len(job.segments)} بخش زیرنویس بارگذاری شد."] if False else [])
        with self.lock:
            self.jobs[jid] = job
        self.q.put(("prepare", jid))
        return job

    def list_projects(self) -> list[dict]:
        out = []
        for p in sorted(self.output.glob("*.project.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                d = json.loads(p.read_text(encoding="utf-8-sig"))
                out.append({"file": p.name, "video_name": d.get("video_name", p.stem), "segments": len(d.get("segments", [])),
                            "saved_at": time.strftime("%Y-%m-%d %H:%M", time.localtime(p.stat().st_mtime))})
            except Exception:  # noqa: BLE001
                continue
        return out

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

    @staticmethod
    def _clean(segments, duration):
        out = []
        for s in segments:
            a = min(max(0.0, float(s["start"])), duration)
            b = min(max(a, float(s["end"])), duration)
            d = {"start": round(a, 2), "end": round(b, 2), "text": str(s["text"]),
                 "effect": s.get("effect", "none"), "emphasis": s.get("emphasis", "")}
            if s.get("words"):
                d["words"] = s["words"]
            out.append(d)
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
                exists = any((self.output / f).exists() for f in (f"{base}.srt", f"{base}.ass", f"{base}_sub.mp4", f"{base}.project.json"))
                if base not in taken and not exists:
                    job.base = base
                    return base
                n += 1

    # --- پروژه و ذخیره
    def _project(self, job: Job, with_cover=True) -> dict:
        d = {"version": 2, "base": job.base, "video_name": job.name, "duration": job.duration, "language": job.language,
             "segments": job.segments, "style": job.style, "export": job.export}
        c = job.dir / "cover.jpg"
        if with_cover and job.has_cover and c.exists() and c.stat().st_size < 700_000:
            d["cover_b64"] = base64.b64encode(c.read_bytes()).decode()
        return d

    def _shift(self, job: Job) -> float:
        return float(job.export["cover_duration"]) if job.export and job.has_cover else 0.0

    def save(self, jid: str, segments: list[dict], style: dict | None = None, export: dict | None = None):
        """متن، زمان‌بندی و تنظیمات را در output/ ذخیره می‌کند (project.json، txt، srt)."""
        job = self.get(jid)
        job.segments = self._clean(segments, job.duration)
        if style is not None:
            job.style = subs.merge_style(style)
        if export is not None:
            job.export = subs.merge_export(export)
        base = self._alloc_base(job)
        st, ex = subs.merge_style(job.style), subs.merge_export(job.export)
        W, H = subs.out_dims(job.width, job.height, ex["reframe"])
        events = subs.layout(job.segments, st, W, H)
        (self.output / f"{base}.project.json").write_text(json.dumps(self._project(job), ensure_ascii=False), encoding="utf-8")
        (self.output / f"{base}.txt").write_text(subs.make_txt(job.segments), encoding="utf-8-sig")
        (self.output / f"{base}.srt").write_text(subs.make_srt(events, self._shift(job)), encoding="utf-8-sig")
        job.outputs.update(project=f"{base}.project.json", txt=f"{base}.txt", srt=f"{base}.srt")

    def export(self, jid: str, style: dict, segments: list[dict], export: dict):
        job = self.get(jid)
        if job.status not in ("ready", "done", "error") or (job.status == "error" and not job.segments):
            raise UserError("این ویدیو هنوز آمادهٔ ساخت خروجی نیست.")
        if not any(s["text"].strip() for s in segments):
            raise UserError("هیچ متن زیرنویسی برای ساخت خروجی وجود ندارد.")
        self.save(jid, segments, style, export)
        ex = job.export
        if "mp4" in ex["formats"]:
            job.status, job.stage, job.progress, job.error = "render_queued", "در صف ساخت خروجی", None, ""
            self.q.put(("render", jid))
        else:
            self._write_text_outputs(job, ex["formats"])

    def _events_widths(self, job, st, W, H):
        events = subs.layout(job.segments, st, W, H)
        widths = self.measure([subs.line_text(l) for e in events for l in e["lines"]], st, W, H)
        return events, widths

    def _write_text_outputs(self, job: Job, formats: list[str]):
        st, ex = subs.merge_style(job.style), subs.merge_export(job.export)
        W, H = subs.out_dims(job.width, job.height, ex["reframe"])
        base, shift = self._alloc_base(job), self._shift(job)
        events, widths = self._events_widths(job, st, W, H)
        if "srt" in formats:
            (self.output / f"{base}.srt").write_text(subs.make_srt(events, shift), encoding="utf-8-sig")
            job.outputs["srt"] = f"{base}.srt"
        if "ass" in formats:
            ass = subs.build_ass(events, st, W, H, widths, EMOJI_FONT, shift, self._project(job, with_cover=False))
            (self.output / f"{base}.ass").write_text(ass, encoding="utf-8-sig")
            job.outputs["ass"] = f"{base}.ass"
        return events, widths

    # --- کاور
    def set_cover(self, jid: str, data: bytes, ext: str):
        job = self.get(jid)
        src = job.dir / f"cover_src{safe_ext('x' + ext)}"
        src.write_bytes(data)
        try:
            self.run_ffmpeg(["-i", src.name, "-frames:v", "1", "-vf", "scale=w='min(1920,iw)':h=-2", "-q:v", "2", "cover.jpg"],
                            job.dir, 0, lambda p: None)
        except UserError:
            raise UserError("تصویر کاور خوانده نشد؛ از JPG، PNG یا WEBP استفاده کنید.")
        finally:
            src.unlink(missing_ok=True)
        job.has_cover = True

    def clear_cover(self, jid: str):
        job = self.get(jid)
        (job.dir / "cover.jpg").unlink(missing_ok=True)
        job.has_cover = False

    # --- اندازه‌گیری عرض خط‌ها با خود libass (برای کادر پس‌زمینه) و فریم پیش‌نمایش
    def _fonts_arg(self, cwd: Path) -> str:
        if self.fonts.exists() and any(self.fonts.glob("*")):
            shutil.copytree(self.fonts, cwd / "fonts", dirs_exist_ok=True)
            return ":fontsdir=fonts"
        return ""

    def measure(self, texts: list[str], st: dict, W: int, H: int) -> dict:
        size = subs.nominal_size(st, W, H)
        key = lambda t: (st["font"], st["bold"], round(size, 1), W, H, t)  # noqa: E731
        todo = [t for t in dict.fromkeys(texts) if t and key(t) not in self._wcache]
        if todo and self.tools_ok():
            d = self.work / f"measure_{secrets.token_hex(3)}"
            d.mkdir(parents=True)
            try:
                (d / "m.ass").write_text(subs.measure_ass(todo, st, W, H, size, EMOJI_FONT), encoding="utf-8-sig")
                fd = self._fonts_arg(d)
                band = min(H, int(size * 2.2) // 2 * 2)  # نوار افقی حول مرکز که خط درون آن رسم می‌شود
                # ستون‌ها را در ارتفاع نوار میانگین می‌گیریم؛ هر ستون غیرصفر یعنی جوهر متن. عرض = اولین تا آخرین ستون.
                r = subprocess.run([self.ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                                    f"color=c=black:s={W}x{H}:r=10:d={len(todo) * 0.1 + 0.1:.1f}", "-vf",
                                    f"ass=m.ass{fd},format=gray,crop={W}:{band}:0:{(H - band) // 2},scale={W}:1:flags=area",
                                    "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                                   cwd=d, capture_output=True, timeout=300, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                for i, t in enumerate(todo):
                    row = r.stdout[i * W:(i + 1) * W]
                    xs = [x for x, v in enumerate(row) if v > 3]
                    if len(row) == W and xs:
                        self._wcache[key(t)] = float(xs[-1] - xs[0] + 1)
            finally:
                shutil.rmtree(d, ignore_errors=True)
        return {t: self._wcache.get(key(t), 0) for t in texts}

    def preview_dims(self, job: Job, reframe: str) -> tuple[int, int]:
        if reframe == "original":
            return job.pw, job.ph
        tw, th = subs.out_dims(job.width, job.height, reframe)
        s = 720 / min(tw, th)
        return round(tw * s / 2) * 2, round(th * s / 2) * 2

    @staticmethod
    def fit_filter(W: int, H: int, reframe: str) -> str:
        if reframe == "original":
            return f"scale={W}:{H}"
        return f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}"

    def layout_info(self, jid: str, segments: list[dict], style: dict, export: dict) -> dict:
        job = self.get(jid)
        st, ex = subs.merge_style(style), subs.merge_export(export)
        W, H = self.preview_dims(job, ex["reframe"])
        evs = subs.layout(self._clean(segments, job.duration), st, W, H)
        return {"events": [{"start": e["start"], "end": e["end"], "effect": e["effect"],
                            "lines": [subs.line_text(l) for l in e["lines"]]} for e in evs],
                "rel_size": subs.nominal_size(st, W, H) / H, "width": W, "height": H}

    def frame(self, jid: str, t: float, segments: list[dict], style: dict, export: dict) -> bytes:
        """فریم واقعی ویدیو با همان ASS و همان libass که رندر نهایی استفاده می‌کند."""
        job = self.get(jid)
        if not job.has_preview:
            raise UserError("پیش‌نمایش هنوز آماده نیست.")
        st, ex = subs.merge_style(style), subs.merge_export(export)
        W, H = self.preview_dims(job, ex["reframe"])
        segs = self._clean(segments, job.duration)
        events = subs.layout(segs, st, W, H)
        widths = self.measure([subs.line_text(l) for e in events for l in e["lines"] if e["start"] - 1 <= t <= e["end"] + 1], st, W, H)
        # فقط رویدادهای نزدیک به لحظهٔ انتخاب‌شده ساخته می‌شوند تا پاسخ سریع باشد
        near = [e for e in events if e["start"] <= t < e["end"]]
        tag = secrets.token_hex(3)
        ass = subs.build_ass(near, st, W, H, widths, EMOJI_FONT)
        (job.dir / f"pv_{tag}.ass").write_text(ass, encoding="utf-8-sig")
        t = max(0.0, min(t, job.duration - 0.05))
        vf = f"setpts=PTS+{t:.3f}/TB,{self.fit_filter(W, H, ex['reframe'])},ass=pv_{tag}.ass{self._fonts_arg(job.dir)}"
        out = job.dir / f"pv_{tag}.jpg"
        try:
            r = subprocess.run([self.ffmpeg, "-y", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", "preview.mp4",
                                "-vf", vf, "-frames:v", "1", "-q:v", "3", out.name], cwd=job.dir, capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=60,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if r.returncode != 0 or not out.exists():
                raise UserError(f"ساخت فریم پیش‌نمایش ناموفق بود: {r.stderr.strip()[-300:]}")
            return out.read_bytes()
        finally:
            for x in (f"pv_{tag}.ass", f"pv_{tag}.jpg"):
                (job.dir / x).unlink(missing_ok=True)

    # --- کارگر (فقط یک پردازش سنگین در هر لحظه)
    def _worker(self):
        while True:
            kind, jid = self.q.get()
            job = self.jobs.get(jid)
            if not job:
                continue
            try:
                {"transcribe": self._transcribe, "prepare": self._prepare, "render": self._render}[kind](job)
            except UserError as e:
                job.status, job.error, job.progress = "error", str(e), None
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                job.status, job.error, job.progress = "error", f"خطای غیرمنتظره: {e}", None

    def run_ffmpeg(self, args: list[str], cwd: Path, total: float, on_progress):
        cmd = [self.ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-nostats", "-progress", "pipe:1", *args]
        log = cwd / "ffmpeg.log"
        with open(log, "wb") as lf:
            p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=lf, text=True, encoding="utf-8",
                                 errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
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

    def _make_proxy(self, job: Job):
        if job.has_preview:
            return
        job.stage, job.progress = "ساخت نسخهٔ سبک برای پیش‌نمایش", 0
        self.run_ffmpeg(["-i", f"input{job.ext}", "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn",
                         "-vf", f"scale={job.pw}:{job.ph}", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-g", "25",
                         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", "preview.mp4"],
                        job.dir, job.duration, lambda p: setattr(job, "progress", p))
        job.has_preview = True

    def _prepare(self, job: Job):
        job.status = "transcribing"
        self._make_proxy(job)
        base = self._alloc_base(job)
        self.save(job.id, job.segments, job.style, job.export)  # ذخیرهٔ اولیه؛ فایل‌های قبلی بدون تأیید بازنویسی نمی‌شوند
        job.status, job.stage, job.progress = "ready", "پروژه باز شد؛ آمادهٔ ویرایش", None
        return base

    def _get_model(self, job: Job):
        if not self._model:
            job.progress = None
            try:
                self._model = asr.load_model(self.models, on_status=lambda m: setattr(job, "stage", m))
            except asr.ASRError as e:
                raise UserError(str(e))
        return self._model

    def _transcribe(self, job: Job):
        job.status = "transcribing"
        self._make_proxy(job)
        model = self._get_model(job)
        job.stage, job.progress = "تبدیل گفتار به متن", 0
        try:
            segs = asr.transcribe(model, str(job.dir / f"input{job.ext}"), job.duration, job.language,
                                  lambda p: setattr(job, "progress", p))
        except asr.ASRError as e:
            raise UserError(str(e))
        if not segs:
            job.warnings.append("گفتاری تشخیص داده نشد. می‌توانید بخش‌ها را دستی اضافه کنید.")
        loops = sum(asr.has_loop(s["text"]) for s in segs)
        if loops:
            job.warnings.append(f"{loops} بخش تکرار غیرعادی یک کلمه دارد (نشانهٔ خطای مدل)؛ متن را بررسی کنید.")
        self._alloc_base(job)
        self.save(job.id, [dict(s, effect="none", emphasis="") for s in segs])
        job.status, job.stage, job.progress = "ready", "آمادهٔ ویرایش", None

    def _render(self, job: Job):
        job.status, job.stage, job.progress, job.warnings = "rendering", "آماده‌سازی زیرنویس", 0, []
        st, ex = subs.merge_style(job.style), subs.merge_export(job.export)
        W, H = subs.out_dims(job.width, job.height, ex["reframe"])
        base = self._alloc_base(job)
        if any(subs.EMOJI_RE.search(s["text"]) for s in job.segments):
            job.warnings.append("ایموجی‌ها با فونت سیستم رندر می‌شوند و ممکن است تک‌رنگ دیده شوند (محدودیت libass)."
                                if self._emoji_font_ok() else f"فونت ایموجی «{EMOJI_FONT}» پیدا نشد؛ ایموجی‌ها ممکن است مربع دیده شوند.")
        if st["font"].lower().startswith("vazir") and not any(self.fonts.glob("*azir*")):
            job.warnings.append("فونت Vazirmatn در پوشهٔ fonts پیدا نشد؛ فونت جایگزین استفاده می‌شود.")
        events, widths = self._write_text_outputs(job, [f for f in ex["formats"] if f in ("srt", "ass")])
        ass = subs.build_ass(events, st, W, H, widths, EMOJI_FONT)  # بدون شیفت: زیرنویس قبل از اتصال کاور روی ویدیو می‌نشیند
        (job.dir / "subs.ass").write_text(ass, encoding="utf-8-sig")
        cover = ex["cover_duration"] > 0 and job.has_cover
        art = ex["embed_artwork"] and job.has_cover
        n, fps = float(ex["cover_duration"]), job.fps
        args = ["-i", f"input{job.ext}"]
        if cover:
            args += ["-loop", "1", "-framerate", fps, "-t", f"{n:.2f}", "-i", "cover.jpg",
                     "-f", "lavfi", "-t", f"{n:.2f}", "-i", "anullsrc=r=48000:cl=stereo"]
        if art:
            args += ["-i", "cover.jpg"]
        fc = [f"[0:v:0]{self.fit_filter(W, H, ex['reframe'])},setsar=1,fps={fps},ass=subs.ass{self._fonts_arg(job.dir)},format=yuv420p[mv]",
              "[0:a:0]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[ma]"]
        if cover:
            fit = (f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}" if ex["cover_fit"] == "fill" else
                   f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black")
            fc += [f"[1:v]{fit},setsar=1,fps={fps},format=yuv420p[cv]", "[2:a]aformat=sample_fmts=fltp:channel_layouts=stereo[ca]",
                   "[cv][ca][mv][ma]concat=n=2:v=1:a=1[v][a]"]
        else:
            fc += ["[mv]null[v]", "[ma]anull[a]"]
        args += ["-filter_complex", ";".join(fc), "-map", "[v]", "-map", "[a]"]
        if art:
            args += ["-map", f"{3 if cover else 1}:v"]
        args += ["-c:v:0", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "192k"]
        if art:
            args += ["-c:v:1", "copy", "-disposition:v:1", "attached_pic", "-metadata:s:v:1", "title=Cover"]
        args += ["-movflags", "+faststart", "out.mp4"]
        job.stage = "چسباندن زیرنویس به تصویر"
        fonts_copy = job.dir / "fonts"
        try:
            self.run_ffmpeg(args, job.dir, job.duration + (n if cover else 0), lambda p: setattr(job, "progress", p))
            final = self.output / f"{base}_sub.mp4"
            shutil.move(str(job.dir / "out.mp4"), str(final))
            if job.has_cover:
                shutil.copyfile(job.dir / "cover.jpg", self.output / f"{base}_thumb.jpg")
                job.outputs["thumb"] = f"{base}_thumb.jpg"
        finally:
            for t in ("subs.ass", "out.mp4"):
                (job.dir / t).unlink(missing_ok=True)
            shutil.rmtree(fonts_copy, ignore_errors=True)
        job.outputs["mp4"] = final.name
        job.status, job.stage, job.progress = "done", "تمام شد", None

    @staticmethod
    def _emoji_font_ok() -> bool:
        if IS_WIN:
            import os
            return (Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "seguiemj.ttf").exists()
        try:
            return EMOJI_FONT in subprocess.run(["fc-list"], capture_output=True, text=True).stdout
        except Exception:  # noqa: BLE001
            return False
