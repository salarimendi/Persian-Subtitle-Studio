"""تشخیص گفتار فارسی روی یک فایل واقعی.
متغیرها: SAMPLE_AUDIO (صوت یا ویدیوی فارسی کوتاه، الزامی) و SAMPLE_REFERENCE (متن مرجع .txt، اختیاری)."""
import os
import re
import time
import unittest
from pathlib import Path

import _paths  # noqa: F401
import asr


class PersianASR(unittest.TestCase):
    def test_transcribe_persian_sample(self):
        sample = os.environ.get("SAMPLE_AUDIO")
        if not sample or not Path(sample).exists():
            self.skipTest("SAMPLE_AUDIO تنظیم نشده یا فایل وجود ندارد")
        if not asr.is_complete(asr.model_dir(_paths.ROOT / "models")):
            self.skipTest("مدل در models/ نیست (این تست دانلود نمی‌کند)")
        model = asr.load_model(_paths.ROOT / "models")
        t0 = time.time()
        segs = asr.transcribe(model, sample, language="fa")
        took = time.time() - t0
        text = " ".join(s["text"] for s in segs)
        self.assertTrue(segs, "هیچ بخشی تشخیص داده نشد")
        self.assertTrue(all(s["end"] > s["start"] for s in segs))
        letters = re.findall(r"\w", text)
        persian = re.findall("[\u0600-\u06FF]", text)
        self.assertGreater(len(persian) / max(1, len(letters)), 0.6, "خروجی عمدتاً فارسی نیست")
        print(f"\n  {len(segs)} بخش در {took:.0f} ثانیه | نمونه: {segs[0]['text']}")
        ref = os.environ.get("SAMPLE_REFERENCE")
        if ref and Path(ref).exists():
            r = Path(ref).read_text(encoding="utf-8")
            print(f"  WER={asr.wer(r, text):.1%}  CER={asr.cer(r, text):.1%}")
        else:
            print("  متن مرجع نیست؛ WER محاسبه نشد")


if __name__ == "__main__":
    unittest.main()
