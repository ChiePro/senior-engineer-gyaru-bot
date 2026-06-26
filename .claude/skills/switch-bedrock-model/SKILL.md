---
name: switch-bedrock-model
description: Change the Bedrock model the gyaru bot uses (BEDROCK_MODEL_ID). Use when the user wants a different/cheaper/smarter model, reports the bot ignoring its persona or confusing speakers, or asks whether a specific model (Nova, Claude, GPT-5, gpt-oss) is usable. Covers Bedrock model availability gotchas and how to verify a model before rolling it out.
---

# Bedrock モデルの切り替え

応答モデルは **`ecs.yaml` の `BedrockModelId` Default が単一ソース**(deploy.yml は override しない)。
モデル単価=お金に関わるので、ここを変えると `ecs.yaml` の差分として**デプロイ前に所有者承認が必要**になる
([deployment ルール](../../rules/deployment.md))。Strands `BedrockModel` 経由でモデルは差し替え可能だが、
「Bedrock に在るか」「Strands の ConverseStream で叩けるか」「複雑なペルソナ+ツール+人物注入プロンプトに
従えるか」を**必ず実測**してから本番へ。

## 既知の事実(2026-06 時点)

- **gpt-oss-120b**(`openai.gpt-oss-120b-1:0`)= 現行採用。指示追従・ツール・話者判別が安定。
  推論は `reasoningContent` に分離され、`str(agent())` は本文だけ返すので Slack に思考が漏れない。
- **gpt-oss-20b** = 弱い。ペルソナ無視・話者取り違え・反復が出た。非推奨。
- **Nova(micro/lite/pro)** = 安いが、ツール引数を壊す/ペルソナが薄い事例あり。
- **Claude Haiku 4.5**(`us.anthropic.claude-haiku-4-5-...`)= 高品質だが、ConverseStream は
  Anthropic の**ユースケース申請フォーム提出が必要**(未提出だと `ResourceNotFoundException`)。
  申請は会社の法的表明を含むので、勝手に捏造して提出しない。コンソールのフォームへ誘導する。
- **GPT-5 / GPT-5 mini** = OpenAI の専用 API モデルで **Bedrock には無い**。使うには OpenAI API へ
  モデルプロバイダを差し替え(コード変更 + API キー + データが OpenAI に出る)。Bedrock の OpenAI は
  オープンウェイトの `gpt-oss` 系のみ。

## 在庫確認

```bash
AWS_PROFILE=gyaru-admin aws bedrock list-foundation-models --region us-east-1 \
  --query "modelSummaries[].modelId" --output text | tr '\t' '\n' | grep -i -E "gpt-oss|nova|claude"
```

## 採用前の実測(scratchpad で / リポジトリに残さない)

```bash
python3 -m venv /tmp/probe && /tmp/probe/bin/pip -q install boto3 strands-agents
export AWS_PROFILE=gyaru-admin PYTHONPATH=$(pwd)
/tmp/probe/bin/python - <<'PY'
from strands import Agent
from strands.models import BedrockModel
from slackbot.persona import STRANDS_SYSTEM_PROMPT
a = Agent(model=BedrockModel(model_id="<候補ID>", region_name="us-east-1"),
          system_prompt=STRANDS_SYSTEM_PROMPT, callback_handler=None)
for q in ["git rebase と merge の使い分け教えて", "Lambdaのコールドスタート減らすには？"]:
    print("A:", str(a(q)).strip(), "\n")
PY
```
口調(タメ口が技術質問でも維持されるか)・正確さ・思考漏れ(英語推論が混入しないか)を目視する。
非ストリーミングで `reasoningContent` 分離を確認したいときは `boto3` の `converse` を直接叩く。

## 本番へ反映

1. `ecs.yaml` の `BedrockModelId` の `Default:` を新 ID に変更(これが単一ソース)。
2. ブランチ → PR(`main` 直 push 禁止)。
3. 反映は2通り:
   - **CI/CD 経由(推奨)**: PR をマージ → CI 成功後、deploy.yml の **detect** が `ecs.yaml` 変更を検出 →
     **approval** で所有者の Approve を待つ → Approve すると deploy が走り新タスク起動。
   - **即時手動**: `deploy-and-rollback` スキルの手順でビルド/デプロイ、または
     `aws cloudformation deploy ... --parameter-overrides BedrockModelId=<新ID> ...`。
4. タスクロール(`ecs.yaml` の TaskRole)に当該モデルの `bedrock:InvokeModel*` があることを確認
   (`Resource: "*"` なので通常は追加不要)。
