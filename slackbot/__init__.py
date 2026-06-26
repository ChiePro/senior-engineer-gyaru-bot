"""senior-engineer-gyaru-bot: Slack Bot on Lambda + Bedrock の本体パッケージ。

純粋ロジック (core / namespaces / persona) と配線 (app / app_strands) を分離する。
重い依存 (boto3 / slack / strands) は app 側でのみ import するため、
ロジックとペルソナはそれらをインストールせずに単体テストできる。
"""
