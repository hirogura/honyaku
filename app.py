"""受付用 PLaMo 3 Translate Web アプリ。

Ollama (127.0.0.1:11434) の OpenAI 互換 API に翻訳リクエストを送り、
ホテル・施設の受付スタッフ向けの大きな 2 カラム UI を提供する。

起動: ./run.sh （127.0.0.1:3374 のみで待ち受ける）
"""

from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
INDEX_HTML = BASE_DIR / "static" / "index.html"

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "plamo-3-translate:2b")
REQUEST_TIMEOUT = float(os.environ.get("OLLAMA_TIMEOUT", "120"))
MAX_INPUT_CHARS = 2000

UPDATE_REPO_URL = "https://github.com/hirogura/honyaku.git"
UPDATE_BRANCH = "main"
LOG_FILE = BASE_DIR / "logs" / "app.log"

# 受付でよく使われる言語。並び順がそのまま画面のボタン順になる。
LANGUAGES = (
    {"code": "en", "label": "English", "html_lang": "en", "prompt_name": "英語"},
    {"code": "zh-Hans", "label": "简体中文", "html_lang": "zh-Hans", "prompt_name": "中国語（簡体字）"},
    {"code": "zh-Hant", "label": "繁體中文", "html_lang": "zh-Hant", "prompt_name": "中国語（繁体字）"},
    {"code": "ko", "label": "한국어", "html_lang": "ko", "prompt_name": "韓国語"},
    {"code": "vi", "label": "Tiếng Việt", "html_lang": "vi", "prompt_name": "ベトナム語"},
    {"code": "tl", "label": "Tagalog", "html_lang": "tl", "prompt_name": "タガログ語"},
    {"code": "es", "label": "Español", "html_lang": "es", "prompt_name": "スペイン語"},
    {"code": "auto", "label": "Other Languages", "html_lang": "", "prompt_name": None},
)
LANGUAGE_BY_CODE = {language["code"]: language for language in LANGUAGES}

SYSTEM_PROMPT = (
    "あなたはホテル・施設の受付で働くプロの翻訳者です。"
    "ゲストの言葉を、受付スタッフがすぐに読める自然で丁寧な日本語（です・ます調）に翻訳します。"
)


class TranslateRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_INPUT_CHARS)
    language: str = Field(min_length=1, max_length=16)


class TranslateResponse(BaseModel):
    translation: str
    model: str


def build_user_prompt(language: dict, text: str) -> str:
    """選択言語に応じた翻訳指示文を組み立てる。"""
    if language["prompt_name"] is None:
        source = "文章（言語は自動的に判別してください）"
    else:
        source = f"{language['prompt_name']}の文章"

    return (
        f"次の{source}を、ホテル・施設の受付スタッフが読むための、"
        "自然で丁寧な日本語（です・ます調）に翻訳してください。\n"
        "固有名詞・数値・日付・時刻・部屋番号などは正確に保ってください。\n"
        "余計な解説や補足は加えず、翻訳結果だけを出力してください。\n\n"
        f"{text.strip()}"
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    timeout = httpx.Timeout(REQUEST_TIMEOUT, connect=5.0)
    async with httpx.AsyncClient(base_url=OLLAMA_BASE_URL, timeout=timeout) as client:
        app.state.ollama = client
        yield


app = FastAPI(title="受付翻訳", lifespan=lifespan)


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(INDEX_HTML, media_type="text/html; charset=utf-8")


@app.get("/api/languages")
async def languages() -> list[dict]:
    return [
        {"code": language["code"], "label": language["label"], "html_lang": language["html_lang"]}
        for language in LANGUAGES
    ]


@app.post("/api/translate", response_model=TranslateResponse)
async def translate(payload: TranslateRequest, request: Request) -> TranslateResponse:
    language = LANGUAGE_BY_CODE.get(payload.language)
    if language is None:
        raise HTTPException(status_code=400, detail="未対応の言語が選択されています。")

    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="原文が入力されていません。")

    request_body = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(language, text)},
        ],
        "temperature": 0.3,
        "max_tokens": 1024,
        "stream": False,
    }

    try:
        response = await request.app.state.ollama.post("/v1/chat/completions", json=request_body)
        response.raise_for_status()
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=504, detail="翻訳サーバーの応答がタイムアウトしました。もう一度お試しください。"
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"翻訳サーバーがエラーを返しました（HTTP {exc.response.status_code}）。",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail="翻訳サーバー（Ollama）に接続できません。起動状態を確認してください。",
        ) from exc

    try:
        translation = response.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=502, detail="翻訳サーバーの応答を解釈できませんでした。"
        ) from exc

    translation = (translation or "").strip()
    if not translation:
        raise HTTPException(status_code=502, detail="翻訳結果が空でした。もう一度お試しください。")

    return TranslateResponse(translation=translation, model=OLLAMA_MODEL)


@app.post("/api/update")
async def update() -> dict:
    """GitHub のリポジトリから最新版を取得する。"""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/bin/true"}
    process = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        str(BASE_DIR),
        "pull",
        "--ff-only",
        "--no-rebase",
        UPDATE_REPO_URL,
        UPDATE_BRANCH,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
    )

    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=120)
    except asyncio.TimeoutError as exc:
        process.kill()
        await process.wait()
        raise HTTPException(status_code=504, detail="アップデートがタイムアウトしました。") from exc

    output = (stdout or b"").decode("utf-8", "replace").strip()
    if process.returncode != 0:
        detail = "アップデートに失敗しました。"
        if output:
            detail += "\n" + output[-1200:]
        raise HTTPException(status_code=502, detail=detail)

    return {
        "ok": True,
        "changed": "Already up to date." not in output,
        "output": output[-1200:],
    }


@app.post("/api/restart", status_code=202)
async def restart() -> dict:
    """このサービスを新しいプロセスとして起動し直す。"""
    LOG_FILE.parent.mkdir(exist_ok=True)
    pid = os.getpid()
    script = (
        "sleep 1\n"
        f"kill {pid} 2>/dev/null || true\n"
        f"for _ in $(seq 1 40); do kill -0 {pid} 2>/dev/null || break; sleep 0.25; done\n"
        "sleep 0.5\n"
        f"cd {shlex.quote(str(BASE_DIR))}\n"
        f"exec ./run.sh >> {shlex.quote(str(LOG_FILE))} 2>&1\n"
    )
    subprocess.Popen(
        ["bash", "-c", script],
        cwd=BASE_DIR,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=3374)
