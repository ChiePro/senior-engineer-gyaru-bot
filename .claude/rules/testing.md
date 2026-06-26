# テスト規約

CI が回すのは `compileall slackbot scripts` + `ruff check .` + `pytest tests/` の3つだけ。
**secret も重い SDK も不要**で完結する。この性質を壊さないこと。

## 原則

- テストは `slackbot.core` / `namespaces` / `persona` / `scripts.create_memory` だけを import する。
  boto3 / slack / strands に依存する `socket_app` / `strands_runtime` / `user_store` は import しない。
- I/O や SDK 連携(Strands・AgentCore・Bedrock・DynamoDB の実挙動)はユニットテストの対象外。
  実環境での結合確認(メンションして応答を見る / CloudWatch ログ)で担保する。
- 純粋ロジックを足したら、対応するテストを `tests/test_unit.py` か `tests/test_persona.py` に足す。

## 何を検査しているか

- `test_unit.py`: メンション整形(`strip_bot_mention` / `mentioned_user_ids`)、壊れたツール引数の
  救済(`normalize_slack_id`)、人物注記(`build_people_note`)、内部タグ除去(`strip_internal_tags`)、
  AgentCore ID 整形(`safe_id`)、**namespace の登録側↔検索側の一致**。
- `test_persona.py`: ギャル口調マーカーと技術正確さガードレールの両立、`STRANDS_SYSTEM_PROMPT` が
  核 + 長期記憶活用の一文になっていること、ツール名(`set_nickname`/`remember_about`/`set_mood`)が
  ガイドに揃っていること、保存を宣言しない方針、塩対応の解除方針。

## 実モデル検証(ユニットテストの外)

口調・ツール挙動など「モデルが従うか」はプロンプトだけ見ても分からない。一時 venv に
`boto3` / `strands-agents` を入れ、`AWS_PROFILE=gyaru-admin` で gpt-oss-120b に数問投げて
`str(agent(...))` を目視する(scratchpad で行い、リポジトリには残さない)。手順は
`tune-persona` / `switch-bedrock-model` スキル参照。

## TDD の建付け

新しい純粋ロジックは red→green→refactor で。既存の振る舞いを変えるときは、まず該当テストを
赤くしてから直す。テストを消して通す/無効化するのは禁止(壊れているなら実装を直す)。
