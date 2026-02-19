from flask import Flask, request, jsonify, send_from_directory, Response
from flask_cors import CORS
import librosa
import essentia.standard as es
import os
import subprocess
import threading
import re
import time
import uuid
import shutil

# ======================
# Configuration
# ======================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
SEPARATED_BASE = os.path.join(BASE_DIR, "separated")

MODEL_NAME = "htdemucs"
OUTPUT_BASE = os.path.join(SEPARATED_BASE, MODEL_NAME)

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_BASE, exist_ok=True)

app = Flask(__name__, static_folder=os.path.join(BASE_DIR, "frontend"), static_url_path="/")
CORS(app)

# --- Job State ---
jobs = {}
jobs_lock = threading.Lock()



# ======================
# Cleanup Functions
# ======================
def cleanup_job_files(job_id, input_path, output_folder):
    try:
        if os.path.exists(input_path):
            os.remove(input_path)
            print(f"[{job_id}] Cleaned up input file: {input_path}")
        if os.path.exists(output_folder):
            for filename in os.listdir(output_folder):
                if f"_{job_id}_temp.mp3" in filename:
                    temp_file = os.path.join(output_folder, filename)
                    if os.path.exists(temp_file):
                        os.remove(temp_file)
                        print(f"[{job_id}] Cleaned up temp MP3: {temp_file}")
    except Exception as e:
        print(f"[{job_id}] Cleanup error: {e}")


def cleanup_old_outputs(max_age_hours=24):
    try:
        current_time = time.time()
        for folder_name in os.listdir(OUTPUT_BASE):
            folder_path = os.path.join(OUTPUT_BASE, folder_name)
            if os.path.isdir(folder_path):
                folder_age = current_time - os.path.getmtime(folder_path)
                if folder_age > (max_age_hours * 3600):
                    shutil.rmtree(folder_path)
                    print(f"Cleaned up old output folder: {folder_name}")
    except Exception as e:
        print(f"Cleanup old outputs error: {e}")


# ======================
# GET / → Landing page
# ======================
@app.route("/")
def landing():
    return send_from_directory(os.path.join(BASE_DIR, "frontend"), "landing.html")


# ======================
# GET /app → Main app
# ======================
@app.route("/app")
def index():
    return send_from_directory(os.path.join(BASE_DIR, "frontend"), "index.html")


# /How It Works
@app.route('/how-it-works')
def how_it_works():
    return send_from_directory(os.path.join(BASE_DIR, "frontend"), 'how-it-works.html')

# /Privacy Policy
@app.route('/privacy')
def privacy():
    return send_from_directory(os.path.join(BASE_DIR, "frontend"), 'privacy.html')

# /YouTube Terms of Use
@app.route('/youtube-terms')
def youtube_terms():
    return send_from_directory(os.path.join(BASE_DIR, "frontend"), 'youtube-terms.html')


# ======================
# YouTube to MP3
# ======================
@app.route("/youtube-info", methods=["POST"])
def youtube_info():
    """YouTube video bilgilerini getir"""
    data = request.get_json()
    url = data.get("url", "").strip()
    
    if not url:
        return jsonify({"success": False, "error": "URL is required"}), 400
    
    if not ("youtube.com" in url or "youtu.be" in url):
        return jsonify({"success": False, "error": "Invalid YouTube URL"}), 400
    
    try:
        import yt_dlp
        
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            # Duration'ı formatla
            duration_seconds = info.get('duration', 0)
            minutes = duration_seconds // 60
            seconds = duration_seconds % 60
            duration_formatted = f"{minutes}:{seconds:02d}"
            
            return jsonify({
                "success": True,
                "title": info.get('title', 'Unknown'),
                "channel": info.get('uploader', 'Unknown'),
                "thumbnail": info.get('thumbnail', ''),
                "duration": duration_formatted,
                "video_id": info.get('id', '')
            })
            
    except Exception as e:
        print(f"[youtube-info] Error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/youtube-download", methods=["POST"])
