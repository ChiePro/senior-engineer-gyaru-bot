"""
単体テスト: bot_core の純粋ロジックと、namespace の整合性。
重い依存(boto3/slack/strands)は不要で実行できる。
"""

import bot_core
import namespaces
import create_memory


# --- clean_mention ---
def test_clean_mention_strips_mention_and_trims():
    assert bot_core.clean_mention("<@U12345> こんにちは ") == "こんにちは"


def test_clean_mention_handles_multiple_mentions():
    assert bot_core.clean_mention("<@U1> hi <@U2> there") == "hi  there"


def test_clean_mention_handles_none_and_empty():
    assert bot_core.clean_mention(None) == ""
    assert bot_core.clean_mention("   ") == ""


# --- is_slack_retry ---
def test_is_slack_retry_true():
    assert bot_core.is_slack_retry({"headers": {"x-slack-retry-num": "1"}}) is True


def test_is_slack_retry_false_when_absent():
    assert bot_core.is_slack_retry({"headers": {}}) is False
    assert bot_core.is_slack_retry({}) is False


# --- build_conversation ---
def _texts(messages):
    return [m["content"][0]["text"] for m in messages]


def _roles(messages):
    return [m["role"] for m in messages]


def assert_valid_converse(messages):
    """Converse の制約: 非空・user 始まり・role 交互。"""
    assert messages, "messages must not be empty"
    assert messages[0]["role"] == "user"
    for i in range(1, len(messages)):
        assert messages[i]["role"] != messages[i - 1]["role"], f"roles not alternating: {_roles(messages)}"


def test_build_conversation_basic_alternation():
    replies = [
        {"user": "U1", "text": "<@BOT> 質問1"},
        {"user": "BOT", "text": "回答1"},
        {"user": "U1", "text": "<@BOT> 質問2"},
    ]
    msgs = bot_core.build_conversation(replies, bot_user_id="BOT")
    assert _roles(msgs) == ["user", "assistant", "user"]
    assert _texts(msgs) == ["質問1", "回答1", "質問2"]
    assert_valid_converse(msgs)


def test_build_conversation_merges_consecutive_same_role():
    replies = [
        {"user": "U1", "text": "一行目"},
        {"user": "U1", "text": "二行目"},
        {"user": "BOT", "text": "返答"},
    ]
    msgs = bot_core.build_conversation(replies, bot_user_id="BOT")
    assert _roles(msgs) == ["user", "assistant"]
    assert _texts(msgs)[0] == "一行目\n二行目"
    assert_valid_converse(msgs)


def test_build_conversation_drops_leading_assistant():
    replies = [
        {"user": "BOT", "text": "先に喋ったボット"},
        {"user": "U1", "text": "ユーザー発言"},
    ]
    msgs = bot_core.build_conversation(replies, bot_user_id="BOT")
    assert _roles(msgs) == ["user"]
    assert_valid_converse(msgs)


def test_build_conversation_skips_subtype_and_empty():
    replies = [
        {"subtype": "channel_join", "user": "U1", "text": "joined"},
        {"user": "U1", "text": "   "},
        {"user": "U1", "text": "<@BOT> 実発言"},
    ]
    msgs = bot_core.build_conversation(replies, bot_user_id="BOT")
    assert _texts(msgs) == ["実発言"]


def test_build_conversation_caps_history():
    replies = []
    for i in range(10):
        replies.append({"user": "U1", "text": f"q{i}"})
        replies.append({"user": "BOT", "text": f"a{i}"})
    msgs = bot_core.build_conversation(replies, bot_user_id="BOT", max_history=4)
    # 直近4件 = q8,a8,q9,a9
    assert _texts(msgs) == ["q8", "a8", "q9", "a9"]
    assert_valid_converse(msgs)


def test_build_conversation_empty_fallback():
    msgs = bot_core.build_conversation([], bot_user_id="BOT")
    assert _roles(msgs) == ["user"]
    assert msgs[0]["content"][0]["text"]  # 非空のフォールバック


# --- build_system_prompt ---
def test_build_system_prompt_without_memory_returns_base():
    base = "ベースプロンプト"
    assert bot_core.build_system_prompt(base, "") == base


def test_build_system_prompt_with_memory_includes_it():
    base = "ベース"
    mem = "・ラーメンが好き\n・犬を飼っている"
    out = bot_core.build_system_prompt(base, mem)
    assert base in out
    assert "ラーメンが好き" in out
    assert "犬を飼っている" in out


# --- namespace 整合 (app_strands.py と create_memory.py が一致する保証) ---
def test_resolve_substitutes_actor_id():
    assert namespaces.resolve(namespaces.NS_PREFERENCES, "U123") == "/users/U123/preferences/"
    assert namespaces.resolve(namespaces.NS_FACTS, "U123") == "/users/U123/facts/"


def test_resolve_substitutes_session_id():
    out = namespaces.resolve(namespaces.NS_SUMMARIES, "U1", session_id="T9")
    assert out == "/users/U1/summaries/T9/"


def test_namespaces_end_with_slash():
    for ns in (namespaces.NS_PREFERENCES, namespaces.NS_FACTS, namespaces.NS_SUMMARIES):
        assert ns.endswith("/"), f"namespace must end with slash: {ns}"


def test_create_memory_strategies_use_same_namespaces():
    """登録側(create_memory)と検索側(namespaces)が単一ソースで一致していること。"""
    by_type = {list(s.keys())[0]: list(s.values())[0] for s in create_memory.STRATEGIES}
    assert by_type["userPreferenceMemoryStrategy"]["namespaces"] == [namespaces.NS_PREFERENCES]
    assert by_type["semanticMemoryStrategy"]["namespaces"] == [namespaces.NS_FACTS]
    assert by_type["summaryMemoryStrategy"]["namespaces"] == [namespaces.NS_SUMMARIES]
