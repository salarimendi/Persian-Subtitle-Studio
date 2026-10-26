"""وب‌اپ محلی زیرنویس فارسی — لایهٔ HTTP. منطق اصلی در core.py، subs.py و asr.py است."""
from __future__ import annotations

import os
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, File, Form, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import core
import subs

APP_DIR = Path(__file__).resolve().parent
MAX_UPLOAD = 3 * 1024**3
mgr = core.Manager(APP_DIR)
HEX = r"^#[0-9a-fA-F]{6}$"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    mgr.start()
    yield


app = FastAPI(title="زیرنویس فارسی محلی", lifespan=lifespan)


@app.exception_handler(core.UserError)
async def user_error(_req, exc: core.UserError):
    return JSONResponse({"detail": str(exc)}, status_code=400)


class Segment(BaseModel):
    start: float = Field(ge=0, le=36000)
    end: float = Field(ge=0, le=36000)
    text: str = Field(max_length=600)
    effect: Literal["none", "fadein", "fadeout", "fadeboth", "pop"] = "none"
    emphasis: str = Field("", max_length=80)
    words: Optional[list[tuple[float, float, str]]] = None


class Style(BaseModel):
    font: str = Field("Tahoma", pattern=r"^[\w \-]{1,40}$")
    size_pct: float = Field(5.5, ge=2, le=14)
    bold: bool = True
    color: str = Field("#ffffff", pattern=HEX)
    outline_color: str = Field("#000000", pattern=HEX)
    outline_w: float = Field(3, ge=0, le=14)
    shadow_w: float = Field(0, ge=0, le=12)
    shadow_color: str = Field("#000000", pattern=HEX)
    shadow_opacity: float = Field(0.6, ge=0, le=1)
    line_spacing: float = Field(1.2, ge=0.9, le=2.0)
    position: Literal["bottom", "middle", "top"] = "bottom"
    margin_pct: float = Field(6, ge=0, le=40)
    bg_on: bool = False
    bg_color: str = Field("#000000", pattern=HEX)
    bg_opacity: float = Field(0.6, ge=0.05, le=1)
    bg_radius: float = Field(0.3, ge=0, le=1)
    bg_pad_x: float = Field(0.45, ge=0, le=1.5)
    bg_pad_y: float = Field(0.12, ge=0, le=1)
    bg_border_w: float = Field(0, ge=0, le=10)
    bg_border_color: str = Field("#ffffff", pattern=HEX)
    emphasis_color: str = Field("#ffd400", pattern=HEX)
    word_mode: Literal["none", "active", "smooth", "progressive", "pop"] = "none"
    word_color: str = Field("#ffd400", pattern=HEX)
    effect_all: Literal["none", "fadein", "fadeout", "fadeboth", "pop"] = "none"
    max_chars: int = Field(32, ge=10, le=80)
    max_words: int = Field(0, ge=0, le=20)
    min_dur: float = Field(0.8, ge=0.1, le=5)
    gap: float = Field(0.08, ge=0, le=1)
    digits: Literal["keep", "persian", "latin"] = "keep"
    fix_punct: bool = True


class Export(BaseModel):
    reframe: Literal["original", "9:16", "1:1", "16:9"] = "original"
    cover_duration: float = Field(0, ge=0, le=10)
    cover_fit: Literal["fill", "fit"] = "fill"
    embed_artwork: bool = True
    formats: list[Literal["mp4", "srt", "ass"]] = Field(["mp4", "srt", "ass"], min_length=1)
    language: str = "fa"


class SaveBody(BaseModel):
    segments: list[Segment] = Field(max_length=3000)
    style: Style = Style()
    export: Export = Export()


class FrameBody(SaveBody):
    t: float = Field(0, ge=0)


def dump(b: SaveBody):
    return [s.model_dump() for s in b.segments], b.style.model_dump(), b.export.model_dump()


async def save_upload(file: UploadFile, dest: Path, limit: int) -> int:
    size = 0
    with open(dest, "wb") as out:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > limit:
                raise core.UserError("حجم فایل بیش از حد مجاز است.")
            out.write(chunk)
    return size


@app.get("/api/status")
def status():
    return mgr.status()


@app.get("/api/fonts")
def fonts():
    return {"builtin": ["Tahoma", "Segoe UI", "Arial", "Vazirmatn"], "custom": mgr.fonts_list()}


@app.get("/api/presets")
def presets():
    return {"default": subs.DEFAULT_STYLE, "presets": [{"id": k, "name": v["name"], "style": v["style"]} for k, v in subs.PRESETS.items()]}


@app.get("/api/projects")
def projects():
    return mgr.list_projects()


