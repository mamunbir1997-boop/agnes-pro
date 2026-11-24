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

    # Render/Railway ei hosting-e PORT env thake -> tokhon 0.0.0.0 lage (na hole site khole na).
    # Nijer computer-e chalale 127.0.0.1 (shudhu tumi). Phone theke LAN-e dhorte: HOST=0.0.0.0
    default_host = "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1"
    uvicorn.run(app, host=os.environ.get("HOST", default_host), port=int(os.environ.get("PORT", 8000)))
