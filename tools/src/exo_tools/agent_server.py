# type: ignore
"""FastAPI server for local agentic chat and coding runs."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from exo_tools.agent_core import AgentRunner, AgentStore
from exo_tools.agent_core.schemas import Message


class CreateSessionRequest(BaseModel):
    title: str = "Untitled session"
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatRequest(BaseModel):
    message: str
    context: dict[str, Any] = Field(default_factory=dict)


class CodingRunRequest(BaseModel):
    prompt: str
    source_dir: str
    target: dict[str, Any] = Field(default_factory=dict)
    limits: dict[str, Any] = Field(default_factory=dict)
    sandbox: dict[str, Any] = Field(default_factory=dict)


class RestoreRunRequest(BaseModel):
    target_dir: str


UI_HTML = r"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Agentic Local Server</title>
  <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.8/dist/katex.min.css">
  <script src="https://cdn.jsdelivr.net/npm/katex@0.16.8/dist/katex.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/katex@0.16.8/dist/contrib/auto-render.min.js"></script>
  <style>

    :root {
      color-scheme: light;
      --bg: #ffffff;
      --sidebar: #f7f7f8;
      --hover: #ececef;
      --panel: #ffffff;
      --line: #e3e3e7;
      --line-strong: #d0d0d6;
      --text: #171717;
      --muted: #6f6f78;
      --soft: #f4f4f5;
      --accent: #111111;
      --accent-weak: #2f2f2f;
      --danger: #b42318;
      --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      --sans: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    * { box-sizing: border-box; }
    html, body { height: 100%; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: var(--sans);
      font-size: 14px;
      line-height: 1.45;
    }
    button, input, textarea, select { font: inherit; }
    button {
      border: 0;
      background: transparent;
      color: inherit;
      cursor: pointer;
    }
    button:disabled { opacity: 0.5; cursor: not-allowed; }
    .app {
      display: grid;
      grid-template-columns: 250px minmax(0, 1fr);
      min-height: 100vh;
      background: var(--bg);
    }
    .sidebar {
      display: grid;
      grid-template-rows: auto auto 1fr auto;
      gap: 12px;
      min-height: 100vh;
      border-right: 1px solid var(--line);
      background: var(--sidebar);
      padding: 12px 8px;
    }
    .brand {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      padding: 6px 8px 8px;
    }
    .brand-title {
      display: flex;
      align-items: baseline;
      gap: 6px;
      min-width: 0;
      font-size: 18px;
      font-weight: 700;
      letter-spacing: 0;
    }
    .brand-title span {
      color: var(--muted);
      font-size: 13px;
      font-weight: 600;
    }
    .icon-btn {
      width: 34px;
      height: 34px;
      display: inline-grid;
      place-items: center;
      border-radius: 10px;
      color: #303038;
    }
    .icon-btn:hover { background: var(--hover); }
    .nav {
      display: grid;
      gap: 2px;
    }
    .nav-item,
    .new-chat {
      width: 100%;
      display: flex;
      align-items: center;
      gap: 10px;
      min-height: 36px;
      border-radius: 10px;
      padding: 8px 10px;
      text-align: left;
      color: #202025;
      font-weight: 520;
    }
    .nav-item:hover,
    .new-chat:hover,
    .session:hover { background: var(--hover); }
    .glyph {
      width: 18px;
      height: 18px;
      display: inline-grid;
      place-items: center;
      flex: 0 0 18px;
      color: #3a3a42;
      font-size: 17px;
      line-height: 1;
    }
    .session-area {
      min-height: 0;
      overflow: auto;
      padding: 4px 0;
    }
    .side-heading {
      margin: 0;
      padding: 12px 10px 6px;
      color: #5f5f68;
      font-size: 12px;
      font-weight: 650;
      letter-spacing: 0;
    }
    .session-list {
      display: grid;
      gap: 2px;
    }
    .session {
      width: 100%;
      min-height: 36px;
      border-radius: 10px;
      padding: 8px 10px;
      text-align: left;
      color: #24242a;
    }
    .session.active {
      background: #e9e9eb;
      color: #111;
    }
    .session-title {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-size: 14px;
      font-weight: 520;
    }
    .session-meta { display: none; }
    .profile {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 10px;
      border-top: 1px solid var(--line);
    }
    .avatar {
      width: 28px;
      height: 28px;
      display: grid;
      place-items: center;
      border-radius: 50%;
      background: #4c8bf5;
      color: #fff;
      font-size: 12px;
      font-weight: 700;
    }
    .profile-main {
      min-width: 0;
      display: grid;
      gap: 1px;
    }
    .profile-name {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-weight: 650;
    }
    .profile-sub {
      color: var(--muted);
      font-size: 12px;
    }
    .workspace {
      min-width: 0;
      min-height: 100vh;
      display: grid;
      grid-template-rows: 56px minmax(0, 1fr);
    }
    .topbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 14px;
      padding: 0 20px;
      background: rgba(255, 255, 255, 0.92);
      backdrop-filter: blur(16px);
    }
    .model-pill {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      min-width: 0;
      max-width: min(560px, 60vw);
      height: 36px;
      border-radius: 10px;
      padding: 0 10px;
      color: #34343a;
      font-weight: 650;
    }
    .model-pill:hover { background: var(--soft); }
    .model-name {
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .status {
      min-height: 18px;
      color: var(--muted);
      font-family: var(--mono);
      font-size: 12px;
    }
    .error { color: var(--danger); }
    .stage {
      min-width: 0;
      min-height: 0;
      display: grid;
      grid-template-rows: minmax(0, 1fr) auto;
      position: relative;
    }
    .chat-pane {
      min-height: 0;
      overflow: auto;
      padding: 8px 24px 24px;
    }
    .welcome {
      min-height: calc(100vh - 220px);
      display: flex;
      align-items: center;
      justify-content: center;
      text-align: center;
      padding: 28px 0;
    }
    .welcome h1 {
      margin: 0;
      font-size: clamp(25px, 3vw, 32px);
      line-height: 1.2;
      font-weight: 700;
      letter-spacing: 0;
    }
    .hidden { display: none !important; }
    .chat-log {
      width: min(820px, 100%);
      display: grid;
      gap: 22px;
      margin: 0 auto;
      padding: 24px 0 120px;
    }
    .msg {
      display: grid;
      gap: 8px;
      max-width: 100%;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }
    .msg.user {
      justify-items: end;
    }
    .msg.assistant {
      justify-items: start;
    }
    .msg-role {
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
      text-transform: capitalize;
    }
    .msg-body {
      max-width: min(690px, 100%);
      padding: 12px 15px;
      border-radius: 18px;
      background: var(--soft);
      color: var(--text);
      line-height: 1.55;
    }
    .msg.assistant .msg-body {
      padding: 4px 0;
      border-radius: 0;
      background: transparent;
    }
    .composer-wrap {
      position: sticky;
      bottom: 0;
      display: grid;
      justify-items: center;
      gap: 10px;
      padding: 10px 24px 22px;
      background: linear-gradient(to top, #fff 78%, rgba(255,255,255,0));
    }
    .attachments-preview {
      grid-column: 1 / -1;
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      padding: 6px 8px 4px;
      border-bottom: 1px solid var(--line);
      margin-bottom: 4px;
    }
    .attachment-pill {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      background: var(--soft);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 4px 10px;
      font-size: 12px;
      color: var(--text);
      max-width: 200px;
    }
    .attachment-pill span {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .attachment-pill img {
      width: 20px;
      height: 20px;
      object-fit: cover;
      border-radius: 4px;
    }
    .attachment-pill .remove-attachment {
      cursor: pointer;
      font-weight: bold;
      color: var(--muted);
      margin-left: 2px;
    }
    .attachment-pill .remove-attachment:hover {
      color: var(--danger);
    }
    .msg-attachments {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 8px;
    }
    .msg-attachment-pill {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      background: rgba(0, 0, 0, 0.04);
      border: 1px solid rgba(0, 0, 0, 0.08);
      border-radius: 12px;
      padding: 4px 10px;
      font-size: 12px;
      color: var(--text);
      cursor: pointer;
    }
    .msg-attachment-pill:hover {
      background: rgba(0, 0, 0, 0.08);
    }
    .msg-attachment-pill img {
      max-width: 120px;
      max-height: 120px;
      object-fit: contain;
      border-radius: 6px;
    }
    .cursor {
      display: inline-block;
      width: 2px;
      height: 1em;
      background: currentColor;
      margin-left: 2px;
      vertical-align: middle;
      animation: blink 1s step-start infinite;
    }
    @keyframes blink {
      50% { opacity: 0; }
    }
    .msg-body table {
      border-collapse: collapse;
      width: 100%;
      margin: 8px 0;
    }
    .msg-body th, .msg-body td {
      border: 1px solid var(--line);
      padding: 6px 10px;
      text-align: left;
    }
    .msg-body th {
      background: var(--soft);
      font-weight: 600;
    }
    .msg-body ul, .msg-body ol {
      margin: 6px 0;
      padding-left: 20px;
    }
    .msg-body p {
      margin: 6px 0;
    }
    .msg-body pre {
      background: #1e1e24 !important;
      color: #e3e3e3 !important;
      border-radius: 8px;
      padding: 10px;
      overflow-x: auto;
      max-width: 100%;
    }

    .composer {
      width: min(780px, 100%);
      display: grid;
      grid-template-columns: auto minmax(0, 1fr) auto auto;
      align-items: end;
      gap: 8px;
      min-height: 54px;
      border: 1px solid var(--line);
      border-radius: 28px;
      background: #fff;
      padding: 8px;
      box-shadow: 0 10px 30px rgba(0,0,0,0.08);
    }
    .composer:focus-within {
      border-color: var(--line-strong);
      box-shadow: 0 12px 34px rgba(0,0,0,0.11);
    }
    .composer textarea {
      width: 100%;
      min-height: 36px;
      max-height: 180px;
      border: 0;
      outline: 0;
      resize: none;
      padding: 9px 4px 7px;
      color: var(--text);
      font-family: var(--sans);
      line-height: 1.45;
    }
    .round-btn {
      width: 38px;
      height: 38px;
      display: inline-grid;
      place-items: center;
      flex: 0 0 38px;
      border-radius: 50%;
      background: #fff;
      color: #222;
    }
    .round-btn:hover { background: var(--soft); }
    .send-btn {
      background: var(--accent);
      color: white;
    }
    .send-btn:hover { background: var(--accent-weak); }
    .mode-tabs {
      display: inline-flex;
      gap: 3px;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: #fff;
      padding: 3px;
    }
    .tab {
      min-width: 66px;
      border-radius: 999px;
      padding: 7px 12px;
      color: var(--muted);
      font-weight: 650;
    }
    .tab.active {
      background: var(--accent);
      color: #fff;
    }
    .tools-panel {
      width: min(780px, calc(100% - 48px));
      margin: 0 auto 12px;
      border: 1px solid var(--line);
      border-radius: 18px;
      background: #fff;
      padding: 14px;
      box-shadow: 0 8px 24px rgba(0,0,0,0.06);
    }
    .run-grid {
      display: grid;
      grid-template-columns: 1fr 180px 118px;
      gap: 10px;
      align-items: end;
    }
    label {
      display: grid;
      gap: 7px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
    }
    input, select {
      width: 100%;
      height: 40px;
      border: 1px solid var(--line);
      background: #fff;
      border-radius: 12px;
      outline: 0;
      padding: 0 12px;
      color: var(--text);
    }
    input:focus, select:focus { border-color: var(--line-strong); }
    .run-action {
      height: 40px;
      border-radius: 12px;
      background: var(--accent);
      color: #fff;
      font-weight: 700;
    }
    .run-action:hover { background: var(--accent-weak); }
    pre {
      width: min(780px, calc(100% - 48px));
      margin: 0 auto 120px;
      min-height: 180px;
      max-height: 42vh;
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 16px;
      background: #111318;
      color: #d7e0ea;
      padding: 14px;
      font-family: var(--mono);
      font-size: 12px;
      line-height: 1.5;
    }
    .quick-actions {
      width: min(780px, 100%);
      display: flex;
      justify-content: center;
      gap: 8px;
      flex-wrap: wrap;
    }
    .chip {
      min-height: 36px;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 8px 13px;
      background: #fff;
      color: var(--muted);
      font-weight: 650;
    }
    .chip:hover { background: var(--soft); }
    @media (max-width: 820px) {
      .app { grid-template-columns: 1fr; }
      .sidebar {
        min-height: auto;
        grid-template-rows: auto auto;
        border-right: 0;
        border-bottom: 1px solid var(--line);
      }
      .session-area,
      .profile { display: none; }
      .workspace { min-height: calc(100vh - 132px); }
      .topbar { padding: 0 12px; }
      .run-grid { grid-template-columns: 1fr; }
      .composer {
        grid-template-columns: auto minmax(0, 1fr) auto;
      }
      .composer .mode-tabs { display: none; }
      .chat-pane { padding: 8px 14px 18px; }
      .composer-wrap { padding: 10px 14px 18px; }
    }
  </style>
</head>
<body>
  <div class="app">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-title">Agentic <span>Local</span></div>
        <button class="icon-btn" id="refresh" title="Refresh">↻</button>
      </div>
      <nav class="nav">
        <button class="new-chat" id="create"><span class="glyph">＋</span><span>새 채팅</span></button>
        <button class="nav-item" data-mode="chat"><span class="glyph">⌕</span><span>대화</span></button>
        <button class="nav-item" data-mode="run"><span class="glyph">⌘</span><span>실행</span></button>
      </nav>
      <div class="session-area">
        <h2 class="side-heading">최근</h2>
        <div class="session-list" id="sessions"></div>
      </div>
      <div class="profile">
        <div class="avatar">AL</div>
        <div class="profile-main">
          <div class="profile-name">Agentic Local Server</div>
          <div class="profile-sub" id="backend">backend: loading</div>
        </div>
      </div>
    </aside>

    <main class="workspace">
      <header class="topbar">
        <button class="model-pill" id="title-focus">
          <span class="model-name">Qwen3.6 Agent</span>
          <span>⌄</span>
        </button>
        <div class="status" id="status"></div>
      </header>

      <section class="stage">
        <div class="chat-pane" id="chat-pane">
          <div class="welcome" id="welcome">
            <h1>지금 무엇을 도와드릴까요?</h1>
          </div>
          <div class="chat-log hidden" id="chatlog"></div>

          <div id="run-tab" class="hidden">
            <div class="tools-panel">
              <div class="run-grid">
                <label>Source Directory
                  <input id="source" value="/Users/dshs_llm/exo">
                </label>
                <label>Profile
                  <select id="profile">
                    <option value="coding">coding</option>
                    <option value="document">document</option>
                    <option value="ocr">ocr</option>
                    <option value="korean_document">korean_document</option>
                    <option value="full">full</option>
                  </select>
                </label>
                <label>Iterations
                  <input id="iterations" type="number" min="1" max="40" value="6">
                </label>
              </div>
            </div>
            <pre id="result"></pre>
          </div>
        </div>

        <div class="composer-wrap">
          <div class="quick-actions" id="quick-actions">
            <button class="chip" data-prompt="LLM 서버 상태를 요약해줘">서버 상태</button>
            <button class="chip" data-prompt="이 프로젝트 구조를 짧게 분석해줘">프로젝트 분석</button>
            <button class="chip" data-prompt="Mac mini LLM 구성에서 다음 점검 항목을 알려줘">클러스터 점검</button>
          </div>
          <div class="composer">
            <div id="composer-attachments" class="attachments-preview hidden"></div>
            <button class="round-btn" id="attach-btn" title="파일 및 사진 첨부">＋</button>
            <input type="file" id="file-input" style="display: none;" multiple>
            <textarea id="message" placeholder="무엇이든 물어보세요"></textarea>
            <div class="mode-tabs">
              <button class="tab active" data-tab="chat">Chat</button>
              <button class="tab" data-tab="run">Run</button>
            </div>
            <button class="round-btn send-btn" id="send" title="Send">↑</button>
            <button class="round-btn send-btn hidden" id="run" title="Run">▶</button>
          </div>
        </div>
      </section>
    </main>
  </div>
  <script>
    const state = { sessionId: null, sessions: [], activeTab: "chat", chatMd: "" };
    const $ = (id) => document.getElementById(id);

    function setStatus(text, error = false) {
      $("status").textContent = text;
      $("status").className = error ? "status error" : "status";
    }

    function setBusy(isBusy) {
      $("send").disabled = isBusy;
      $("run").disabled = isBusy;
      $("create").disabled = isBusy;
      $("message").disabled = isBusy;
    }

    async function api(path, options = {}) {
      const res = await fetch(path, {
        headers: { "Content-Type": "application/json", ...(options.headers || {}) },
        ...options,
      });
      const text = await res.text();
      let data = {};
      if (text) {
        try { data = JSON.parse(text); } catch { data = { text }; }
      }
      if (!res.ok) {
        throw new Error(data.detail || data.error?.message || res.statusText);
      }
      return data;
    }

    function renderSessions() {
      const box = $("sessions");
      box.innerHTML = "";
      for (const session of state.sessions) {
        const button = document.createElement("button");
        button.className = "session" + (session.session_id === state.sessionId ? " active" : "");
        button.innerHTML = `<div class="session-title"></div><div class="session-meta"></div>`;
        button.querySelector(".session-title").textContent = session.title;
        button.querySelector(".session-meta").textContent = session.session_id;
        button.onclick = () => selectSession(session.session_id);
        box.appendChild(button);
      }
    }

    let selectedFiles = [];

    function escapeHtml(text) {
      return (text || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
    }

    function renderMarkdownAndMath(text, element) {
      if (!text) {
        element.innerHTML = "";
        return;
      }
      
      const mathBlocks = [];
      
      // Protect display math $$ ... $$
      text = text.replace(/\$\$([\s\S]+?)\$\$/g, (match, math) => {
        const id = `__MATH_BLOCK_${mathBlocks.length}__`;
        mathBlocks.push({ id, math, display: true });
        return id;
      });

      // Protect inline math $ ... $
      text = text.replace(/\$([^\$\n]+?)\$/g, (match, math) => {
        const id = `__MATH_BLOCK_${mathBlocks.length}__`;
        mathBlocks.push({ id, math, display: false });
        return id;
      });

      // Protect inline math \( ... \)
      text = text.replace(/\\\(([\s\S]+?)\\\)/g, (match, math) => {
        const id = `__MATH_BLOCK_${mathBlocks.length}__`;
        mathBlocks.push({ id, math, display: false });
        return id;
      });

      // Protect display math \[ ... \]
      text = text.replace(/\\\[([\s\S]+?)\\\]/g, (match, math) => {
        const id = `__MATH_BLOCK_${mathBlocks.length}__`;
        mathBlocks.push({ id, math, display: true });
        return id;
      });

      // Parse markdown
      let html = "";
      try {
        html = marked.parse(text);
      } catch (e) {
        html = escapeHtml(text);
      }

      // Restore and render math using KaTeX
      for (const block of mathBlocks) {
        let renderedMath = "";
        try {
          renderedMath = katex.renderToString(block.math, {
            displayMode: block.display,
            throwOnError: false
          });
        } catch (e) {
          renderedMath = escapeHtml(block.math);
        }
        html = html.replace(block.id, renderedMath);
      }

      element.innerHTML = html;
    }

    function cleanUserMessageAndExtractAttachments(content) {
      let cleaned = content;
      const attachments = [];
      
      // Extract file attachment blocks
      const fileRegex = /--- File Attachment: (.*?) ---\n([\s\S]*?)\n--- End File ---/g;
      let match;
      while ((match = fileRegex.exec(content)) !== null) {
        attachments.push({ name: match[1], type: 'text/plain', previewText: match[2].slice(0, 100) });
      }
      cleaned = cleaned.replace(fileRegex, "").trim();

      // Extract image blocks
      const imgRegex = /\[Attached Image: (.*?)\]/g;
      while ((match = imgRegex.exec(content)) !== null) {
        attachments.push({ name: match[1], type: 'image/jpeg' });
      }
      cleaned = cleaned.replace(imgRegex, "").trim();

      return { text: cleaned, attachments };
    }

    function parseChat(md) {
      const chunks = [];
      const lines = md.split("\n");
      let current = null;
      for (const line of lines) {
        const normalized = line.toLowerCase();
        if (normalized.startsWith("## user")) {
          current = { role: "user", content: "" };
          chunks.push(current);
        } else if (normalized.startsWith("## assistant")) {
          current = { role: "assistant", content: "" };
          chunks.push(current);
        } else if (current && !line.startsWith("# ")) {
          current.content += (current.content ? "\n" : "") + line;
        }
      }
      return chunks.map((item) => ({ ...item, content: item.content.trim() })).filter((item) => item.content);
    }

    function renderChat(chatMd) {
      const log = $("chatlog");
      const messages = parseChat(chatMd || "");
      state.chatMd = chatMd || "";
      log.innerHTML = "";
      for (const msg of messages) {
        const item = document.createElement("div");
        item.className = "msg " + msg.role;
        const role = document.createElement("div");
        role.className = "msg-role";
        role.textContent = msg.role;
        const body = document.createElement("div");
        body.className = "msg-body";
        
        if (msg.role === "user") {
          const { text, attachments } = cleanUserMessageAndExtractAttachments(msg.content);
          body.textContent = text;
          
          if (attachments.length > 0) {
            const attachContainer = document.createElement("div");
            attachContainer.className = "msg-attachments";
            attachments.forEach(file => {
              const pill = document.createElement("div");
              pill.className = "msg-attachment-pill";
              pill.textContent = `📎 ${file.name}`;
              if (file.previewText) {
                pill.title = file.previewText + "...";
              }
              attachContainer.appendChild(pill);
            });
            body.appendChild(attachContainer);
          }
        } else {
          renderMarkdownAndMath(msg.content, body);
        }
        item.append(role, body);
        log.appendChild(item);
      }
      $("welcome").classList.toggle("hidden", messages.length > 0 || state.activeTab !== "chat");
      $("chatlog").classList.toggle("hidden", messages.length === 0 || state.activeTab !== "chat");
      $("quick-actions").classList.toggle("hidden", messages.length > 0 || state.activeTab !== "chat");
      $("chat-pane").scrollTop = $("chat-pane").scrollHeight;
    }

    async function loadSessions() {
      const data = await api("/sessions");
      state.sessions = data.sessions || [];
      if (!state.sessionId && state.sessions.length) {
        state.sessionId = state.sessions[0].session_id;
      }
      renderSessions();
      if (state.sessionId) {
        await selectSession(state.sessionId);
      }
    }

    async function selectSession(sessionId) {
      state.sessionId = sessionId;
      renderSessions();
      const data = await api(`/sessions/${sessionId}`);
      renderChat(data.chat_md || "");
      $("result").textContent = JSON.stringify(data.runs || [], null, 2);
    }

    async function createSession() {
      setBusy(true);
      setStatus("creating");
      const title = "Local session";
      const data = await api("/sessions", {
        method: "POST",
        body: JSON.stringify({ title, metadata: { ui: true } }),
      });
      state.sessionId = data.session_id;
      await loadSessions();
      $("message").focus();
      setStatus("ready");
      setBusy(false);
    }

    async function sendMessage() {
      if (!state.sessionId) await createSession();
      const message = $("message").value.trim();
      if (!message && selectedFiles.length === 0) return;
      
      let fullMessage = message;
      if (selectedFiles.length > 0) {
        fullMessage += "\n\n";
        selectedFiles.forEach(file => {
          if (file.content) {
            fullMessage += `--- File Attachment: ${file.name} ---\n${file.content}\n--- End File ---\n\n`;
          } else {
            fullMessage += `[Attached Image: ${file.name}]\n`;
          }
        });
      }

      $("message").value = "";
      const currentAttachments = [...selectedFiles];
      selectedFiles = [];
      renderAttachmentPreview();

      const log = $("chatlog");
      $("welcome").classList.add("hidden");
      $("quick-actions").classList.add("hidden");
      log.classList.remove("hidden");

      const userItem = document.createElement("div");
      userItem.className = "msg user";
      const userRole = document.createElement("div");
      userRole.className = "msg-role";
      userRole.textContent = "user";
      const userBody = document.createElement("div");
      userBody.className = "msg-body";
      userBody.textContent = message;
      
      if (currentAttachments.length > 0) {
        const attachContainer = document.createElement("div");
        attachContainer.className = "msg-attachments";
        currentAttachments.forEach(file => {
          const pill = document.createElement("div");
          pill.className = "msg-attachment-pill";
          pill.textContent = `📎 ${file.name}`;
          attachContainer.appendChild(pill);
        });
        userBody.appendChild(attachContainer);
      }
      userItem.append(userRole, userBody);
      log.appendChild(userItem);

      const assistantItem = document.createElement("div");
      assistantItem.className = "msg assistant";
      const assistantRole = document.createElement("div");
      assistantRole.className = "msg-role";
      assistantRole.textContent = "assistant";
      const assistantBody = document.createElement("div");
      assistantBody.className = "msg-body";
      assistantBody.innerHTML = `<span class="cursor"></span>`;
      assistantItem.append(assistantRole, assistantBody);
      log.appendChild(assistantItem);

      $("chat-pane").scrollTop = $("chat-pane").scrollHeight;

      setBusy(true);
      setStatus("generating");

      try {
        const response = await fetch(`/sessions/${state.sessionId}/messages/stream`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message: fullMessage, context: { ui: true } }),
        });

        if (!response.ok) {
          throw new Error(response.statusText);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let done = false;
        let responseText = "";

        while (!done) {
          const { value, done: readerDone } = await reader.read();
          done = readerDone;
          if (value) {
            responseText += decoder.decode(value, { stream: !done });
            renderMarkdownAndMath(responseText, assistantBody);
            if (!done) {
              assistantBody.innerHTML += `<span class="cursor"></span>`;
            }
            $("chat-pane").scrollTop = $("chat-pane").scrollHeight;
          }
        }
      } catch (e) {
        setStatus(e.message, true);
        assistantBody.textContent = "에러 발생: " + e.message;
      } finally {
        await selectSession(state.sessionId);
        setStatus("ready");
        setBusy(false);
      }
    }


    async function runAgent() {
      if (!state.sessionId) await createSession();
      const prompt = $("message").value.trim() || "Inspect the project and finish with a short summary.";
      setStatus("running");
      setBusy(true);
      const body = {
        prompt,
        source_dir: $("source").value,
        limits: { max_tool_iterations: Number($("iterations").value || 6), timeout_seconds: 600 },
        sandbox: { environment_profile: $("profile").value, create_venv: true, install_packages: false },
        target: { connect_type: "line" },
      };
      const data = await api(`/sessions/${state.sessionId}/runs`, {
        method: "POST",
        body: JSON.stringify(body),
      });
      $("result").textContent = JSON.stringify(data, null, 2);
      setStatus(data.status || "done");
      setBusy(false);
    }

    function switchTab(name) {
      state.activeTab = name;
      for (const button of document.querySelectorAll(".tab")) {
        button.classList.toggle("active", button.dataset.tab === name);
      }
      $("run-tab").classList.toggle("hidden", name !== "run");
      $("send").classList.toggle("hidden", name !== "chat");
      $("run").classList.toggle("hidden", name !== "run");
      renderChat(state.chatMd);
      $("message").placeholder = name === "run" ? "무엇을 실행할까요?" : "무엇이든 물어보세요";
    }

    $("create").onclick = () => createSession().catch((e) => setStatus(e.message, true));
    $("refresh").onclick = () => loadSessions().catch((e) => setStatus(e.message, true));
    $("send").onclick = () => sendMessage().catch((e) => setStatus(e.message, true));
    $("run").onclick = () => runAgent().catch((e) => setStatus(e.message, true));
    $("title-focus").onclick = () => $("message").focus();
    $("attach-btn").onclick = () => $("file-input").click();

    $("file-input").onchange = (e) => {
      const files = e.target.files;
      if (!files.length) return;
      
      for (let i = 0; i < files.length; i++) {
        const file = files[i];
        const reader = new FileReader();
        
        if (file.type.startsWith("image/")) {
          reader.onload = (event) => {
            selectedFiles.push({
              name: file.name,
              type: file.type,
              dataUrl: event.target.result,
              size: file.size
            });
            renderAttachmentPreview();
          };
          reader.readAsDataURL(file);
        } else {
          reader.onload = (event) => {
            selectedFiles.push({
              name: file.name,
              type: file.type,
              content: event.target.result,
              size: file.size
            });
            renderAttachmentPreview();
          };
          reader.readAsText(file);
        }
      }
      $("file-input").value = "";
    };

    function renderAttachmentPreview() {
      const preview = $("composer-attachments");
      if (selectedFiles.length === 0) {
        preview.classList.add("hidden");
        preview.innerHTML = "";
        return;
      }
      
      preview.classList.remove("hidden");
      preview.innerHTML = "";
      
      selectedFiles.forEach((file, index) => {
        const pill = document.createElement("div");
        pill.className = "attachment-pill";
        
        if (file.type.startsWith("image/") && file.dataUrl) {
          const img = document.createElement("img");
          img.src = file.dataUrl;
          pill.appendChild(img);
        } else {
          const icon = document.createElement("span");
          icon.textContent = "📎 ";
          pill.appendChild(icon);
        }
        
        const nameSpan = document.createElement("span");
        nameSpan.textContent = file.name;
        pill.appendChild(nameSpan);
        
        const removeBtn = document.createElement("span");
        removeBtn.className = "remove-attachment";
        removeBtn.textContent = " ×";
        removeBtn.onclick = () => {
          selectedFiles.splice(index, 1);
          renderAttachmentPreview();
        };
        pill.appendChild(removeBtn);
        
        preview.appendChild(pill);
      });
    }
    for (const button of document.querySelectorAll(".tab")) {
      button.onclick = () => switchTab(button.dataset.tab);
    }
    for (const button of document.querySelectorAll(".nav-item")) {
      button.onclick = () => switchTab(button.dataset.mode);
    }
    for (const button of document.querySelectorAll(".chip")) {
      button.onclick = () => {
        $("message").value = button.dataset.prompt || "";
        $("message").focus();
      };
    }
    $("message").addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        if (state.activeTab === "run") {
          runAgent().catch((e) => setStatus(e.message, true));
        } else {
          sendMessage().catch((e) => setStatus(e.message, true));
        }
      }
    });

    $("backend").textContent = "backend: local";
    loadSessions().then(() => setStatus("ready")).catch((e) => setStatus(e.message, true));
  </script>
</body>
</html>
"""


