"""مقایسهٔ مدل‌ها روی یک فایل فارسی یکسان (زمان، WER و CER).
اجرا:  python tests/compare_models.py فایل_صوتی_یا_ویدیو.mp4 --ref متن_مرجع.txt
مدل‌ها در models/ دانلود می‌شوند (فقط اگر نباشند). بدون --ref فقط زمان و متن چاپ می‌شود، نه WER.
پیش‌فرض: مدل قبلی (small) و مدل فعلی برنامه."""
import argparse
import time
from pathlib import Path

import _paths  # noqa: F401
import asr

ap = argparse.ArgumentParser()
ap.add_argument("audio")
ap.add_argument("--ref", help="فایل متنی UTF-8 با گفتار درست (برای WER)")
ap.add_argument("--models", nargs="+", default=["Systran/faster-whisper-small", asr.DEFAULT_MODEL])
a = ap.parse_args()
ref = Path(a.ref).read_text(encoding="utf-8") if a.ref else None
rows = []
for repo in a.models:
    print(f"\n== {repo}", flush=True)
    t0 = time.time()
    model = asr.load_model(_paths.ROOT / "models", repo, on_status=lambda m: print("  ", m, end="\r", flush=True))
    load = time.time() - t0
    t0 = time.time()
    text = " ".join(s["text"] for s in asr.transcribe(model, a.audio, language="fa"))
    took = time.time() - t0
    print(f"  متن: {text[:300]}")
    rows.append((repo, load, took, asr.wer(ref, text) if ref else None, asr.cer(ref, text) if ref else None))
    del model
print("\nمدل | بارگذاری(s) | رونویسی(s) | WER | CER")
for r in rows:
    print(f"{r[0]} | {r[1]:.0f} | {r[2]:.0f} | " + (f"{r[3]:.1%} | {r[4]:.1%}" if r[3] is not None else "- | - (متن مرجع نداده شد)"))
