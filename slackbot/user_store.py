"""
ユーザーごとの状態(あだ名・特徴・機嫌)を DynamoDB に持つストア。

キーは「対象ユーザーの Slack ID」。あだ名や特徴(個性・役割・得意分野など)は“発言者の好み”
ではなく“その人物に紐づき、ワークスペース全員で共有される情報”なので、AgentCore の actor
単位メモリではなくここで持つ(発言者と第三者の取り違えを防ぐ)。
機嫌(cold)は塩対応モードのフラグで、ツール set_mood から立て下げする。

I/O 層なので boto3 に依存する。app から注入し、純粋ロジック(core / persona)とは分離する。
テーブルは PK=user_id の単純な KVS(属性 nickname / cold / notes[list])。
"""

import boto3

# 特徴メモの肥大化を防ぐ上限(古い順に間引く運用は将来の拡張)
MAX_NOTES = 30


class UserStore:
    def __init__(self, table_name: str, region: str | None = None):
        self._table = boto3.resource("dynamodb", region_name=region).Table(table_name)

    def get(self, user_id: str) -> dict:
        """{'nickname': str|None, 'cold': bool, 'notes': list[str]} を返す。未登録なら空状態。"""
        item = self._table.get_item(Key={"user_id": user_id}).get("Item") or {}
        return {
            "nickname": item.get("nickname"),
            "cold": bool(item.get("cold", False)),
            "notes": list(item.get("notes") or []),
        }

    def set_nickname(self, user_id: str, nickname: str) -> None:
        self._table.update_item(
            Key={"user_id": user_id},
            UpdateExpression="SET nickname = :n",
            ExpressionAttributeValues={":n": nickname},
        )

    def set_cold(self, user_id: str, cold: bool) -> None:
        self._table.update_item(
            Key={"user_id": user_id},
            UpdateExpression="SET cold = :c",
            ExpressionAttributeValues={":c": bool(cold)},
        )

    def add_note(self, user_id: str, note: str) -> None:
        """その人物に関する横断的な特徴メモを1件追記する(重複はスキップ)。"""
        note = (note or "").strip()
        if not note or note in self.get(user_id)["notes"]:
            return
        self._table.update_item(
            Key={"user_id": user_id},
            UpdateExpression="SET notes = list_append(if_not_exists(notes, :empty), :n)",
            ExpressionAttributeValues={":empty": [], ":n": [note][:MAX_NOTES]},
        )

    def all_nicknames(self) -> dict:
        """あだ名が設定されている全ユーザーの {user_id: nickname} を返す。

        あだ名で呼ばれたとき誰のことか必ず照合できるよう、会話に未登場の人も含め全件を引く
        (build_nickname_directory が逆引き辞書に整形する)。テーブルはワークスペース規模(小)
        前提なので Scan で十分。nickname 属性が無い行はスキップする。
        """
        out = {}
        kwargs = {"ProjectionExpression": "user_id, nickname"}
        while True:
            resp = self._table.scan(**kwargs)
            for item in resp.get("Items", []):
                nn = item.get("nickname")
                if nn:
                    out[item["user_id"]] = nn
            lek = resp.get("LastEvaluatedKey")
            if not lek:
                return out
            kwargs["ExclusiveStartKey"] = lek

    def profiles_for(self, user_ids: list) -> dict:
        """指定ユーザー群のうち、あだ名か特徴がある人だけ {id: {nickname, notes}} で返す。"""
        out = {}
        for uid in dict.fromkeys(u for u in user_ids if u):
            st = self.get(uid)
            if st["nickname"] or st["notes"]:
                out[uid] = {"nickname": st["nickname"], "notes": st["notes"]}
        return out