def create_app() -> FastAPI:
    store = AgentStore()
    runner = AgentRunner(store=store)
    app = FastAPI(title="Agentic Local Server")

    @app.get("/", response_class=HTMLResponse)
    def ui() -> str:
        cwd = str(Path.cwd().resolve())
        return UI_HTML.replace('value="/Users/dshs_llm/exo"', f'value="{cwd}"')

    @app.post("/sessions")
    def create_session(payload: CreateSessionRequest) -> dict[str, Any]:
        session = store.create_session(payload.title, payload.metadata)
        return session.to_dict()

    @app.get("/sessions")
    def list_sessions() -> dict[str, Any]:
        return {"sessions": [session.to_dict() for session in store.list_sessions()]}

    @app.get("/sessions/{session_id}")
    def get_session(session_id: str) -> dict[str, Any]:
        try:
            session = store.get_session(session_id)
            chat = (store.session_dir(session_id) / "chat.md").read_text(encoding="utf-8")
            runs = []
            for path in sorted((store.session_dir(session_id) / "runs").glob("*/manifest.json")):
                runs.append(json.loads(path.read_text(encoding="utf-8")))
            return {"session": session.to_dict(), "chat_md": chat, "runs": runs}
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail="session not found") from e

    @app.post("/sessions/{session_id}/messages")
    def chat(session_id: str, payload: ChatRequest) -> dict[str, Any]:
        try:
            response = runner.chat(session_id, payload.message, context=payload.context)
            return {"message": Message(role="assistant", content=response).to_dict()}
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail="session not found") from e

    @app.post("/sessions/{session_id}/messages/stream")
    def chat_stream(session_id: str, payload: ChatRequest) -> StreamingResponse:
        try:
            generator = runner.chat_stream(session_id, payload.message, context=payload.context)
            return StreamingResponse(generator, media_type="text/plain")
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail="session not found") from e


    @app.post("/sessions/{session_id}/runs")
    def run_coding(session_id: str, payload: CodingRunRequest) -> dict[str, Any]:
        try:
            store.get_session(session_id)
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail="session not found") from e
        run = runner.run_coding(
            session_id=session_id,
            prompt=payload.prompt,
            source_dir=Path(payload.source_dir),
            target={**payload.target, "sandbox": payload.sandbox},
            limits=payload.limits,
        )
        return run.to_dict()

    @app.get("/sessions/{session_id}/runs/{run_id}")
    def get_run(session_id: str, run_id: str) -> dict[str, Any]:
        manifest = store.run_dir(session_id, run_id) / "manifest.json"
        if not manifest.exists():
            raise HTTPException(status_code=404, detail="run not found")
        return {"manifest": json.loads(manifest.read_text(encoding="utf-8"))}

    @app.get("/sessions/{session_id}/runs/{run_id}/patch")
    def get_patch(session_id: str, run_id: str) -> dict[str, str]:
        try:
            return {"patch": store.read_run_artifact(session_id, run_id, "result.patch")}
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail="patch not found") from e

    @app.post("/sessions/{session_id}/runs/{run_id}/restore")
    def restore_run(session_id: str, run_id: str, payload: RestoreRunRequest) -> dict[str, bool]:
        try:
            runner.restore_run(session_id, run_id, Path(payload.target_dir))
            return {"ok": True}
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    @app.delete("/sessions/{session_id}/runs/{run_id}/workspace")
    def cleanup_workspace(session_id: str, run_id: str) -> dict[str, bool]:
        try:
            runner.cleanup_run_workspace(session_id, run_id)
            return {"ok": True}
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail="run not found") from e

    return app


app = create_app()


def main() -> None:
    import uvicorn

    host = os.environ.get("AGENTIC_HOST", "127.0.0.1")
    port = int(os.environ.get("AGENTIC_PORT", "8765"))
    uvicorn.run("exo_tools.agent_server:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
