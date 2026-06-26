# ADR-0003: 応答モデルに gpt-oss-120b を採用する

- Status: Accepted
- Date: 2026-06-26

## Context

複雑な system prompt(ギャル人格 + 3つの function calling ツール + 登場人物注入 + 塩対応)に
**安定して従い**、かつ **Slack に思考が漏れない**、コスパの良いモデルが必要だった。候補の実測結果:

- **Nova(micro/lite/pro)**: 安いが、ツール引数を壊す/人格が薄い事例。
- **gpt-oss-20b**: 人格を無視して敬語化、話者を取り違え(他人を発言者と混同)、同じ回答を反復。弱い。
- **Claude Haiku 4.5**: 品質は高いが、Strands が使う **ConverseStream** に対し Anthropic の
  **ユースケース申請フォーム提出が必須**(未提出だと `ResourceNotFoundException`)。フォームは会社の
  法的表明を含むため、その場で代理提出はしない方針。
- **GPT-5 / GPT-5 mini**: OpenAI 専用 API モデルで **Bedrock には存在しない**。使うにはモデルプロバイダを
  OpenAI API に差し替え(コード変更 + API キー + 会話データが OpenAI に出る)。Bedrock の OpenAI 系は
  オープンウェイト `gpt-oss` のみ。
- **gpt-oss-120b**: 指示追従・ツール・話者判別が安定。非ストリーミング `converse` で確認すると、推論は
  `reasoningContent` に分離され `text` は綺麗。Strands の `str(agent())` は text ブロックだけ返すので、
  **Slack に英語の思考が漏れない**(CloudWatch には既定 callback が出すが、`callback_handler=None` で停止)。

## Decision

**`openai.gpt-oss-120b-1:0` を採用する**(`us-east-1`)。`callback_handler=None` で推論ログの
stdout 漏れを止める。データは AWS 内に留め、追加の外部依存・別課金を持ち込まない。

## Consequences

- 利点: AWS 内完結・追加コストなし・ペルソナ/ツール/話者を安定処理・思考漏れなし。
- 制約: モデル変更は `BEDROCK_MODEL_ID`(GitHub 変数 + `ecs.yaml` Default + ECS env)の差し替えで可能だが、
  **採用前に実モデルで口調・正確さ・思考漏れを必ず実測**する(`switch-bedrock-model` スキル)。
- 将来: より高品質が要れば Claude Haiku 4.5(要フォーム提出, AWS 内)か、GPT-5 mini(OpenAI API,
  データ外部送出 + コード変更)が選択肢。判断は都度ユーザーに委ねる。
