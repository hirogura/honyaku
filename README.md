# honyaku

ホテル・施設の受付向け翻訳 Web UI。PLaMo-3-Translate-2B（GGUF, Ollama）を
OpenAI 互換 API 経由で利用し、ゲストの原文を日本語（です・ます調）に翻訳します。

## 構成

- `app.py` — FastAPI バックエンド（言語定義・プロンプト生成・Ollama 連携）
- `static/index.html` — 受付用 UI（言語ボタン / 原文 / ➔ / 訳文）
- `run.sh` — 起動スクリプト（`127.0.0.1:3374` 固定）

現在のバージョン: `v.0.1.0`（画面右上に表示）

## セットアップ

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Ollama 側でモデルを取得（名前は環境に合わせて変更）
ollama pull plamo-3-translate:2b
```

## 起動

```bash
./run.sh
```

`http://127.0.0.1:3374` で待ち受けます（外部から直接アクセス不可）。

| 環境変数 | 既定値 | 用途 |
| --- | --- | --- |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Ollama の接続先 |
| `OLLAMA_MODEL` | `plamo-3-translate:2b` | 使用モデル名 |
| `OLLAMA_TIMEOUT` | `120` | 推論タイムアウト（秒） |

## Tailscale 経由の公開

サーバー上で Tailscale Serve を使い、HTTPS 経由で `127.0.0.1:3374` に
リバースプロキシします（アプリ側の SSL 実装は不要）。

```bash
tailscale serve --bg --https=3374 3374
```

これで `https://<ノード名>.<tailnet>.ts.net:3374/` からアクセスできます。

## 使い方

1. 左の言語ボタンからゲストの言語を選択する
2. 原文を入力する
3. 中央の ➔ ボタン（または Ctrl + Enter）で翻訳する
4. 右側に日本語訳が表示される

「Other Languages」を選ぶと、言語は LLM 側で自動判別されます。

## メンテナンス

画面右上のボタンから操作できます（実行前に確認ダイアログが出ます）。

- **アップデート**: `https://github.com/hirogura/honyaku.git` の `main` を `git pull --ff-only` で取得
- **再起動**: サービスを終了し、`run.sh` で起動し直す（ログは `logs/app.log`）

アップデート後は「再起動」を押すと内容が反映されます。ローカルに未コミットの変更や
未プッシュのコミットがある場合、`--ff-only` のためアップデートは失敗します。
