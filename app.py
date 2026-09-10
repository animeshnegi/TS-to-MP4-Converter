import json
import os
import shutil
import subprocess
import threading
import uuid
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, send_file, abort
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


def find_ffmpeg_tools():
    """Find FFmpeg and FFprobe on Windows or Linux/Docker."""
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")

    if ffmpeg and ffprobe:
        return ffmpeg, ffprobe

    # WinGet installs Gyan FFmpeg under a versioned directory, so do not
    # hard-code a specific FFmpeg version.
    if os.name == "nt":
        winget_root = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
        candidates = sorted(winget_root.glob("Gyan.FFmpeg*/*/bin"), reverse=True)
        for bin_dir in candidates:
            candidate_ffmpeg = bin_dir / "ffmpeg.exe"
            candidate_ffprobe = bin_dir / "ffprobe.exe"
            if candidate_ffmpeg.exists() and candidate_ffprobe.exists():
                return str(candidate_ffmpeg), str(candidate_ffprobe)

    return "ffmpeg", "ffprobe"


FFMPEG, FFPROBE = find_ffmpeg_tools()


def allowed_file(filename):
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


def ffprobe_duration(path):
    try:
        result = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
            capture_output=True, text=True, check=True,
        )
        return float(json.loads(result.stdout)["format"]["duration"])
    except (Exception,):
        return 0.0


def run_ffmpeg(job_id, input_path, output_path):
    duration = ffprobe_duration(input_path)
    with JOBS_LOCK:
        JOBS[job_id].update(status="converting", progress=0, duration=duration)

    # Stream copy is intentionally preferred: it is normally much faster than re-encoding.
    cmd = [FFMPEG, "-hide_banner", "-y", "-i", str(input_path), "-map", "0:v:0?", "-map", "0:a:0?", "-c", "copy", "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", str(output_path)]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    last_time = 0.0
    logs = []
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        if line.startswith("out_time_ms="):
            try:
                last_time = float(line.split("=", 1)[1]) / 1_000_000
                progress = min(99.9, (last_time / duration) * 100) if duration else 0
                with JOBS_LOCK:
                    JOBS[job_id]["progress"] = progress
            except ValueError:
                pass
        elif line.startswith("speed="):
            with JOBS_LOCK:
                JOBS[job_id]["speed"] = line.split("=", 1)[1]
        elif len(logs) < 30:
            logs.append(line)
    return_code = proc.wait()

    # Some TS streams need remuxing/re-encoding. Fall back automatically.
    if return_code != 0:
        fallback = [FFMPEG, "-hide_banner", "-y", "-i", str(input_path), "-map", "0:v:0?", "-map", "0:a:0?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", str(output_path)]
        proc = subprocess.Popen(fallback, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if line.startswith("out_time_ms="):
                try:
                    current = float(line.split("=", 1)[1]) / 1_000_000
                    progress = min(99.9, (current / duration) * 100) if duration else 0
                    with JOBS_LOCK:
                        JOBS[job_id]["progress"] = progress
                except ValueError:
                    pass
            elif line.startswith("speed="):
                with JOBS_LOCK:
                    JOBS[job_id]["speed"] = line.split("=", 1)[1]
        return_code = proc.wait()

    with JOBS_LOCK:
        if return_code == 0 and output_path.exists():
            JOBS[job_id].update(status="complete", progress=100, download=f"/download/{job_id}")
        else:
            JOBS[job_id].update(status="error", progress=0, error="FFmpeg could not convert this TS file.")
    try:
        input_path.unlink(missing_ok=True)
    except OSError:
        pass


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
    output_path = OUTPUT_DIR / f"{job_id}.mp4"
    file.save(input_path)
    with JOBS_LOCK:
        JOBS[job_id] = {"status": "queued", "progress": 0, "filename": safe_name, "speed": "-", "duration": 0}
    threading.Thread(target=run_ffmpeg, args=(job_id, input_path, output_path), daemon=True).start()
    return jsonify(job_id=job_id, filename=safe_name)


@app.get("/status/<job_id>")
def status(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify(error="Job not found"), 404
        return jsonify(job)


@app.get("/download/<job_id>")
def download(job_id):
    output_path = OUTPUT_DIR / f"{job_id}.mp4"
    if not output_path.exists():
        abort(404)
    return send_file(output_path, as_attachment=True, download_name=f"converted-{job_id[:8]}.mp4")


@app.errorhandler(413)
def too_large(_):
    return jsonify(error="File is too large."), 413


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
