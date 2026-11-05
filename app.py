"""受付用 PLaMo 3 Translate Web アプリ。

Ollama (127.0.0.1:11434) の /api/generate に PFML3 プロンプトを送り、
ホテル・施設の受付スタッフ向けの大きな 2 カラム UI を提供する。

起動: ./run.sh （127.0.0.1:3374 のみで待ち受ける）
"""

from __future__ import annotations

import asyncio
import os
import shlex
import shutil
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

OLLAMA_INSTALL_URL = os.environ.get("OLLAMA_INSTALL_URL", "https://ollama.com/install.sh")
OLLAMA_INSTALL_TIMEOUT = float(os.environ.get("OLLAMA_INSTALL_TIMEOUT", "1800"))
OLLAMA_INSTALL_LOG = BASE_DIR / "logs" / "ollama-install.log"
OLLAMA_SERVE_LOG = BASE_DIR / "logs" / "ollama.log"
OLLAMA_INSTALL_STATE: dict[str, str] = {"state": "idle", "message": ""}
_ollama_install_task: asyncio.Task | None = None

MODEL_NAME = OLLAMA_MODEL
MODEL_SOURCE = os.environ.get("OLLAMA_MODEL_SOURCE", "hf.co/pfnet/plamo-3-translate-2b-GGUF:Q4_K_M")
MODEL_INSTALL_LOG = BASE_DIR / "logs" / "model-install.log"
MODEL_INSTALL_STATE: dict[str, str] = {"state": "idle", "message": ""}
_model_install_task: asyncio.Task | None = None

# 受付でよく使われる言語。並び順がそのまま画面のボタン順になる。
LANGUAGES = (
    {"code": "en", "label": "English", "html_lang": "en", "plamo_lang": "en"},
    {"code": "zh-Hans", "label": "简体中文", "html_lang": "zh-Hans", "plamo_lang": "zh-hans"},
    {"code": "zh-Hant", "label": "繁體中文", "html_lang": "zh-Hant", "plamo_lang": "zh-hant"},
    {"code": "ko", "label": "한국어", "html_lang": "ko", "plamo_lang": "ko"},
    {"code": "vi", "label": "Tiếng Việt", "html_lang": "vi", "plamo_lang": "vi"},
    {"code": "tl", "label": "Tagalog", "html_lang": "tl", "plamo_lang": "tl"},
    {"code": "es", "label": "Español", "html_lang": "es", "plamo_lang": "es"},
    {"code": "auto", "label": "Other Languages", "html_lang": "", "plamo_lang": None},
)
LANGUAGE_BY_CODE = {language["code"]: language for language in LANGUAGES}


class TranslateRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_INPUT_CHARS)
    language: str = Field(min_length=1, max_length=16)


class TranslateResponse(BaseModel):
    translation: str
    model: str


def build_translation_prompt(
    text: str, source_lang: str | None, target_lang: str, form: str | None = None
) -> str:
    """PLaMo 3 Translate の PFML3 プロンプトを組み立てる。

    原文と言語情報だけを渡し、翻訳の指示文は含めない。
    """
    parts = ["<|plamo:tag|>dataset<|plamo:msg|>translation", "<|plamo:tag|>input"]
    if source_lang:
        parts.append(f"<|plamo:key|>lang<|plamo:val|>{source_lang}")
    parts.append(f"<|plamo:msg|>{text}")
    parts.append(f"<|plamo:tag|>output<|plamo:key|>lang<|plamo:val|>{target_lang}")
    if form:
        parts.append(f"<|plamo:key|>form<|plamo:val|>{form}")
    parts.append("<|plamo:msg|>")
    return "".join(parts)


def _validated_text(raw: str) -> str:
    text = raw.strip()
    if not text:
        raise HTTPException(status_code=400, detail="原文が入力されていません。")
    if "<|plamo:" in text:
        raise HTTPException(status_code=400, detail="原文に使用できない文字列が含まれています。")
    return text


