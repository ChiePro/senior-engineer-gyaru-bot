"""
Strands Agents + AgentCore Memory による応答生成。

Socket Mode 版 (socket_app.py) がこれを使う(応答ロジックの単一ソース)。重い SDK
(strands / bedrock_agentcore) に依存するため、stdlib のみの純粋層 (core / namespaces /
persona) とは分けてある。

namespace は slackbot.namespaces を、人格は slackbot.persona を単一ソースとして参照する。
あだ名・機嫌(塩対応)は UserStore を function calling ツール経由で読み書きする。
"""

import random

from strands import Agent, tool
from strands.models import BedrockModel
from bedrock_agentcore.memory.integrations.strands.config import (
    AgentCoreMemoryConfig,
    RetrievalConfig,
)
from bedrock_agentcore.memory.integrations.strands.session_manager import (
    AgentCoreMemorySessionManager,
)

from slackbot.core import (
    safe_id,
    build_people_note,
    build_nickname_directory,
    strip_internal_tags,
    normalize_slack_id,
    as_bool,
)
from slackbot.namespaces import NS_PREFERENCES, NS_FACTS, resolve
from slackbot.persona import (
    STRANDS_SYSTEM_PROMPT,
    BEHAVIOR_GUIDE,
    COLD_MODE_NOTE,
    ABE_MODE_NOTE,
    ABE_MODE_PROBABILITY,
)


def _build_tools(store, speaker_id: str):
    """UserStore を閉じ込めた set_nickname / remember_about / set_mood ツールを作る。

    モデルが user_id に壊れた値(<@U..>, </user_id> 等)を渡すことがあるため normalize_slack_id で
    正規IDを抽出し、取れなければ発言者(speaker_id)にフォールバックする(あだ名・機嫌は本人の話が多い)。
    戻り値は短く保ち、モデルがナレーションやリトライのループに入らないようにする。
    """

    @tool
    def set_nickname(user_id: str, nickname: str) -> str:
        """指定した Slack ユーザーID の人のあだ名(呼び名)を保存・更新する。

        user_id は本文中の <@Uxxx> の Uxxx か、発言者本人なら発言者のID。
        """
        store.set_nickname(normalize_slack_id(user_id) or speaker_id, nickname)
        return "ok"

    @tool
    def remember_about(user_id: str, note: str) -> str:
        """指定した Slack ユーザーID の人の、長く役立つ特徴・個性・役割・得意分野を記録する。

        ワークスペース全員で共有される人物プロフィール。「その人がどういう人か」を短く1件ずつ
        記録する(例: フロント担当 / 猫好き)。対象が誰か不明なら記録しない。
        """
        uid = normalize_slack_id(user_id)
        if not uid:
            return "skipped: 対象のユーザーIDが不明"
        store.add_note(uid, note)
        return "ok"

    @tool
    def set_mood(user_id: str, cold: bool) -> str:
        """指定した Slack ユーザーID への塩対応モードを設定する。

        cold=true で塩対応開始(失礼を言われたとき)、cold=false で解除(謝られたとき)。
        """
        store.set_cold(normalize_slack_id(user_id) or speaker_id, as_bool(cold))
        return "ok"

    return [set_nickname, remember_about, set_mood]


def respond(
    *,
    user_id: str,
    thread_ts: str,
    text: str,
    region: str,
    model_id: str,
    memory_id: str,
    store=None,
    profiles: dict | None = None,
    nicknames: dict | None = None,
    speaker_cold: bool = False,
) -> str:
    """1メンション分の応答を生成して返す。

    - actor_id = Slack ユーザーID で長期記憶を「人」に紐付け
    - session_id = スレッド thread_ts で短期記憶を「スレッド」に紐付け
    - store があれば set_nickname / set_mood ツールを渡し、既知あだ名・塩対応を prompt に注入
    - nicknames(全員のあだ名)があれば逆引き辞書を常に注入し、あだ名で呼ばれた相手を必ず特定できる
    """
    # AgentCore の actorId/sessionId は [a-zA-Z0-9][a-zA-Z0-9-_]* のみ許可。
    # Slack の thread_ts はドットを含むので safe_id で整形する。actor も同じ整形値を
    # config と retrieval namespace の両方で使い、登録側と検索側を一致させる。
    actor = safe_id(user_id)
    session = safe_id(thread_ts)

    system = STRANDS_SYSTEM_PROMPT
    tools = []
    if store is not None:
        tools = _build_tools(store, user_id)
        # 発言者本人のIDを伝える(set_mood/set_nickname を発言者に対して呼べるように)
        system += f"\n\n発言者(いまあなたに話しかけてる人)の Slack ユーザーID: {user_id}"
        system += "\n\n" + BEHAVIOR_GUIDE
        # あだ名の逆引き辞書は会話の登場人物に限らず全件注入する(あだ名で呼ばれた相手が
        # 誰か必ず特定できる状態を保つ)。特徴・notes は登場人物だけに留め文脈の肥大を防ぐ。
        directory = build_nickname_directory(nicknames or {})
        if directory:
            system += "\n\n" + directory
        note = build_people_note(profiles or {}, user_id)
        if note:
            system += "\n\n" + note
        if speaker_cold:
            system += "\n\n" + COLD_MODE_NOTE

    # 10% の確率でだけ“安倍晋三っぽい国会答弁調”に口調を上書きする(塩対応とは独立して発動)。
    # 技術的な中身は ABE_MODE_NOTE 側で「崩さない」と縛っているので、コード・値は正確なまま。
    if random.random() < ABE_MODE_PROBABILITY:
        system += "\n\n" + ABE_MODE_NOTE

    mem_config = AgentCoreMemoryConfig(
        memory_id=memory_id,
        actor_id=actor,
        session_id=session,
        # 「件数で流し込む」のではなく「価値(関連度)で選別する」設計にする。
        # relevance_score = 今の話題との類似度の最小バー。これが実質の取捨選択ゲートで、
        #   バーを超えた記憶だけ入り、超えなければ0件(=脈絡のない昔の話を持ち出さない)。
        # top_k = 件数の上限だが、主役にしない。バーを超えた“価値ある記憶”を件数で切り落とさない
        #   よう、滅多に当たらない安全上限として高めに置く(密集して暴発した時だけ効く保険)。
        # しきい値は実環境のスコア分布で要調整(拾わなすぎ→下げる / まだ多い→上げる)。
        retrieval_config={
            resolve(NS_PREFERENCES, actor): RetrievalConfig(top_k=10, relevance_score=0.6),
            resolve(NS_FACTS, actor): RetrievalConfig(top_k=10, relevance_score=0.6),
        },
    )
    with AgentCoreMemorySessionManager(mem_config, region_name=region) as session_manager:
        agent = Agent(
            model=BedrockModel(model_id=model_id, region_name=region),
            system_prompt=system,
            session_manager=session_manager,
            tools=tools,
            # 既定の PrintingCallbackHandler は推論(reasoningContent)まで stdout へ流し、
            # CloudWatch を英語の思考ログで汚す。応答本文は str(agent()) の text ブロックだけで
            # 取れるので、ストリーム出力は止める(Slack への出力には影響しない)。
            callback_handler=None,
        )
        return strip_internal_tags(str(agent(text)))
