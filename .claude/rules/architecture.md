# アーキテクチャ規約(最重要)

このプロジェクトは「ライブの本番システムが今こう動いている」状態を、コードと docs で
一致させ続けることを最優先する。新しいコードを足す前に、まず以下の境界を壊さないか確認する。

## 1. 純粋ロジックと I/O の分離(絶対に壊さない)

| レイヤー | ファイル | 依存 | 役割 |
|---|---|---|---|
| 純粋ロジック | `slackbot/core.py` / `namespaces.py` / `persona.py` | **stdlib のみ** | 文字列整形・人物注記・タグ除去・ID整形・人格・namespace 定義 |
| I/O・配線 | `slackbot/socket_app.py` / `strands_runtime.py` / `user_store.py` | boto3 / slack-bolt / strands / bedrock-agentcore | Slack・Bedrock・AgentCore・DynamoDB へのアクセスと `os.environ` 読み取り |

**ルール:**
- `core.py` / `persona.py` / `namespaces.py` に boto3 / slack / strands を **絶対に import しない**。`os.environ` も読まない。
- 新しい外部 I/O は I/O レイヤー(socket_app / strands_runtime / user_store)に置く。
- これにより、テストは重い SDK・secret 無しで純粋ロジックだけを import して動く(CI が secret 不要なのはこのため)。
- `compileall` は I/O ファイルを「コンパイルするだけで実行しない」ので、env 未設定でも CI は壊れない。

## 2. 単一ソースモジュール(drift 防止)

- **人格は `persona.py` だけ。** 口調・ふるまいの変更はこのファイル一箇所で完結させる。応答生成は `STRANDS_SYSTEM_PROMPT` を参照する。
- **AgentCore namespace は `namespaces.py` だけ。** 登録側(`scripts/create_memory.py`)と検索側(`strands_runtime.py` の `retrieval_config`)が同じ定数から解決するので、食い違い(長期記憶が永久にヒットしない不具合)が起きない。`tests/test_unit.py` がこの一致を検査している — namespace を変えるならテストも一緒に通すこと。

## 3. 応答生成は `strands_runtime.respond()` に集約

- Strands `Agent`(`BedrockModel` + `AgentCoreMemorySessionManager` + function calling ツール)の構築はここだけ。
- `callback_handler=None` は意図的。既定の printing handler はモデルの推論を stdout に流して CloudWatch を汚す。Slack へ出すのは `str(agent(text))`(text コンテンツブロックのみ)。
- `socket_app.handle_mention` は Slack 配線の薄いラッパに留める。

## 4. 三層メモリ(設計意図を保つ)

- **短期(スレッド単位)**: AgentCore `session_id = safe_id(thread_ts)`。スレッド間は隔離。
- **長期(人単位・横断)**: AgentCore `actor_id = safe_id(user_id)`。嗜好・事実を自動抽出。
- **あだ名・特徴・機嫌(横断・ワークスペース共有)**: `user_store.py`(DynamoDB)。キーは **対象ユーザーの ID**(発言者ではない)。詳細は [docs/design/memory-model.md](../../docs/design/memory-model.md)、判断の経緯は [docs/adr/0002-per-target-user-store.md](../../docs/adr/0002-per-target-user-store.md)。

## 変更時のチェックリスト

- [ ] 純粋ロジック層に重い import / env 読みを足していないか
- [ ] 人格・namespace を単一ソース以外で重複定義していないか
- [ ] `python -m pytest`(27件)/ `ruff check .` / `compileall slackbot scripts` が green か
- [ ] モジュールを移動/改名したら、entrypoint(`python -m slackbot.socket_app`)・`Dockerfile.fargate`・CI の `compileall` パス・README・テスト import を**揃えて**更新したか
