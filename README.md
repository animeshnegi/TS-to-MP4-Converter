# TS to MP4 Converter

Fast Flask web app for converting `.ts` video files to `.mp4` using FFmpeg.

## Features

- Drag-and-drop `.ts` uploads
- Upload progress bar
- Live FFmpeg conversion progress
- Conversion speed and ETA
- Stream-copy mode for very fast remuxing when compatible
- Automatic H.264/AAC fallback when stream copy fails
- Download converted MP4
- Automatic cleanup of uploaded source files
- Docker-ready with FFmpeg included

## Run with Docker

```bash
docker build -t ts-to-mp4-converter .
docker run -d --name ts-to-mp4 -p 5000:5000 -v ts_converter_data:/app/outputs ts-to-mp4-converter
```

Open `http://SERVER-IP:5000`.

## Run locally

Install Python 3.11+ and FFmpeg, then:

```bash
pip install -r requirements.txt
python app.py
```

## Notes

The first conversion attempt uses `-c copy`, which does not re-encode the media and is therefore very fast. If the input cannot be cleanly remuxed to MP4, the app automatically retries with H.264/AAC encoding.

For production use behind OnePanel/Nginx, put the Flask app behind a reverse proxy and configure the proxy's client upload size to match the desired file limit.
