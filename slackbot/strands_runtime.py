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
    format_search_results,
    strip_internal_tags,
    normalize_slack_id,
    parse_speak_decision,
    as_bool,
)
from slackbot.namespaces import NS_PREFERENCES, NS_FACTS, resolve
from slackbot.persona import (
    STRANDS_SYSTEM_PROMPT,
    BEHAVIOR_GUIDE,
    COLD_MODE_NOTE,
    GROUP_REPLY_GUIDE,
    SPEAK_GATE_PROMPT,
    ABE_MODE_NOTE,
    ABE_MODE_PROBABILITY,
)


def should_speak(
    *,
    region: str,
    model_id: str,
    message: str,
    transcript: str = "",
    speaker_id: str = "",
    nickname_directory: str = "",
) -> bool:
    """複数人スレッド(または他人宛ての発言)で、きあらが今この最新発言に口を挟むべきか判定する。

    回答生成(respond)とは独立した軽い1回の呼び出し。原則 NO に倒した SPEAK_GATE_PROMPT を使い、
    記憶もツールも持たせない(=安く・保守的に)。判定だけなので結果は parse_speak_decision で bool 化。
    あだ名辞書を渡すと「名前で他人に呼びかけている」発言を他人宛てと見抜きやすくなる。
    呼び出し側は失敗時に黙る(fail-closed)よう False 扱いにすること。
    """
    system = SPEAK_GATE_PROMPT
    if nickname_directory:
        system += "\n\n" + nickname_directory
    if speaker_id:
        system += f"\n\n最新の発言をした人の Slack ユーザーID: {speaker_id}"

    parts = []
    if transcript:
        parts.append("これまでの流れ:\n" + transcript)
    parts.append("最新の発言:\n" + message)
    parts.append("きあらは今この最新の発言に口を挟むべき? YES か NO の1語だけで答えて。")
    user = "\n\n".join(parts)

    agent = Agent(
        model=BedrockModel(model_id=model_id, region_name=region),
        system_prompt=system,
        callback_handler=None,
    )
    return parse_speak_decision(str(agent(user)))


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


def _build_web_search_tool(api_key: str):
    """Tavily を叩く web_search ツールを作る(I/O)。

    最新性が要るときだけモデルが呼ぶ前提(発動条件は persona.BEHAVIOR_GUIDE で縛る)。生の検索
    ペイロードはそのまま返さず core.format_search_results で短い注記に整形し、context とコストを抑える。
    失敗時も短い日本語を返し、モデルがリトライ/ナレーションのループに入らないようにする。
    tavily-python は本番イメージにのみ入るため import は遅延(キーがある実行時だけ評価)。
    """
    from tavily import TavilyClient

    client = TavilyClient(api_key=api_key)

    @tool
    def web_search(query: str) -> str:
        """最新情報を Web 検索する。最新性・確実性が要るときだけ使う。

        query は調べたいことの短い検索クエリ。結果は要約済みの短いテキストで返る。
        """
        try:
            resp = client.search(query=query, max_results=5, search_depth="basic")
        except Exception:
            return "検索に失敗した(今は使えないかも)"
        results = resp.get("results") if isinstance(resp, dict) else None
        return format_search_results(results) or "それっぽい結果が見つからんかった"

    return web_search


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
    tavily_api_key: str | None = None,
    may_stay_silent: bool = False,
    thread_context: str | None = None,
) -> str:
    """1メンション分(またはスレッド内の自発応答1回分)の応答を生成して返す。

    - actor_id = Slack ユーザーID で長期記憶を「人」に紐付け
    - session_id = スレッド thread_ts で短期記憶を「スレッド」に紐付け
    - store があれば set_nickname / set_mood ツールを渡し、既知あだ名・塩対応を prompt に注入
    - nicknames(全員のあだ名)があれば逆引き辞書を常に注入し、あだ名で呼ばれた相手を必ず特定できる
    - tavily_api_key があれば web_search ツールを渡す(最新情報を検索できる。無ければ検索なしで動く)
    - may_stay_silent: グループスレッドへの自発参加。割り込むべきでない時は本文を出さず <skip/> を返す
    - thread_context: 直近スレッドのやり取り(自発参加時に空気を読むための文脈。AgentCore 短期記憶には
      自分が処理していない他者の発言が入らないため、ここで補う)
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

    # Web 検索は store の有無と独立(キーがある実行時だけ追加)。発動条件は BEHAVIOR_GUIDE で縛る。
    if tavily_api_key:
        tools.append(_build_web_search_tool(tavily_api_key))

    # グループスレッドへの自発参加(メンション無し)。割り込み判断のガイドと、空気を読むための
    # 直近やり取りを system prompt に足す。1対1や通常メンションでは付けない(may_stay_silent=False)。
    if may_stay_silent:
        system += "\n\n" + GROUP_REPLY_GUIDE
        if thread_context:
            system += "\n\n直近のやり取り:\n" + thread_context

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
