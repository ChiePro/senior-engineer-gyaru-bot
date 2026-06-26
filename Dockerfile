# Strands 版 (app_strands.py) 用のコンテナイメージ。
# strands-agents / bedrock-agentcore は依存が大きく zip 上限に当たりやすいため、
# コンテナイメージでデプロイする(README「Strands 版セットアップ」の推奨どおり)。
FROM public.ecr.aws/lambda/python:3.12

# 依存だけ先に入れてレイヤーキャッシュを効かせる
COPY requirements_strands.txt ./
RUN pip install --no-cache-dir -r requirements_strands.txt

# 本体パッケージを同梱(LAMBDA_TASK_ROOT 直下に slackbot/ が来る)
COPY slackbot ./slackbot

# Lambda ハンドラ。app_strands の handler を指す。
CMD ["slackbot.app_strands.handler"]
