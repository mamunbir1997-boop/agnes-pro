# Agnes Pro - Backend proxy server
# Browser -> ei server -> Agnes API. Tai CORS atkay na, key-o browser theke Agnes-e jay na.
#
# Run:
#   pip install fastapi uvicorn httpx
#   python server.py
# Browser: http://localhost:8000
#
# Video_Studio_Pro.html er jonno: ⚙ অ্যাডভান্সড সেটিংসে "Agnes API domain" = http://localhost:8000
# (ei file er pashe index.html ba Video_Studio_Pro.html thakle "/" te oi page-i khulbe)

import asyncio
import ipaddress
import os
import socket
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response

AGNES_HOST = "https://apihub.agnes-ai.com"
VIDEO_MODEL = "agnes-video-2.5-flash"
HERE = os.path.dirname(os.path.abspath(__file__))
MAX_MEDIA_BYTES = 150 * 1024 * 1024

app = FastAPI()

# allow_credentials=True + "*" origin valid na, tai bad deoa hoyeche (cookie lagche na)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

client = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0))


@app.on_event("shutdown")
async def _close():
    await client.aclose()


def err(msg, code=500):
    return JSONResponse({"error": msg, "detail": msg}, status_code=code)


def passthrough(r: httpx.Response) -> Response:
    return Response(
        content=r.content,
        status_code=r.status_code,
        media_type=r.headers.get("content-type", "application/json"),
    )


# ---------- Frontend ----------
@app.get("/")
def home():
    for name in ("index.html", "Video_Studio_Pro.html"):
        p = os.path.join(HERE, name)
        if os.path.exists(p):
            return FileResponse(p)
    return err("index.html ba Video_Studio_Pro.html server.py er pashe rakho", 404)


# ---------- 1) Transparent proxy (Video_Studio_Pro.html eita use kore) ----------
async def forward(request: Request, url: str):
    headers = {}
    if request.headers.get("authorization"):
        headers["Authorization"] = request.headers["authorization"]
    if request.headers.get("content-type"):
        headers["Content-Type"] = request.headers["content-type"]
    if request.url.query:
        url += "?" + request.url.query
    try:
        r = await client.request(request.method, url, headers=headers, content=await request.body())
        return passthrough(r)
    except httpx.HTTPError as e:
        return err(f"Agnes-er sathe connect hoy ni: {e}", 502)


@app.api_route("/v1/{path:path}", methods=["GET", "POST"])
async def v1(path: str, request: Request):
    return await forward(request, f"{AGNES_HOST}/v1/{path}")


@app.get("/agnesapi")
async def agnesapi(request: Request):
    return await forward(request, f"{AGNES_HOST}/agnesapi")


