"""منطق زیرنویس بدون مدل: چیدمان، زمان‌بندی، ASS/SRT/TXT، ورودی‌گیری، کاور/Artwork و قالب‌های خروجی."""
import json
import shutil
import subprocess
import time
import unittest

import _paths  # noqa: F401
import asr
import core
import subs

ST = dict(subs.PRESETS["documentary"]["style"], font="DejaVu Sans" if not core.IS_WIN else "Tahoma")
SEGS = [
    {"start": 0.2, "end": 2.2, "text": "سلام، این یک آزمایش است 😀", "effect": "pop", "emphasis": "آزمایش"},
    {"start": 2.25, "end": 5.0, "text": "این متن خیلی بلند است و باید در دو خط بشکند تا خوانا بماند و حتی بیشتر از آن ادامه پیدا کند 123?", "effect": "none", "emphasis": ""},
]
mgr = core.Manager(_paths.ROOT)


class Layout(unittest.TestCase):
    def test_wrap_timing_digits_and_clean(self):
        st = subs.merge_style(dict(ST, digits="persian", min_dur=0.8, gap=0.1, max_chars=20))
        evs = subs.layout(SEGS, st, 1920, 1080)
        self.assertTrue(all(1 <= len(e["lines"]) <= 2 for e in evs))
        self.assertTrue(all(len(subs.line_text(l)) <= 20 for e in evs for l in e["lines"]))
        for a, b in zip(evs, evs[1:]):
            self.assertGreaterEqual(b["start"] - a["end"], 0.1 - 1e-6, "زیرنویس‌ها نباید هم‌پوشانی یا چسبندگی داشته باشند")
        txt = " ".join(subs.line_text(l) for e in evs for l in e["lines"])
        self.assertIn("۱۲۳", txt)
        self.assertIn("؟", txt)
        wide = subs.layout(SEGS[1:], subs.merge_style(dict(ST, max_chars=30)), 1920, 1080)
        tall = subs.layout(SEGS[1:], subs.merge_style(dict(ST, max_chars=30)), 1080, 1920)
        self.assertGreaterEqual(len(tall), len(wide))  # در عمودی خط‌ها کوتاه‌تر می‌شوند
        self.assertEqual(asr.clean_text("كتاب ياد ٣ ، خوب"), "کتاب یاد ۳، خوب")
        self.assertEqual(asr.wer("می‌شیم", "می شیم"), 0)

    def test_ass_and_roundtrips(self):
        st = subs.merge_style(dict(ST, word_mode="active", bg_border_w=2))
        words = [[0.2, 0.7, "سلام،"], [0.8, 1.2, "این"], [1.3, 1.6, "یک"], [1.7, 2.0, "آزمایش"], [2.0, 2.1, "است"], [2.1, 2.2, "😀"]]
        evs = subs.layout([dict(SEGS[0], words=words)], st)
        ass = subs.build_ass(evs, st, 1280, 720, {}, core.EMOJI_FONT, project={"segments": SEGS, "style": st})
        for token in ("\\p1", "\\t(", "SUBPROJECT", "Style: Main", ",-1"):
            self.assertIn(token, ass)
        self.assertEqual(subs.parse_ass(ass)["segments"], SEGS)  # بازیابی کامل از ASS خود برنامه
        srt = subs.make_srt(evs, shift=1.5)
        self.assertTrue(srt.startswith("1\n00:00:01,700 --> "))
        self.assertEqual(len(subs.parse_srt(srt)), len(evs))
        txt, timed = subs.parse_txt(subs.make_txt(SEGS), 10)
        self.assertTrue(timed)
        self.assertEqual([s["text"] for s in txt], [s["text"] for s in SEGS])
        other = ("[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
                 "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,{\\an8}سلام\\Nدنیا\n")
        self.assertEqual(subs.parse_ass(other)["segments"][0]["text"], "سلام\nدنیا")


class Render(unittest.TestCase):
    def test_measure_cover_artwork_reframe_and_limits(self):
        if not mgr.tools_ok():
            self.skipTest("FFmpeg در دسترس نیست")
        mgr.start()
        jid, d = mgr.new_dir()
        job = None
        run = lambda *a: subprocess.run([mgr.ffmpeg, "-y", "-loglevel", "error", *a], check=True)  # noqa: E731
        try:
            run("-f", "lavfi", "-i", "testsrc=size=1280x720:rate=25:duration=5", "-f", "lavfi", "-i", "sine=frequency=440:duration=5",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(d / "input.mp4"))
            run("-f", "lavfi", "-i", "color=c=orange:s=640x640:d=1", "-frames:v", "1", str(d / "c.png"))
            job = mgr._make_job(jid, "نمونه.mp4", ".mp4", "fa", "open")
            mgr.jobs[jid] = job
            w = mgr.measure(["سلام دنیا", "سلام دنیا این یک جملهٔ بلندتر است"], subs.merge_style(ST), 1280, 720)
            self.assertTrue(0 < w["سلام دنیا"] < w["سلام دنیا این یک جملهٔ بلندتر است"] < 1280)
            mgr.set_cover(jid, (d / "c.png").read_bytes(), ".png")
            mgr._make_proxy(job)
            job.status = "ready"
            ex = dict(reframe="9:16", cover_duration=1.5, cover_fit="fill", embed_artwork=True, formats=["mp4", "srt", "ass"])
            mgr.export(jid, dict(ST, word_mode="pop"), SEGS, ex)
            while job.status in ("render_queued", "rendering"):
                time.sleep(0.2)
            self.assertEqual(job.status, "done", job.error)
            mp4 = str(mgr.output / job.outputs["mp4"])
            info = json.loads(subprocess.run([mgr.ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", mp4],
                                             capture_output=True, text=True).stdout)
            v0 = info["streams"][0]
            self.assertEqual((v0["width"], v0["height"]), (1080, 1920))
            self.assertAlmostEqual(float(info["format"]["duration"]), 6.5, delta=0.3)  # ۵ ثانیه ویدیو + ۱٫۵ ثانیه کاور
            art = [s for s in info["streams"] if s.get("disposition", {}).get("attached_pic")]
            self.assertTrue(art and art[0]["codec_name"] == "mjpeg", "Artwork تعبیه نشده است")
            self.assertTrue((mgr.output / job.outputs["thumb"]).exists())
            self.assertFalse((d / "subs.ass").exists() or (d / "out.mp4").exists(), "فایل موقت پاک نشده")
            self.assertTrue((d / "input.mp4").exists())
            first = subs.parse_srt((mgr.output / job.outputs["srt"]).read_text("utf-8-sig"))[0]
            self.assertGreaterEqual(first["start"], 1.5)  # SRT با مدت کاور هماهنگ است
            self.assertEqual(mgr.frame(jid, 1.0, SEGS, dict(ST), dict(ex))[:2], b"\xff\xd8")  # فریم پیش‌نمایش JPEG
            for n in job.outputs.values():
                (mgr.output / n).unlink(missing_ok=True)
            long = d / "long.mp4"
            run("-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-f", "lavfi", "-i", "color=c=black:s=64x64:r=1", "-t", "310",
                "-c:v", "libx264", "-c:a", "aac", "-shortest", str(long))
            with self.assertRaises(core.UserError):
                mgr.probe(long)
        finally:
            for n in (job.outputs.values() if job else []):
                (mgr.output / n).unlink(missing_ok=True)
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
