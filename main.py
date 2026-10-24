"""وب‌اپ محلی زیرنویس فارسی — لایهٔ HTTP. منطق اصلی در core.py است."""
from __future__ import annotations

import os
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import core

APP_DIR = Path(__file__).resolve().parent
MAX_UPLOAD = 3 * 1024**3  # ۳ گیگابایت؛ مدت ۵ دقیقه جداگانه با ffprobe بررسی می‌شود
mgr = core.Manager(APP_DIR)


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
    text: str = Field(max_length=500)
    effect: Literal["none", "fadein", "fadeout", "fadeboth", "pop"] = "none"
    emphasis: str = Field("", max_length=60)


class Style(BaseModel):
    font: str = Field("Tahoma", pattern=r"^[\w \-]{1,40}$")
    size_pct: float = Field(5.5, ge=2, le=14)
    color: str = Field("#ffffff", pattern=r"^#[0-9a-fA-F]{6}$")
    outline_color: str = Field("#000000", pattern=r"^#[0-9a-fA-F]{6}$")
    outline_w: float = Field(3, ge=0, le=12)
    bg_on: bool = False
    bg_color: str = Field("#000000", pattern=r"^#[0-9a-fA-F]{6}$")
    bg_opacity: float = Field(0.6, ge=0.05, le=1)
    position: Literal["bottom", "middle", "top"] = "bottom"
    margin_pct: float = Field(6, ge=0, le=30)
    bold: bool = True
    emphasis_color: str = Field("#ffd400", pattern=r"^#[0-9a-fA-F]{6}$")


class SegmentsBody(BaseModel):
    segments: list[Segment] = Field(max_length=2000)


class RenderBody(SegmentsBody):
    style: Style
    effects_on: bool = True


@app.get("/api/status")
def status():
    return mgr.status()


@app.post("/api/jobs")
async def upload(file: UploadFile = File(...), language: str = Form("fa")):
    jid, d = mgr.new_dir()
    ext = core.safe_ext(file.filename or "video")
    try:
        size = 0
        with open(d / f"input{ext}", "wb") as out:
            while chunk := await file.read(1 << 20):
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise core.UserError("حجم فایل بیش از حد مجاز (۳ گیگابایت) است.")
                out.write(chunk)
        job = await run_in_threadpool(mgr.register, jid, file.filename or "video", ext, language)
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
def put_segments(jid: str, body: SegmentsBody):
    mgr.save_segments(jid, [s.model_dump() for s in body.segments])
    return mgr.get(jid).public(with_segments=True)


@app.post("/api/jobs/{jid}/render")
def render(jid: str, body: RenderBody):
    mgr.render(jid, body.style.model_dump(), [s.model_dump() for s in body.segments], body.effects_on)
    return mgr.get(jid).public()


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
def download(jid: str, kind: Literal["mp4", "srt", "ass"]):
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
