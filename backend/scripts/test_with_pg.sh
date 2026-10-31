#!/bin/sh
# 用一次性 PostgreSQL 容器跑后端全量测试（含 db_session 集成测试），跑完自动删除容器。
# 用法（在 backend/ 下）：sh scripts/test_with_pg.sh [pytest 参数...]
# 不连接、不修改任何业务库；容器数据只在内存里。
set -eu
NAME="kaikou-test-pg-$$"
PORT="${TEST_PG_PORT:-15499}"
PASS="test-only-$(date +%s)"
docker run -d --rm --name "$NAME" -e POSTGRES_USER=printfilm -e POSTGRES_PASSWORD="$PASS" -e POSTGRES_DB=printfilm \
  -p "127.0.0.1:$PORT:5432" --tmpfs /var/lib/postgresql/data postgres:16-alpine -c max_connections=300 >/dev/null
trap 'docker rm -f "$NAME" >/dev/null 2>&1 || true' EXIT INT TERM
i=0
until docker exec "$NAME" pg_isready -U printfilm >/dev/null 2>&1; do
  i=$((i + 1)); [ "$i" -gt 30 ] && { echo "测试库启动超时" >&2; exit 1; }; sleep 1
done
export DATABASE_URL="postgresql+asyncpg://printfilm:$PASS@127.0.0.1:$PORT/printfilm"
export DATABASE_URL_SYNC="postgresql+psycopg2://printfilm:$PASS@127.0.0.1:$PORT/printfilm"
PY="${PYTHON:-.venv/bin/python}"
"$PY" -m pytest "$@"