def youtube_download():
    """YouTube videoyu MP3'e çevir ve indir"""
    data = request.get_json()
    url = data.get("url", "").strip()
    
    if not url:
        return jsonify({"success": False, "error": "URL is required"}), 400
    
    job_id = str(uuid.uuid4())
    
    with jobs_lock:
        jobs[job_id] = {
            "progress": 0,
            "done": False,
            "failed": False,
            "error": "",
            "file_path": None
        }
    
    def download_youtube():
        try:
            import yt_dlp
            
            output_path = os.path.join(UPLOAD_FOLDER, f"youtube_{job_id}.mp3")
            
            ydl_opts = {
                'format': 'bestaudio/best',
                'outtmpl': output_path.replace('.mp3', ''),
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
                'progress_hooks': [lambda d: update_progress(d, job_id)],
                'quiet': True,
                'no_warnings': True,
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            
            with jobs_lock:
                jobs[job_id]["progress"] = 100
                jobs[job_id]["done"] = True
                jobs[job_id]["file_path"] = output_path
                
        except Exception as e:
            print(f"[youtube-download] Error: {e}")
            with jobs_lock:
                jobs[job_id]["failed"] = True
                jobs[job_id]["error"] = str(e)
                jobs[job_id]["done"] = True
    
    threading.Thread(target=download_youtube, daemon=True).start()
    
    return jsonify({
        "success": True,
        "message": "Download started",
        "job_id": job_id
    }), 202


def update_progress(d, job_id):
    """yt-dlp progress hook"""
    if d['status'] == 'downloading':
        try:
            percent_str = d.get('_percent_str', '0%')
            percent = float(percent_str.strip('%'))
            with jobs_lock:
                jobs[job_id]["progress"] = int(percent)
        except:
            pass
    elif d['status'] == 'finished':
        with jobs_lock:
            jobs[job_id]["progress"] = 95


@app.route("/youtube-file/<job_id>")
def youtube_file(job_id):
    """YouTube MP3 dosyasını indir"""
    with jobs_lock:
        job = jobs.get(job_id)
    
    if not job or not job.get("done"):
        return jsonify({"error": "Job not ready"}), 404
    
    if job.get("failed"):
        return jsonify({"error": job.get("error", "Download failed")}), 500
    
    file_path = job.get("file_path")
    if not file_path or not os.path.exists(file_path):
        return jsonify({"error": "File not found"}), 404
    
    # Dosya adını temizle
    filename = os.path.basename(file_path)
    
    response = send_from_directory(
        os.path.dirname(file_path),
        filename,
        mimetype="audio/mpeg",
        as_attachment=True,
        download_name=filename
    )
    
    # İndirme sonrası dosyayı sil
    @response.call_on_close
    def cleanup():
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                print(f"[{job_id}] Cleaned up YouTube MP3: {file_path}")
        except Exception as e:
            print(f"[{job_id}] Cleanup error: {e}")
    
    return response


# ======================
# Demucs runner
# ======================
def run_demucs(job_id, command, expected_folder, input_path):
    with jobs_lock:
        jobs[job_id]["progress"] = 0
        jobs[job_id]["done"] = False
        jobs[job_id]["failed"] = False
        jobs[job_id]["error"] = ""

    failed = False
    try:
        print(f"[{job_id}] COMMAND: {' '.join(command)}")

        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            shell=False
        )

        for line in process.stdout:
            stripped = line.strip()
            print(f"[{job_id}] {stripped}")
            match = re.search(r"(\d+)%", stripped)
            if match:
                with jobs_lock:
                    jobs[job_id]["progress"] = int(match.group(1))

        process.wait()

        if process.returncode != 0:
            with jobs_lock:
                jobs[job_id]["failed"] = True
                jobs[job_id]["error"] = f"Demucs exited with code {process.returncode}"
            failed = True

    except Exception as e:
        with jobs_lock:
            jobs[job_id]["failed"] = True
            jobs[job_id]["error"] = str(e)
        print(f"[{job_id}] EXCEPTION: {e}")
        failed = True

    if not os.path.exists(expected_folder):
        with jobs_lock:
            jobs[job_id]["failed"] = True
            jobs[job_id]["error"] = f"Output folder not found: {expected_folder}"
        failed = True

    with jobs_lock:
        jobs[job_id]["progress"] = 100
        jobs[job_id]["done"] = True

    cleanup_job_files(job_id, input_path, expected_folder)

    if failed and os.path.exists(expected_folder):
        try:
            shutil.rmtree(expected_folder)
            print(f"[{job_id}] Cleaned up failed output folder: {expected_folder}")
        except Exception as e:
            print(f"[{job_id}] Error cleaning failed output: {e}")


