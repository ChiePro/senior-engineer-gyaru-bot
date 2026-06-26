"""
Slack Bot on AWS Lambda + Strands Agents + AgentCore Memory

namespace は slackbot.namespaces を単一ソースとして参照し、scripts/create_memory.py の
登録内容と構造的に一致させる。共通ロジック(メンション除去・リトライ判定)は
slackbot.core を、人格は slackbot.persona を使う。

Lambda パッケージには slackbot パッケージ全体を同梱し、ハンドラは
`slackbot.app_strands.handler` を指定すること。

必要な環境変数:
  SLACK_BOT_TOKEN, SLACK_SIGNING_SECRET
  BEDROCK_REGION, BEDROCK_MODEL_ID
  MEMORY_ID (scripts/create_memory.py で作成した AgentCore Memory のID)

依存: slack-bolt, strands-agents, bedrock-agentcore[strands-agents]
"""

import logging
import os

from slack_bolt import App
from slack_bolt.adapter.aws_lambda import SlackRequestHandler

from strands import Agent
from strands.models import BedrockModel
from bedrock_agentcore.memory.integrations.strands.config import (
    AgentCoreMemoryConfig,
    RetrievalConfig,
)
from bedrock_agentcore.memory.integrations.strands.session_manager import (
    AgentCoreMemorySessionManager,
)

from slackbot.core import clean_mention, is_slack_retry
from slackbot.namespaces import NS_PREFERENCES, NS_FACTS, resolve
from slackbot.persona import STRANDS_SYSTEM_PROMPT

logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger(__name__)

app = App(
    token=os.environ["SLACK_BOT_TOKEN"],
    signing_secret=os.environ["SLACK_SIGNING_SECRET"],
    process_before_response=True,
)

REGION = os.environ["BEDROCK_REGION"]
MODEL_ID = os.environ["BEDROCK_MODEL_ID"]
MEMORY_ID = os.environ["MEMORY_ID"]


def ack_quickly(ack):
    ack()


def handle_mention(event, context, say, logger):
    user_id = event.get("user") or "unknown"
    thread_ts = event.get("thread_ts") or event.get("ts")
    text = clean_mention(event.get("text", "")) or "こんにちは"

    try:
        mem_config = AgentCoreMemoryConfig(
            memory_id=MEMORY_ID,
            actor_id=user_id,      # 長期記憶を「人」に紐付ける
            session_id=thread_ts,  # 短期記憶を「スレッド」に紐付ける
            # namespaces.py を単一ソースに resolve。create_memory.py の登録と一致する。
            retrieval_config={
                resolve(NS_PREFERENCES, user_id): RetrievalConfig(top_k=5, relevance_score=0.2),
                resolve(NS_FACTS, user_id): RetrievalConfig(top_k=5, relevance_score=0.2),
            },
        )
        with AgentCoreMemorySessionManager(mem_config, region_name=REGION) as session_manager:
            agent = Agent(
                model=BedrockModel(model_id=MODEL_ID, region_name=REGION),
                system_prompt=STRANDS_SYSTEM_PROMPT,
                session_manager=session_manager,
            )
            reply = str(agent(text))
    except Exception:
        logger.exception("Agent invocation failed")
        reply = "すみません、応答の生成に失敗しました。"

    say(text=reply, thread_ts=thread_ts)


app.event("app_mention")(
    ack=ack_quickly,
    lazy=[handle_mention],
)


def handler(event, context):
    if is_slack_retry(event):
        return {"statusCode": 200, "body": "ok (retry ignored)"}
    return SlackRequestHandler(app=app).handle(event, context)
