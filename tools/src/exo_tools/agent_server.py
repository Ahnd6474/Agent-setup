# type: ignore
"""FastAPI server for local agentic chat."""

from __future__ import annotations

import hmac
import httpx
import json
import os
import sqlite3
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from exo_tools.agent_core import AgentRunner, AgentStore
from exo_tools.agent_core.auth import AuthService, AuthUser, LoginRateLimiter
from exo_tools.agent_core.jobs import ChatJobManager
from exo_tools.agent_core.schemas import Message

EXODUS_LOGO_PATH = Path(__file__).with_name("assets") / "exodus-logo.png"


class CreateSessionRequest(BaseModel):
    title: str = "Untitled session"
    metadata: dict[str, Any] = Field(default_factory=dict)


class UpdateSessionRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    model: str | None = None


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=262_144)
    context: dict[str, Any] = Field(default_factory=dict)
    reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh"] = "medium"
    images: list[
        Annotated[
            str,
            Field(
                max_length=12_000_000,
                pattern=r"^data:image/(jpeg|png|webp|gif);base64,",
            ),
        ]
    ] = Field(default_factory=list, max_length=4)


class ResourceRequest(BaseModel):
    compute_slots: int = Field(default=1, ge=1, le=8)
    compute_nodes: list[str] = Field(default_factory=lambda: ["node2", "node3", "node4"])
    disk_quota_bytes: int = Field(default=5_000_000_000, ge=1, le=20_000_000_000)
    memory_message_limit: int = Field(default=24, ge=1, le=200)
    memory_char_limit: int = Field(default=48_000, ge=1, le=1_000_000)


class LoginRequest(BaseModel):
    username: str
    password: str


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str


class UserCreateRequest(BaseModel):
    username: str
    password: str
    role: str = "user"


class RoleUpdateRequest(BaseModel):
    role: str


class PermissionUpdateRequest(BaseModel):
    permission: str
    allowed: bool


class UserStatusRequest(BaseModel):
    disabled: bool


LOGIN_HTML = """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Exodus Login</title>
  <link rel="icon" href="/assets/exodus-logo.png">
  <style>
    body { margin:0; min-height:100vh; display:grid; place-items:center; font:14px system-ui; background:#f6f6f7; color:#18181b; }
    main { width:min(380px,calc(100vw - 32px)); background:white; padding:28px; border:1px solid #ddd; border-radius:16px; }
    h1 { margin:0; font-size:24px; }
    .login-brand { display:flex; align-items:center; gap:12px; margin:0 0 24px; }
    .login-logo { width:42px; height:42px; border-radius:10px; object-fit:cover; border:1px solid #d8d8de; }
    label { display:grid; gap:6px; margin:14px 0; font-weight:600; }
    input,button,a { box-sizing:border-box; width:100%; min-height:42px; border-radius:9px; font:inherit; }
    input { border:1px solid #ccc; padding:0 12px; }
    button,a { display:grid; place-items:center; border:0; background:#18181b; color:white; text-decoration:none; cursor:pointer; }
    a { margin-top:10px; background:white; color:#18181b; border:1px solid #ccc; }
    #error { color:#b42318; min-height:20px; margin-top:12px; }
  </style>
</head>
<body><main>
  <div class="login-brand"><img class="login-logo" src="/assets/exodus-logo.png" alt="Exodus logo"><h1>Exodus</h1></div>
  <form id="login">
    <label>사용자 이름<input id="username" autocomplete="username" required></label>
    <label>비밀번호<input id="password" type="password" autocomplete="current-password" required></label>
    <button>로그인</button>
  </form>
  __GOOGLE_BUTTON__
  <div id="error"></div>
  <script>
    document.getElementById("login").onsubmit = async (event) => {
      event.preventDefault();
      const usernameVal = document.getElementById("username").value;
      const passwordVal = document.getElementById("password").value;
      const response = await fetch("/auth/login", {
        method:"POST", headers:{"Content-Type":"application/json"},
        body:JSON.stringify({username:usernameVal,password:passwordVal})
      });
      if (response.ok) location.href="/";
      else document.getElementById("error").textContent="로그인에 실패했습니다.";
    };
  </script>
</main></body></html>"""

