"""منطق مشترک نمایش زیرنویس: استایل، قالب‌های آماده، شکست خط، زمان‌بندی، ASS/SRT/TXT و ورودی‌گیری.
هم پیش‌نمایش و هم خروجی نهایی از همین ماژول می‌آیند. به FFmpeg و FastAPI وابسته نیست."""
from __future__ import annotations

import base64
import json
import math
import re
import struct
from pathlib import Path

EMOJI_FONT_WIN, EMOJI_FONT_OTHER = "Segoe UI Emoji", "Noto Color Emoji"
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\u2300-\u23FF\uFE0F\u200D]+")
ARABIC_RE = re.compile("[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]")

DEFAULT_STYLE = dict(
    font="Tahoma", size_pct=5.5, bold=True, color="#ffffff", outline_color="#000000", outline_w=3,
    shadow_w=0, shadow_color="#000000", shadow_opacity=0.6, line_spacing=1.2, position="bottom", margin_pct=6,
    bg_on=False, bg_color="#000000", bg_opacity=0.6, bg_radius=0.3, bg_pad_x=0.45, bg_pad_y=0.12,
    bg_border_w=0, bg_border_color="#ffffff", emphasis_color="#ffd400",
    word_mode="none", word_color="#ffd400", effect_all="none",
    max_chars=32, max_words=0, min_dur=0.8, gap=0.08, digits="keep", fix_punct=True,
)
DEFAULT_EXPORT = dict(reframe="original", cover_duration=0.0, cover_fit="fill", embed_artwork=True,
                      formats=["mp4", "srt", "ass"], language="fa")

PRESETS = {
    "cinematic": dict(name="سینمایی", style=dict(font="Segoe UI", bold=False, size_pct=4.8, color="#f3ead7", outline_w=1.6,
        shadow_w=2.5, shadow_opacity=0.7, line_spacing=1.25, margin_pct=9, effect_all="fadeboth", max_chars=38, min_dur=1.0)),
    "documentary": dict(name="مستند", style=dict(font="Tahoma", bold=True, size_pct=5.0, outline_w=0, bg_on=True,
        bg_color="#0b1d2a", bg_opacity=0.72, bg_radius=0.25, bg_pad_x=0.5, bg_pad_y=0.14, margin_pct=7, max_chars=36)),
    "social": dict(name="شبکه‌های اجتماعی", style=dict(font="Tahoma", bold=True, size_pct=7.0, outline_w=6, shadow_w=3,
        line_spacing=1.1, margin_pct=20, word_mode="pop", word_color="#ffd400", max_chars=22, min_dur=0.5, gap=0.04)),
    "minimal": dict(name="مینیمال روشن", style=dict(font="Segoe UI", bold=False, size_pct=4.8, color="#111827", outline_w=0,
        bg_on=True, bg_color="#ffffff", bg_opacity=0.92, bg_radius=0.5, bg_pad_x=0.6, bg_pad_y=0.16, margin_pct=8, max_chars=34)),
    "karaoke": dict(name="کاراوکه", style=dict(font="Tahoma", bold=True, size_pct=6.0, color="#e8e8ff", outline_color="#1b1340",
        outline_w=4, shadow_w=2, margin_pct=14, word_mode="progressive", word_color="#ff4fa3", max_chars=26)),
}
for _p in PRESETS.values():
    _p["style"] = {**DEFAULT_STYLE, **_p["style"]}


def merge_style(d: dict | None) -> dict:
    return {**DEFAULT_STYLE, **{k: v for k, v in (d or {}).items() if k in DEFAULT_STYLE}}


def merge_export(d: dict | None) -> dict:
    return {**DEFAULT_EXPORT, **{k: v for k, v in (d or {}).items() if k in DEFAULT_EXPORT}}


