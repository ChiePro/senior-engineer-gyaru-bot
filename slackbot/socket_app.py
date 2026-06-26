"""
Slack Bot (Strands 版) を ECS Fargate 上で常駐させる Socket Mode エントリポイント。

Socket Mode は Slack へ outbound の WebSocket を張る方式。公開エンドポイント / API Gateway /
URL 検証 / lazy listener の自己 invoke が一切不要で、Lambda のコールドスタート問題も起きない。
長時間起動の ECS Fargate Service 上で `python -m slackbot.socket_app` として動かす前提。

あだ名・機嫌(塩対応)は UserStore(DynamoDB)に「対象ユーザーID」をキーで保存し、
function calling ツール経由でモデルが読み書きする(発言者と第三者の取り違え防止)。

必要な環境変数:
  SLACK_BOT_TOKEN  (xoxb-...)
  SLACK_APP_TOKEN  (xapp-...; App-Level Token, scope: connections:write)
  BEDROCK_REGION, BEDROCK_MODEL_ID, MEMORY_ID
  USER_TABLE       (あだ名・機嫌を保存する DynamoDB テーブル名)

依存: slack-bolt, websocket-client, strands-agents, bedrock-agentcore[strands-agents], boto3
"""

import logging
import os

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from slackbot.core import strip_bot_mention, mentioned_user_ids
from slackbot.persona import FALLBACK_MESSAGE
from slackbot.strands_runtime import respond
from slackbot.user_store import UserStore

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

REGION = os.environ["BEDROCK_REGION"]
MODEL_ID = os.environ["BEDROCK_MODEL_ID"]
MEMORY_ID = os.environ["MEMORY_ID"]

# Socket Mode では署名検証 (signing secret) は不要。bot token だけで App を作る。
app = App(token=os.environ["SLACK_BOT_TOKEN"])
store = UserStore(os.environ["USER_TABLE"], REGION)

# ボット自身のユーザーIDを1回だけ取得(自分宛てメンションの除去に使う)。
try:
    BOT_USER_ID = app.client.auth_test()["user_id"]
except Exception:
    logger.exception("auth_test failed; bot mention stripping may be degraded")
    BOT_USER_ID = None


@app.event("app_mention")
def handle_mention(event, say, logger):
    user_id = event.get("user") or "unknown"
    thread_ts = event.get("thread_ts") or event.get("ts")
    raw = event.get("text", "")

    # ボット自身のメンションだけ除去し、第三者 <@Uxxx> は残す(誰のあだ名か判別するため)。
    text = strip_bot_mention(raw, BOT_USER_ID) or "こんにちは"
    mentioned = mentioned_user_ids(raw, exclude=BOT_USER_ID)

    # 発言者と本文中の人物のプロフィール(あだ名・特徴)と発言者の機嫌をストアから引いて注入する。
    try:
        profiles = store.profiles_for([user_id] + mentioned)
        speaker_cold = store.get(user_id)["cold"]
    except Exception:
        logger.exception("user store read failed")
        profiles, speaker_cold = {}, False

    try:
        reply = respond(
            user_id=user_id,
            thread_ts=thread_ts,
            text=text,
            region=REGION,
            model_id=MODEL_ID,
            memory_id=MEMORY_ID,
            store=store,
            profiles=profiles,
            speaker_cold=speaker_cold,
        )
    except Exception:
        logger.exception("Agent invocation failed")
        reply = FALLBACK_MESSAGE

    say(text=reply, thread_ts=thread_ts)


def main() -> None:
    logger.info("Starting Slack Socket Mode handler...")
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()


if __name__ == "__main__":
    main()