async def _request_translation(request: Request, prompt: str) -> str:
    request_body = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "raw": True,
        "stream": False,
        "options": {
            "temperature": 0,
            "num_predict": 1024,
            "stop": ["<|plamo:tag|>", "<|plamo:bos|>"],
        },
    }

    try:
        response = await request.app.state.ollama.post("/api/generate", json=request_body)
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
        translation = response.json()["response"]
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=502, detail="翻訳サーバーの応答を解釈できませんでした。"
        ) from exc

    translation = (translation or "").strip()
    if not translation:
        raise HTTPException(status_code=502, detail="翻訳結果が空でした。もう一度お試しください。")
    return translation


def ollama_installed() -> bool:
    return shutil.which("ollama") is not None


async def ollama_responding(timeout: float = 2.0) -> bool:
    """Ollama の API が応答するかを確認する。"""
    try:
        async with httpx.AsyncClient(base_url=OLLAMA_BASE_URL, timeout=timeout) as client:
            response = await client.get("/api/tags")
            return response.status_code == 200
    except httpx.HTTPError:
        return False


def _append_log(path: Path, text: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(text + "\n")
    except OSError:
        pass


async def _run_command(command: list[str], timeout: float) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        return -1, "timeout"
    return process.returncode or 0, (output or b"").decode("utf-8", "replace")


async def _ensure_ollama_service() -> None:
    """インストール後に Ollama のサービスが起動していることを確認する。"""
    if await ollama_responding():
        return

    if Path("/run/systemd/system").is_dir() and shutil.which("systemctl"):
        await _run_command(["systemctl", "start", "ollama"], timeout=120)
        if await ollama_responding():
            return

    OLLAMA_SERVE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with OLLAMA_SERVE_LOG.open("ab") as log_handle:
        subprocess.Popen(
            ["ollama", "serve"],
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    for _ in range(60):
        if await ollama_responding():
            return
        await asyncio.sleep(1)


async def installed_models() -> list[str]:
    """Ollama に登録されているモデル名の一覧を返す。"""
    try:
        async with httpx.AsyncClient(base_url=OLLAMA_BASE_URL, timeout=5.0) as client:
            response = await client.get("/api/tags")
        if response.status_code != 200:
            return []
        return [
            model.get("name") or model.get("model", "")
            for model in response.json().get("models", [])
        ]
    except (httpx.HTTPError, ValueError, AttributeError):
        return []


async def model_installed() -> bool:
    return MODEL_NAME in await installed_models()


async def _run_model_install() -> None:
    output = ""
    try:
        code, output = await _run_command(
            ["ollama", "pull", MODEL_SOURCE], timeout=OLLAMA_INSTALL_TIMEOUT
        )
        _append_log(MODEL_INSTALL_LOG, f"$ ollama pull {MODEL_SOURCE}\n{output}")
        if code != 0:
            raise RuntimeError("モデルのダウンロードに失敗しました。")

        if MODEL_SOURCE != MODEL_NAME:
            code, copy_output = await _run_command(
                ["ollama", "cp", MODEL_SOURCE, MODEL_NAME], timeout=120
            )
            _append_log(MODEL_INSTALL_LOG, f"$ ollama cp {MODEL_SOURCE} {MODEL_NAME}\n{copy_output}")
            if code != 0:
                raise RuntimeError("モデルの登録に失敗しました。")

        if not await model_installed():
            raise RuntimeError("インストール後にモデルが見つかりませんでした。")

        MODEL_INSTALL_STATE.update(state="installed", message="モデルのインストールが完了しました。")
    except Exception as exc:
        detail = str(exc)
        if output:
            detail += "\n" + output[-1200:]
        MODEL_INSTALL_STATE.update(state="error", message=detail)


async def _run_ollama_install() -> None:
    process: asyncio.subprocess.Process | None = None
    output = ""
    try:
        process = await asyncio.create_subprocess_exec(
            "bash",
            "-c",
            f"curl -fsSL {shlex.quote(OLLAMA_INSTALL_URL)} | sh",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"},
        )
        raw, _ = await asyncio.wait_for(process.communicate(), timeout=OLLAMA_INSTALL_TIMEOUT)
        output = (raw or b"").decode("utf-8", "replace").strip()
        _append_log(OLLAMA_INSTALL_LOG, output)

        if process.returncode != 0:
            raise RuntimeError("インストーラーがエラーで終了しました。")
        if not ollama_installed():
            raise RuntimeError("インストール後に ollama コマンドが見つかりませんでした。")

        await _ensure_ollama_service()
        OLLAMA_INSTALL_STATE.update(state="installed", message="Ollama のインストールが完了しました。")
    except asyncio.TimeoutError:
        if process is not None:
            process.kill()
            await process.wait()
        OLLAMA_INSTALL_STATE.update(state="error", message="インストールがタイムアウトしました。")
    except Exception as exc:
        detail = str(exc)
        if output:
            detail += "\n" + output[-1200:]
        OLLAMA_INSTALL_STATE.update(state="error", message=detail)


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
    """選択言語の原文を日本語（です・ます調）に翻訳する。"""
    language = LANGUAGE_BY_CODE.get(payload.language)
    if language is None:
        raise HTTPException(status_code=400, detail="未対応の言語が選択されています。")

    text = _validated_text(payload.text)
    prompt = build_translation_prompt(text, language["plamo_lang"], "ja", "polite")
    translation = await _request_translation(request, prompt)
    return TranslateResponse(translation=translation, model=OLLAMA_MODEL)


@app.post("/api/translate/reverse", response_model=TranslateResponse)
async def translate_reverse(payload: TranslateRequest, request: Request) -> TranslateResponse:
    """日本語を選択言語へ翻訳する。"""
    language = LANGUAGE_BY_CODE.get(payload.language)
    if language is None:
        raise HTTPException(status_code=400, detail="未対応の言語が選択されています。")
    if language["plamo_lang"] is None:
        raise HTTPException(
            status_code=400,
            detail="「Other Languages」では逆方向の翻訳ができません。言語を選択してください。",
        )

    text = _validated_text(payload.text)
    prompt = build_translation_prompt(text, "ja", language["plamo_lang"])
    translation = await _request_translation(request, prompt)
    return TranslateResponse(translation=translation, model=OLLAMA_MODEL)


@app.get("/api/ollama")
async def ollama_status() -> dict:
    installed = ollama_installed()
    return {
        "installed": installed,
        "running": await ollama_responding() if installed else False,
        "state": OLLAMA_INSTALL_STATE["state"],
        "message": OLLAMA_INSTALL_STATE["message"],
    }


@app.post("/api/ollama/install", status_code=202)
async def install_ollama() -> dict:
    """Ollama を公式インストーラーで導入する（非同期実行）。"""
    global _ollama_install_task

    if ollama_installed():
        OLLAMA_INSTALL_STATE.update(state="installed", message="Ollama はインストール済みです。")
        return {"ok": True, "state": "installed"}

    if _ollama_install_task is not None and not _ollama_install_task.done():
        raise HTTPException(status_code=409, detail="Ollama をインストール中です。しばらくお待ちください。")

    OLLAMA_INSTALL_STATE.update(state="installing", message="インストールしています…")
    _ollama_install_task = asyncio.create_task(_run_ollama_install())
    return {"ok": True, "state": "installing"}


@app.get("/api/model")
async def model_status() -> dict:
    return {
        "model": MODEL_NAME,
        "installed": await model_installed(),
        "state": MODEL_INSTALL_STATE["state"],
        "message": MODEL_INSTALL_STATE["message"],
    }


@app.post("/api/model/install", status_code=202)
async def install_model() -> dict:
    """翻訳モデルを Ollama に導入する（非同期実行）。"""
    global _model_install_task

    if await model_installed():
        MODEL_INSTALL_STATE.update(state="installed", message="モデルはインストール済みです。")
        return {"ok": True, "state": "installed"}

    if not ollama_installed():
        raise HTTPException(status_code=409, detail="先に Ollama をインストールしてください。")

    if _model_install_task is not None and not _model_install_task.done():
        raise HTTPException(status_code=409, detail="モデルをインストール中です。しばらくお待ちください。")

    MODEL_INSTALL_STATE.update(state="installing", message="インストールしています…")
    _model_install_task = asyncio.create_task(_run_model_install())
    return {"ok": True, "state": "installing"}


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
