"""تشخیص گفتار فارسی: دانلود و بارگذاری مدل، رونویسی، تمیزکاری امن متن و معیار WER.
به FastAPI وابسته نیست؛ برنامه، تست‌ها و اسکریپت مقایسه از همین ماژول استفاده می‌کنند."""
from __future__ import annotations

import os
import re
import threading
from pathlib import Path

# مدل پیش‌فرض: تبدیل CTranslate2 (float16) از فاین‌تیون whisper-large-v3 روی Common Voice 17 فارسی.
# با متغیر محیطی SUBTITLE_MODEL می‌توان مدل CT2 دیگری انتخاب کرد (مثلاً Systran/faster-whisper-large-v3).
DEFAULT_MODEL = "oi-uae/farsi-faster-whisper-large-v3"
MODEL_REPO = os.environ.get("SUBTITLE_MODEL", DEFAULT_MODEL)
COMPUTE_TYPE = os.environ.get("SUBTITLE_COMPUTE", "int8")  # int8: حدود ۳ گیگابایت RAM؛ float32: دقیق‌تر ولی حدود دو برابر RAM و کندتر
CPU_THREADS = int(os.environ.get("SUBTITLE_THREADS", max(4, (os.cpu_count() or 8) // 2)))
ALLOW = ["model.bin", "config.json", "tokenizer.json", "vocabulary.*", "preprocessor_config.json"]

# پارامترهای رمزگشایی. condition_on_previous_text=False از حلقهٔ تکرار و پخش خطا بین جمله‌ها جلوگیری می‌کند.
DECODE = dict(
    task="transcribe", beam_size=5, best_of=5, patience=1.0,
    temperature=(0.0, 0.2, 0.4, 0.6, 0.8, 1.0), condition_on_previous_text=False,
    compression_ratio_threshold=2.4, log_prob_threshold=-1.0, no_speech_threshold=0.6,
    vad_filter=True, vad_parameters=dict(min_silence_duration_ms=500, speech_pad_ms=300),
)


class ASRError(Exception):
    """خطایی که پیامش مستقیم به کاربر نشان داده می‌شود."""


# ---------------------------------------------------------------- مدل
def model_dir(models_root: Path, repo: str = MODEL_REPO) -> Path:
    return Path(models_root) / repo.split("/")[-1]


def is_complete(d: Path) -> bool:
    mb = d / "model.bin"
    return (mb.exists() and mb.stat().st_size > 50_000_000 and (d / "config.json").exists()
            and (d / "tokenizer.json").exists() and any(d.glob("vocabulary.*")))


def ensure_model(models_root: Path, repo: str = MODEL_REPO, on_status=None) -> Path:
    """مدل را در models/ نگه می‌دارد؛ فقط اگر کامل نباشد دانلود می‌کند (کش پیش‌فرض Hugging Face استفاده نمی‌شود)."""
    d = model_dir(models_root, repo)
    if is_complete(d):
        return d
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        raise ASRError("کتابخانهٔ huggingface_hub نصب نیست؛ وابستگی‌ها را دوباره نصب کنید.")
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    d.mkdir(parents=True, exist_ok=True)
    stop = threading.Event()

    def poll():
        while not stop.wait(1.0):
            try:
                size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
                if on_status:
                    on_status(f"دریافت مدل {repo.split('/')[-1]} (فقط بار اول): {size / 1e6:.0f} مگابایت")
            except OSError:
                pass

    threading.Thread(target=poll, daemon=True).start()
    try:
        snapshot_download(repo_id=repo, local_dir=str(d), allow_patterns=ALLOW)
    except Exception as e:  # noqa: BLE001
        raise ASRError(f"دریافت مدل «{repo}» ناموفق بود. اتصال اینترنت را بررسی کنید و دوباره تلاش کنید؛ دانلود از ادامه شروع می‌شود. ({e})")
    finally:
        stop.set()
    if not is_complete(d):
        raise ASRError(f"فایل‌های مدل در {d} ناقص است. آن پوشه را پاک کنید و دوباره تلاش کنید.")
    return d


def load_model(models_root: Path, repo: str = MODEL_REPO, on_status=None):
    d = ensure_model(models_root, repo, on_status)
    if on_status:
        on_status("بارگذاری مدل در حافظه")
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise ASRError("کتابخانهٔ faster-whisper نصب نیست؛ وابستگی‌ها را دوباره نصب کنید.")
    try:
        return WhisperModel(str(d), device="cpu", compute_type=COMPUTE_TYPE, cpu_threads=CPU_THREADS)
    except Exception as e:  # noqa: BLE001
        raise ASRError(f"بارگذاری مدل ناموفق بود ({e}). اگر فایل‌ها ناقص‌اند پوشهٔ {d} را پاک کنید تا دوباره دانلود شود.")


# ---------------------------------------------------------------- متن
_DIACRITICS = re.compile("[\u064B-\u065F\u0670\u06D6-\u06ED]")
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "۰۱۲۳۴۵۶۷۸۹")


def clean_text(t: str) -> str:
    """فقط یکسان‌سازی املایی بی‌خطر (ي/ك عربی، ـ، ارقام عربی، فاصلهٔ اضافه). هیچ واژه‌ای جایگزین یا حذف نمی‌شود."""
    t = t.replace("ي", "ی").replace("ى", "ی").replace("ك", "ک").replace("ـ", "").translate(_DIGITS)
    t = re.sub(r"\s+([،؛؟!:])", r"\1", t)
    return re.sub(r"\s+", " ", t).strip()


def has_loop(text: str, n: int = 5) -> bool:
    """نشانهٔ توهم مدل: یک کلمه پشت سر هم ≥ n بار. فقط برای هشدار است، متن تغییر نمی‌کند."""
    run, prev = 0, None
    for w in text.split():
        run = run + 1 if w == prev else 1
        prev = w
        if run >= n:
            return True
    return False


def group_words(words: list[tuple[float, float, str]], max_chars=36, max_dur=4.5, max_gap=0.8) -> list[dict]:
    """کلمه‌ها را به خطوط کوتاه زیرنویس تبدیل می‌کند."""
    segs, cur = [], []

    def flush():
        if cur:
            segs.append({"start": cur[0][0], "end": cur[-1][1], "text": " ".join(w[2] for w in cur).strip()})
            cur.clear()

    for w in words:
        if cur:
            chars = sum(len(x[2]) + 1 for x in cur) + len(w[2])
            if chars > max_chars or w[1] - cur[0][0] > max_dur or w[0] - cur[-1][1] > max_gap:
                flush()
        cur.append(w)
        if re.search(r"[.!؟?!…]$", w[2]) and sum(len(x[2]) + 1 for x in cur) > 14:
            flush()
    flush()
    return segs


def _spread(start: float, end: float, text: str) -> list[tuple[float, float, str]]:
    """اگر زمان کلمه‌ها موجود نباشد، زمان بخش را بر اساس طول کلمه‌ها تقسیم می‌کند."""
    ws = text.split()
    total = sum(len(w) + 1 for w in ws) or 1
    out, pos = [], 0
    for w in ws:
        a = start + (end - start) * pos / total
        pos += len(w) + 1
        out.append((a, start + (end - start) * pos / total, w))
    return out


# ---------------------------------------------------------------- رونویسی
def transcribe(model, path: str, duration: float | None = None, language: str = "fa", on_progress=None) -> list[dict]:
    """خروجی: فهرست {start,end,text} با متن تمیزشده. زبان پیش‌فرض fa است مگر language='auto'."""
    kw = dict(DECODE, language=None if language == "auto" else "fa")
    for word_ts in (True, False):
        count = 0
        try:
            gen, _info = model.transcribe(path, word_timestamps=word_ts, **kw)
            words: list[tuple[float, float, str]] = []
            for seg in gen:
                count += 1
                if on_progress and duration:
                    on_progress(min(99.0, seg.end / duration * 100))
                got = [(w.start, w.end, w.word.strip()) for w in (seg.words or []) if w.word.strip()] if word_ts else []
                words += got or _spread(seg.start, seg.end, seg.text)
            segs = group_words(words)
            for s in segs:
                s["text"] = clean_text(s["text"])
            return [s for s in segs if s["text"]]
        except Exception as e:  # noqa: BLE001
            if word_ts and count == 0:
                continue  # مدل تبدیل‌شده ممکن است برای زمان کلمه‌ها alignment_heads نداشته باشد؛ با زمان بخش تکرار می‌شود
            raise ASRError(f"خطا در تبدیل گفتار به متن: {e}")
    return []


# ---------------------------------------------------------------- ارزیابی
def normalize_for_eval(t: str) -> str:
    t = _DIACRITICS.sub("", clean_text(t)).replace("\u200c", " ").lower()
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", t)).strip()


def _edit(a: list, b: list) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def wer(ref: str, hyp: str) -> float:
    r = normalize_for_eval(ref).split()
    return _edit(r, normalize_for_eval(hyp).split()) / max(1, len(r))


def cer(ref: str, hyp: str) -> float:
    """خطای نویسه بدون فاصله؛ تفاوت «می‌شیم / میشیم» را جریمه نمی‌کند."""
    r = list(normalize_for_eval(ref).replace(" ", ""))
    return _edit(r, list(normalize_for_eval(hyp).replace(" ", ""))) / max(1, len(r))
