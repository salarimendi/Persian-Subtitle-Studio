# زیرنویس فارسی محلی

تبدیل گفتار ویدیو به زیرنویس فارسی (faster-whisper روی CPU) و چسباندن آن به تصویر با FFmpeg. همه‌چیز محلی است.

## مدل تشخیص گفتار
- پیش‌فرض: **`oi-uae/farsi-faster-whisper-large-v3`**؛ تبدیل CTranslate2 (float16) از یک فاین‌تیون whisper-large-v3 روی Common Voice 17 فارسی. طبق صفحهٔ مدل، WER روی تست Common Voice 17 فارسی حدود ۲۳٫۵٪ گزارش شده است.
- دانلود: حدود **۳ گیگابایت**، هنگام اولین تبدیل (نه هنگام `pip install`). مسیر ذخیره: `models/farsi-faster-whisper-large-v3/`. بعد از آن بدون اینترنت و بدون دانلود مجدد.
- حافظه و سرعت: با `int8` روی CPU حدود ۳ گیگابایت RAM لازم است و بسیار کندتر از مدل `small` اجرا می‌شود.
- تنظیمات (متغیر محیطی، اختیاری): `SUBTITLE_MODEL` (مدل CT2 دیگر، مثلاً `Systran/faster-whisper-large-v3`)، `SUBTITLE_COMPUTE` (`int8` پیش‌فرض یا `float32`)، `SUBTITLE_THREADS`.
- روش رمزگشایی: زبان `fa`، beam=5، `condition_on_previous_text=False`، فیلتر VAD و temperature fallback. تنها اصلاح خودکار متن، یکسان‌سازی املایی بی‌خطر است (ي/ك عربی، ارقام عربی، فاصلهٔ اضافه)؛ هیچ واژه‌ای حدس زده یا جایگزین نمی‌شود. بخش‌هایی با تکرار غیرعادی یک کلمه فقط هشدار می‌گیرند.

## نصب و اجرا
با محیط مجازی مشترک در یک شاخه بالاتر:
```
cd D:\Projects
py -3 -m venv .venv
.venv\Scripts\python -m pip install -r subtitle-app\requirements.txt
cd subtitle-app
..\.venv\Scripts\python main.py
```

..\SubVENV\Scripts\activate
python main.py

سپس `http://127.0.0.1:8000` را باز کنید. FFmpeg لازم است: `winget install Gyan.FFmpeg`. (`start.bat` همین کار را خودکار انجام می‌دهد و اگر `..\.venv` باشد از آن استفاده می‌کند.)

## استفاده
ویدیو (حداکثر ۵ دقیقه) را رها کنید، متن را ویرایش کنید، ایموجی/افکت/ظاهر را تنظیم کنید و «چسباندن زیرنویس و ساخت MP4» را بزنید. خروجی‌ها در `output/`: ‏`_sub.mp4`، `.srt`، `.ass`. ویدیوی اصلی دست‌نخورده می‌ماند.

## مقایسهٔ دقت روی فایل خودتان
```
..\.venv\Scripts\python tests\compare_models.py نمونه.mp4 --ref متن_درست.txt
```
مدل قبلی (`small`) و مدل فعلی را روی همان فایل اجرا می‌کند و زمان، WER و CER را چاپ می‌کند. بدون `--ref` فقط زمان و متن چاپ می‌شود. برای نتیجهٔ معتبر، فایلی با گفتار واقعی خودتان و متن مرجع دستی استفاده کنید.

## آزمون
```
..\.venv\Scripts\python -m unittest discover -s tests -v
```
آزمون مدل و آزمون فارسی (`SAMPLE_AUDIO`، اختیاری `SAMPLE_REFERENCE`) اگر پیش‌نیازشان نباشد SKIP می‌شوند و هرگز مدل را دانلود نمی‌کنند.

## ساختار
`main.py` (API)، `core.py` (صف، SRT/ASS، FFmpeg)، `asr.py` (مدل، رونویسی، WER)، `static/`، `tests/`، `models/`، `fonts/`، `output/`.

## محدودیت‌ها
ایموجی در MP4 ممکن است تک‌رنگ باشد (libass). فونت پیش‌فرض Tahoma است؛ برای Vazirmatn فایل `.ttf` را در `fonts/` بگذارید.
