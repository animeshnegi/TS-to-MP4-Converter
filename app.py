import json
import os
import shutil
import subprocess
import threading
import uuid
from queue import Queue
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request, send_file
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("MAX_UPLOAD_SIZE", str(10 * 1024**3)))
ALLOWED_EXTENSIONS = {".ts"}
JOBS = {}
JOBS_LOCK = threading.Lock()
CONVERSION_QUEUE = Queue()


def find_ffmpeg_tools():
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg and ffprobe:
        return ffmpeg, ffprobe
    if os.name == "nt":
        winget_root = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
        for bin_dir in sorted(winget_root.glob("Gyan.FFmpeg*/*/bin"), reverse=True):
            f, p = bin_dir / "ffmpeg.exe", bin_dir / "ffprobe.exe"
            if f.exists() and p.exists():
                return str(f), str(p)
    return "ffmpeg", "ffprobe"

FFMPEG, FFPROBE = find_ffmpeg_tools()


def allowed_file(filename):
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


def ffprobe_duration(path):
    try:
        result = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)], capture_output=True, text=True, check=True)
        return float(json.loads(result.stdout)["format"]["duration"])
    except Exception:
        return 0.0


def update_job(job_id, **values):
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(values)


def _run_process(job_id, cmd, duration):
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.strip()
        if line.startswith("out_time_ms="):
            try:
                current = float(line.split("=", 1)[1]) / 1_000_000
                update_job(job_id, progress=min(99.9, current / duration * 100) if duration else 0)
            except ValueError:
                pass
        elif line.startswith("speed="):
            update_job(job_id, speed=line.split("=", 1)[1])
    return proc.wait()


def valid_output(path):
    return path.exists() and path.stat().st_size > 1024


def convert_job(job_id, input_path, output_path):
    duration = ffprobe_duration(input_path)
    update_job(job_id, status="converting", progress=0, duration=duration, mode="turbo-copy", queue_position=0)

    # First choice: damaged TS packets are discarded where possible and both
    # compatible streams are copied. This is the fastest path by far.
    copy_cmd = [FFMPEG, "-hide_banner", "-nostdin", "-y", "-fflags", "+discardcorrupt", "-err_detect", "ignore_err", "-i", str(input_path), "-map", "0:v:0?", "-map", "0:a:0?", "-c", "copy", "-progress", "pipe:1", "-nostats", str(output_path)]
    rc = _run_process(job_id, copy_cmd, duration)

    # If AAC/TS corruption prevents direct copy, never re-encode the video.
    if rc != 0 or not valid_output(output_path):
        try:
            output_path.unlink(missing_ok=True)
        except OSError:
            pass
        update_job(job_id, progress=0, speed="-", mode="audio-repair")
        repair_cmd = [FFMPEG, "-hide_banner", "-nostdin", "-y", "-fflags", "+discardcorrupt", "-err_detect", "ignore_err", "-i", str(input_path), "-map", "0:v:0?", "-map", "0:a:0?", "-c:v", "copy", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", str(output_path)]
        rc = _run_process(job_id, repair_cmd, duration)

    # Last resort only: full video encode.
    if rc != 0 or not valid_output(output_path):
        try:
            output_path.unlink(missing_ok=True)
        except OSError:
            pass
        update_job(job_id, progress=0, speed="-", mode="full-transcode")
        fallback_cmd = [FFMPEG, "-hide_banner", "-nostdin", "-y", "-fflags", "+discardcorrupt", "-err_detect", "ignore_err", "-i", str(input_path), "-map", "0:v:0?", "-map", "0:a:0?", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", str(output_path)]
        rc = _run_process(job_id, fallback_cmd, duration)

    if rc == 0 and valid_output(output_path):
        update_job(job_id, status="complete", progress=100, download=f"/download/{job_id}", queue_position=0)
    else:
        update_job(job_id, status="error", progress=0, error="FFmpeg could not convert this TS file.", queue_position=0)

    try:
        input_path.unlink(missing_ok=True)
    except OSError:
        pass


def queue_worker():
    while True:
        job_id, input_path, output_path = CONVERSION_QUEUE.get()
        try:
            convert_job(job_id, input_path, output_path)
        except Exception as exc:
            update_job(job_id, status="error", progress=0, error=f"Conversion error: {exc}", queue_position=0)
        finally:
            CONVERSION_QUEUE.task_done()
            refresh_queue_positions()


def refresh_queue_positions():
    with JOBS_LOCK:
        position = 0
        for job_id, job in JOBS.items():
            if job.get("status") == "queued":
                position += 1
                job["queue_position"] = position


threading.Thread(target=queue_worker, daemon=True, name="ffmpeg-worker").start()


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/upload")
def upload():
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify(error="Please select a .ts file."), 400
    if not allowed_file(file.filename):
        return jsonify(error="Only .ts files are supported."), 400

    job_id = uuid.uuid4().hex
    safe_name = secure_filename(file.filename) or "video.ts"
    input_path = UPLOAD_DIR / f"{job_id}_{safe_name}"
    output_name = f"{Path(safe_name).stem}.mp4"
    output_path = OUTPUT_DIR / f"{job_id}_{output_name}"
    file.save(input_path)
    with JOBS_LOCK:
        JOBS[job_id] = {"status": "queued", "progress": 0, "filename": safe_name, "output_filename": output_name, "speed": "-", "duration": 0, "mode": "queued", "queue_position": 0}
    CONVERSION_QUEUE.put((job_id, input_path, output_path))
    refresh_queue_positions()
    with JOBS_LOCK:
        position = JOBS[job_id]["queue_position"]
    return jsonify(job_id=job_id, filename=safe_name, output_filename=output_name, queue_position=position)


@app.get("/status/<job_id>")
def status(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify(error="Job not found"), 404
        return jsonify(job)


@app.get("/download/<job_id>")
def download(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            abort(404)
        output_name = job.get("output_filename", "converted.mp4")
    output_path = OUTPUT_DIR / f"{job_id}_{output_name}"
    if not output_path.exists():
        abort(404)
    return send_file(output_path, as_attachment=True, download_name=output_name)


@app.errorhandler(413)
def too_large(_):
    return jsonify(error="File is too large."), 413


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