ADMIN_HTML = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Exodus 계정 관리</title><link rel="icon" href="/assets/exodus-logo.png"><style>
body{font:14px system-ui;margin:0;background:#f6f6f7;color:#18181b}main{max-width:960px;margin:32px auto;padding:0 20px}
section{background:white;border:1px solid #ddd;border-radius:14px;padding:20px;margin:16px 0}
input,select,button{font:inherit;min-height:38px;border:1px solid #ccc;border-radius:8px;padding:0 10px}
button{background:#18181b;color:white;cursor:pointer}table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:10px;border-bottom:1px solid #eee}
.row{display:flex;gap:8px;flex-wrap:wrap}.muted{color:#666}a{color:inherit}</style></head>
<body><main><a href="/">← 채팅으로</a><h1>계정 및 권한 관리</h1>
<section><h2>계정 생성</h2><p class="muted" id="creation-help">권한을 확인하는 중입니다.</p><div class="row">
<input id="username" autocomplete="new-username" placeholder="변경 불가 사용자 이름"><input id="password" type="password" autocomplete="new-password" placeholder="12자 이상 임시 비밀번호">
<select id="role"><option value="user">일반 사용자</option><option value="admin">관리자 (master만 생성 가능)</option></select><button id="create">계정 생성</button>
</div><p class="muted" id="status"></p></section>
<section><h2>계정</h2><table><thead><tr><th>Username</th><th>Role</th><th>Google</th><th>권한 설정</th></tr></thead><tbody id="users"></tbody></table></section>
<script>
let csrf="";
async function api(path,options={}){const method=(options.method||"GET").toUpperCase();const response=await fetch(path,{...options,headers:{"Content-Type":"application/json",...(method!=="GET"?{"X-CSRF-Token":csrf}:{}),...(options.headers||{})}});const data=await response.json();if(!response.ok)throw new Error(data.detail||response.statusText);return data}
const statusElement=document.getElementById("status");
const usernameInput=document.getElementById("username");
const passwordInput=document.getElementById("password");
const roleSelectInput=document.getElementById("role");
const createButton=document.getElementById("create");
async function load(){const me=await api("/auth/me");csrf=me.csrf_token;const isMaster=me.user.role==="master";roleSelectInput.querySelector('option[value="admin"]').disabled=!isMaster;if(!isMaster&&roleSelectInput.value!=="user")roleSelectInput.value="user";document.getElementById("creation-help").textContent=isMaster?"일반 사용자와 admin 계정을 만들 수 있습니다.":"일반 사용자 계정만 만들 수 있습니다.";const data=await api("/admin/users");const body=document.getElementById("users");body.innerHTML="";for(const user of data.users){const row=document.createElement("tr");row.innerHTML=`<td>${user.username}</td><td></td><td>${user.google_email||"-"}</td><td></td>`;const roleCell=row.children[1];if(user.role==="master"){roleCell.textContent="master";}else{const roleSelect=document.createElement("select");roleSelect.innerHTML='<option value="user">user</option><option value="admin">admin</option>';roleSelect.value=user.role;const roleSave=document.createElement("button");roleSave.textContent="역할 저장";roleSave.onclick=async()=>{await api(`/admin/users/${user.user_id}/role`,{method:"PUT",body:JSON.stringify({role:roleSelect.value})});statusElement.textContent="역할 저장 완료";await load()};roleCell.append(roleSelect,roleSave);}const cell=row.lastElementChild;const input=document.createElement("input");input.placeholder="예: resources:update";const select=document.createElement("select");select.innerHTML='<option value="true">allow</option><option value="false">deny</option>';const save=document.createElement("button");save.textContent="저장";save.onclick=async()=>{await api(`/admin/users/${user.user_id}/permissions`,{method:"PUT",body:JSON.stringify({permission:input.value,allowed:select.value==="true"})});statusElement.textContent="권한 저장 완료"};cell.append(input,select,save);body.appendChild(row)}}
createButton.onclick=async()=>{try{await api("/admin/users",{method:"POST",body:JSON.stringify({username:usernameInput.value,password:passwordInput.value,role:roleSelectInput.value})});statusElement.textContent=`${roleSelectInput.value} 계정 생성 완료`;usernameInput.value="";passwordInput.value="";await load()}catch(error){statusElement.textContent=error.message}};
load().catch(error=>{statusElement.textContent=error.message});
</script></main></body></html>"""

ACCOUNT_HTML = """<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Exodus 내 계정</title><link rel="icon" href="/assets/exodus-logo.png"><style>body{font:14px system-ui;background:#f6f6f7}main{max-width:480px;margin:40px auto;background:white;padding:24px;border:1px solid #ddd;border-radius:14px}
label{display:grid;gap:6px;margin:12px 0}input,button,a{box-sizing:border-box;width:100%;min-height:40px;border-radius:8px;font:inherit}input{border:1px solid #ccc;padding:0 10px}
button,a{display:grid;place-items:center;border:0;background:#18181b;color:white;text-decoration:none;margin-top:10px}#status{min-height:20px}</style></head>
<body><main><a href="/">← 채팅으로</a><h1>내 계정</h1><p id="identity"></p>
<label>현재 비밀번호<input id="current" type="password"></label><label>새 비밀번호<input id="next" type="password" minlength="12"></label>
<button id="change">비밀번호 변경</button><a id="google" href="/auth/google/start?link=true">Google 계정 연결</a><p id="status"></p>

<section style="margin-top: 24px; padding-top: 18px; border-top: 1px solid #eee;">
  <h2 style="font-size: 16px; margin: 0 0 12px;">Claude Code / VS Code 연동</h2>
  <p style="color: #666; font-size: 13px; margin: 0 0 14px; line-height: 1.4;">
    Exodus 계정의 API 토큰을 사용하여 터미널의 Claude Code나 VS Code(Cline, Continue 등)에서 클러스터의 고성능 exo 인프라를 사용할 수 있습니다.
  </p>

  <label>API 인증 토큰
    <div style="display: flex; gap: 8px;">
      <input id="api-token" readonly style="flex: 1; background: #f9f9fa; font-family: monospace; font-size: 12px; color: #555;">
      <button id="copy-token" style="width: 80px; min-height: 40px; margin-top: 0; background: #3f3f46;">복사</button>
    </div>
  </label>

  <label>터미널(Claude Code) 설정 명령어
    <div style="position: relative;">
      <textarea id="claude-cmd" readonly style="width: 100%; height: 72px; padding: 10px; font-family: monospace; font-size: 11px; border: 1px solid #ccc; border-radius: 8px; background: #1e1e24; color: #e3e3e3; resize: none;"></textarea>
      <button id="copy-cmd" style="margin-top: 8px; background: #18181b;">명령어 복사</button>
    </div>
  </label>
</section>

<script>let csrf="";
const apiTokenInput = document.getElementById("api-token");
const claudeCmdText = document.getElementById("claude-cmd");

async function start(){const response=await fetch("/auth/me");if(!response.ok){location.href="/login";return}const data=await response.json();csrf=data.csrf_token;identity.textContent=`${data.user.username} · ${data.user.role}`;google.style.display=data.google_enabled?"grid":"none";
const token = data.session_token || ""; apiTokenInput.value = token; const host = window.location.origin; claudeCmdText.value = `export ANTHROPIC_BASE_URL="${host}/v1"\\nexport ANTHROPIC_AUTH_TOKEN="${token}"`;}
change.onclick=async()=>{const response=await fetch("/auth/password",{method:"POST",headers:{"Content-Type":"application/json","X-CSRF-Token":csrf},body:JSON.stringify({current_password:current.value,new_password:next.value})});const data=await response.json();status.textContent=response.ok?"변경 완료. 다시 로그인하세요.":data.detail||"변경 실패";if(response.ok)setTimeout(()=>location.href="/login",700)};
document.getElementById("copy-token").onclick = () => { navigator.clipboard.writeText(apiTokenInput.value); alert("API 토큰이 복사되었습니다."); };
document.getElementById("copy-cmd").onclick = () => { navigator.clipboard.writeText(claudeCmdText.value); alert("Claude Code 설정 명령어가 복사되었습니다.\\n터미널에 붙여넣어 실행하세요."); };
start();</script>
</main></body></html>"""


UI_HTML = r"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Exodus</title>
  <link rel="icon" href="/assets/exodus-logo.png">
  <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.8/dist/katex.min.css">
  <script src="https://cdn.jsdelivr.net/npm/katex@0.16.8/dist/katex.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/katex@0.16.8/dist/contrib/auto-render.min.js"></script>
  <style>

    :root {
      color-scheme: light;
      --bg: #ffffff;
      --sidebar: #0b4f23;
      --sidebar-hover: rgba(255, 255, 255, 0.13);
      --sidebar-active: rgba(255, 255, 255, 0.20);
      --sidebar-line: rgba(255, 255, 255, 0.18);
      --sidebar-text: #f4fff6;
      --sidebar-muted: rgba(244, 255, 246, 0.68);
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
      min-height: 100dvh;
      background: var(--bg);
    }
    .sidebar {
      display: grid;
      grid-template-rows: auto auto 1fr auto;
      gap: 12px;
      position: sticky;
      top: 0;
      height: 100dvh;
      min-height: 0;
      border-right: 1px solid var(--sidebar-line);
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
      align-items: center;
      gap: 6px;
      min-width: 0;
      font-size: 18px;
      font-weight: 700;
      letter-spacing: 0;
    }
    .brand-logo {
      width: 30px;
      height: 30px;
      flex: 0 0 30px;
      border-radius: 8px;
      object-fit: cover;
      border: 1px solid rgba(255, 255, 255, 0.72);
      background: #fff;
    }
    .brand-title span {
      color: var(--muted);
      font-size: 13px;
      font-weight: 600;
    }
    .brand-title .brand-name {
      color: var(--sidebar-text);
      font-size: 18px;
      font-weight: 700;
    }
    .icon-btn {
      width: 34px;
      height: 34px;
      display: inline-grid;
      place-items: center;
      border-radius: 10px;
      color: var(--sidebar-text);
    }
    .icon-btn:hover { background: var(--sidebar-hover); }
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
      color: var(--sidebar-text);
      font-weight: 520;
    }
    .nav-item:hover,
    .new-chat:hover,
    .session:hover { background: var(--sidebar-hover); }
    .glyph {
      width: 18px;
      height: 18px;
      display: inline-grid;
      place-items: center;
      flex: 0 0 18px;
      color: var(--sidebar-muted);
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
      color: var(--sidebar-muted);
      font-size: 12px;
      font-weight: 650;
      letter-spacing: 0;
    }
    .session-list {
      display: grid;
      gap: 2px;
    }
    .session-row {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 30px;
      align-items: center;
      gap: 2px;
    }
    .session {
      width: 100%;
      min-height: 36px;
      border-radius: 10px;
      padding: 8px 10px;
      text-align: left;
      color: var(--sidebar-text);
    }
    .session.active {
      background: var(--sidebar-active);
      color: #ffffff;
    }
    .session-title {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-size: 14px;
      font-weight: 520;
    }
    .session-title-input {
      width: 100%;
      border: 1px solid rgba(255, 255, 255, 0.45);
      border-radius: 7px;
      padding: 3px 5px;
      font: inherit;
      color: #ffffff;
      background: rgba(255, 255, 255, 0.12);
      outline: none;
    }
    .session-meta { display: none; }
    .session-delete {
      width: 28px;
      height: 28px;
      border-radius: 8px;
      color: var(--sidebar-muted);
      opacity: 0;
    }
    .session-row:hover .session-delete,
    .session-delete:focus { opacity: 1; }
    .session-delete:hover {
      background: #fee4e2;
      color: var(--danger);
    }
    .profile {
      display: flex;
      align-items: center;
      align-self: end;
      gap: 10px;
      position: sticky;
      bottom: 0;
      z-index: 2;
      padding: 10px;
      border-top: 1px solid var(--sidebar-line);
      background: var(--sidebar);
      color: var(--sidebar-text);
    }
    .avatar {
      width: 28px;
      height: 28px;
      display: grid;
      place-items: center;
      border-radius: 50%;
      border: 1px solid rgba(255, 255, 255, 0.72);
      object-fit: cover;
      background: #fff;
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
      color: var(--sidebar-muted);
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
    .model-selector-wrap {
      display: inline-flex;
      align-items: center;
      min-width: 0;
      max-width: min(560px, 60vw);
    }
    .model-select-dropdown {
      height: 36px;
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 0 28px 0 12px;
      color: #34343a;
      font-weight: 650;
      background: var(--bg);
      cursor: pointer;
      font-size: 13px;
      outline: none;
      appearance: none;
      -webkit-appearance: none;
      background-image: url("data:image/svg+xml;charset=UTF-8,%3csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%2334343a' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3e%3cpolyline points='6 9 12 15 18 9'%3e%3c/polyline%3e%3c/svg%3e");
      background-repeat: no-repeat;
      background-position: right 10px center;
      background-size: 12px;
    }
    .model-select-dropdown:hover {
      background-color: var(--soft);
      border-color: var(--line-strong);
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
    .reasoning-trace {
      width: min(690px, 100%);
      margin: 0 0 12px;
      border-left: 2px solid var(--line-strong);
      padding-left: 12px;
      color: var(--muted);
    }
    .reasoning-trace summary {
      display: flex;
      align-items: center;
      gap: 6px;
      width: fit-content;
      cursor: pointer;
      list-style: none;
      font-size: 13px;
      font-weight: 650;
      user-select: none;
    }
    .reasoning-trace summary::-webkit-details-marker { display: none; }
    .reasoning-trace summary::after {
      content: "›";
      transition: transform 160ms ease;
    }
    .reasoning-trace[open] summary::after { transform: rotate(90deg); }
    .reasoning-content {
      max-height: 340px;
      overflow: auto;
      margin-top: 8px;
      padding: 4px 8px 4px 0;
      color: #5f5f68;
      font-size: 13px;
      line-height: 1.55;
    }
    .reasoning-content p { margin: 5px 0; }
    .answer-content:empty { display: none; }
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
      grid-template-columns: auto minmax(0, 1fr) auto auto auto;
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
    .reasoning-select {
      width: auto;
      min-width: 76px;
      height: 38px;
      border: 0;
      border-radius: 999px;
      background: var(--soft);
      padding: 0 28px 0 12px;
      color: #3f3f46;
      font-size: 12px;
      font-weight: 650;
      cursor: pointer;
    }
    .reasoning-select:hover,
    .reasoning-select:focus {
      background: var(--hover);
      border: 0;
    }
    .send-btn {
      background: var(--accent);
      color: white;
    }
    .send-btn:hover { background: var(--accent-weak); }
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
    /* Sidebar Backdrop (mobile only) */
    .sidebar-backdrop {
      position: fixed;
      top: 0;
      left: 0;
      right: 0;
      bottom: 0;
      background: rgba(0, 0, 0, 0.4);
      backdrop-filter: blur(2px);
      -webkit-backdrop-filter: blur(2px);
      z-index: 98;
      opacity: 0;
      pointer-events: none;
      transition: opacity 0.22s ease;
    }
    .sidebar-backdrop.show {
      opacity: 1;
      pointer-events: auto;
    }

    @media (max-width: 820px) {
      .app { grid-template-columns: 1fr; }
      .sidebar {
        position: fixed;
        top: 0;
        left: 0;
        bottom: 0;
        width: 280px;
        height: 100dvh;
        z-index: 99;
        transform: translateX(-100%);
        transition: transform 0.22s cubic-bezier(0.4, 0, 0.2, 1);
        border-right: 1px solid var(--sidebar-line);
        box-shadow: 10px 0 30px rgba(0,0,0,0.15);
        grid-template-rows: auto auto 1fr auto;
      }
      .sidebar.open {
        transform: translateX(0);
      }
      .session-area,
      .profile { display: grid !important; }
      .workspace {
        min-height: 100dvh;
        grid-template-rows: 56px minmax(0, 1fr);
      }
      .topbar {
        padding: 0 16px;
        border-bottom: 1px solid var(--line);
        justify-content: flex-start;
        gap: 12px;
      }
      .mobile-menu-btn {
        display: inline-grid !important;
        color: var(--text) !important;
        font-size: 22px !important;
        cursor: pointer;
        width: 38px;
        height: 38px;
        place-items: center;
        border-radius: 50%;
      }
      .mobile-menu-btn:hover {
        background: var(--soft);
      }
      .topbar > div {
        margin-left: auto;
        display: flex;
        align-items: center;
        gap: 8px;
      }
      .composer {
        grid-template-columns: auto minmax(0, 1fr) auto auto;
      }
      .chat-pane { padding: 8px 14px 18px; }
      .composer-wrap { padding: 10px 14px 18px; }
    }
  </style>
</head>
<body>
  <div class="app">
    <div class="sidebar-backdrop" id="sidebar-backdrop"></div>
    <aside class="sidebar" id="sidebar">
      <div class="brand">
        <div class="brand-title"><img class="brand-logo" src="/assets/exodus-logo.png" alt="Exodus logo"><span class="brand-name">Exodus</span></div>
        <button class="icon-btn" id="refresh" title="Refresh">↻</button>
      </div>
      <nav class="nav">
        <button class="new-chat" id="create"><span class="glyph">＋</span><span>새 채팅</span></button>
        <button class="nav-item" id="chat-link"><span class="glyph">⌕</span><span>대화</span></button>
        <button class="nav-item hidden" id="cluster-link"><span class="glyph">◫</span><span>리소스 관리</span></button>
        <button class="nav-item hidden" id="admin-link"><span class="glyph">⚙</span><span>계정 관리</span></button>
      </nav>
      <div class="session-area">
        <h2 class="side-heading">최근</h2>
        <div class="session-list" id="sessions"></div>
      </div>
      <div class="profile" id="account-link">
        <img class="avatar" src="/assets/exodus-logo.png" alt="Exodus logo">
        <div class="profile-main">
          <div class="profile-name">Exodus</div>
          <div class="profile-sub" id="backend">backend: loading</div>
        </div>
      </div>
    </aside>

    <main class="workspace">
      <header class="topbar">
        <button class="mobile-menu-btn" id="menu-btn" title="메뉴" style="display: none;">☰</button>
        <div class="model-selector-wrap">
          <select id="model-select" class="model-select-dropdown" title="모델 선택">
            <option value="mlx-community/Huihui-Qwen3.6-35B-A3B-abliterated-4.4bit-msq">Qwen3.6 35B (Uncensored)</option>
            <option value="mlx-community/gemma-4-31b-it-4bit">Gemma 4 31B (Standard)</option>
            <option value="mlx-community/DeepSeek-R1-Distill-Qwen-32B-abliterated">DeepSeek-R1 32B (Uncensored)</option>
          </select>
        </div>
        <div>
          <span class="status" id="status"></span>
          <button class="icon-btn" id="logout" title="로그아웃">⇥</button>
        </div>
      </header>

      <section class="stage">
        <div class="chat-pane" id="chat-pane">
          <div class="welcome" id="welcome">
            <h1>지금 무엇을 도와드릴까요?</h1>
          </div>
          <div class="chat-log hidden" id="chatlog"></div>

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
            <select
              id="reasoning-effort"
              class="reasoning-select"
              aria-label="추론 수준"
              title="추론 수준"
            >
              <option value="none">즉시</option>
              <option value="low">낮음</option>
              <option value="medium">중간</option>
              <option value="high">높음</option>
              <option value="xhigh">최고</option>
            </select>
            <button class="round-btn send-btn" id="send" title="Send">↑</button>
          </div>
        </div>
      </section>
    </main>
  </div>
  <script>
    const allowedReasoningEfforts = new Set(["none", "low", "medium", "high", "xhigh"]);
    const storedReasoningEffort = localStorage.getItem("agentic-reasoning-effort");
    const state = {
      sessionId: null,
      sessions: [],
      chatMd: "",
      csrfToken: "",
      user: null,
      permissions: [],
      pendingRequests: 0,
      reasoningEffort: allowedReasoningEfforts.has(storedReasoningEffort)
        ? storedReasoningEffort
        : "medium"
    };
    const $ = (id) => document.getElementById(id);
    $("reasoning-effort").value = state.reasoningEffort;

    function setStatus(text, error = false) {
      $("status").textContent = text;
      $("status").className = error ? "status error" : "status";
    }

    function setBusy(isBusy) {
      $("send").disabled = isBusy;
      $("create").disabled = isBusy;
      $("message").disabled = isBusy;
    }

    async function api(path, options = {}) {
      const method = (options.method || "GET").toUpperCase();
      const res = await fetch(path, {
        headers: {
          "Content-Type": "application/json",
          ...(method !== "GET" && state.csrfToken ? {"X-CSRF-Token": state.csrfToken} : {}),
          ...(options.headers || {})
        },
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
        const row = document.createElement("div");
        row.className = "session-row";
        const button = document.createElement("button");
        button.className = "session" + (session.session_id === state.sessionId ? " active" : "");
        button.innerHTML = `<div class="session-title"></div><div class="session-meta"></div>`;
        button.querySelector(".session-title").textContent = session.title;
        button.querySelector(".session-meta").textContent = session.session_id;
        button.onclick = () => selectSession(session.session_id);
        button.ondblclick = (event) => {
          event.preventDefault();
          event.stopPropagation();
          startRenameSession(session, button);
        };
        const deleteButton = document.createElement("button");
        deleteButton.className = "session-delete";
        deleteButton.title = "세션 삭제";
        deleteButton.setAttribute("aria-label", `${session.title} 삭제`);
        deleteButton.textContent = "×";
        deleteButton.onclick = (event) => {
          event.stopPropagation();
          deleteSession(session.session_id, session.title).catch(error => setStatus(error.message, true));
        };
        row.append(button, deleteButton);
        box.appendChild(row);
      }
    }

    async function refreshSessions() {
      const data = await api("/sessions");
      state.sessions = data.sessions || [];
      renderSessions();
    }

    function startRenameSession(session, button) {
      const titleBox = button.querySelector(".session-title");
      if (!titleBox || titleBox.querySelector("input")) return;
      const original = session.title || "";
      titleBox.innerHTML = "";
      const input = document.createElement("input");
      input.className = "session-title-input";
      input.value = original;
      titleBox.appendChild(input);
      let finished = false;
      const finish = async (save) => {
        if (finished) return;
        finished = true;
        const next = input.value.trim();
        renderSessions();
        if (!save || !next || next === original) return;
        try {
          const data = await api(`/sessions/${session.session_id}`, {
            method: "PUT",
            body: JSON.stringify({ title: next }),
          });
          const updated = data.session || data;
          state.sessions = state.sessions.map((item) =>
            item.session_id === session.session_id ? updated : item
          );
          renderSessions();
          setStatus("session renamed");
        } catch (error) {
          setStatus(error.message, true);
          await refreshSessions();
        }
      };
      input.onkeydown = (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          input.blur();
        } else if (event.key === "Escape") {
          event.preventDefault();
          finish(false).catch(error => setStatus(error.message, true));
        }
      };
      input.onblur = () => finish(true).catch(error => setStatus(error.message, true));
      input.focus();
      input.select();
    }

    async function deleteSession(sessionId, title) {
      if (!confirm(`"${title}" 세션을 삭제할까요?`)) return;
      await api(`/sessions/${sessionId}`, { method: "DELETE" });
      if (state.sessionId === sessionId) state.sessionId = null;
      await loadSessions();
      if (!state.sessionId) renderChat("", []);
      setStatus("session deleted");
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
        const id = `\uE000MATH${mathBlocks.length}\uE001`;
        mathBlocks.push({ id, math, display: true });
        return id;
      });

      // Protect inline math $ ... $
      text = text.replace(/\$([^\$\n]+?)\$/g, (match, math) => {
        const id = `\uE000MATH${mathBlocks.length}\uE001`;
        mathBlocks.push({ id, math, display: false });
        return id;
      });

      // Protect inline math \( ... \)
      text = text.replace(/\\\(([\s\S]+?)\\\)/g, (match, math) => {
        const id = `\uE000MATH${mathBlocks.length}\uE001`;
        mathBlocks.push({ id, math, display: false });
        return id;
      });

      // Protect display math \[ ... \]
      text = text.replace(/\\\[([\s\S]+?)\\\]/g, (match, math) => {
        const id = `\uE000MATH${mathBlocks.length}\uE001`;
        mathBlocks.push({ id, math, display: true });
        return id;
      });

      // Protect code blocks to avoid rendering bold/italic inside them
      const codeBlocks = [];
      text = text.replace(/```[\s\S]+?```/g, (match) => {
        const id = `\uE002CODE${codeBlocks.length}\uE003`;
        codeBlocks.push({ id, content: match });
        return id;
      });
      text = text.replace(/`[^`\n]+?`/g, (match) => {
        const id = `\uE002CODE${codeBlocks.length}\uE003`;
        codeBlocks.push({ id, content: match });
        return id;
      });

      // Fix GFM bold (**) and italic (*) not parsing when immediately followed by Korean characters
      text = text.replace(/\*\*([^\*]+?)\*\*(?=[\u1100-\u11ff\u3130-\u318f\uac00-\ud7a3])/g, '**$1**\u200B');
      text = text.replace(/\*([^\*]+?)\*(?=[\u1100-\u11ff\u3130-\u318f\uac00-\ud7a3])/g, '*$1*\u200B');

      // Restore code blocks
      for (const block of codeBlocks) {
        text = text.replace(block.id, block.content);
      }

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

      const documentFragment = new DOMParser().parseFromString(html, "text/html");
      for (const unsafe of documentFragment.querySelectorAll("script, iframe, object, embed, form, style")) {
        unsafe.remove();
      }
      for (const node of documentFragment.body.querySelectorAll("*")) {
        for (const attribute of [...node.attributes]) {
          const name = attribute.name.toLowerCase();
          const value = attribute.value.trim().toLowerCase();
          if (name.startsWith("on") || (["href", "src", "xlink:href"].includes(name) && value.startsWith("javascript:"))) {
            node.removeAttribute(attribute.name);
          }
        }
      }
      element.replaceChildren(...documentFragment.body.childNodes);
    }

    function splitThinkTaggedContent(content) {
      const text = content || "";
      const lower = text.toLowerCase();
      const startTag = "<think>";
      const endTag = "</think>";
      let answer = "";
      let reasoning = "";
      let index = 0;
      let inThink = false;
      let open = false;

      while (index < text.length) {
        const nextStart = lower.indexOf(startTag, index);
        const nextEnd = lower.indexOf(endTag, index);

        if (inThink) {
          if (nextEnd === -1) {
            reasoning += text.slice(index);
            open = true;
            break;
          }
          reasoning += text.slice(index, nextEnd);
          index = nextEnd + endTag.length;
          inThink = false;
          continue;
        }

        if (nextEnd !== -1 && (nextStart === -1 || nextEnd < nextStart)) {
          const prefix = text.slice(index, nextEnd);
          if (answer.trim()) answer += prefix;
          else reasoning += prefix;
          index = nextEnd + endTag.length;
          continue;
        }

        if (nextStart === -1) {
          answer += text.slice(index);
          break;
        }

        answer += text.slice(index, nextStart);
        index = nextStart + startTag.length;
        inThink = true;
      }

      return {
        answer: answer.replace(/^\s*\n/, ""),
        reasoning: reasoning.trim(),
        open,
      };
    }

    function normalizeAssistantContent(content, reasoning) {
      const split = splitThinkTaggedContent(content || "");
      const parts = [];
      if (reasoning) parts.push(reasoning.trim());
      if (split.reasoning) parts.push(split.reasoning);
      return {
        content: split.answer,
        reasoning: parts.join(parts.length > 1 ? "\n\n" : ""),
        thinkOpen: split.open,
      };
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

    function createReasoningTrace(reasoning, open = false) {
      const details = document.createElement("details");
      details.className = "reasoning-trace";
      details.open = open;
      const summary = document.createElement("summary");
      summary.textContent = "생각 과정";
      const content = document.createElement("div");
      content.className = "reasoning-content";
      renderMarkdownAndMath(reasoning, content);
      details.append(summary, content);
      return { details, summary, content };
    }

    function renderAssistantMessage(body, content, reasoning, pending = false) {
      const normalized = normalizeAssistantContent(content, reasoning);
      body.innerHTML = "";
      if (normalized.reasoning || normalized.thinkOpen) {
        const trace = createReasoningTrace(normalized.reasoning || "", pending && normalized.thinkOpen);
        if (pending && normalized.thinkOpen) trace.summary.textContent = "생각 중...";
        body.appendChild(trace.details);
      }
      const answer = document.createElement("div");
      answer.className = "answer-content";
      renderMarkdownAndMath(normalized.content, answer);
      if (pending) {
        const cursor = document.createElement("span");
        cursor.className = "cursor";
        answer.appendChild(cursor);
      }
      body.appendChild(answer);
    }

    function renderChat(chatMd, structuredMessages = null, pendingMessageIds = []) {
      const log = $("chatlog");
      const pending = new Set(pendingMessageIds || []);
      const messages = Array.isArray(structuredMessages)
        ? structuredMessages.filter(message => message.role === "user" || message.role === "assistant")
        : parseChat(chatMd || "");
      state.chatMd = chatMd || "";
      log.innerHTML = "";
      for (const msg of messages) {
        const item = document.createElement("div");
        item.className = "msg " + msg.role;
        if (msg.message_id) item.dataset.messageId = msg.message_id;
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
          renderAssistantMessage(
            body,
            msg.content,
            msg.reasoning,
            pending.has(msg.message_id),
          );
        }
        item.append(role, body);
        log.appendChild(item);
      }
      $("welcome").classList.toggle("hidden", messages.length > 0);
      $("chatlog").classList.toggle("hidden", messages.length === 0);
      $("quick-actions").classList.toggle("hidden", messages.length > 0);
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
      closeSidebar();
      state.sessionId = sessionId;
      renderSessions();
      const data = await api(`/sessions/${sessionId}`);
      if (state.sessionId !== sessionId) return;

      if (data.session && data.session.metadata && data.session.metadata.model) {
        $("model-select").value = data.session.metadata.model;
      } else {
        $("model-select").value = "mlx-community/Huihui-Qwen3.6-35B-A3B-abliterated-4.4bit-msq";
      }

      renderChat(
        data.chat_md || "",
        data.messages || null,
        data.pending_message_ids || [],
      );
    }

    async function createSession() {
      setBusy(true);
      setStatus("creating");
      const title = "새 채팅";
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
      const targetSessionId = state.sessionId;
      const requestReasoningEffort = state.reasoningEffort;

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
      const liveReasoning = createReasoningTrace("", true);
      if (requestReasoningEffort === "none") {
        liveReasoning.details.classList.add("hidden");
      }
      liveReasoning.summary.textContent = "생각 중...";
      const answerBody = document.createElement("div");
      answerBody.className = "answer-content";
      const cursor = document.createElement("span");
      cursor.className = "cursor";
      answerBody.appendChild(cursor);
      assistantBody.append(liveReasoning.details, answerBody);
      assistantItem.append(assistantRole, assistantBody);
      log.appendChild(assistantItem);

      $("chat-pane").scrollTop = $("chat-pane").scrollHeight;

      state.pendingRequests += 1;
      setStatus(`generating · ${state.pendingRequests}`);

      try {
        const response = await fetch(`/sessions/${targetSessionId}/messages/stream`, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRF-Token": state.csrfToken },
          body: JSON.stringify({
            message: fullMessage,
            context: { ui: true },
            reasoning_effort: requestReasoningEffort,
            images: currentAttachments
              .filter(file => file.type.startsWith("image/") && file.dataUrl)
              .map(file => file.dataUrl)
          }),
        });

        if (!response.ok) {
          throw new Error(response.statusText);
        }
        const assistantMessageId = response.headers.get("X-Assistant-Message-Id");
        if (assistantMessageId) {
          assistantItem.dataset.messageId = assistantMessageId;
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let done = false;
        let responseText = "";
        let reasoningText = "";
        let eventBuffer = "";

        const renderLiveResponse = (pending = true) => {
          if (state.sessionId !== targetSessionId) return;
          let currentItem = assistantItem.isConnected ? assistantItem : null;
          if (assistantMessageId) {
            currentItem = [...$("chatlog").querySelectorAll(".msg.assistant")]
              .find(item => item.dataset.messageId === assistantMessageId) || currentItem;
          }
          const currentBody = currentItem?.querySelector(".msg-body");
          if (!currentBody) return;
          renderAssistantMessage(
            currentBody,
            responseText,
            reasoningText,
            pending,
          );
          $("chat-pane").scrollTop = $("chat-pane").scrollHeight;
        };

        while (!done) {
          const { value, done: readerDone } = await reader.read();
          done = readerDone;
          if (value) {
            eventBuffer += decoder.decode(value, { stream: !done });
          }
          if (done) {
            eventBuffer += decoder.decode();
          }
          const lines = eventBuffer.split("\n");
          eventBuffer = lines.pop() || "";
          for (const line of lines) {
            if (!line.trim()) continue;
            const event = JSON.parse(line);
            if (event.type === "reasoning") {
              reasoningText += event.delta || "";
            } else if (event.type === "content") {
              responseText += event.delta || "";
            } else if (event.type === "tool") {
              setStatus(`tool · ${event.delta || "running"}`);
            } else if (event.type === "error") {
              const errorText = event.delta || "stream failed";
              responseText = responseText || `오류: ${errorText}`;
              setStatus(errorText, true);
            }
            renderLiveResponse(true);
          }
        }
        renderLiveResponse(false);
      } catch (e) {
        setStatus(e.message, true);
        if (assistantBody.isConnected) {
          assistantBody.textContent = "에러 발생: " + e.message;
        }
      } finally {
        state.pendingRequests = Math.max(0, state.pendingRequests - 1);
        if (state.sessionId === targetSessionId) {
          await refreshSessions();
          await selectSession(targetSessionId);
        }
        setStatus(
          state.pendingRequests ? `generating · ${state.pendingRequests}` : "ready",
        );
      }
    }


    function closeSidebar() {
      if (window.innerWidth <= 820) {
        $("sidebar").classList.remove("open");
        $("sidebar-backdrop").classList.remove("show");
      }
    }
    $("menu-btn").onclick = () => {
      $("sidebar").classList.add("open");
      $("sidebar-backdrop").classList.add("show");
    };
    $("sidebar-backdrop").onclick = closeSidebar;

    $("create").onclick = () => {
      closeSidebar();
      createSession().catch((e) => setStatus(e.message, true));
    };
    $("refresh").onclick = () => {
      closeSidebar();
      loadSessions().catch((e) => setStatus(e.message, true));
    };
    $("send").onclick = () => {
      closeSidebar();
      sendMessage().catch((e) => setStatus(e.message, true));
    };
    $("chat-link").onclick = () => {
      closeSidebar();
      $("message").focus();
    };
    $("model-select").onchange = async (event) => {
      const selectedModel = event.target.value;
      if (!state.sessionId) return;
      try {
        setBusy(true);
        setStatus("updating model");
        await api(`/sessions/${state.sessionId}`, {
          method: "PUT",
          body: JSON.stringify({ model: selectedModel }),
        });
        setStatus("model updated");
      } catch (error) {
        setStatus(error.message, true);
      } finally {
        setBusy(false);
      }
    };
    $("reasoning-effort").onchange = (event) => {
      state.reasoningEffort = event.target.value;
      localStorage.setItem("agentic-reasoning-effort", state.reasoningEffort);
    };
    $("attach-btn").onclick = () => $("file-input").click();
    $("logout").onclick = async () => {
      await api("/auth/logout", {method:"POST", body:"{}"});
      location.href="/login";
    };
    $("admin-link").onclick = () => {
      closeSidebar();
      location.href="/admin";
    };
    $("cluster-link").onclick = () => {
      closeSidebar();
      window.open("/cluster-control", "_blank", "noopener,noreferrer");
    };
    $("account-link").onclick = () => {
      closeSidebar();
      location.href="/account";
    };

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
    for (const button of document.querySelectorAll(".chip[data-prompt]")) {
      button.onclick = () => {
        $("message").value = button.dataset.prompt || "";
        $("message").focus();
      };
    }
    $("message").addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        sendMessage().catch((e) => setStatus(e.message, true));
      }
    });

    async function bootstrap() {
      const auth = await api("/auth/me");
      state.csrfToken = auth.csrf_token || "";
      state.user = auth.user;
      state.permissions = auth.permissions || [];
      $("admin-link").classList.toggle("hidden", !state.permissions.includes("users:read"));
      $("cluster-link").classList.toggle("hidden", !state.permissions.includes("cluster:manage"));
      $("backend").textContent = `${auth.user.username} · ${auth.user.role}`;
      await loadSessions();
      setStatus("ready");
    }
    bootstrap().catch((e) => {
      if (String(e.message).includes("authenticated")) location.href="/login";
      else setStatus(e.message, true);
    });
  </script>
</body>
</html>
"""


def create_app() -> FastAPI:
    store = AgentStore()
    runner = AgentRunner(store=store)
    jobs = ChatJobManager(runner)
    auth = AuthService(store.root / "auth")
    login_limiter = LoginRateLimiter()
    auth_disabled = os.environ.get("AGENTIC_AUTH_DISABLED", "").lower() == "true" or os.environ.get("EXO_TESTS") == "1"
    cookie_secure = os.environ.get("AGENTIC_PUBLIC_URL", "").startswith("https://")
    app = FastAPI(title="Exodus")
    allowed_hosts = [host.strip() for host in os.environ.get("AGENTIC_ALLOWED_HOSTS", "").split(",") if host.strip()]
    if allowed_hosts:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Any) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if cookie_secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    def request_identity(request: Request) -> tuple[AuthUser, str]:
        if auth_disabled:
            return AuthUser(0, "test-master", "master", None, False), "test-csrf"

        session_token = request.cookies.get("agentic_session")
        if not session_token:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.lower().startswith("bearer "):
                session_token = auth_header[7:].strip()
        if not session_token:
            session_token = request.headers.get("x-api-key")

        identity = auth.user_for_session(session_token)
        if identity is None:
            raise HTTPException(status_code=401, detail="authenticated session required")
        return identity

    def require(request: Request, permission: str, *, csrf: bool = False) -> AuthUser:
        user, csrf_token = request_identity(request)
        if csrf and not auth_disabled:
            supplied = request.headers.get("X-CSRF-Token", "")
            if not supplied or not hmac.compare_digest(supplied, csrf_token):
                raise HTTPException(status_code=403, detail="invalid CSRF token")
        try:
            auth.require(user, permission)
        except PermissionError as error:
            raise HTTPException(status_code=403, detail=str(error)) from error
        return user

    def require_session_access(request: Request, session_id: str, permission: str, *, csrf: bool = False) -> AuthUser:
        user = require(request, permission, csrf=csrf)
        session = store.get_session(session_id)
        owner = session.metadata.get("owner_user_id")
        if owner != user.user_id:
            raise HTTPException(status_code=404, detail="session not found")
        return user

    def set_login_cookie(response: Response, token: str) -> None:
        response.set_cookie(
            "agentic_session",
            token,
            httponly=True,
            secure=cookie_secure,
            samesite="lax",
            max_age=86_400,
            path="/",
        )

    @app.get("/assets/exodus-logo.png")
    def exodus_logo() -> FileResponse:
        if not EXODUS_LOGO_PATH.exists():
            raise HTTPException(status_code=404, detail="logo asset not found")
        return FileResponse(EXODUS_LOGO_PATH, media_type="image/png")

    @app.get("/login", response_class=HTMLResponse)
    def login_page() -> str:
        google_button = (
            '<a href="/auth/google/start">Google 계정으로 로그인</a>'
            if os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
            else ""
        )
        return LOGIN_HTML.replace("__GOOGLE_BUTTON__", google_button)

    @app.post("/auth/login")
    def login(request: Request, payload: LoginRequest, response: Response) -> dict[str, Any]:
        client_host = request.headers.get("CF-Connecting-IP") or (request.client.host if request.client else "unknown")
        limiter_key = f"{client_host}:{payload.username.lower()}"
        try:
            login_limiter.check(limiter_key)
        except PermissionError as error:
            raise HTTPException(status_code=429, detail=str(error)) from error
        user = auth.authenticate(payload.username, payload.password)
        if user is None:
            login_limiter.failed(limiter_key)
            raise HTTPException(status_code=401, detail="invalid credentials")
        login_limiter.success(limiter_key)
        login_session = auth.create_session(user.user_id)
        set_login_cookie(response, login_session.token)
        return {"user": user.to_dict(), "csrf_token": login_session.csrf_token}

    @app.post("/auth/logout")
    def logout(request: Request, response: Response) -> dict[str, bool]:
        require(request, "chat:read", csrf=True)
        auth.logout(request.cookies.get("agentic_session"))
        response.delete_cookie("agentic_session", path="/")
        return {"ok": True}

    @app.get("/auth/me")
    def auth_me(request: Request) -> dict[str, Any]:
        user, csrf_token = request_identity(request)
        session_token = request.cookies.get("agentic_session")
        if not session_token:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.lower().startswith("bearer "):
                session_token = auth_header[7:].strip()
        if not session_token:
            session_token = request.headers.get("x-api-key")
        return {
            "user": user.to_dict(),
            "csrf_token": csrf_token,
            "permissions": sorted(auth.permissions(user)),
            "google_enabled": bool(os.environ.get("GOOGLE_OAUTH_CLIENT_ID")),
            "session_token": session_token,
        }

    @app.post("/auth/password")
    def change_password(request: Request, payload: PasswordChangeRequest) -> dict[str, bool]:
        user = require(request, "account:password", csrf=True)
        try:
            auth.change_password(user.user_id, payload.current_password, payload.new_password)
        except (PermissionError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {"ok": True}

    @app.get("/auth/google/start")
    def google_start(request: Request, link: bool = False) -> RedirectResponse:
        client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
        redirect_uri = os.environ.get("GOOGLE_OAUTH_REDIRECT_URI")
        if not client_id or not redirect_uri:
            raise HTTPException(status_code=503, detail="Google OAuth is not configured")
        user_id = request_identity(request)[0].user_id if link else None
        state = auth.create_oauth_state(user_id)
        query = urllib.parse.urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": "openid email profile",
                "state": state,
                "access_type": "online",
                "prompt": "select_account",
            }
        )
        return RedirectResponse("https://accounts.google.com/o/oauth2/v2/auth?" + query, status_code=302)

    @app.get("/auth/google/callback")
    def google_callback(code: str, state: str) -> RedirectResponse:
        client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
        client_secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
        redirect_uri = os.environ.get("GOOGLE_OAUTH_REDIRECT_URI")
        if not client_id or not client_secret or not redirect_uri:
            raise HTTPException(status_code=503, detail="Google OAuth is not configured")
        try:
            link_user_id = auth.consume_oauth_state(state)
            token_request = urllib.request.Request(
                "https://oauth2.googleapis.com/token",
                data=urllib.parse.urlencode(
                    {
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "code": code,
                        "grant_type": "authorization_code",
                        "redirect_uri": redirect_uri,
                    }
                ).encode("utf-8"),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )
            with urllib.request.urlopen(token_request, timeout=15) as token_response:
                token_data = json.loads(token_response.read().decode("utf-8"))
            userinfo_request = urllib.request.Request(
                "https://openidconnect.googleapis.com/v1/userinfo",
                headers={"Authorization": f"Bearer {token_data['access_token']}"},
            )
            with urllib.request.urlopen(userinfo_request, timeout=15) as userinfo_response:
                userinfo = json.loads(userinfo_response.read().decode("utf-8"))
            if not userinfo.get("email_verified"):
                raise PermissionError("Google email is not verified")
            google_sub = str(userinfo["sub"])
            google_email = str(userinfo["email"])
            if link_user_id is not None:
                user = auth.link_google(link_user_id, google_sub, google_email)
            else:
                user = auth.get_user_by_google_sub(google_sub) or auth.create_google_user(google_sub, google_email)
        except (KeyError, PermissionError, OSError, ValueError) as error:
            raise HTTPException(status_code=400, detail=f"Google OAuth failed: {error}") from error
        login_session = auth.create_session(user.user_id)
        response = RedirectResponse("/", status_code=302)
        set_login_cookie(response, login_session.token)
        return response

    @app.get("/admin/users")
    def list_users(request: Request) -> dict[str, Any]:
        require(request, "users:read")
        return {
            "users": [
                {**user.to_dict(), "permissions": sorted(auth.permissions(user))}
                for user in auth.list_users()
            ]
        }

    @app.get("/admin", response_class=HTMLResponse)
    def admin_page(request: Request) -> str:
        require(request, "users:read")
        return ADMIN_HTML

    @app.get("/account", response_class=HTMLResponse)
    def account_page(request: Request) -> str:
        request_identity(request)
        return ACCOUNT_HTML

    @app.get("/cluster-control")
    def cluster_control(request: Request) -> RedirectResponse:
        require(request, "cluster:manage")
        configured_url = os.environ.get("AGENTIC_CLUSTER_CONTROL_URL", "").strip()
        if not configured_url:
            backend_url = os.environ.get(
                "AGENTIC_LLM_BASE_URL", "http://127.0.0.1:52415/v1"
            )
            parsed_backend = urllib.parse.urlsplit(backend_url)
            configured_url = urllib.parse.urlunsplit(
                (
                    parsed_backend.scheme,
                    parsed_backend.netloc,
                    "",
                    "",
                    "",
                )
            )
        parsed_control = urllib.parse.urlsplit(configured_url)
        if parsed_control.scheme not in ("http", "https") or not parsed_control.netloc:
            raise HTTPException(
                status_code=500,
                detail="invalid AGENTIC_CLUSTER_CONTROL_URL",
            )
        return RedirectResponse(configured_url.rstrip("/") + "/", status_code=302)

    @app.post("/admin/users")
    def create_user(request: Request, payload: UserCreateRequest) -> dict[str, Any]:
        actor = require(request, "users:create", csrf=True)
        if payload.role not in ("user", "admin"):
            raise HTTPException(status_code=400, detail="role must be user or admin")
        if payload.role == "admin" and actor.role != "master":
            raise HTTPException(status_code=403, detail="only master can create admins")
        try:
            return auth.create_user(payload.username, payload.password, payload.role).to_dict()
        except (ValueError, sqlite3.IntegrityError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.put("/admin/users/{user_id}/role")
    def update_role(request: Request, user_id: int, payload: RoleUpdateRequest) -> dict[str, Any]:
        actor = require(request, "users:update", csrf=True)
        if payload.role not in ("user", "admin"):
            raise HTTPException(status_code=400, detail="role must be user or admin")
        try:
            return auth.set_role(actor, user_id, payload.role).to_dict()
        except (KeyError, PermissionError) as error:
            raise HTTPException(status_code=403, detail=str(error)) from error

    @app.put("/admin/users/{user_id}/permissions")
    def update_permission(request: Request, user_id: int, payload: PermissionUpdateRequest) -> dict[str, bool]:
        actor = require(request, "permissions:update", csrf=True)
        try:
            auth.set_permission(actor, user_id, payload.permission, payload.allowed)
        except (KeyError, PermissionError) as error:
            raise HTTPException(status_code=403, detail=str(error)) from error
        return {"ok": True}

    @app.put("/admin/users/{user_id}/status")
    def update_user_status(request: Request, user_id: int, payload: UserStatusRequest) -> dict[str, Any]:
        actor = require(request, "users:update", csrf=True)
        try:
            return auth.set_disabled(actor, user_id, payload.disabled).to_dict()
        except (KeyError, PermissionError) as error:
            raise HTTPException(status_code=403, detail=str(error)) from error

    @app.get("/", response_class=HTMLResponse)
    def ui(request: Request) -> Response:
        try:
            request_identity(request)
        except HTTPException:
            return RedirectResponse("/login", status_code=302)
        cwd = str(Path.cwd().resolve())
        return HTMLResponse(UI_HTML.replace('value="/Users/dshs_llm/exo"', f'value="{cwd}"'))

    @app.post("/sessions")
    def create_session(request: Request, payload: CreateSessionRequest) -> dict[str, Any]:
        user = require(request, "chat:write", csrf=True)
        owned_sessions = [
            session
            for session in store.list_sessions()
            if session.metadata.get("owner_user_id") == user.user_id
        ]
        max_sessions = int(os.environ.get("AGENTIC_MAX_SESSIONS_PER_USER", "20"))
        if len(owned_sessions) >= max_sessions:
            raise HTTPException(status_code=409, detail="session limit reached")
        metadata = {**payload.metadata, "owner_user_id": user.user_id}
        session = store.create_session(payload.title, metadata)
        resources = runner.resources.allocate(session.session_id)
        return {**session.to_dict(), "resources": resources.to_dict()}

    @app.get("/sessions")
    def list_sessions(request: Request) -> dict[str, Any]:
        user = require(request, "chat:read")
        sessions = store.list_sessions()
        sessions = [session for session in sessions if session.metadata.get("owner_user_id") == user.user_id]
        return {"sessions": [session.to_dict() for session in sessions]}

    @app.get("/sessions/{session_id}")
    def get_session(request: Request, session_id: str) -> dict[str, Any]:
        try:
            require_session_access(request, session_id, "chat:read")
            session = store.get_session(session_id)
            chat = (store.session_dir(session_id) / "chat.md").read_text(encoding="utf-8")
            resources = runner.resources.get(session_id)
            usage = runner.resources.usage(session_id)
            return {
                "session": session.to_dict(),
                "chat_md": chat,
                "messages": [
                    message.to_dict() for message in store.list_messages(session_id)
                ],
                "pending_message_ids": jobs.pending_message_ids(session_id),
                "resources": resources.to_dict(),
                "resource_usage": usage,
            }
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail="session not found") from e

    @app.put("/sessions/{session_id}")
    def update_session(request: Request, session_id: str, payload: UpdateSessionRequest) -> dict[str, Any]:
        try:
            require_session_access(request, session_id, "chat:write", csrf=True)
            if jobs.has_pending(session_id):
                raise HTTPException(
                    status_code=409,
                    detail="cannot modify a session while responses are pending",
                )
            session = store.get_session(session_id)
            if payload.title is not None:
                session = store.rename_session(session_id, payload.title, user_edited=True)
            if payload.model is not None:
                with store.transaction():
                    session.metadata["model"] = payload.model
                    store._write_json(store.session_dir(session_id) / "session.json", session.to_dict())
            return {"session": session.to_dict()}
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail="session not found") from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.delete("/sessions/{session_id}")
    def delete_session(request: Request, session_id: str) -> dict[str, bool]:
        try:
            require_session_access(
                request, session_id, "chat:write", csrf=True
            )
            if jobs.has_pending(session_id):
                raise HTTPException(
                    status_code=409,
                    detail="cannot delete a session while responses are pending",
                )
            store.delete_session(session_id)
            return {"ok": True}
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail="session not found") from e

    @app.post("/sessions/{session_id}/messages")
    def chat(request: Request, session_id: str, payload: ChatRequest) -> dict[str, Any]:
        try:
            require_session_access(request, session_id, "chat:write", csrf=True)
            response = runner.chat(
                session_id,
                payload.message,
                context=payload.context,
                reasoning_effort=payload.reasoning_effort,
                images=payload.images,
            )
            return {"message": Message(role="assistant", content=response).to_dict()}
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail="session not found") from e

    @app.get("/sessions/{session_id}/resources")
    def get_resources(request: Request, session_id: str) -> dict[str, Any]:
        try:
            require_session_access(request, session_id, "resources:read")
            store.get_session(session_id)
            allocation = runner.resources.get(session_id)
            return {"allocation": allocation.to_dict(), "usage": runner.resources.usage(session_id)}
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail="session not found") from e

    @app.put("/sessions/{session_id}/resources")
    def update_resources(request: Request, session_id: str, payload: ResourceRequest) -> dict[str, Any]:
        try:
            require_session_access(request, session_id, "resources:update", csrf=True)
            store.get_session(session_id)
            allocation = runner.resources.allocate(session_id, payload.model_dump())
            runner.resources.ensure_disk_available(session_id)
            return {"allocation": allocation.to_dict(), "usage": runner.resources.usage(session_id)}
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail="session not found") from e
        except (RuntimeError, ValueError) as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.post("/sessions/{session_id}/messages/stream")
    def chat_stream(request: Request, session_id: str, payload: ChatRequest) -> StreamingResponse:
        try:
            require_session_access(request, session_id, "chat:write", csrf=True)
            job = jobs.submit(
                session_id,
                payload.message,
                context=payload.context,
                reasoning_effort=payload.reasoning_effort,
                images=payload.images,
            )
            return StreamingResponse(
                (
                    json.dumps(event, ensure_ascii=False) + "\n"
                    for event in job.stream()
                ),
                media_type="application/x-ndjson",
                headers={
                    "X-Chat-Job-Id": job.job_id,
                    "X-Assistant-Message-Id": job.turn.assistant_message_id,
                },
            )
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail="session not found") from e

    @app.post("/v1/chat/completions")
    async def proxy_chat_completions(request: Request):
        user, _ = request_identity(request)
        body = await request.body()
        target_url = "http://127.0.0.1:52415/v1/chat/completions"

        is_stream = False
        try:
            req_json = json.loads(body)
            is_stream = bool(req_json.get("stream", False))
            user_sessions = [
                s for s in store.list_sessions()
                if s.metadata.get("owner_user_id") == user.user_id
            ]
            if user_sessions:
                user_sessions.sort(key=lambda s: s.updated_at, reverse=True)
                latest_model = user_sessions[0].metadata.get("model")
                if latest_model:
                    req_json["model"] = latest_model
                    body = json.dumps(req_json).encode("utf-8")
        except Exception:
            pass

        async def stream_generator(client, response):
            try:
                async for chunk in response.aiter_bytes():
                    yield chunk
            finally:
                await response.aclose()
                await client.aclose()

        client = httpx.AsyncClient(timeout=300.0)
        req_headers = {"Content-Type": "application/json"}
        try:
            response = await client.send(
                client.build_request("POST", target_url, content=body, headers=req_headers),
                stream=True
            )
            if is_stream:
                return StreamingResponse(
                    stream_generator(client, response),
                    status_code=response.status_code,
                    media_type=response.headers.get("content-type", "text/event-stream")
                )
            else:
                resp_content = await response.aread()
                await response.aclose()
                await client.aclose()
                return Response(
                    content=resp_content,
                    status_code=response.status_code,
                    media_type=response.headers.get("content-type", "application/json")
                )
        except Exception as e:
            await client.aclose()
            raise HTTPException(status_code=500, detail=f"Proxy error: {e}")

    @app.post("/v1/messages")
    async def proxy_claude_messages(request: Request):
        user, _ = request_identity(request)
        body = await request.body()
        target_url = "http://127.0.0.1:52415/v1/messages"

        is_stream = False
        try:
            req_json = json.loads(body)
            is_stream = bool(req_json.get("stream", False))
            user_sessions = [
                s for s in store.list_sessions()
                if s.metadata.get("owner_user_id") == user.user_id
            ]
            if user_sessions:
                user_sessions.sort(key=lambda s: s.updated_at, reverse=True)
                latest_model = user_sessions[0].metadata.get("model")
                if latest_model:
                    req_json["model"] = latest_model
                    body = json.dumps(req_json).encode("utf-8")
        except Exception:
            pass

        async def stream_generator(client, response):
            try:
                async for chunk in response.aiter_bytes():
                    yield chunk
            finally:
                await response.aclose()
                await client.aclose()

        client = httpx.AsyncClient(timeout=300.0)
        req_headers = {"Content-Type": "application/json"}
        try:
            response = await client.send(
                client.build_request("POST", target_url, content=body, headers=req_headers),
                stream=True
            )
            if is_stream:
                return StreamingResponse(
                    stream_generator(client, response),
                    status_code=response.status_code,
                    media_type=response.headers.get("content-type", "text/event-stream")
                )
            else:
                resp_content = await response.aread()
                await response.aclose()
                await client.aclose()
                return Response(
                    content=resp_content,
                    status_code=response.status_code,
                    media_type=response.headers.get("content-type", "application/json")
                )
        except Exception as e:
            await client.aclose()
            raise HTTPException(status_code=500, detail=f"Proxy error: {e}")

    @app.get("/v1/models")
    def list_v1_models(request: Request) -> dict[str, Any]:
        require(request, "chat:read")
        return {
            "object": "list",
            "data": [
                {
                    "id": "mlx-community/Huihui-Qwen3.6-35B-A3B-abliterated-4.4bit-msq",
                    "object": "model",
                    "created": 1782879934,
                    "owned_by": "exo"
                },
                {
                    "id": "mlx-community/gemma-4-31b-it-4bit",
                    "object": "model",
                    "created": 1782882884,
                    "owned_by": "exo"
                },
                {
                    "id": "mlx-community/DeepSeek-R1-Distill-Qwen-32B-abliterated",
                    "object": "model",
                    "created": 1782882886,
                    "owned_by": "exo"
                }
            ]
        }

    return app


app = create_app()


def main() -> None:
    import uvicorn

    host = os.environ.get("AGENTIC_HOST", "127.0.0.1")
    port = int(os.environ.get("AGENTIC_PORT", "8765"))
    uvicorn.run("exo_tools.agent_server:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