@app.route("/analyze", methods=["POST"])
def analyze_audio():
    if "audio" not in request.files:
        return jsonify({"success": False, "error": "No audio file"}), 400

    file = request.files["audio"]

    # Geçici dosyaya kaydet
    file_ext = os.path.splitext(file.filename)[1].lower() or ".mp3"
    temp_id   = str(uuid.uuid4())
    temp_path = os.path.join(UPLOAD_FOLDER, f"analyze_{temp_id}{file_ext}")
    file.save(temp_path)

    try:
        import essentia.standard as es
        import numpy as np

        # --- Essentia ile audio yükle ---
        loader = es.MonoLoader(filename=temp_path, sampleRate=44100)
        audio = loader()

        # Max 60 saniye al (hız için)
        max_samples = 44100 * 60
        if len(audio) > max_samples:
            audio = audio[:max_samples]

        # --- BPM Detection (RhythmExtractor2013 - çok doğru) ---
        rhythm_extractor = es.RhythmExtractor2013(method="multifeature")
        bpm, beats, beats_confidence, _, beats_intervals = rhythm_extractor(audio)

        # Makul aralığa çek
        while bpm < 60:  bpm *= 2
        while bpm > 200: bpm /= 2

        # --- KEY Detection (KeyExtractor - state-of-art) ---
        key_extractor = es.KeyExtractor()
        key, scale, strength = key_extractor(audio)

        # Key formatını düzenle (C# yerine C♯)
        key = key.replace('#', '♯')
        
        # Scale'i düzelt (major/minor)
        mode = "Major" if scale == "major" else "Minor"

        return jsonify({
            "success": True,
            "bpm":  round(bpm),
            "key":  key,
            "mode": mode
        })

    except Exception as e:
        print(f"[analyze] Error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500

    finally:
        # Her durumda temp dosyayı sil
        if os.path.exists(temp_path):
            os.remove(temp_path)
# ======================
# POST /separate
# ======================
@app.route("/separate", methods=["POST"])
def separate_audio():
    if "audio" not in request.files:
        return jsonify({"success": False, "error": "No audio file"}), 400

    file = request.files["audio"]

    # Dosya boyutu kontrolü — 50MB üzeri reddet
    file.seek(0, 2)
    file_size = file.tell()
    file.seek(0)
    if file_size > 50 * 1024 * 1024:
        return jsonify({
            "success": False,
            "error": f"File too large ({file_size // (1024*1024)}MB). Maximum allowed size is 50MB."
        }), 400

    # Dosya tipi kontrolü
    allowed_extensions = {'.mp3', '.wav'}
    file_ext = os.path.splitext(file.filename)[1].lower()
    if file_ext not in allowed_extensions:
        return jsonify({
            "success": False,
            "error": "Only MP3 and WAV files are allowed"
        }), 400

    original_name, _ = os.path.splitext(file.filename)

    safe_filename = "".join(
        [c for c in file.filename if c.isalnum() or c in ('.', '_')]
    ).replace(" ", "_")

    input_path = os.path.join(UPLOAD_FOLDER, safe_filename)
    file.save(input_path)

    # Süre kontrolü — 5 dakika üzeri reddedilir
    try:
        import soundfile as sf
        info = sf.info(input_path)
        duration = info.duration
        if duration > 300:
            os.remove(input_path)
            return jsonify({
                "success": False,
                "error": f"Audio too long ({int(duration//60)}m {int(duration%60)}s). Maximum allowed duration is 5 minutes."
            }), 400
    except Exception:
        pass

    output_folder_name = safe_filename.rsplit('.', 1)[0]
    expected_folder = os.path.join(OUTPUT_BASE, output_folder_name)

    job_id = str(uuid.uuid4())

    with jobs_lock:
        jobs[job_id] = {
            "progress": 0,
            "done": False,
            "failed": False,
            "error": "",
            "folder": output_folder_name,
            "original_name": original_name
        }

    command = [
        "python3", "-m", "demucs.separate",
        "-n", MODEL_NAME,
        "-d", "cpu",
        "--out", SEPARATED_BASE,
        input_path
    ]

    threading.Thread(
        target=run_demucs,
        args=(job_id, command, expected_folder, input_path),
        daemon=True
    ).start()

    return jsonify({
        "success": True,
        "message": "Separation started.",
        "job_id": job_id
    }), 202


# ======================
# GET /progress/<job_id>  (SSE)
# ======================
@app.route("/progress/<job_id>")
def progress(job_id):
    def generate():
        last = -1
        max_wait = 600
        waited = 0
        interval = 0.5

        while waited < max_wait:
            with jobs_lock:
                job = jobs.get(job_id)

            if not job:
                yield f"data:0\n\n"
                break

            prog = job["progress"]
            done = job["done"]

            if prog != last:
                yield f"data:{prog}\n\n"
                last = prog

            if done:
                if last != 100:
                    yield f"data:100\n\n"
                break

            time.sleep(interval)
            waited += interval

    return Response(generate(), mimetype="text/event-stream")


# ======================
# GET /status/<job_id>
# ======================
@app.route("/status/<job_id>")
def status(job_id):
    with jobs_lock:
        job = jobs.get(job_id)

    if not job:
        return jsonify({"done": False, "progress": 0})

    if not job["done"]:
        return jsonify({"done": False, "progress": job["progress"]})

    if job["failed"]:
        return jsonify({
            "done": True,
            "success": False,
            "error": job["error"] or "Separation failed."
        }), 500

    folder = job["folder"]
    return jsonify({
        "done": True,
        "success": True,
        "vocals":       f"/download/{job_id}/{folder}/vocals.wav",
        "drums":        f"/download/{job_id}/{folder}/drums.wav",
        "bass":         f"/download/{job_id}/{folder}/bass.wav",
        "instrumental": f"/download/{job_id}/{folder}/other.wav"
    })


# ======================
# GET /download/<job_id>/<song>/<filename>
# ======================
@app.route("/download/<job_id>/<song>/<filename>")
def download_file(job_id, song, filename):
    target_dir = os.path.join(OUTPUT_BASE, song)
    safe_filename = os.path.basename(filename)

    if not os.path.exists(target_dir):
        return jsonify({"error": f"Directory not found: {target_dir}"}), 404

    file_path = os.path.join(target_dir, safe_filename)
    if not os.path.exists(file_path):
        return jsonify({"error": f"File '{safe_filename}' not found."}), 404

    with jobs_lock:
        job = jobs.get(job_id, {})
    original_name = job.get("original_name", song)

    fmt = request.args.get("format", "wav").lower()
    stem_map = {
    "vocals.wav": "vocals",
    "drums.wav": "drums",
    "bass.wav": "bass",
    "other.wav": "instrumental"
    }
    stem_label = stem_map.get(safe_filename, "output")

    if fmt == "mp3":
        mp3_path = file_path.replace(".wav", f"_{job_id}_temp.mp3")
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", file_path, "-q:a", "2", mp3_path],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except subprocess.CalledProcessError:
            return jsonify({"error": "MP3 conversion failed."}), 500
        serve_dir  = os.path.dirname(mp3_path)
        serve_file = os.path.basename(mp3_path)
        download_name = f"{original_name} [{stem_label}].mp3"
        mimetype = "audio/mpeg"
    else:
        serve_dir  = target_dir
        serve_file = safe_filename
        download_name = f"{original_name} [{stem_label}].wav"
        mimetype = "audio/wav"

    response = send_from_directory(
        serve_dir, serve_file,
        mimetype=mimetype,
        as_attachment=True,
        download_name=download_name
    )
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Expose-Headers"] = "Content-Disposition"

    if fmt == "mp3":
        @response.call_on_close
        def cleanup_temp():
            if os.path.exists(mp3_path):
                try:
                    os.remove(mp3_path)
                    print(f"[{job_id}] Cleaned up temp MP3: {mp3_path}")
                except Exception as e:
                    print(f"[{job_id}] Error cleaning temp MP3: {e}")

    return response


# ======================
# Run
# ======================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    app.run(host="0.0.0.0", port=port, debug=False)
