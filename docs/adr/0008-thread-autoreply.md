# ADR-0008: メンション済みスレッドではメンション無しでも自発応答する

- Status: Accepted
- Date: 2026-06-27

## Context

毎回 `@きあら` とメンションしないと会話できないのが面倒、という要望。一度会話が始まった
スレッドでは、メンション無しの発言にも空気を読んで反応してほしい。一方で、チャンネルの
無関係なスレッドに勝手に割り込む(=ノイズ・コスト・気持ち悪さ)のは避けたい。

これまでは `app_mention` イベントだけを購読しており、メンションされた発言しか Slack から
届かなかった。

## Decision

**「過去に1回でもきあらがメンションされたスレッド」に限り、メンション無しの発言にも自発応答する。**
挙動はスレッドに*実際に書き込んでいる人数*で分ける(Slack のチャンネル種別ではない):

- **1対1**(きあら + 発言者だけが書き込んでいる) → メンション無しでも毎回返答。
- **グループ**(発言者以外の人間も書き込んでいる) → 積極的に参加。ただし割り込むべきでない時は、
  モデルが本文の代わりに `<skip/>`(`core.SKIP_TOKEN`)だけを返し、投稿をスキップする。

実装:

- `message` イベントを購読し、`socket_app.handle_message` で処理する。`app_mention` は従来どおり残し、
  Bot メンション付きの発言は `handle_message` 側で無視して二重応答を防ぐ。
- 判定ロジックは純粋関数として `core.py` に置く(Slack/SDK 非依存・テスト可能):
  `is_autoreply_candidate`(API 前の安いフィルタ)/ `summarize_thread`(replies → 参加者・直近やり取り)/
  `classify_thread`(ignore / one_on_one / group)/ `is_silent_reply` ・ `strip_skip_token`。
- 候補だけ `conversations.replies` を1回叩き、参加者判定と直近やり取りの取得を兼ねる。
- グループ参加の方針(積極参加 + `<skip/>` の使い方)は `persona.GROUP_REPLY_GUIDE` に集約。
  短期記憶(AgentCore)には自分が処理していない他者発言が入らないため、直近やり取りを
  `respond(thread_context=…)` で補って空気を読ませる。

## Consequences

- 利点: メンション無しで自然に会話が続く。短期記憶は従来どおり `session_id = safe_id(thread_ts)` で
  スレッドに紐づくので文脈は途切れない。
- 前提: Slack 側でイベント購読 `message.channels`(必要に応じ groups/im/mpim)とスコープ
  `channels:history` 等を追加し、アプリ再インストール + Bot をチャンネルに招待する必要がある(README 参照)。
- トレードオフ(コスト): グループスレッドの人間発言1件ごとに本応答が1回走る(積極参加なので大半は投稿)。
  賑やかなスレッドでは Bedrock コストが積む。重ければ将来「安いモデルでの事前ゲート(judge)」を足す余地。
- 副作用: `message` 購読により Bot が見えるチャンネルの全メッセージが届くが、`is_autoreply_candidate` で
  スレッド返信以外を API 呼び出し前に捨てるため、無関係な発言で課金は発生しない。
- 関連: グループの「黙る」は judge を別途呼ばず、本応答1回 + `<skip/>` で表現する(積極参加前提のため
  judge を挟むと yes 確認だけの無駄打ちになる)。