def out_dims(w: int, h: int, reframe: str) -> tuple[int, int]:
    return {"9:16": (1080, 1920), "1:1": (1080, 1080), "16:9": (1920, 1080)}.get(reframe, (w // 2 * 2, h // 2 * 2))


def unit(w: int, h: int) -> float:
    """واحد طراحی: برای ۱۶:۹ برابر ارتفاع است؛ برای عمودی و مربعی متناسب مساحت تصویر می‌شود."""
    return 1080 * math.sqrt(w * h) / 1440


# ---------------------------------------------------------------- متن نمایشی
_FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
_EN = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def display(tok: str, st: dict) -> str:
    if st["digits"] == "persian":
        tok = tok.translate(_FA)
    elif st["digits"] == "latin":
        tok = tok.translate(_EN)
    if st["fix_punct"]:
        tok = re.sub(r"(?<=[\u0600-\u06FF])\?", "؟", tok)
        tok = re.sub(r"(?<=[\u0600-\u06FF]),", "،", tok)
        tok = re.sub(r"(?<=[\u0600-\u06FF]);", "؛", tok)
    return tok


def norm_words(seg: dict) -> list[tuple[float, float, str]]:
    toks = seg["text"].split()
    s, e = seg["start"], seg["end"]
    ws = seg.get("words") or []
    if ws and len(ws) == len(toks) and ws[-1][1] - ws[0][0] > 1e-6:
        a, f = ws[0][0], (e - s) / (ws[-1][1] - ws[0][0])
        return [(s + (w[0] - a) * f, s + (w[1] - a) * f, t) for w, t in zip(ws, toks)]
    total, pos, out = sum(len(t) + 1 for t in toks) or 1, 0, []
    for t in toks:
        a = s + (e - s) * pos / total
        pos += len(t) + 1
        out.append((a, s + (e - s) * pos / total, t))
    return out


def _fits(toks, maxc, maxw):
    return len(" ".join(toks)) <= maxc and (maxw <= 0 or len(toks) <= maxw)


def split_lines(toks: list[str], maxc: int, maxw: int) -> list[list[list[int]]]:
    """خروجی: فهرست بخش‌ها؛ هر بخش حداکثر دو خط (فهرست اندیس کلمه) است و دو خط متوازن می‌شوند."""
    lines, cur = [], []
    for i, t in enumerate(toks):
        if cur and not _fits([toks[j] for j in cur] + [t], maxc, maxw):
            lines.append(cur)
            cur = []
        cur.append(i)
    if cur:
        lines.append(cur)
    chunks = [lines[k:k + 2] for k in range(0, len(lines), 2)]
    for ch in chunks:
        if len(ch) == 2:
            allx, best = ch[0] + ch[1], None
            for k in range(1, len(allx)):
                a, b = allx[:k], allx[k:]
                if _fits([toks[j] for j in a], maxc, maxw) and _fits([toks[j] for j in b], maxc, maxw):
                    sc = abs(len(" ".join(toks[j] for j in a)) - len(" ".join(toks[j] for j in b)))
                    if best is None or sc < best[0]:
                        best = (sc, a, b)
            if best:
                ch[0], ch[1] = best[1], best[2]
    return chunks


def retime(evs: list[dict], st: dict):
    """حداقل مدت نمایش و فاصلهٔ بین زیرنویس‌ها؛ هیچ دو زیرنویسی هم‌زمان دیده نمی‌شوند."""
    evs.sort(key=lambda e: e["start"])
    for i, e in enumerate(evs):
        end = max(e["end"], e["start"] + st["min_dur"])
        floor = e["start"] + 0.05
        if i + 1 < len(evs):
            end = min(end, evs[i + 1]["start"] - st["gap"])
            floor = min(floor, evs[i + 1]["start"])  # در ورودی‌های بسیار فشرده هم هم‌پوشانی ایجاد نمی‌شود
        e["end"] = max(end, floor)


def layout(segments: list[dict], st: dict, w: int = 1920, h: int = 1080) -> list[dict]:
    ratio = w / h
    orient = 1.0 if ratio > 1.2 else 0.65 if ratio < 0.8 else 0.8
    maxc, maxw = max(8, round(st["max_chars"] * orient)), int(st["max_words"])
    evs = []
    for seg in sorted(segments, key=lambda s: s["start"]):
        text = seg["text"].strip()
        if not text or seg["end"] <= seg["start"]:
            continue
        words = norm_words(seg)
        toks = [display(x[2], st) for x in words]
        eff = seg.get("effect", "none")
        eff = eff if eff != "none" else st["effect_all"]
        emph, et = set(), [display(x, st) for x in seg.get("emphasis", "").split()]
        for i in range(len(toks) - len(et) + 1 if et else 0):
            if toks[i:i + len(et)] == et:
                emph = set(range(i, i + len(et)))
                break
        if "\n" in text:
            lines, pos = [], 0
            for ln in text.split("\n"):
                n = len(ln.split())
                if n:
                    lines.append(list(range(pos, pos + n)))
                    pos += n
            chunks = [lines[k:k + 2] for k in range(0, len(lines), 2)]
        else:
            chunks = split_lines(toks, maxc, maxw)
        for ci, ch in enumerate(chunks):
            idx = [i for ln in ch for i in ln]
            evs.append(dict(
                start=seg["start"] if ci == 0 else words[idx[0]][0],
                end=seg["end"] if ci == len(chunks) - 1 else words[idx[-1]][1], effect=eff,
                lines=[[(words[i][0], words[i][1], toks[i], i in emph) for i in ln] for ln in ch]))
    retime(evs, st)
    return evs


def line_text(line) -> str:
    return " ".join(t[2] for t in line)


# ---------------------------------------------------------------- ASS
def _bgr(c: str) -> str:
    c = c.lstrip("#")
    return f"{c[4:6]}{c[2:4]}{c[0:2]}".upper()


def _alpha(opacity: float) -> str:
    return "%02X" % round((1 - opacity) * 255)


def ass_time(t: float) -> str:
    cs = max(0, int(round(t * 100)))
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02}:{s:02}.{cs:02}"


def esc(text: str, font: str, emoji_font: str) -> str:
    text = text.replace("\\", "/").replace("{", "\\{").replace("}", "\\}")
    return EMOJI_RE.sub(lambda m: "{\\fn%s}%s{\\fn%s}" % (emoji_font, m.group(), font), text)


def _rr(w, h, r, ccw=False, ox=0.0, oy=0.0):
    k, f = r * 0.4477, lambda *p: " ".join(f"{v:.1f}" for v in p)
    x0, y0, x1, y1 = ox, oy, ox + w, oy + h
    if not ccw:
        return (f"m {f(x0 + r, y0)} l {f(x1 - r, y0)} b {f(x1 - k, y0, x1, y0 + k, x1, y0 + r)} l {f(x1, y1 - r)} "
                f"b {f(x1, y1 - k, x1 - k, y1, x1 - r, y1)} l {f(x0 + r, y1)} b {f(x0 + k, y1, x0, y1 - k, x0, y1 - r)} "
                f"l {f(x0, y0 + r)} b {f(x0, y0 + k, x0 + k, y0, x0 + r, y0)}")
    return (f"m {f(x0 + r, y0)} b {f(x0 + k, y0, x0, y0 + k, x0, y0 + r)} l {f(x0, y1 - r)} "
            f"b {f(x0, y1 - k, x0 + k, y1, x0 + r, y1)} l {f(x1 - r, y1)} b {f(x1 - k, y1, x1, y1 - k, x1, y1 - r)} "
            f"l {f(x1, y0 + r)} b {f(x1, y0 + k, x1 - k, y0, x1 - r, y0)} l {f(x0 + r, y0)}")


def _head(w, h, style_line, note="") -> list[str]:
    fmt = ("Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,"
           "ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding")
    return ["[Script Info]", *([note] if note else []), "ScriptType: v4.00+", f"PlayResX: {w}", f"PlayResY: {h}", "WrapStyle: 2",
            "ScaledBorderAndShadow: yes", "", "[V4+ Styles]", f"Format: {fmt}", style_line, "",
            "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]


def nominal_size(st: dict, w: int, h: int) -> float:
    return st["size_pct"] / 100 * unit(w, h)


def _rlm(text: str) -> str:
    return "\u200f" if ARABIC_RE.search(text) else ""  # جهت پایهٔ راست‌به‌چپ حتی اگر خط با لاتین یا ایموجی شروع شود


def measure_ass(texts: list[str], st: dict, w: int, h: int, size: float, emoji_font: str) -> str:
    sl = (f"Style: M,{st['font']},{size:.1f},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,{-1 if st['bold'] else 0},0,0,0,100,100,0,0,1,0,0,5,0,0,0,-1")
    ev = [f"Dialogue: 0,{ass_time(i * 0.1)},{ass_time(i * 0.1 + 0.1)},M,,0,0,0,,{{\\an5\\pos({w // 2},{h // 2})\\q2}}"
          f"{_rlm(t)}{esc(t, st['font'], emoji_font)}" for i, t in enumerate(texts)]
    return "\n".join(_head(w, h, sl) + ev) + "\n"


def build_ass(events: list[dict], st: dict, w: int, h: int, widths: dict, emoji_font: str,
              shift: float = 0.0, project: dict | None = None) -> str:
    u = unit(w, h)
    size = nominal_size(st, w, h)
    bg = st["bg_on"]
    est = lambda t: widths.get(t) or len(t) * size * 0.55
    need = max([est(line_text(ln)) for e in events for ln in e["lines"]] or [0]) + (2 * st["bg_pad_x"] * size if bg else 0)
    sc = max(0.5, min(1.0, 0.92 * w / need)) if need > 0 else 1.0  # کوچک‌کردن خودکار اگر خط از عرض تصویر بیشتر شد
    size *= sc
    outline, shadow, mg = st["outline_w"] * u / 1080, st["shadow_w"] * u / 1080, h * st["margin_pct"] / 100
    padx, pady, rad = st["bg_pad_x"] * size, st["bg_pad_y"] * size, st["bg_radius"] * size
    bw = st["bg_border_w"] * u / 1080
    line_h = size * 1.2
    box_h = line_h + 2 * pady
    pitch = max(size * st["line_spacing"], box_h + 0.1 * size) if bg else size * st["line_spacing"]
    cell = box_h if bg else line_h
    prim, outl = _bgr(st["color"]), _bgr(st["outline_color"])
    sl = (f"Style: Main,{st['font']},{size:.1f},&H00{prim},&H00{prim},&H00{outl},&H{_alpha(st['shadow_opacity'])}{_bgr(st['shadow_color'])},"
          f"{-1 if st['bold'] else 0},0,0,0,100,100,0,0,1,{outline:.1f},{shadow:.1f},5,0,0,0,-1")
    note = "; SUBPROJECT:" + base64.b64encode(json.dumps(project, ensure_ascii=False).encode()).decode() if project else ""
    out = _head(w, h, sl, note)
    mode, wc, norm = st["word_mode"], _bgr(st["word_color"]), prim
    for ev in events:
        a, b = ev["start"] + shift, ev["end"] + shift
        dur = ev["end"] - ev["start"]
        f = int(min(300, dur * 500))
        fad = {"fadein": f"\\fad({f},0)", "fadeout": f"\\fad(0,{f})", "fadeboth": f"\\fad({f},{f})"}.get(ev["effect"], "")
        pop = "\\fscx70\\fscy70\\t(0,180,\\fscx115\\fscy115)\\t(180,320,\\fscx100\\fscy100)" if ev["effect"] == "pop" else ""
        n = len(ev["lines"])
        for k, line in enumerate(ev["lines"]):
            if st["position"] == "bottom":
                cy = h - mg - cell / 2 - (n - 1 - k) * pitch
            elif st["position"] == "top":
                cy = mg + cell / 2 + k * pitch
            else:
                cy = h / 2 + (k - (n - 1) / 2) * pitch
            cy = min(max(cy, cell / 2), h - cell / 2)
            text = line_text(line)
            if bg:
                bwid = est(text) * sc + 2 * padx
                x0, y0 = w / 2 - bwid / 2, cy - box_h / 2
                dr = f"{{\\an7\\pos({x0:.1f},{y0:.1f})\\p1\\bord0\\shad0\\1c&H{_bgr(st['bg_color'])}&\\1a&H{_alpha(st['bg_opacity'])}&{fad}}}"
                out.append(f"Dialogue: 0,{ass_time(a)},{ass_time(b)},Main,,0,0,0,,{dr}{_rr(bwid, box_h, rad)}{{\\p0}}")
                if bw > 0:
                    rg = (f"{{\\an7\\pos({x0 - bw:.1f},{y0 - bw:.1f})\\p1\\bord0\\shad0\\1c&H{_bgr(st['bg_border_color'])}&{fad}}}"
                          f"{_rr(bwid + 2 * bw, box_h + 2 * bw, rad + bw)} {_rr(bwid, box_h, rad, True, bw, bw)}{{\\p0}}")
                    out.append(f"Dialogue: 0,{ass_time(a)},{ass_time(b)},Main,,0,0,0,,{rg}")
            head = f"{{\\an5\\pos({w / 2:.1f},{cy:.1f})\\q2{fad}"
            simple = mode == "none" and not any(t[3] for t in line) and not pop
            if simple:
                body = head + "}" + _rlm(text) + esc(text, st["font"], emoji_font)
            else:
                parts = []
                for j, (ws_, we_, tok, em) in enumerate(line):
                    nxt = line[j + 1][0] if j + 1 < len(line) else ev["end"]
                    t0, t1 = max(0, int((ws_ - ev["start"]) * 1000)), max(1, int((max(nxt, we_) - ev["start"]) * 1000))
                    tg = "\\r" + pop
                    if em:
                        tg += f"\\1c&H{_bgr(st['emphasis_color'])}&\\b1"
                    if mode in ("active", "smooth", "progressive", "pop"):
                        d = 120 if mode == "smooth" else 1
                        grow = "\\fscx115\\fscy115" if mode == "pop" else ""
                        rst = "\\fscx100\\fscy100" if grow else ""
                        back = "" if mode == "progressive" else f"\\t({t1},{t1 + d},\\1c&H{norm}&{rst})"
                        tg += f"\\t({t0},{t0 + d},\\1c&H{wc}&{grow}){back}"
                    parts.append("{" + tg + "}" + esc(tok, st["font"], emoji_font))
                body = head + "}" + _rlm(text) + " ".join(parts)
            out.append(f"Dialogue: 1,{ass_time(a)},{ass_time(b)},Main,,0,0,0,,{body}")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- SRT / TXT
def srt_time(t: float) -> str:
    ms = max(0, int(round(t * 1000)))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def make_srt(events: list[dict], shift: float = 0.0) -> str:
    return "\n".join(f"{i}\n{srt_time(e['start'] + shift)} --> {srt_time(e['end'] + shift)}\n" + "\n".join(line_text(l) for l in e["lines"]) + "\n"
                     for i, e in enumerate(events, 1))


def make_txt(segments: list[dict]) -> str:
    return "\n".join(f"[{srt_time(s['start']).replace(',', '.')} --> {srt_time(s['end']).replace(',', '.')}] " + s["text"].strip().replace("\n", "\\n")
                     for s in sorted(segments, key=lambda s: s["start"]) if s["text"].strip()) + "\n"


# ---------------------------------------------------------------- ورودی‌گیری
def decode_bytes(b: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16"):
        try:
            return b.decode(enc)
        except UnicodeError:
            pass
    return b.decode("cp1256", errors="replace")


def _secs(h, m, s, f) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(f.ljust(3, "0")[:3]) / 1000


def _seg(a, b, t):
    return {"start": round(a, 3), "end": round(b, 3), "text": t, "effect": "none", "emphasis": ""}


_TIME = r"(\d+):(\d+):(\d+)[,.](\d+)"


def parse_srt(text: str) -> list[dict]:
    out = []
    for blk in re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n").strip()):
        lines = blk.split("\n")
        for i, ln in enumerate(lines):
            m = re.search(_TIME + r"\s*-->\s*" + _TIME, ln)
            if m:
                t = "\n".join(re.sub(r"<[^>]+>|\{\\[^}]*\}", "", x).strip() for x in lines[i + 1:] if x.strip())
                out.append(_seg(_secs(*m.groups()[:4]), _secs(*m.groups()[4:]), t))
                break
    return out


def parse_txt(text: str, duration: float) -> tuple[list[dict], bool]:
    out = []
    for ln in text.splitlines():
        m = re.match(r"\s*\[" + _TIME + r"\s*-->\s*" + _TIME + r"\]\s*(.*)", ln)
        if m:
            out.append(_seg(_secs(*m.groups()[:4]), _secs(*m.groups()[4:8]), m.group(9).replace("\\n", "\n")))
    if out:
        return out, True
    rows = [ln.strip() for ln in text.splitlines() if ln.strip()]
    total, pos = sum(len(r) for r in rows) or 1, 0
    for r in rows:
        out.append(_seg(duration * pos / total, duration * (pos + len(r)) / total, r))
        pos += len(r)
    return out, False


def parse_ass(text: str) -> dict:
    m = re.search(r"^;\s*SUBPROJECT:(\S+)", text, re.M)
    if m:
        try:
            return json.loads(base64.b64decode(m.group(1)).decode())
        except Exception:  # noqa: BLE001
            pass
    fmt = next((ln.split(":", 1)[1].split(",") for ln in text.splitlines() if ln.startswith("Format:") and "Text" in ln), None)
    cols = [c.strip() for c in fmt] if fmt else ["Layer", "Start", "End", "Style", "Name", "MarginL", "MarginR", "MarginV", "Effect", "Text"]
    groups: dict[tuple, list[str]] = {}
    for ln in text.splitlines():
        if not ln.startswith("Dialogue:"):
            continue
        p = ln.split(":", 1)[1].lstrip().split(",", len(cols) - 1)
        if len(p) < len(cols) or "\\p1" in p[-1]:
            continue
        t = re.sub(r"\{[^}]*\}", "", p[-1]).replace("\\N", "\n").replace("\\n", "\n").replace("\\h", " ").replace("\u200f", "").strip()
        tm = [re.match(r"(\d+):(\d+):(\d+)[.](\d+)", p[cols.index(c)].strip()) for c in ("Start", "End")]
        if t and all(tm):
            key = (_secs(*tm[0].groups()), _secs(*tm[1].groups()))
            if t not in groups.setdefault(key, []):
                groups[key].append(t)
    return {"segments": [_seg(a, b, "\n".join(v)) for (a, b), v in sorted(groups.items())]}


def parse_project(text: str) -> dict:
    d = json.loads(text)
    if not isinstance(d, dict) or "segments" not in d:
        raise ValueError("فایل پروژه معتبر نیست.")
    return d


# ---------------------------------------------------------------- فونت‌های پوشهٔ fonts
def font_family(path: Path) -> str | None:
    try:
        data = path.read_bytes()
        n = struct.unpack(">H", data[4:6])[0]
        for i in range(n):
            tag, _cs, off, ln = struct.unpack(">4sIII", data[12 + 16 * i:28 + 16 * i])
            if tag == b"name":
                cnt, so = struct.unpack(">HH", data[off + 2:off + 6])
                best = None
                for j in range(cnt):
                    pid, eid, lid, nid, l, o = struct.unpack(">6H", data[off + 6 + 12 * j:off + 18 + 12 * j])
                    if nid in (1, 16) and pid == 3:
                        s = data[off + so + o:off + so + o + l].decode("utf-16-be", "ignore")
                        if best is None or nid == 16:
                            best = s
                return best
    except Exception:  # noqa: BLE001
        return None
    return None
