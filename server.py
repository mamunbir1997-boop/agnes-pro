import asyncio
import ipaddress
import os
import socket
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response

AGNES_HOST = "https://agnes-ai.com"
VIDEO_MODEL = "agnes-video-2.5-flash"
HERE = os.path.dirname(os.path.abspath(__file__))
MAX_MEDIA_BYTES = 150 * 1024 * 1024

app = FastAPI()

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


def extract_error_message(data, fallback="Unknown API Error"):
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        for key in ("message", "detail", "error", "msg", "reason"):
            val = data.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
            if isinstance(val, dict):
                nested = extract_error_message(val, "")
                if nested:
                    return nested
        return str(data)
    return str(data or fallback)


def err(msg, code=500):
    clean_msg = extract_error_message(msg)
    return JSONResponse({"error": clean_msg, "detail": clean_msg}, status_code=code)


def passthrough(r: httpx.Response) -> Response:
    return Response(
        content=r.content,
        status_code=r.status_code,
        media_type=r.headers.get("content-type", "application/json"),
    )


@app.get("/")
def home():
    for name in ("index.html", "Video_Studio_Pro.html"):
        p = os.path.join(HERE, name)
        if os.path.exists(p):
            return FileResponse(p)
    return err("index.html ba Video_Studio_Pro.html server.py er pashe rakho", 404)


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


@app.post("/api/generate")
async def generate(req: Request):
    try:
        body = await req.json()
    except Exception:
        return err("Invalid JSON request", 400)
    
    api_key = (body.get("api_key") or "").strip()
    prompt = (body.get("prompt") or "").strip()
    
    if not api_key:
        return err("API key deoa hoy ni!", 400)
    if not prompt:
        return err("Prompt khali rakha jabe na!", 400)

    ratio = body.get("ratio", "9:16")
    if ratio not in ("21:9", "16:9", "4:3", "1:1", "3:4", "9:16"):
        ratio = "9:16"
    secs = "".join(ch for ch in str(body.get("duration", "5")) if ch.isdigit()) or "5"
    secs = str(min(12, max(4, int(secs))))

    payload = {
        "model": VIDEO_MODEL,
        "prompt": prompt,
        "mode": "text",
        "seconds": secs,
        "size": "720P",
        "aspect_ratio": ratio,
    }
    try:
        r = await client.post(
            f"{AGNES_HOST}/v1/videos",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
        )
    except httpx.HTTPError as e:
        return err(f"Agnes server connection failed: {str(e)}", 502)
    
    try:
        data = r.json()
    except ValueError:
        return err(r.text[:300] or f"HTTP Error {r.status_code}", r.status_code)
    
    if r.status_code >= 400:
        return err(data, r.status_code)
        
    task_id = data.get("video_id") or data.get("id") or data.get("task_id")
    if not task_id:
        return err(f"Task ID paoya jay ni. Response: {data}", 500)
        
    data["task_id"] = task_id
    return JSONResponse(data)


@app.get("/api/status")
async def status(request: Request, task_id: str, api_key: str = ""):
    key = (request.headers.get("x-api-key") or api_key).strip()
    if not key:
        return err("API key missing", 400)
    try:
        r = await client.get(
            f"{AGNES_HOST}/agnesapi",
            params={"video_id": task_id, "model_name": VIDEO_MODEL},
            headers={"Authorization": f"Bearer {key}"},
        )
    except httpx.HTTPError as e:
        return err(f"Agnes status check failed: {str(e)}", 502)
    
    try:
        data = r.json()
    except ValueError:
        return err(r.text[:300] or f"HTTP Error {r.status_code}", r.status_code)
    
    if r.status_code >= 400:
        return err(data, r.status_code)
        
    data["video_url"] = data.get("url") or data.get("video_url")
    return JSONResponse(data)


if __name__ == "__main__":
    import uvicorn
    default_host = "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1"
    uvicorn.run(app, host=os.environ.get("HOST", default_host), port=int(os.environ.get("PORT", 8000)))
