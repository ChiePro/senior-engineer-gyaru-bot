"""
シナリオテスト: マルチターン会話を fake I/O で駆動し、
短期記憶(スレッド)・長期記憶(プロフィール)・投稿順序を検証する。

app.py の handle_mention は重い依存を import するため直接は使わず、
handle_mention と同じ合成(prepare_reply → 投稿 → update_long_term_memory)を
このハーネスで再現してテストする。
"""

from slackbot import core as bot_core

BOT = "BOT"
USER = "U_ALICE"
CHANNEL = "C1"
BASE_PROMPT = "あなたは社内向けアシスタントです。"
MAX_HISTORY = 20


class FakeWorld:
    """Slack スレッド / Bedrock / DynamoDB を模した最小の世界。"""

    def __init__(self):
        self.thread = []          # スレッド内のメッセージ(Slack 風 dict)
        self.memory_store = {}    # user_id -> profile text
        self.events = []          # 起きた副作用の順序記録
        self.generate_calls = []  # generate_reply に渡された (messages, system_prompt)

    # --- 注入する callable 群 ---
    def fetch_thread(self, channel, thread_ts):
        self.events.append("fetch")
        return list(self.thread)

    def load_memory(self, user_id):
        self.events.append("load")
        return self.memory_store.get(user_id, "")

    def save_memory(self, user_id, text):
        self.events.append("save")
        self.memory_store[user_id] = text

    def generate_reply(self, messages, system_prompt):
        self.events.append("generate")
        self.generate_calls.append((messages, system_prompt))
        # 直近の user 発言をエコーする擬似応答
        last_user = [m for m in messages if m["role"] == "user"][-1]
        return f"了解: {last_user['content'][0]['text']}"

    def post_reply(self, text, thread_ts):
        self.events.append("post")
        self.thread.append({"user": BOT, "text": text})

    def extract_memory(self, existing, new_text):
        self.events.append("extract")
        # 既存に新しい事実を1行追記する擬似抽出
        line = f"・{new_text}"
        return (existing + "\n" + line).strip() if existing else line


def run_one_turn(world, user_text, thread_ts):
    """handle_mention と同じ順序で1ターンを処理する。"""
    # ユーザー発言をスレッドへ追加
    world.thread.append({"user": USER, "text": user_text})
    event = {
        "user": USER,
        "channel": CHANNEL,
        "ts": thread_ts,
        "thread_ts": thread_ts,
        "text": user_text,
    }
    result = bot_core.prepare_reply(
        event=event,
        bot_user_id=BOT,
        base_prompt=BASE_PROMPT,
        max_history=MAX_HISTORY,
        fetch_thread=world.fetch_thread,
        load_memory=world.load_memory,
        generate_reply=world.generate_reply,
    )
    world.post_reply(result.reply, result.thread_ts)  # 先に投稿
    bot_core.update_long_term_memory(           # その後で長期記憶更新
        result, extract_memory=world.extract_memory, save_memory=world.save_memory
    )
    return result


def _roles(messages):
    return [m["role"] for m in messages]


def test_scenario_multiturn_memory_and_ordering():
    world = FakeWorld()
    T = "T_THREAD_1"

    # --- ターン1: 初対面。長期記憶はまだ空 ---
    r1 = run_one_turn(world, "<@BOT> 僕は辛いラーメンが好きです", T)
    msgs1, sys1 = world.generate_calls[0]
    # 初回は長期記憶が無いので system prompt はベースのみ
    assert sys1 == BASE_PROMPT
    assert _roles(msgs1) == ["user"]
    # 返信内容が投稿され、スレッドに積まれている
    assert r1.reply.startswith("了解:")
    # ターン1の後に長期記憶が保存されている
    assert "辛いラーメン" in world.memory_store[USER]

    # --- ターン2: 長期記憶が注入され、短期記憶(スレッド)も引き継がれる ---
    run_one_turn(world, "<@BOT> 何を食べるといい?", T)
    msgs2, sys2 = world.generate_calls[1]
    # system prompt に長期記憶(前ターンの嗜好)が含まれる
    assert "辛いラーメン" in sys2
    assert BASE_PROMPT in sys2
    # 短期記憶: ターン1の user/assistant + ターン2の user が入っている
    assert _roles(msgs2) == ["user", "assistant", "user"]
    # Converse 制約(user 始まり・role 交互)を満たす
    assert msgs2[0]["role"] == "user"
    for i in range(1, len(msgs2)):
        assert msgs2[i]["role"] != msgs2[i - 1]["role"]

    # --- ターン3: 長期記憶がさらに積み上がる ---
    run_one_turn(world, "<@BOT> 犬も飼っています", T)
    assert "辛いラーメン" in world.memory_store[USER]
    assert "犬も飼っています" in world.memory_store[USER]

    # --- 投稿順序: 各ターンで post が save より前(返信が先・記憶更新は後) ---
    post_indices = [i for i, e in enumerate(world.events) if e == "post"]
    save_indices = [i for i, e in enumerate(world.events) if e == "save"]
    assert len(post_indices) == 3 and len(save_indices) == 3
    for p, s in zip(post_indices, save_indices):
        assert p < s, "返信(post)は長期記憶更新(save)より先に行われるべき"


def test_scenario_separate_threads_keep_short_term_isolated():
    """別スレッドでは短期記憶が混ざらないが、長期記憶(ユーザー単位)は共有される。"""
    world = FakeWorld()

    # スレッドA で会話
    run_one_turn(world, "<@BOT> Aの話題", "T_A")
    # 新しいスレッドB を開始(world.thread を切り替える代わりに別 world にせず、
    # スレッドは1本ずつ処理する設計なのでここではスレッドをリセットして別スレッドを模す)
    world.thread = []
    msgs_before = len(world.generate_calls)
    run_one_turn(world, "<@BOT> Bの話題", "T_B")
    msgs_b, sys_b = world.generate_calls[msgs_before]

    # スレッドB の短期履歴に「Aの話題」は含まれない(スレッド分離)
    joined = " ".join(m["content"][0]["text"] for m in msgs_b)
    assert "Aの話題" not in joined
    # 長期記憶(ユーザー単位)は引き継がれ、system prompt に A の内容が出る
    assert "Aの話題" in sys_b


def test_scenario_history_cap_keeps_user_start():
    """履歴上限で切り詰めても user 始まり・role 交互が保たれる。"""
    world = FakeWorld()
    T = "T_CAP"
    # 上限を小さくして複数ターン回す
    global MAX_HISTORY
    saved = MAX_HISTORY
    MAX_HISTORY = 3
    try:
        for i in range(4):
            run_one_turn(world, f"<@BOT> メッセージ{i}", T)
        # 最後のターンで渡された messages を検証
        last_msgs, _ = world.generate_calls[-1]
        assert last_msgs[0]["role"] == "user"
        for i in range(1, len(last_msgs)):
            assert last_msgs[i]["role"] != last_msgs[i - 1]["role"]
        assert len(last_msgs) <= 3
    finally:
        MAX_HISTORY = saved
