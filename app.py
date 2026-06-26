"""
Slack Bot on AWS Lambda + Amazon Bedrock (DIY 版, long-term + short-term memory)

純粋ロジックは bot_core.py に分離し、ここは外部 I/O (Slack/Bedrock/DynamoDB) の
実装と配線だけを担当する。これによりロジックを重い依存なしで単体テストできる。

メモリ:
  - 短期記憶: Slack スレッド履歴 (conversations.replies)。DB 不要。
  - 長期記憶: ユーザーごとの趣味嗜好・性格などを DynamoDB に永続化。

必要な環境変数:
  SLACK_BOT_TOKEN, SLACK_SIGNING_SECRET
  BEDROCK_REGION, BEDROCK_MODEL_ID
  MEMORY_MODEL_ID (任意, 既定は BEDROCK_MODEL_ID)
  MEMORY_TABLE (DynamoDB テーブル名)

Lambda パッケージには bot_core.py を同梱すること。
"""

import logging
import os

import boto3
from slack_bolt import App
from slack_bolt.adapter.aws_lambda import SlackRequestHandler

from bot_core import (
    prepare_reply,
    update_long_term_memory,
    is_slack_retry,
)

logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger(__name__)

app = App(
    token=os.environ["SLACK_BOT_TOKEN"],
    signing_secret=os.environ["SLACK_SIGNING_SECRET"],
    process_before_response=True,
)

bedrock = boto3.client("bedrock-runtime", region_name=os.environ["BEDROCK_REGION"])
MODEL_ID = os.environ["BEDROCK_MODEL_ID"]
MEMORY_MODEL_ID = os.environ.get("MEMORY_MODEL_ID", MODEL_ID)

dynamodb = boto3.resource("dynamodb")
memory_table = dynamodb.Table(os.environ["MEMORY_TABLE"])

BASE_SYSTEM_PROMPT = "あなたは社内向けの親切なアシスタントです。簡潔に日本語で回答してください。"
MAX_HISTORY = 20
MAX_MEMORY_CHARS = 4000


# --- 外部 I/O 実装(bot_core に callable として渡す) ---
def load_memory(user_id: str) -> str:
    try:
        item = memory_table.get_item(Key={"user_id": user_id}).get("Item")
        return item.get("memory", "") if item else ""
    except Exception:
        logger.exception("Failed to load memory for %s", user_id)
        return ""


def save_memory(user_id: str, memory_text: str) -> None:
    try:
        memory_table.put_item(
            Item={"user_id": user_id, "memory": memory_text[:MAX_MEMORY_CHARS]}
        )
    except Exception:
        logger.exception("Failed to save memory for %s", user_id)


def generate_reply(messages: list, system_prompt: str) -> str:
    resp = bedrock.converse(
        modelId=MODEL_ID,
        system=[{"text": system_prompt}],
        messages=messages,
        inferenceConfig={"maxTokens": 1024, "temperature": 0.7},
    )
    return resp["output"]["message"]["content"][0]["text"]


def extract_memory(existing: str, new_user_text: str) -> str:
    """新しい発言から長期的に有用な情報のみ抽出・統合。失敗時は空文字。"""
    extractor_system = (
        "あなたはユーザーの長期プロフィールを管理する係です。"
        "既存プロフィールと新しい発言をもとに、趣味・嗜好・性格・繰り返し言及される事実など"
        "長期的に有用な情報のみを統合し、更新後のプロフィールを箇条書きで出力してください。"
        "一時的な話題・その場限りの雑談・質問内容そのものは含めないこと。"
        "重複は統合し、最大15項目・各項目は簡潔に。プロフィール本文のみを出力すること。"
    )
    user_msg = f"既存プロフィール:\n{existing or '(なし)'}\n\n新しい発言:\n{new_user_text}"
    try:
        resp = bedrock.converse(
            modelId=MEMORY_MODEL_ID,
            system=[{"text": extractor_system}],
            messages=[{"role": "user", "content": [{"text": user_msg}]}],
            inferenceConfig={"maxTokens": 512, "temperature": 0.2},
        )
        return resp["output"]["message"]["content"][0]["text"].strip()
    except Exception:
        logger.exception("Failed to extract memory")
        return ""


# --- Slack handlers(配線のみ) ---
def ack_quickly(ack):
    ack()


def handle_mention(event, client, context, say, logger):
    bot_user_id = context.get("bot_user_id")

    def fetch_thread(channel: str, thread_ts: str) -> list:
        return client.conversations_replies(
            channel=channel, ts=thread_ts, limit=100
        ).get("messages", [])

    fallback_thread = event.get("thread_ts") or event.get("ts")

    try:
        result = prepare_reply(
            event=event,
            bot_user_id=bot_user_id,
            base_prompt=BASE_SYSTEM_PROMPT,
            max_history=MAX_HISTORY,
            fetch_thread=fetch_thread,
            load_memory=load_memory,
            generate_reply=generate_reply,
        )
    except Exception:
        logger.exception("Failed to prepare reply")
        say(text="すみません、応答の生成に失敗しました。", thread_ts=fallback_thread)
        return

    # 先に返信を投稿
    say(text=result.reply, thread_ts=result.thread_ts)

    # 返信後に長期記憶を更新(体感遅延なし)
    try:
        update_long_term_memory(
            result, extract_memory=extract_memory, save_memory=save_memory
        )
    except Exception:
        logger.exception("Failed to update long-term memory")


app.event("app_mention")(
    ack=ack_quickly,
    lazy=[handle_mention],
)


def handler(event, context):
    if is_slack_retry(event):
        return {"statusCode": 200, "body": "ok (retry ignored)"}
    return SlackRequestHandler(app=app).handle(event, context)