@app.post("/api/jobs")
async def upload(file: UploadFile = File(...), language: str = Form("fa")):
    jid, d = mgr.new_dir()
    ext = core.safe_ext(file.filename or "video")
    try:
        await save_upload(file, d / f"input{ext}", MAX_UPLOAD)
        job = await run_in_threadpool(mgr.register, jid, file.filename or "video", ext, language)
    except BaseException:
        shutil.rmtree(d, ignore_errors=True)
        raise
    return job.public()


@app.post("/api/open")
async def open_project(video: UploadFile = File(...), sub: Optional[UploadFile] = File(None), saved: str = Form(""),
                       shift: float = Form(0.0), overwrite: bool = Form(False)):
    """باز کردن پروژهٔ قبلی: ویدیو + (پروژهٔ ذخیره‌شده یا فایل .json/.srt/.ass/.txt) بدون رونویسی دوباره."""
    jid, d = mgr.new_dir()
    ext = core.safe_ext(video.filename or "video")
    try:
        await save_upload(video, d / f"input{ext}", MAX_UPLOAD)
        data = None
        if sub is not None and sub.filename:
            data = await sub.read()
            if len(data) > 20 * 1024 * 1024:
                raise core.UserError("فایل زیرنویس بیش از حد بزرگ است.")
        job = await run_in_threadpool(mgr.open_project, jid, video.filename or "video", ext, sub.filename if sub else None,
                                      data, saved or None, shift, overwrite)
    except BaseException:
        shutil.rmtree(d, ignore_errors=True)
        raise
    return job.public()


@app.get("/api/jobs")
def list_jobs():
    return mgr.list()


@app.get("/api/jobs/{jid}")
def get_job(jid: str):
    return mgr.get(jid).public(with_segments=True)


@app.put("/api/jobs/{jid}/segments")
def put_segments(jid: str, body: SaveBody):
    segs, st, ex = dump(body)
    mgr.save(jid, segs, st, ex)
    return mgr.get(jid).public(with_segments=True)


@app.post("/api/jobs/{jid}/render")
def render(jid: str, body: SaveBody):
    segs, st, ex = dump(body)
    mgr.export(jid, st, segs, ex)
    return mgr.get(jid).public()


@app.post("/api/jobs/{jid}/layout")
def layout(jid: str, body: SaveBody):
    segs, st, ex = dump(body)
    return mgr.layout_info(jid, segs, st, ex)


@app.post("/api/jobs/{jid}/frame")
def frame(jid: str, body: FrameBody):
    segs, st, ex = dump(body)
    return Response(mgr.frame(jid, body.t, segs, st, ex), media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.post("/api/jobs/{jid}/cover")
async def set_cover(jid: str, file: UploadFile = File(...)):
    data = await file.read()
    if len(data) > 30 * 1024 * 1024:
        raise core.UserError("تصویر کاور بیش از حد بزرگ است.")
    await run_in_threadpool(mgr.set_cover, jid, data, Path(file.filename or ".jpg").suffix)
    return mgr.get(jid).public()


@app.delete("/api/jobs/{jid}/cover")
def clear_cover(jid: str):
    mgr.clear_cover(jid)
    return mgr.get(jid).public()


@app.get("/api/jobs/{jid}/cover")
def get_cover(jid: str):
    p = mgr.get(jid).dir / "cover.jpg"
    if not p.exists():
        raise core.UserError("کاوری انتخاب نشده است.")
    return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.post("/api/jobs/{jid}/retry")
def retry(jid: str):
    mgr.retry(jid)
    return mgr.get(jid).public()


@app.delete("/api/jobs/{jid}")
def delete(jid: str):
    mgr.delete(jid)
    return {"ok": True}


@app.get("/api/jobs/{jid}/preview")
def preview(jid: str, request: Request):
    job = mgr.get(jid)
    if not job.has_preview:
        raise core.UserError("پیش‌نمایش هنوز آماده نیست.")
    status_code, data, headers = core.read_range(job.dir / "preview.mp4", request.headers.get("range"))
    return Response(data, status_code=status_code, media_type="video/mp4", headers=headers)


@app.get("/api/jobs/{jid}/download/{kind}")
def download(jid: str, kind: Literal["mp4", "srt", "ass", "txt", "project", "thumb"]):
    name = mgr.get(jid).outputs.get(kind)
    path = mgr.output / name if name else None
    if not path or not path.exists():
        raise core.UserError("این فایل هنوز ساخته نشده است.")
    return FileResponse(path, filename=path.name)


@app.post("/api/open-output")
def open_output():
    if not hasattr(os, "startfile"):
        raise core.UserError(f"پوشهٔ خروجی: {mgr.output}")
    os.startfile(mgr.output)  # type: ignore[attr-defined]
    return {"ok": True}


app.mount("/", StaticFiles(directory=APP_DIR / "static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
