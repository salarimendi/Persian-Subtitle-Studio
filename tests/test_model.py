"""بارگذاری مدل فارسی. هرگز دانلود نمی‌کند؛ اگر مدل در models/ نباشد SKIP می‌شود."""
import unittest

import _paths  # noqa: F401
import asr


class ModelLoad(unittest.TestCase):
    def test_model_loads_and_supports_persian(self):
        d = asr.model_dir(_paths.ROOT / "models")
        if not asr.is_complete(d):
            self.skipTest(f"مدل در {d} نیست؛ یک‌بار برنامه یا tests/compare_models.py را اجرا کنید تا دانلود شود")
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            self.skipTest("faster-whisper نصب نیست")
        model = asr.load_model(_paths.ROOT / "models")
        langs = getattr(model, "supported_languages", None)
        if langs is not None:
            self.assertIn("fa", langs)
        print(f"\n  مدل {asr.MODEL_REPO} با compute_type={asr.COMPUTE_TYPE} بارگذاری شد")


if __name__ == "__main__":
    unittest.main()
