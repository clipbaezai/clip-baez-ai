from pathlib import Path
from uuid import uuid4
from urllib.parse import urlparse
import threading
import subprocess
import json
import re

from fastapi import FastAPI, BackgroundTasks, HTTPException, UploadFile, File, Form
import shutil
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi import Request
from pydantic import BaseModel, Field
import yt_dlp

BASE = Path(__file__).resolve().parent
UPLOADS = BASE / "media" / "uploads"
CLIPS = BASE / "media" / "clips"
for folder in (UPLOADS, CLIPS):
    folder.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="CLIP BAEZ AI")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
app.mount("/media/clips", StaticFiles(directory=CLIPS), name="clips")
templates = Jinja2Templates(directory=BASE / "templates")

jobs = {}
jobs_lock = threading.Lock()

ALLOWED_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}

class ClipRequest(BaseModel):
    url: str
    clip_seconds: int = Field(default=30, ge=5, le=180)
    max_clips: int = Field(default=18, ge=1, le=18)

def valid_youtube_url(value: str) -> bool:
    try:
        parsed = urlparse(value.strip())
        host = (parsed.hostname or "").lower()
        return parsed.scheme in ("http", "https") and host in ALLOWED_HOSTS
    except Exception:
        return False

def set_job(job_id, **updates):
    with jobs_lock:
        jobs[job_id].update(updates)

