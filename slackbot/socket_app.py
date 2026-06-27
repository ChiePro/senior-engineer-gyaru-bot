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
  TAVILY_API_KEY   (任意; あれば web_search ツールで最新情報を検索。無ければ検索なしで動く)

依存: slack-bolt, websocket-client, strands-agents, bedrock-agentcore[strands-agents], boto3
"""

import logging
import os

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from slackbot.core import (
    strip_bot_mention,
    mentioned_user_ids,
    is_autoreply_candidate,
    summarize_thread,
    classify_thread,
    is_silent_reply,
    strip_skip_token,
)
from slackbot.persona import FALLBACK_MESSAGE
from slackbot.strands_runtime import respond
from slackbot.user_store import UserStore

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

REGION = os.environ["BEDROCK_REGION"]
MODEL_ID = os.environ["BEDROCK_MODEL_ID"]
MEMORY_ID = os.environ["MEMORY_ID"]
# Web 検索(任意)。未設定なら web_search ツールを渡さず、検索なしで普通に動く。
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY")

# Socket Mode では署名検証 (signing secret) は不要。bot token だけで App を作る。
app = App(token=os.environ["SLACK_BOT_TOKEN"])
store = UserStore(os.environ["USER_TABLE"], REGION)

# ボット自身のユーザーIDを1回だけ取得(自分宛てメンションの除去に使う)。
try:
    BOT_USER_ID = app.client.auth_test()["user_id"]
except Exception:
    logger.exception("auth_test failed; bot mention stripping may be degraded")
    BOT_USER_ID = None


def _generate_and_post(event, say, *, text, may_stay_silent=False, thread_context=None):
    """応答生成 → Slack 投稿の共通処理。app_mention と自発応答(message)で共有する。

    may_stay_silent=True(グループ自発参加)で、モデルが「今は黙る」と判断(<skip/> のみ)したら
    投稿せずに戻る。投稿時は念のため skip 印を除去する。
    """
    user_id = event.get("user") or "unknown"
    thread_ts = event.get("thread_ts") or event.get("ts")
    raw = event.get("text", "")

    # 第三者 <@Uxxx> は残したまま、本文中の人物プロフィール解決に使う。
    mentioned = mentioned_user_ids(raw, exclude=BOT_USER_ID)

    # 発言者と本文中の人物のプロフィール(あだ名・特徴)、発言者の機嫌、
    # さらに全員のあだ名辞書(あだ名で呼ばれた相手を必ず特定するため)をストアから引いて注入する。
    try:
        profiles = store.profiles_for([user_id] + mentioned)
        nicknames = store.all_nicknames()
        speaker_cold = store.get(user_id)["cold"]
    except Exception:
        logger.exception("user store read failed")
        profiles, nicknames, speaker_cold = {}, {}, False

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
            nicknames=nicknames,
            speaker_cold=speaker_cold,
            tavily_api_key=TAVILY_API_KEY,
            may_stay_silent=may_stay_silent,
            thread_context=thread_context,
        )
    except Exception:
        logger.exception("Agent invocation failed")
        reply = FALLBACK_MESSAGE

    # グループ自発参加で「黙る」判断なら投稿しない。
    if may_stay_silent and is_silent_reply(reply):
        return
    say(text=strip_skip_token(reply), thread_ts=thread_ts)


@app.event("app_mention")
def handle_mention(event, say, logger):
    raw = event.get("text", "")
    # ボット自身のメンションだけ除去し、第三者 <@Uxxx> は残す(誰のあだ名か判別するため)。
    text = strip_bot_mention(raw, BOT_USER_ID) or "こんにちは"
    _generate_and_post(event, say, text=text)


@app.event("message")
def handle_message(event, client, say, logger):
    """メンション無しのスレッド発言に、空気を読んで自発的に反応する。

    対象は「過去にきあらがメンションされたスレッド」だけ。安いフィルタで候補を絞ってから
    conversations.replies を1回だけ叩き、参加者と直近やり取りを取得して挙動を決める:
      - one_on_one(きあら+発言者だけ): 毎回返答
      - group(他の人もいる): 積極参加。割り込むべきでない時はモデルが <skip/> で黙る
    Bot をメンションした発言は app_mention が処理するのでここでは無視(二重応答防止)。
    """
    thread_ts = event.get("thread_ts")
    is_thread_reply = bool(thread_ts) and thread_ts != event.get("ts")
    is_from_bot = bool(event.get("bot_id")) or event.get("user") == BOT_USER_ID
    raw = event.get("text", "")
    mentions_bot = bool(BOT_USER_ID) and BOT_USER_ID in mentioned_user_ids(raw)

    if not is_autoreply_candidate(
        is_thread_reply=is_thread_reply,
        is_from_bot=is_from_bot,
        has_subtype=bool(event.get("subtype")),
        mentions_bot=mentions_bot,
    ):
        return

    # 参加者判定と直近やり取りを1回の API で取得する。
    try:
        replies = client.conversations_replies(channel=event["channel"], ts=thread_ts, limit=200)
        messages = replies.get("messages", [])
    except Exception:
        logger.exception("conversations_replies failed")
        return

    bot_mentioned, human_ids, transcript = summarize_thread(messages, BOT_USER_ID)
    speaker_id = event.get("user") or "unknown"
    kind = classify_thread(
        bot_mentioned_in_thread=bot_mentioned,
        human_participant_ids=human_ids,
        speaker_id=speaker_id,
    )
    if kind == "ignore":
        return

    text = strip_bot_mention(raw, BOT_USER_ID) or "(発言)"
    is_group = kind == "group"
    _generate_and_post(
        event,
        say,
        text=text,
        may_stay_silent=is_group,
        thread_context=transcript if is_group else None,
    )


def main() -> None:
    logger.info("Starting Slack Socket Mode handler...")
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()


if __name__ == "__main__":
    main()
