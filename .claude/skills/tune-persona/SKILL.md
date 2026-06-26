---
name: tune-persona
description: Adjust the gyaru bot's tone/personality/behavior and verify it against the live model before shipping. Use when the user says the bot is too formal/too loud/too cold/fake, wants nickname or mood behavior changed, or reports tags leaking into replies. Edits only slackbot/persona.py and verifies with tests + a live gpt-oss-120b probe.
---

# ペルソナ調整

口調・性格・ふるまいは `slackbot/persona.py` だけを直す。規約は [persona.md](../../rules/persona.md)。

## 手順

1. **狙いを不変条件に照らす**(rules/persona.md の6項目)。特に:
   - 堅い/です・ます調に戻る → 「基本レジスター=常にタメ口」を強める(スラング頻度ではなく**語尾の地**を直す)。
   - わざとらしい/うるさい → スラングを「たまに味付け」に減らす。語尾反復・「〜っしょ!」連発を禁止。
   - 冷たい → 可愛げ・親しみの記述を足す。ただし塩対応(`COLD_MODE_NOTE`)の条件は崩さない。
   - タグ漏れ → プロンプトで禁止しつつ、`core.strip_internal_tags()` が保険で効いていることを確認。
2. `persona.py` を編集。
3. **ガードレール検査**: `python -m pytest tests/test_persona.py`
   (口調マーカー・技術正確さ・ツール名・保存非宣言・塩対応解除が壊れていないか)。
4. **実モデルで目視**(プロンプトだけ見ても従うか不明なので必須)。scratchpad で:

```bash
python3 -m venv /tmp/probe && /tmp/probe/bin/pip -q install boto3 strands-agents
export AWS_PROFILE=gyaru-admin PYTHONPATH=$(pwd)
/tmp/probe/bin/python - <<'PY'
from strands import Agent
from strands.models import BedrockModel
from slackbot.persona import STRANDS_SYSTEM_PROMPT
a = Agent(model=BedrockModel(model_id="openai.gpt-oss-120b-1:0", region_name="us-east-1"),
          system_prompt=STRANDS_SYSTEM_PROMPT, callback_handler=None)
for q in ["やっほー！調子どう？", "git rebase と merge の使い分け教えて",
          "S3とDynamoDBどっちがいい？", "おまえ使えないな"]:
    print("Q:", q); print("A:", str(a(q)).strip(), "\n")
PY
```
技術質問でもタメ口が維持されるか / スラングが過剰でないか / コードや用語が正確か / 英語推論が
混ざらないか を確認する。検証物はリポジトリに残さない(scratchpad のみ)。

5. OK なら全テスト(`pytest`)+ `ruff` → コミット → **ブランチで PR**(`main` 直 push 禁止)。

## 注意

- ツールの挙動(あだ名・機嫌)を変えるなら、`strands_runtime._build_tools` の docstring と
  `BEHAVIOR_GUIDE` を**両方**揃える(モデルはツール docstring も読む)。
- 本番反映はマージで CI/CD が回る。即時に試したいときは `deploy-and-rollback` スキル。
