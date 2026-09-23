# ARIA — AI Desktop Assistant

A sleek, dark-themed AI assistant powered by **open-source LLMs** via **Ollama** (local, offline inference).

## 📁 Files

```
ai-assistant/
├── index.html     ← Main UI (open in browser)
├── style.css      ← Styling
├── app.js         ← Frontend logic (API calls, chat, tools, memory, settings)
├── server.py      ← Python Flask backend (proxies Ollama API)
└── README.md      ← This file
```

---

## 🚀 Quick Start

### 1. Install Python dependencies

```bash
pip install flask flask-cors ollama pypdf python-docx vosk flask-sock
```

### 2. Install and Start Ollama Services

1. Install Ollama from [ollama.com](https://ollama.com/download)
2. Open your CLI / terminal and start the Ollama service:
   ```bash
   ollama serve
   ```
3. Open another CLI window and pull the LLaMA 3.2 3B parameter model:
   ```bash
   ollama pull llama3.2
   ```

### 3. Start the backend

```bash
python server.py
```

You should see:
```text
+--------------------------------------------------+
|   ARIA v2.0 Backend - Ollama (Local, Offline)    |
+--------------------------------------------------+
```

### 4. Build / Open the frontend

Open `index.html` in your browser (double-click it, or use a simple server):

```bash
# Option A: just open the file
open index.html   # macOS
start index.html  # Windows

# Option B: serve locally to avoid CORS on some browsers
python -m http.server 8080
# then visit http://localhost:8080
```

---

## ✨ Features

| Feature | Description |
|---|---|
| **Chat** | Full conversation with open-source LLMs, with markdown rendering |
| **Quick Tools** | Summarize, Translate, Code Review, Grammar Fix, Explain, Brainstorm |
| **Memory** | Auto-extracts and stores context from conversations |
| **Settings** | Switch AI models, customize system prompt, accent colors, font size |
| **Responsive** | Adapts to smaller screen widths |

---

## 🤖 Available Models (via Ollama)

| Model | Speed | Best For |
|---|---|---|
| **Llama 3.2 3B** | ⚡ Fastest | Balanced local model — good default for voice |
| **Phi-3 Mini** | Fast | Very lightweight, fastest on CPU-only machines |
| **Qwen 2.5 3B** | Fast | Small and quick alternative |

All models are **open-source** and run **locally** on your machine.

---

## 🔌 API Endpoints (server.py)

| Method | Path | Description |
|---|---|---|
| GET | `/` | Health check |
| GET | `/api/health` | Health check JSON |
| GET | `/api/models` | List available models |
| POST | `/api/chat` | Chat with AI (full history) |
| POST | `/api/tool` | One-shot tool prompt |

### POST /api/chat body:
```json
{
  "messages": [{"role": "user", "content": "Hello!"}],
  "model": "llama3.2",
  "system": "You are ARIA..."
}
```

### GET /api/models response:
```json
{
  "models": [
    {"id": "llama3.2", "name": "Llama 3.2 3B", "description": "Fast, balanced local model — good default for voice"},
    ...
  ]
}
```

---

## 🎨 Customization

- **AI Model** — choose between Llama 3.2, Phi-3, and Qwen 2.5 in Settings
- **Accent color** — click color swatches in Settings tab
- **System prompt** — fully customizable in Settings
- **Assistant name** — change from ARIA to anything

---

## 🔄 Migration from Cloud APIs

This project has been migrated to use **Ollama** with **open-source models** for:
- **Free access** — no paid API subscriptions needed
- **Privacy** — everything runs 100% locally
- **Open source** — all models are fully open-source (Llama, Phi, Qwen)

---

Built with HTML, CSS, JavaScript + Python (Flask) · Powered by Ollama + Open-Source LLMs
# ollama-Aria