# ---------- 2) Media proxy: ready video/image file-e CORS khule dey (final jora lagano-r jonno) ----------
def _is_public(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


@app.get("/media")
async def media(url: str):
    # SSRF theke bachte: shudhu https + public host, redirect-o prottekbar check
    for _ in range(4):
        u = urlparse(url)
        if u.scheme != "https" or not u.hostname or not await asyncio.to_thread(_is_public, u.hostname):
            return err("Ei URL allowed na (shudhu public https)", 400)
        try:
            r = await client.get(url, follow_redirects=False)
        except httpx.HTTPError as e:
            return err(f"File ana jay ni: {e}", 502)
        if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
            url = str(httpx.URL(url).join(r.headers["location"]))
            continue
        if len(r.content) > MAX_MEDIA_BYTES:
            return err("File onek boro", 413)
        return Response(
            content=r.content,
            status_code=r.status_code,
            media_type=r.headers.get("content-type", "application/octet-stream"),
        )
    return err("Onek beshi redirect", 502)


# ---------- 3) Purono-style simple API (apnar nijer index.html er jonno) ----------
@app.post("/api/generate")
async def generate(req: Request):
    try:
        body = await req.json()
    except Exception:
        return err("JSON body dao", 400)
    api_key = (body.get("api_key") or "").strip()
    prompt = (body.get("prompt") or "").strip()
    if not api_key:
        return err("API key dao", 400)
    if not prompt:
        return err("Prompt dao", 400)

    ratio = body.get("ratio", "9:16")
    if ratio not in ("21:9", "16:9", "4:3", "1:1", "3:4", "9:16"):
        ratio = "9:16"
    secs = "".join(ch for ch in str(body.get("duration", "5")) if ch.isdigit()) or "5"
    secs = str(min(12, max(4, int(secs))))  # Agnes Flash: 4-12 sec

    payload = {
        "model": VIDEO_MODEL,
        "prompt": prompt,
        "mode": "text",
        "seconds": secs,
        "size": "720P",  # Flash shudhu 720P
        "aspect_ratio": ratio,
    }
    try:
        r = await client.post(
            f"{AGNES_HOST}/v1/videos",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
        )
    except httpx.HTTPError as e:
        return err(f"Agnes-er sathe connect hoy ni: {e}", 502)
    try:
        data = r.json()
    except ValueError:
        return err(r.text[:300] or f"HTTP {r.status_code}", r.status_code)
    if r.status_code >= 400:
        return JSONResponse(data, status_code=r.status_code)
    data["task_id"] = data.get("video_id") or data.get("id") or data.get("task_id")
    return JSONResponse(data)


@app.get("/api/status")
async def status(request: Request, task_id: str, api_key: str = ""):
    # key header-e dile bhalo (URL-e thakle log-e dhora porte pare); purono frontend-er jonno query-o cholbe
    key = (request.headers.get("x-api-key") or api_key).strip()
    if not key:
        return err("API key dao", 400)
    try:
        r = await client.get(
            f"{AGNES_HOST}/agnesapi",
            params={"video_id": task_id, "model_name": VIDEO_MODEL},
            headers={"Authorization": f"Bearer {key}"},
        )
    except httpx.HTTPError as e:
        return err(f"Agnes-er sathe connect hoy ni: {e}", 502)
    try:
        data = r.json()
    except ValueError:
        return err(r.text[:300] or f"HTTP {r.status_code}", r.status_code)
    if r.status_code >= 400:
        return JSONResponse(data, status_code=r.status_code)
    # Agnes-er asol response: status = completed/failed, video-r link = top-level "url"
    data["video_url"] = data.get("url") or data.get("video_url")
    return JSONResponse(data)


if __name__ == "__main__":
    import uvicorn

    # 127.0.0.1 = shudhu nijer computer theke. Phone/onno device theke dhorte HOST=0.0.0.0 dao
    uvicorn.run(app, host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", 8000)))
# Agnes Pro - Backend server (official site er moto)
# Frontend kokhono Agnes ke direct call kore na.
# Frontend -> ei server -> Agnes API. Tai CORS atkay na.
# Run: pip install -r requirements.txt && python server.py
# Tarpor browser e http://localhost:8000

import os, requests
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

AGNES_BASE = "https://apihub.agnes-ai.com/v1"
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Frontend serve
@app.get("/")
def home():
    return FileResponse("index.html")

@app.post("/api/generate")
async def generate(req: Request):
    body = await req.json()
    api_key = (body.get("api_key") or "").strip()
    prompt = (body.get("prompt") or "").strip()
    if not api_key:
        return JSONResponse({"error": "API key dao"}, status_code=400)
    if not prompt:
        return JSONResponse({"error": "Prompt dao"}, status_code=400)

    ratio = body.get("ratio", "9:16")
    duration = body.get("duration", "5s")

    # Agnes er asol spec: width/height/num_frames
    if ratio == "16:9":
        width, height = 1152, 768
    elif ratio == "1:1":
        width, height = 768, 768
    else:
        width, height = 768, 1152

    num_frames = 241 if duration == "10s" else 121

    payload = {
        "model": "agnes-video-v2.0",
        "prompt": prompt,
        "width": width,
        "height": height,
        "num_frames": num_frames,
        "frame_rate": 24,
    }
    try:
        r = requests.post(
            f"{AGNES_BASE}/videos",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=30,
        )
        return JSONResponse(r.json(), status_code=r.status_code)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/status")
async def status(task_id: str, api_key: str):
    # Poll: GET /v1/videos/{id} - asol Agnes endpoint
    try:
        r = requests.get(
            f"{AGNES_BASE}/videos/{task_id}",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30,
        )
        data = r.json()
        # Fallback: jodi video_url na thake, agnesapi try koro
        if r.status_code == 200 and data.get("status") == "completed" and not data.get("video_url"):
            r2 = requests.get(
                f"https://apihub.agnes-ai.com/agnesapi?video_id={task_id}",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=30,
            )
            if r2.status_code == 200:
                d2 = r2.json()
                if d2.get("video_url"):
                    data["video_url"] = d2["video_url"]
        return JSONResponse(data, status_code=r.status_code)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
