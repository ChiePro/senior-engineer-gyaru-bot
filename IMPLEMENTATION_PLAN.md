# スレッド内メンションレス応答

## 背景 / 要望

Bot(きあら)にメンションしないと会話できないのが面倒。**一度でもメンションされたスレッド**では、
メンションなしの発言にも空気を読んで反応してほしい。

## 決定事項

- 対象は「**そのスレッドで過去に1回でもきあらにメンションが来ている**」スレッドだけ。
  満たさないスレッドには一切割り込まない(チャンネルの無関係なスレッドに勝手に入らない)。
- スレッドの状況で挙動を分ける(ここで言う 1対1/グループ は *実際に書き込んでいる人数* の話。
  Slack のチャンネル種別ではない):
  - **1対1**(きあら + 発言者だけが書き込んでいる) → メンションなしで毎回返答。
  - **グループ**(発言者以外の人間も書き込んでいる) → **積極的に参加**。割り込むべきでない時だけ黙る。
- グループの「黙る」は、本応答を1回回し、モデルが「今は割り込まない」と判断したら本文の代わりに
  `<skip/>` だけ返す方式(積極参加なので大半は喋る前提。別途 judge 呼び出しは挟まない)。

## アーキテクチャ上の前提(壊さない)

- 純粋ロジック層(`core.py`/`persona.py`/`namespaces.py`)は stdlib のみ。判定ロジックは core に置きテスト可能にする。
- 応答生成は `strands_runtime.respond()` に集約。人格・ふるまいは `persona.py` 単一ソース。
- Slack 側設定変更が前提(コードだけでは完結しない): イベント購読 `message.channels`(必要に応じ
  groups/im/mpim)、スコープ `channels:history` 等、アプリ再インストール、Bot をチャンネルに招待。

## Stage 1: 純粋ロジック + テスト(core.py / TDD)
**Goal**: Slack/SDK 無しでテストできる判定ロジック
**追加関数**:
- `is_autoreply_candidate(*, is_thread_reply, is_from_bot, has_subtype, mentions_bot)` — API を叩く前の安いフィルタ
- `summarize_thread(messages, bot_user_id, *, max_transcript)` — replies の dict 配列から
  (bot_mentioned, human_ids, transcript) を作る
- `classify_thread(*, bot_mentioned_in_thread, human_participant_ids, speaker_id)` → "ignore"|"one_on_one"|"group"
- `SKIP_TOKEN` / `strip_skip_token(text)` / `is_silent_reply(text)` — グループの「黙る」判定
**Tests**: `tests/test_unit.py` に red→green
**Status**: Complete

## Stage 2: 配線(socket_app.py / strands_runtime.py)
**Goal**: `@app.event("message")` 追加。安いフィルタ → `conversations.replies` 1回 → classify →
1対1は投稿 / グループは `<skip/>` 以外なら投稿。`handle_mention` と共通処理を `_generate_and_post` に抽出。
`respond()` に `may_stay_silent` / `thread_context` を追加。人格(積極参加 + skip ルール)は
`persona.GROUP_REPLY_GUIDE` に集約。
**Status**: Complete

## Stage 3: ドキュメント / 規約 / 実モデル確認
**Goal**: README に Slack 設定追記、`docs/adr/000X-thread-autoreply.md`、CLAUDE.md/.claude/rules に追記、
gpt-oss-120b へローカル probe(黙る/参加の目視)。`pytest`/`ruff`/`compileall` green。
**Status**: In Progress(docs/ADR/README/CLAUDE.md/rules + 全自動ゲート green は完了。
実モデル probe と Slack 側設定〔イベント購読・スコープ・再インストール・チャンネル招待〕は
AWS/Slack 認証が要る手動ステップで、デプロイ時にユーザーが実施)
