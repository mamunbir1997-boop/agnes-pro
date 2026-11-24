import asyncio
import ipaddress
import os
import socket
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response

# ==========================================
# AGNES PRO - ALL-IN-ONE SERVER (v2.0)
# Frontend + Backend Proxy in a Single File
# ==========================================

AGNES_HOST = "https://agnes-ai.com"
VIDEO_MODEL = "agnes-video-2.5-flash"
MAX_MEDIA_BYTES = 150 * 1024 * 1024

app = FastAPI(title="Agnes Pro All-in-One")

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


# ---------- Embedded Frontend HTML ----------
HTML_PAGE = """<!DOCTYPE html>
<html lang="bn">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>AGNES PRO - Real Video Generator (v2.0)</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; font-family: system-ui, -apple-system, sans-serif; }
    body { background: #0f172a; color: #f8fafc; padding: 16px; line-height: 1.5; }
    .container { max-width: 680px; margin: 0 auto; background: #1e293b; padding: 22px; border-radius: 16px; border: 1px solid #334155; box-shadow: 0 10px 25px rgba(0,0,0,0.4); }
    .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; }
    h1 { font-size: 1.45rem; color: #38bdf8; }
    .badge { background: #0284c7; color: #fff; font-size: 0.72rem; padding: 3px 8px; border-radius: 20px; font-weight: 700; }
    .sub { font-size: 0.85rem; color: #94a3b8; margin-bottom: 18px; }
    label { display: block; font-size: 0.88rem; font-weight: 600; margin-bottom: 6px; color: #cbd5e1; }
    input, textarea, select { width: 100%; background: #0f172a; border: 1px solid #334155; color: #fff; padding: 12px; border-radius: 8px; margin-bottom: 14px; font-size: 0.95rem; }
    input:focus, textarea:focus, select:focus { outline: none; border-color: #38bdf8; }
    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    .btn-group { display: flex; gap: 10px; margin-bottom: 16px; }
    button { flex: 1; padding: 14px; border: none; border-radius: 8px; font-weight: 700; font-size: 0.95rem; cursor: pointer; transition: 0.2s; }
    #genBtn { background: #0284c7; color: #fff; }
    #genBtn:hover { background: #0369a1; }
    #genBtn:disabled { background: #475569; cursor: not-allowed; }
    #stopBtn { background: #dc2626; color: #fff; max-width: 100px; display: none; }
    .status-bar { display: flex; justify-content: space-between; background: #0f172a; padding: 10px 14px; border-radius: 8px; font-size: 0.85rem; font-weight: bold; margin-bottom: 12px; border: 1px solid #334155; }
    video { width: 100%; border-radius: 10px; background: #000; display: none; margin-bottom: 12px; max-height: 450px; }
    .dl-row { display: none; gap: 10px; margin-bottom: 16px; }
    .dl-btn { background: #16a34a; color: #fff; text-decoration: none; text-align: center; padding: 12px; border-radius: 8px; font-weight: bold; flex: 1; font-size: 0.9rem; }
    .copy-btn { background: #334155; color: #fff; padding: 12px; border-radius: 8px; font-weight: bold; border: none; cursor: pointer; }
    .log-box { background: #020617; color: #4ade80; font-family: monospace; font-size: 0.82rem; padding: 12px; border-radius: 8px; height: 165px; overflow-y: auto; border: 1px solid #1e293b; white-space: pre-wrap; word-break: break-word; }
    .err-text { color: #f87171; font-weight: bold; }
  </style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>AGNES PRO</h1>
    <span class="badge">v2.0 All-in-One</span>
  </div>
  <div class="sub">Frontend + Backend • 720p Real AI Video Generator</div>

  <label>Agnes API Key</label>
  <input type="password" id="apiKey" placeholder="sk-..." />

  <label>Prompt (English-e likhle valo hoy)</label>
  <textarea id="prompt" rows="3">A cinematic shot of a cat walking on the beach at sunset, soft ocean waves, warm golden lighting, realistic motion</textarea>

  <div class="row">
    <div>
      <label>Aspect Ratio</label>
      <select id="ratio">
        <option value="9:16">9:16 (Shorts / Reels)</option>
        <option value="16:9">16:9 (YouTube)</option>
        <option value="1:1">1:1 (Square)</option>
        <option value="4:3">4:3 (Classic)</option>
        <option value="3:4">3:4 (Portrait)</option>
        <option value="21:9">21:9 (Cinematic)</option>
      </select>
    </div>
    <div>
      <label>Duration (sec)</label>
      <select id="duration">
        <option value="5">5 Seconds</option>
        <option value="8">8 Seconds</option>
        <option value="10">10 Seconds</option>
        <option value="12">12 Seconds</option>
      </select>
    </div>
  </div>

  <div class="btn-group">
    <button id="genBtn" onclick="startGenerate()">▶ GENERATE VIDEO (720p)</button>
    <button id="stopBtn" onclick="stopPolling()">⏹ Stop</button>
  </div>

  <div class="status-bar">
    <span id="statusText">IDLE</span>
    <span id="progressText">0%</span>
  </div>

  <video id="videoPlayer" controls playsinline></video>

  <div class="dl-row" id="dlRow">
    <a id="downloadLink" class="dl-btn" href="#" download="agnes_video.mp4" target="_blank">⬇ DIRECT DOWNLOAD VIDEO</a>
    <button class="copy-btn" onclick="copyLink()">Copy Direct Link</button>
  </div>

  <label>Live Log (Asol Status & Error)</label>
  <div class="log-box" id="logBox">Ready (v2.0). API Key diye Generate chap dau...</div>
</div>

<script>
  let pollTimer = null;
  let currentVideoUrl = "";

  const savedKey = localStorage.getItem("agnes_api_key");
  if (savedKey) document.getElementById("apiKey").value = savedKey;

  function log(msg, isError = false) {
    const box = document.getElementById("logBox");
    const time = new Date().toLocaleTimeString();
    const cleanMsg = typeof msg === "object" ? JSON.stringify(msg) : String(msg);
    const line = document.createElement("div");
    if (isError) line.className = "err-text";
    line.textContent = `[${time}] ${cleanMsg}`;
    box.appendChild(line);
    box.scrollTop = box.scrollHeight;
  }

  function parseError(errData) {
    if (!errData) return "Unknown error occurred";
    if (typeof errData === "string") return errData;
    if (errData.error) return typeof errData.error === "object" ? JSON.stringify(errData.error) : errData.error;
    if (errData.detail) return typeof errData.detail === "object" ? JSON.stringify(errData.detail) : errData.detail;
    if (errData.message) return errData.message;
    return JSON.stringify(errData);
  }

  async function startGenerate() {
    const apiKey = document.getElementById("apiKey").value.trim();
    const prompt = document.getElementById("prompt").value.trim();
    const ratio = document.getElementById("ratio").value;
    const duration = document.getElementById("duration").value;

    if (!apiKey) { alert("Age Agnes API Key dao!"); return; }
    if (!prompt) { alert("Prompt likho!"); return; }

    localStorage.setItem("agnes_api_key", apiKey);
    document.getElementById("genBtn").disabled = true;
    document.getElementById("stopBtn").style.display = "block";
    document.getElementById("videoPlayer").style.display = "none";
    document.getElementById("dlRow").style.display = "none";
    document.getElementById("statusText").textContent = "SUBMITTING...";
    document.getElementById("progressText").textContent = "5%";

    log("Server-e request pathano hocche...");

    try {
      const res = await fetch("/api/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ api_key: apiKey, prompt, ratio, duration })
      });

      const rawText = await res.text();
      let data;
      try {
        data = JSON.parse(rawText);
      } catch {
        throw new Error(`Server Response (${res.status}): ${rawText.slice(0, 200)}`);
      }

      if (!res.ok) {
        throw new Error(parseError(data));
      }

      const taskId = data.task_id;
      log(`Task create hoyeche! ID: ${taskId}`);
      document.getElementById("statusText").textContent = "PROCESSING...";
      document.getElementById("progressText").textContent = "15%";

      pollStatus(taskId, apiKey);
    } catch (err) {
      log(`Error: ${err.message || err}`, true);
      resetUI("FAILED");
    }
  }

  function pollStatus(taskId, apiKey) {
    let attempts = 0;
    pollTimer = setInterval(async () => {
      attempts++;
      try {
        const res = await fetch(`/api/status?task_id=${encodeURIComponent(taskId)}`, {
          headers: { "x-api-key": apiKey }
        });
        const rawText = await res.text();
        let data;
        try {
          data = JSON.parse(rawText);
        } catch {
          throw new Error(`Status Parse Error (${res.status}): ${rawText.slice(0, 150)}`);
        }

        if (!res.ok) {
          throw new Error(parseError(data));
        }

        const st = (data.status || "").toLowerCase();
        const fakePct = Math.min(95, 15 + attempts * 3);
        document.getElementById("progressText").textContent = `${fakePct}%`;

        if (st === "completed" || st === "succeeded" || data.video_url) {
          clearInterval(pollTimer);
          currentVideoUrl = data.video_url;
          log("Video ready hoyeche!");
          showVideo(currentVideoUrl);
          resetUI("COMPLETED", "100%");
        } else if (st === "failed" || st === "error") {
          clearInterval(pollTimer);
          throw new Error(parseError(data) || "Agnes video generate korte pare ni.");
        } else {
          log(`Queue-te kaj cholche... (${st || "pending"}) [${attempts * 5}s]`);
        }
      } catch (err) {
