# 設計: メモリモデル(三層)

ボットの記憶は性質の異なる3層に分かれている。「何をどこに置くか」を取り違えると、
話者の混同や、長期記憶が永久にヒットしない不具合につながる。判断経緯は
[ADR-0002](../adr/0002-per-target-user-store.md)。運用は `agentcore-memory-ops` スキル。

## 三層

| 層 | 実体 | キー | 何を持つ | 共有範囲 |
|---|---|---|---|---|
| 短期 | AgentCore Memory events | `session_id = safe_id(thread_ts)` | スレッドの会話文脈 | そのスレッドだけ |
| 長期 | AgentCore Memory records | `actor_id = safe_id(user_id)` | 発言者本人の嗜好・事実(自動抽出) | その人の全スレッド横断 |
| 人物属性 | DynamoDB ユーザーテーブル | `user_id`(=対象者) | あだ名 / 特徴(notes) / 機嫌(cold) | ワークスペース全員で共有 |

### なぜ長期記憶と人物属性を分けるのか

- AgentCore の長期記憶は **発言者(actor)単位**。「発言者本人の嗜好」には合うが、
  「第三者の属性」や「全員で共有したいプロフィール」には構造的に合わない(発言者ごとに分裂する)。
- 「`@坂本` は坂もっちゃん」のような第三者のあだ名・特徴は、**対象者の ID をキー**にした
  DynamoDB ストアに置くことで、誰が話しても同じ情報を参照でき、発言者との取り違えも防げる。
- 機嫌(塩対応)も相手単位で持つ必要がある(失礼を言った人だけ冷たく、謝れば解除)。

## ID 整形(`core.safe_id`)

AgentCore の `actorId`/`sessionId` は `[a-zA-Z0-9][a-zA-Z0-9-_]*` のみ許可。Slack の `thread_ts` は
`1719374400.123456` のようにドットを含むため、許可外文字を `-` に置換し先頭を英数字にする。
**決定的**(同じ入力→同じ出力)なので、同じスレッドは常に同じ session に紐づく。

## namespace の単一ソース(ドリフト防止)

AgentCore の長期記憶は namespace で出し入れする。これを `slackbot/namespaces.py` に一元化し、
**登録側**(`scripts/create_memory.py` の戦略定義)と**検索側**(`strands_runtime.py` の
`retrieval_config`)が同じ定数から `resolve()` する。

| 戦略 | namespace | 用途 |
|---|---|---|
| userPreferenceMemoryStrategy | `/users/{actorId}/preferences/` | 嗜好・性格(retrieval 対象) |
| semanticMemoryStrategy | `/users/{actorId}/facts/` | 事実情報(retrieval 対象) |
| summaryMemoryStrategy | `/users/{actorId}/summaries/{sessionId}/` | スレッド要約(保存のみ) |

- **末尾スラッシュまで完全一致**が必須。食い違うと検索が永久に空振りする。
- `tests/test_unit.py` が登録側↔検索側の一致を検査している。namespace を変えるならテストも通すこと。
- TTL: 短期 events は `EVENT_EXPIRY_DAYS`(7〜365日)。長期 records は抽出後も残るので、肥大化が
  気になれば定期整理を別途検討。

## 読み出し(プロンプト注入)

メンションごとに、応答生成前に2系統を system prompt へ注入する(`strands_runtime.respond`)。
**選別方針が違う**点が重要:

| 種類 | ソース | 選別 | 注入対象 |
|---|---|---|---|
| 長期記憶(嗜好・事実) | AgentCore retrieval | **関連度ゲート**(`relevance_score`)。今の話題に近いものだけ | 閾値超えのみ(0件もありうる) |
| 人物プロフィール(あだ名+特徴) | DynamoDB `profiles_for` | 会話の**登場人物**(発言者+本文の `<@Uxxx>`) | `build_people_note` |
| あだ名辞書(全員) | DynamoDB `all_nicknames` | **無条件・全件**(関連度フィルタ無し) | `build_nickname_directory` |

- 長期記憶を関連度で絞るのは、別スレッド・別話題の記憶を脈絡なく持ち出すのを防ぐため
  (`relevance_score` が実質の取捨選択ゲート、`top_k` は安全上限)。
- 一方**あだ名は逆引きの正確さが命**なので、関連度では絞らない。ユーザーがあだ名で人を呼んだとき
  (`@メンション無しで「さかもっちゃん元気?」`など)に誰のことか必ず特定できるよう、会話に未登場の
  人も含めた**全員のあだ名辞書を常に注入**する。特徴(notes)は肥大を避けるため登場人物だけに留める。

## モデルによる読み書き(function calling)

人物属性ストアへの書き込みはモデルがツール経由で行う(`strands_runtime._build_tools`):

| ツール | 効果 | 対象 |
|---|---|---|
| `set_nickname(user_id, nickname)` | あだ名を保存 | 本文の `<@Uxxx>` か、本人なら発言者 |
| `remember_about(user_id, note)` | 特徴を1件追記(重複スキップ, 上限あり) | 対象が不明なら記録しない |
| `set_mood(user_id, cold)` | 塩対応の on/off | 失礼を言った発言者 / 謝ったら解除 |

- 各ツールは `core.normalize_slack_id` で壊れた引数(`<@U..>`・`</user_id>`)から正規 ID を救出し、
  取れなければ発言者 ID にフォールバックする。
- 保存は**宣言しない**(裏方)。覚えた内容は次から自然に使うだけ(`persona.BEHAVIOR_GUIDE`)。

## 既知の不具合パターン

- 「同じ回答の反復 / 謝罪ループ」= 壊れたツール引数でゴミ行ができた / 特定 actor の events が
  誤学習で固着。→ ゴミ行削除 + actor events 整理。
- 「発言者と他人の取り違え」= 対象者 ID キー設計と `<@Uxxx>` 保持で防ぐ(本層の主眼)。
- 「長期記憶が全くヒットしない」= namespace 不一致(末尾スラッシュ含む)を疑う。
