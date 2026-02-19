from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import yt_dlp, uuid, os

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/convert")
def convert(url: str):
    uid = str(uuid.uuid4())
    out_template = f"/tmp/{uid}.%(ext)s"
    
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': out_template,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
    }
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    
    mp3_path = f"/tmp/{uid}.mp3"
    return FileResponse(mp3_path, media_type="audio/mpeg", filename="audio.mp3")

@app.get("/health")
def health():
    return {"status": "ok"}
