# honyaku

ホテル・施設の受付向け翻訳 Web UI。PLaMo-3-Translate-2B（GGUF, Ollama）を
Ollama API（PFML3 プロンプト）経由で利用し、ゲストの原文を日本語（です・ます調）に翻訳します。

## 構成

- `app.py` — FastAPI バックエンド（言語定義・プロンプト生成・Ollama 連携）
- `static/index.html` — 受付用 UI（言語ボタン / 原文 / ➔ / 訳文）
- `run.sh` — 起動スクリプト（`127.0.0.1:3374` 固定）

現在のバージョン: `v.0.7.0`（画面右上に表示）

## セットアップ

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# モデルは Hugging Face の GGUF を取得して登録する
ollama pull hf.co/pfnet/plamo-3-translate-2b-GGUF:Q4_K_M
ollama cp hf.co/pfnet/plamo-3-translate-2b-GGUF:Q4_K_M plamo-3-translate:2b
```

Ollama 本体とモデルは、画面右上の「Ollama」「plamo-3-translate:2b」ボタンからも導入できます。

## 起動

```bash
./run.sh
```

`http://127.0.0.1:3374` で待ち受けます（外部から直接アクセス不可）。

| 環境変数 | 既定値 | 用途 |
| --- | --- | --- |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Ollama の接続先 |
| `OLLAMA_MODEL` | `plamo-3-translate:2b` | 使用モデル名 |
| `OLLAMA_MODEL_SOURCE` | `hf.co/pfnet/plamo-3-translate-2b-GGUF:Q4_K_M` | モデル導入元（Hugging Face の GGUF） |
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
2. 左側にゲストの原文を入力し、➔ ボタン（または Ctrl + Enter）で右側に日本語訳を表示する
3. 右側の日本語訳を編集し、⬅ ボタン（または右側で Ctrl + Enter）で左側に選択言語の訳文を表示する

「Other Languages」を選ぶと、言語は LLM 側で自動判別されます。
逆方向（日本語 → ゲスト言語）の翻訳では「Other Languages」は使えないため、
具体的な言語を選択してください。

各カード下部の「音声」ボタンで音声入力できます。原文側は選択中の言語、
日本語側は日本語（`ja-JP`）で認識し、ボタンをもう一度押すと停止します。
Web Speech API を使うため Chrome / Safari を推奨します（HTTPS が必要）。
「音声」の右の「クリア」を押すと、その欄の入力内容を消去できます。

## メンテナンス

画面右上のボタンから操作できます（実行前に確認ダイアログが出ます）。

- **Ollama**: 未導入のときだけ押せる。公式インストーラー（`https://ollama.com/install.sh`）で導入し、サービスを起動する。導入済みになると自動で無効化される
- **plamo-3-translate:2b**: モデル未導入のときだけ押せる。Hugging Face の GGUF（Q4_K_M）を取得し、`plamo-3-translate:2b` として登録する。Ollama 未導入時は押せない
- **アップデート**: `https://github.com/hirogura/honyaku.git` の `main` を `git pull --ff-only` で取得
- **再起動**: サービスを終了し、`run.sh` で起動し直す（ログは `logs/app.log`）

アップデート後は「再起動」を押すと内容が反映されます。ローカルに未コミットの変更や
未プッシュのコミットがある場合、`--ff-only` のためアップデートは失敗します。