def run_job(job_id, request_data):
    try:
        set_job(job_id, status="downloading", message="Descargando video de YouTube…")
        output_template = str(UPLOADS / f"{job_id}.%(ext)s")
        ydl_opts = {
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "merge_output_format": "mp4",
            "outtmpl": output_template,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(request_data["url"], download=True)
            title = info.get("title", "Video")
            downloaded = Path(ydl.prepare_filename(info))
            # yt-dlp may change extension after merging.
            candidates = list(UPLOADS.glob(f"{job_id}.*"))
            video_path = next((p for p in candidates if p.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"}), None)
            if video_path is None:
                raise RuntimeError("No se encontró el archivo de video después de descargarlo.")

        set_job(job_id, status="processing", message="Preparando clips verticales…", title=title)
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
            capture_output=True, text=True, check=True
        )
        duration = float(probe.stdout.strip())
        clip_seconds = request_data["clip_seconds"]
        possible = max(1, int((duration + clip_seconds - 1) // clip_seconds))
        count = min(request_data["max_clips"], possible)
        made = []

        for index in range(count):
            start = index * clip_seconds
            remaining = duration - start
            length = min(clip_seconds, remaining)
            if length <= 0:
                break
            out = CLIPS / f"{job_id}_clip_{index+1:02d}.mp4"
            cmd = [
                "ffmpeg", "-y", "-ss", str(start), "-i", str(video_path),
                "-t", str(length),
                "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
                "-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p",
                "-preset", "veryfast", "-movflags", "+faststart", str(out)
            ]
            subprocess.run(cmd, capture_output=True, text=True, check=True)
            made.append({
                "name": out.name,
                "url": f"/media/clips/{out.name}",
                "number": index + 1,
                "seconds": round(length, 1),
            })

        set_job(job_id, status="completed", message=f"Listo: {len(made)} clips creados.", clips=made)
    except Exception as exc:
        set_job(job_id, status="error", message=str(exc), clips=[])

def run_uploaded_job(job_id, video_path, clip_seconds, max_clips):
    try:
        set_job(job_id, status="processing", message="Preparando clips verticales…")
        video_path = Path(video_path)

        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
            capture_output=True, text=True, check=True
        )
        duration = float(probe.stdout.strip())
        count = min(max_clips, max(1, int((duration + clip_seconds - 1) // clip_seconds)))
        made = []

        for index in range(count):
            start = index * clip_seconds
            length = min(clip_seconds, duration - start)
            if length <= 0:
                break

            out = CLIPS / f"{job_id}_clip_{index+1:02d}.mp4"
            subprocess.run([
                "ffmpeg", "-y", "-ss", str(start), "-i", str(video_path),
                "-t", str(length),
                "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
                "-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p",
                "-preset", "veryfast", "-movflags", "+faststart", str(out)
            ], capture_output=True, text=True, check=True)

            made.append({
                "name": out.name,
                "url": f"/media/clips/{out.name}",
                "number": index + 1,
                "seconds": round(length, 1),
            })

        set_job(job_id, status="completed", message=f"Listo: {len(made)} clips creados.", clips=made)

    except Exception as exc:
        set_job(job_id, status="error", message=str(exc), clips=[])
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(request=request, name="index.html", context={"request": request})

@app.post("/api/upload")
async def upload_video(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    clip_seconds: int = Form(30),
    max_clips: int = Form(18)
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="Selecciona un video.")

    extension = Path(file.filename).suffix.lower()
    if extension not in {".mp4", ".mov", ".mkv", ".webm"}:
        raise HTTPException(status_code=400, detail="Formato no permitido.")

    if clip_seconds not in {15, 30, 45, 60, 90} or max_clips not in {5, 10, 18}:
        raise HTTPException(status_code=400, detail="Configuración inválida.")

    job_id = uuid4().hex[:12]
    video_path = UPLOADS / f"{job_id}{extension}"
    size = 0

    try:
        with video_path.open("wb") as output:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > 200 * 1024 * 1024:
                    raise HTTPException(status_code=413, detail="El video supera el límite de 200 MB.")
                output.write(chunk)
    except Exception:
        video_path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    with jobs_lock:
        jobs[job_id] = {
            "id": job_id,
            "status": "queued",
            "message": "Video recibido. Preparando clips…",
            "clips": []
        }

    background_tasks.add_task(
        run_uploaded_job, job_id, str(video_path), clip_seconds, max_clips
    )
    return {"job_id": job_id}
@app.post("/api/jobs")
def create_job(payload: ClipRequest, background_tasks: BackgroundTasks):
    if not valid_youtube_url(payload.url):
        raise HTTPException(status_code=400, detail="Pega un enlace válido de YouTube (youtube.com o youtu.be).")
    job_id = uuid4().hex[:12]
    with jobs_lock:
        jobs[job_id] = {"id": job_id, "status": "queued", "message": "Trabajo en cola…", "clips": []}
    background_tasks.add_task(run_job, job_id, payload.model_dump())
    return {"job_id": job_id}

@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="No se encontró este trabajo.")
        return dict(job)

@app.get("/api/gallery")
def gallery():
    items = []
    for path in sorted(CLIPS.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True):
        items.append({"name": path.name, "url": f"/media/clips/{path.name}"})
    return {"clips": items}

@app.get("/download/{filename}")
def download_clip(filename: str):
    # Only allow a filename, never a path supplied by the browser.
    if Path(filename).name != filename or not re.fullmatch(r"[A-Za-z0-9_.-]+\.mp4", filename):
        raise HTTPException(status_code=400, detail="Nombre de archivo inválido.")
    path = CLIPS / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Clip no encontrado.")
    return FileResponse(path, media_type="video/mp4", filename=filename)


# =====================================
# MOTOR DE YOUTUBE LIVE - CLIP BAEZ AI
# =====================================

class LiveRequest(BaseModel):
    url: str


live_jobs = {}
live_processes = {}
live_stop_requested = set()


def run_live_job(job_id, url):
    output_pattern = str(CLIPS / f"live_{job_id}_clip_%03d.mp4")
    clips = []
    publicados = set()
    vistos = {}

    try:
        live_jobs[job_id] = {
            "status": "processing",
            "message": "Conectando con YouTube LIVE...",
            "clips": []
        }

        result = subprocess.run(
            [
                "yt-dlp",
                "--no-warnings",
                "--js-runtimes", "deno",
                "-f", "bestvideo+bestaudio",
                "-g", url
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=True
        )

        stream_urls = result.stdout.strip().splitlines()

        if len(stream_urls) < 2:
            raise RuntimeError("YouTube no entregó por separado el video y el audio.")

        video_url = stream_urls[0]
        audio_url = stream_urls[1]
        ffmpeg = subprocess.Popen(
            [
                "ffmpeg", "-y",
                "-hide_banner",
                "-loglevel", "error",
                "-reconnect", "1",
                "-reconnect_streamed", "1",
                "-reconnect_delay_max", "5",
                "-i", video_url,
"-reconnect", "1",
"-reconnect_streamed", "1",
"-reconnect_delay_max", "5",
"-i", audio_url,
                "-map", "0:v:0",
                "-map", "1:a:0",
                "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-c:a", "aac",
                "-f", "segment",
                "-segment_time", "30",
                "-reset_timestamps", "1",
                "-segment_format", "mp4",
                output_pattern
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        live_processes[job_id] = ffmpeg

        while ffmpeg.poll() is None:
            if job_id in live_stop_requested:
                try:
                    ffmpeg.stdin.write(b"q\n")
                    ffmpeg.stdin.flush()
                except Exception:
                    ffmpeg.terminate()
                break

            archivos = sorted(
                CLIPS.glob(f"live_{job_id}_clip_*.mp4")
            )

            for archivo in archivos:
                tamano = archivo.stat().st_size

                if tamano > 0 and vistos.get(archivo.name) == tamano:
                    if archivo.name not in publicados:
                        publicados.add(archivo.name)
                        clips.append({
                            "name": archivo.name,
                            "url": f"/media/clips/{archivo.name}"
                        })

                        live_jobs[job_id] = {
                            "status": "processing",
                            "message": f"¡Clip {len(clips)} creado!",
                            "clips": list(clips)
                        }

                vistos[archivo.name] = tamano

            threading.Event().wait(2)

        try:
            ffmpeg.wait(timeout=15)
        except subprocess.TimeoutExpired:
            ffmpeg.terminate()
            ffmpeg.wait()

        # Publicar los clips terminados
        for archivo in sorted(
            CLIPS.glob(f"live_{job_id}_clip_*.mp4")
        ):
            if archivo.stat().st_size > 0 and archivo.name not in publicados:
                publicados.add(archivo.name)
                clips.append({
                    "name": archivo.name,
                    "url": f"/media/clips/{archivo.name}"
                })

        if job_id in live_stop_requested:
            mensaje = f"LIVE detenido. {len(clips)} clips creados."
        else:
            mensaje = f"Transmisión finalizada. {len(clips)} clips creados."

        live_jobs[job_id] = {
            "status": "completed",
            "message": mensaje,
            "clips": clips
        }

    except Exception as e:
        live_jobs[job_id] = {
            "status": "error",
            "message": str(e),
            "clips": clips
        }

    finally:
        live_processes.pop(job_id, None)
        live_stop_requested.discard(job_id)


@app.post("/api/live")
def create_live_job(payload: LiveRequest, background_tasks: BackgroundTasks):
    if not valid_youtube_url(payload.url):
        raise HTTPException(
            status_code=400,
            detail="Pega un enlace válido de YouTube."
        )

    job_id = uuid4().hex[:12]

    live_jobs[job_id] = {
        "status": "queued",
        "message": "Preparando transmisión LIVE...",
        "clips": []
    }

    background_tasks.add_task(run_live_job, job_id, payload.url)

    return {"job_id": job_id}


@app.get("/api/live/{job_id}")
def get_live_job(job_id: str):
    job = live_jobs.get(job_id)

    if not job:
        raise HTTPException(
            status_code=404,
            detail="No se encontró esta transmisión."
        )

    return {"job_id": job_id, **job}


@app.post("/api/live/{job_id}/stop")
def stop_live_job(job_id: str):
    if job_id not in live_jobs:
        raise HTTPException(
            status_code=404,
            detail="No se encontró esta transmisión."
        )

    live_stop_requested.add(job_id)

    return {
        "job_id": job_id,
        "message": "Solicitud para detener LIVE enviada."
    }

