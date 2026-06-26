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

from slackbot.core import clean_mention, is_slack_retry
from slackbot.persona import FALLBACK_MESSAGE
from slackbot.strands_runtime import respond

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
        reply = respond(
            user_id=user_id,
            thread_ts=thread_ts,
            text=text,
            region=REGION,
            model_id=MODEL_ID,
            memory_id=MEMORY_ID,
        )
    except Exception:
        logger.exception("Agent invocation failed")
        reply = FALLBACK_MESSAGE

    say(text=reply, thread_ts=thread_ts)


app.event("app_mention")(
    ack=ack_quickly,
    lazy=[handle_mention],
)


def handler(event, context):
    if is_slack_retry(event):
        return {"statusCode": 200, "body": "ok (retry ignored)"}
    return SlackRequestHandler(app=app).handle(event, context)
