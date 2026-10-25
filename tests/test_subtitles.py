"""تولید زیرنویس: SRT/ASS، افکت‌ها، ابزار WER، رندر MP4 و محدودیت ۵ دقیقه. به مدل نیاز ندارد."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import _paths  # noqa: F401
import asr
import core

STYLE = dict(font="DejaVu Sans" if not core.IS_WIN else "Tahoma", size_pct=6, color="#ffffff", outline_color="#000000",
             outline_w=3, bg_on=True, bg_color="#000000", bg_opacity=0.6, position="bottom", margin_pct=6, bold=True,
             emphasis_color="#ffd400")
SEGS = [
    {"start": 0.2, "end": 2.2, "text": "سلام، این یک آزمایش است 😀", "effect": "pop", "emphasis": "آزمایش"},
    {"start": 2.4, "end": 4.6, "text": "زیرنویس فارسی\nدر دو خط", "effect": "fadeboth", "emphasis": ""},
]
mgr = core.Manager(_paths.ROOT)


class Subtitles(unittest.TestCase):
    def test_srt_ass_and_text_tools(self):
        srt = core.make_srt(SEGS)
        self.assertTrue(srt.startswith("1\n00:00:00,200 --> 00:00:02,200\n"))
        ass = core.build_ass(SEGS, STYLE, 1280, 720, True)
        self.assertIn("\\t(0,180", ass); self.assertIn("\\fad(", ass); self.assertIn("😀", ass)
        self.assertNotIn("\\fad(", core.build_ass(SEGS, STYLE, 1280, 720, False))
        self.assertEqual(len(asr.group_words([(0, .5, "الف"), (.5, 1, "ب"), (3, 3.5, "ج")])), 2)
        self.assertEqual(asr.clean_text("كتاب ياد ٣ ، خوب"), "کتاب یاد ۳، خوب")
        self.assertEqual(asr.wer("سلام دنیا خوبی", "سلام دنیا"), 1 / 3)
        self.assertEqual(asr.wer("می‌شیم", "می شیم"), 0)  # نیم‌فاصله و فاصله یکسان حساب می‌شوند

    def test_render_mp4_and_limits(self):
        if not mgr.tools_ok():
            self.skipTest("FFmpeg در دسترس نیست")
        mgr.start()
        jid, d = mgr.new_dir()
        try:
            subprocess.run([mgr.ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=25:duration=5",
                            "-f", "lavfi", "-i", "sine=frequency=440:duration=5", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                            "-c:a", "aac", "-shortest", str(d / "input.mp4")], check=True)
            info = mgr.probe(d / "input.mp4")
            job = core.Job(jid, "نمونه.mp4", ".mp4", "fa", d, **info)
            job.segments = mgr._clean(SEGS, job.duration)
            job.render_req = {"style": STYLE, "effects_on": True}
            mgr.jobs[jid] = job
            mgr._render(job)
            self.assertGreater((mgr.output / job.outputs["mp4"]).stat().st_size, 10_000)
            self.assertTrue((d / "input.mp4").exists())
            self.assertFalse((d / "subs.ass").exists() or (d / "out.mp4").exists())
            for n in job.outputs.values():
                (mgr.output / n).unlink(missing_ok=True)
            long = d / "long.mp4"
            subprocess.run([mgr.ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-f", "lavfi",
                            "-i", "color=c=black:s=64x64:r=1", "-t", "310", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(long)], check=True)
            with self.assertRaises(core.UserError):
                mgr.probe(long)
            bad = d / "x.mp4"; bad.write_bytes(b"not a video")
            with self.assertRaises(core.UserError):
                mgr.probe(bad)
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
