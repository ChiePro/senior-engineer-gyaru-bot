"""senior-engineer-gyaru-bot: ECS Fargate + Slack Socket Mode + Bedrock の本体パッケージ。

純粋ロジック (core / namespaces / persona) と配線・I/O (socket_app / strands_runtime /
user_store) を分離する。重い依存 (boto3 / slack / strands) は配線側でのみ import するため、
ロジックとペルソナはそれらをインストールせずに単体テストできる。
"""
