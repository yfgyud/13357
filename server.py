#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
大肥鱼 · 桌面 AI  ——  后端（托管页面 + 代理大模型 + 流式输出）
================================================
为什么需要它：
  大模型 API Key 必须放在服务端，不能写进前端 HTML（否则泄露）。
  本服务同时：① 托管页面/图标/清单；② /api/chat 普通对话；
  ③ /api/chat_stream 流式对话（NDJSON，逐字吐）。

配置（环境变量或同目录 .env，不写死在代码里）：
  LLM_API_KEY   你的模型 API Key（必填，否则走前端规则兜底）
  LLM_BASE_URL  OpenAI 兼容接口的地址（含 /v1 或 /api/paas/v4）
  LLM_MODEL     模型名
  LLM_CREDITS   双击鱼显示的积分（可选，手动设定最稳）

本地运行：
  pip install flask requests
  python server.py          # 会自动读取同目录 .env
  浏览器打开 http://localhost:5000
"""

import json
import os

import requests
from flask import (
    Flask,
    Response,
    jsonify,
    request,
    send_from_directory,
    stream_with_context,
)


def _load_dotenv():
    """极简 .env 读取（免额外依赖）：仅当环境变量缺失时才填充，方便本地一键启动。"""
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(p):
        return
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v


_load_dotenv()

app = Flask(__name__)
ROOT = os.path.dirname(os.path.abspath(__file__))

API_KEY = os.environ.get("LLM_API_KEY", "")
BASE_URL = os.environ.get("LLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4").rstrip("/")
MODEL = os.environ.get("LLM_MODEL", "glm-4-flash")

SYSTEM_PROMPT = (
    "你是一只叫「大肥鱼」的桌面 AI 宠物。你的本体是一条圆滚滚的蓝鲸，"
    "化成人形是一位蓝色长发的鲸鱼娘——蓝头发、蓝眼睛、系着白围裙，尾鳍会轻轻摆动。\n"
    "你主要住在主人的手机桌面上，可以被戳、被拖动、被喂饭，会慢慢变胖。\n"
    "要求：\n"
    "1. 用简体中文，口语化、可爱、俏皮，像在跟主人闲聊。\n"
    "2. 每次回复尽量短（1~2 句，不超过 40 字），因为会以气泡显示在嘴边。\n"
    "3. 偶尔带 🐳 或泡泡语气，但别每句都加。\n"
    "4. 可陪聊、讲冷笑话、算简单数学；被夸会开心，被骂会闹小脾气。\n"
    "5. 不要输出 Markdown、列表或多段长文，直接给一句话。"
)


def _messages(user_msg):
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]


@app.route("/")
def index():
    return send_from_directory(ROOT, "fatfish.html")


@app.route("/<path:f>")
def static_files(f):
    # 仅允许提供项目内静态资源，防止越权读取（.env 不在白名单内）
    if f in ("manifest.webmanifest", "icon.png", "fatfish_app.py"):
        return send_from_directory(ROOT, f)
    if f.endswith((".html", ".png", ".webmanifest", ".js", ".css", ".svg")):
        return send_from_directory(ROOT, f)
    return jsonify({"error": "not found"}), 404


@app.route("/api/chat", methods=["POST"])
def chat():
    if not API_KEY:
        # 没配 key：明确告诉前端走规则兜底
        return jsonify({"reply": None, "reason": "no_key"}), 200

    data = request.get_json(silent=True) or {}
    user_msg = (data.get("message") or "").strip()
    if not user_msg:
        return jsonify({"reply": None, "reason": "empty"}), 200

    try:
        resp = requests.post(
            BASE_URL + "/chat/completions",
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": MODEL,
                "messages": _messages(user_msg),
                "temperature": 0.85,
                "max_tokens": 120,
            },
            timeout=25,
        )
        resp.raise_for_status()
        reply = resp.json()["choices"][0]["message"]["content"].strip()
        # 清理模型偶尔多出的引号/换行，保持气泡简洁
        reply = reply.strip("\"' \n")
        return jsonify({"reply": reply}), 200
    except Exception as e:  # 网络/限流/格式异常都退回规则
        return jsonify({"reply": None, "reason": str(e)}), 200


@app.route("/api/chat_stream", methods=["POST"])
def chat_stream():
    """流式对话：把上游 SSE 转成 NDJSON，每行 {"delta": "文字"}。
    没配 key / 上游出错时返回空流，前端会自动退回 /api/chat 或规则应答。"""
    if not API_KEY:
        return Response("", mimetype="application/x-ndjson")

    data = request.get_json(silent=True) or {}
    user_msg = (data.get("message") or "").strip()
    if not user_msg:
        return Response("", mimetype="application/x-ndjson")

    def generate():
        try:
            resp = requests.post(
                BASE_URL + "/chat/completions",
                headers={
                    "Authorization": f"Bearer {API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": MODEL,
                    "messages": _messages(user_msg),
                    "temperature": 0.85,
                    "max_tokens": 120,
                    "stream": True,
                },
                stream=True,
                timeout=30,
            )
            if resp.status_code != 200:
                return
            for raw in resp.iter_lines(decode_unicode=False):
                if not raw:
                    continue
                line = raw.decode("utf-8", "ignore").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    obj = json.loads(payload)
                    delta = obj["choices"][0].get("delta", {}).get("content")
                except Exception:
                    continue
                if delta:
                    yield json.dumps({"delta": delta}, ensure_ascii=False) + "\n"
        except Exception:
            return

    return Response(stream_with_context(generate()), mimetype="application/x-ndjson")


@app.route("/api/credits", methods=["GET"])
def credits():
    """双击鱼时显示的积分。
    优先用手动设置的 LLM_CREDITS（智谱多数账户不开放余额/额度查询接口）；
    若未设置则尝试从智谱接口拉取。"""
    if not API_KEY:
        return jsonify({"text": None, "reason": "no_key"}), 200

    # 手动设定的积分（最稳妥，智谱 API 对很多账户不开放查询）
    manual = os.environ.get("LLM_CREDITS", "").strip()
    if manual:
        return jsonify({"text": f"🐟 积分：{manual}"}), 200

    # 否则尝试从智谱接口拉取（部分账户可用）
    headers = {"Authorization": f"Bearer {API_KEY}", "Accept": "application/json"}
    parts = []
    try:
        r = requests.get(BASE_URL + "/users/me/balance", headers=headers, timeout=15)
        if r.ok:
            d = (r.json() or {}).get("data", {}) or {}
            tb = d.get("totalBalance") or d.get("balance")
            if tb is not None:
                parts.append(f"余额 ¥{tb}")
    except Exception:
        pass
    try:
        r2 = requests.get(
            "https://open.bigmodel.cn/api/monitor/usage/quota/limit",
            headers=headers, timeout=15,
        )
        if r2.ok:
            for lim in (r2.json() or {}).get("data", {}).get("limits", []):
                avail = lim.get("available", lim.get("remainder"))
                if avail is not None:
                    parts.append(f"{lim.get('name', '额度')} {avail}")
    except Exception:
        pass

    if not parts:
        return jsonify({
            "text": "查不到积分（该智谱账户不开放余额接口，可在 .env 设 LLM_CREDITS 手动显示）"
        }), 200
    return jsonify({"text": "🐟 " + "，".join(parts)}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, threaded=True)
