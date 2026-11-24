
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
