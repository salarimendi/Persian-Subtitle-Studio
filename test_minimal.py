"""آزمون‌های حداقلی. اجرا: python test_minimal.py
هر آزمونی که پیش‌نیازش نباشد «SKIP» می‌شود و دلیلش چاپ می‌شود؛ هیچ نتیجه‌ای ساختگی نیست."""
import os, shutil, subprocess, sys, tempfile, traceback
from pathlib import Path

import core

APP = Path(__file__).parent
results = []


def case(name):
    def deco(fn):
        try:
            msg = fn()
            results.append((name, "SKIP" if msg else "PASS", msg or ""))
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            results.append((name, "FAIL", str(e)))
        return fn
    return deco


STYLE = dict(font="DejaVu Sans" if not core.IS_WIN else "Tahoma", size_pct=6, color="#ffffff", outline_color="#000000",
             outline_w=3, bg_on=True, bg_color="#000000", bg_opacity=0.6, position="bottom", margin_pct=6, bold=True,
             emphasis_color="#ffd400")
SEGS = [
    {"start": 0.2, "end": 2.2, "text": "سلام، این یک آزمایش است 😀", "effect": "pop", "emphasis": "آزمایش"},
    {"start": 2.4, "end": 4.6, "text": "زیرنویس فارسی\nدر دو خط", "effect": "fadeboth", "emphasis": ""},
]


@case("1. import و راه‌اندازی FastAPI")
def _():
    try:
        import fastapi  # noqa: F401
    except ImportError:
        return "fastapi نصب نیست (وابستگی‌ها نصب نشده‌اند)"
    import main
    assert main.app.title
    from fastapi.testclient import TestClient
    try:
        c = TestClient(main.app)
    except RuntimeError as e:
        return f"TestClient در دسترس نیست: {e}"
    assert c.get("/api/status").status_code == 200
    assert c.get("/").status_code == 200


mgr = core.Manager(APP)


@case("2. دسترسی به FFmpeg و ffprobe")
def _():
    assert mgr.tools_ok(), core.FFMPEG_HELP
    assert "ass" in subprocess.run([mgr.ffmpeg, "-hide_banner", "-filters"], capture_output=True, text=True).stdout


@case("3. SRT/ASS: قالب و نرمال‌سازی")
def _():
    srt = core.make_srt(SEGS)
    assert srt.startswith("1\n00:00:00,200 --> 00:00:02,200\n"), srt
    assert "2\n00:00:02,400 --> 00:00:04,600" in srt
    ass = core.build_ass(SEGS, STYLE, 1280, 720, True)
    assert "\\t(0,180" in ass and "\\fad(" in ass and "Style: Box" in ass and "😀" in ass
    off = core.build_ass(SEGS, STYLE, 1280, 720, False)
    assert "\\fad(" not in off and "\\t(0,180" not in off
    words = [(0, .5, "الف"), (.5, 1, "ب"), (3, 3.5, "ج")]
    assert len(core.group_words(words)) == 2


@case("3b. پاسخ Range برای پیش‌نمایش")
def _():
    f = Path(tempfile.mkstemp()[1]); f.write_bytes(bytes(range(100)))
    try:
        assert core.read_range(f, "bytes=10-19")[:2] == (206, bytes(range(10, 20)))
        assert core.read_range(f, "bytes=90-")[1] == bytes(range(90, 100))
        assert core.read_range(f, "bytes=-5")[1] == bytes(range(95, 100))
        assert core.read_range(f, None)[0] == 200 and core.read_range(f, "bytes=200-300")[0] == 416
    finally:
        f.unlink()


def _mkvideo(d: Path, dur=5):
    out = d / "input.mp4"
    subprocess.run([mgr.ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc=size=1280x720:rate=25:duration={dur}",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={dur}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(out)], check=True)
    return out


@case("4. مسیر کامل رندر: probe → ASS → MP4 (ویدیوی مصنوعی FFmpeg، بدون Whisper)")
def _():
    if not mgr.tools_ok():
        return "FFmpeg در دسترس نیست"
    mgr.start()
    jid, d = mgr.new_dir()
    _mkvideo(d)
    info = mgr.probe(d / "input.mp4")
    assert (info["width"], info["height"]) == (1280, 720), info
    job = core.Job(jid, "نمونه ویدیو.mp4", ".mp4", "fa", d, **info)
    job.segments = mgr._clean(SEGS, job.duration)
    job.render_req = {"style": STYLE, "effects_on": True}
    mgr.jobs[jid] = job
    mgr._render(job)
    mp4 = mgr.output / job.outputs["mp4"]
    assert mp4.stat().st_size > 10_000
    assert (mgr.output / job.outputs["srt"]).exists() and (mgr.output / job.outputs["ass"]).exists()
    assert not (d / "subs.ass").exists() and not (d / "out.mp4").exists(), "فایل موقت پاک نشده"
    assert (d / "input.mp4").exists(), "ورودی نباید حذف شود"
    shot = d / "frame.png"
    subprocess.run([mgr.ffmpeg, "-y", "-loglevel", "error", "-ss", "1.5", "-i", str(mp4), "-frames:v", "1", str(shot)], check=True)
    shutil.copy(shot, tempfile.gettempdir() + "/subtitle_test_frame.png")
    print("  فریم نمونه:", tempfile.gettempdir() + "/subtitle_test_frame.png")
    for name in job.outputs.values():  # پاک‌سازی خروجی‌های خود آزمون
        (mgr.output / name).unlink(missing_ok=True)
    shutil.rmtree(d, ignore_errors=True)


@case("5. ویدیوی بلندتر از ۵ دقیقه و فایل نامعتبر رد می‌شوند")
def _():
    if not mgr.tools_ok():
        return "FFmpeg در دسترس نیست"
    d = Path(tempfile.mkdtemp())
    try:
        bad = d / "x.mp4"; bad.write_bytes(b"not a video")
        try: mgr.probe(bad); raise AssertionError("فایل نامعتبر پذیرفته شد")
        except core.UserError: pass
        long = d / "long.mp4"
        subprocess.run([mgr.ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-f", "lavfi",
                        "-i", "color=c=black:s=64x64:r=1", "-t", "310", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(long)], check=True)
        try: mgr.probe(long); raise AssertionError("ویدیوی ۵:۱۰ پذیرفته شد")
        except core.UserError as e: assert "حداکثر" in str(e)
    finally:
        shutil.rmtree(d, ignore_errors=True)


@case("6. Whisper: تولید SRT از نمونهٔ واقعی (نیاز به SAMPLE_VIDEO و مدل)")
def _():
    sample = os.environ.get("SAMPLE_VIDEO")
    if not sample or not Path(sample).exists():
        return "متغیر SAMPLE_VIDEO (مسیر یک ویدیوی فارسی کوتاه) تنظیم نشده"
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return "faster-whisper نصب نیست"
    jid, d = mgr.new_dir()
    shutil.copy(sample, d / f"input{core.safe_ext(sample)}")
    job = mgr.register(jid, Path(sample).name, core.safe_ext(sample), "fa")
    import time
    while job.status in ("queued", "transcribing"):
        time.sleep(1)
    assert job.status == "ready", job.error
    assert job.segments and all(s["end"] > s["start"] for s in job.segments)
    print("  نمونهٔ متن:", job.segments[0]["text"])


print()
for n, s, m in results:
    print(f"[{s}] {n}" + (f" — {m}" if m else ""))
sys.exit(1 if any(s == "FAIL" for _, s, _ in results) else 0)
