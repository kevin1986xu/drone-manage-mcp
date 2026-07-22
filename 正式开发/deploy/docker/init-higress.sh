#!/usr/bin/env bash
# Higress 首配自动化（固化 docs/07 + README-higress 的控制台/API 操作）——幂等。
# 起完 docker-compose.full.yml 后跑一次：配服务来源 + 消费者鉴权 + 全局认证 + 限流。
#
# 依赖：宿主能访问 Higress 控制台（默认 localhost:8888）；docker exec uav-higress 可用。
# 读 .env.docker 取凭据。用法：./init-higress.sh
set -euo pipefail
cd "$(dirname "$0")"

ENV_FILE="${1:-.env.docker}"
[ -f "$ENV_FILE" ] || { echo "缺 $ENV_FILE（cp .env.docker.example 后填值）"; exit 1; }
set -a; . "./$ENV_FILE"; set +a

CONSOLE="http://localhost:${HIGRESS_CONSOLE_PORT:-8888}"
ADMIN_USER="${HIGRESS_ADMIN_USER:-admin}"
ADMIN_PASS="${HIGRESS_ADMIN_PASS:?请在 .env.docker 配 HIGRESS_ADMIN_PASS}"
NACOS_PW="${NACOS_PASSWORD:?}"
NACOS_USER="${NACOS_USERNAME:-nacos}"
# 网关消费者（其 key 必须同时在 UAV_TENANT_KEYS 里，后端才认得）
CK_NAME="${HIGRESS_CONSUMER_NAME:-tenant-demo}"
CK_KEY="${HIGRESS_CONSUMER_KEY:?请在 .env.docker 配 HIGRESS_CONSUMER_KEY（须在 UAV_TENANT_KEYS 中）}"
CK_QPM="${HIGRESS_CONSUMER_QPM:-120}"
CJ=$(mktemp)

echo "① 登录控制台 $CONSOLE"
code=$(curl -s -o /dev/null -w '%{http_code}' -c "$CJ" -X POST "$CONSOLE/session/login" \
  -H 'Content-Type: application/json' -d "{\"username\":\"$ADMIN_USER\",\"password\":\"$ADMIN_PASS\"}" || true)
if [ "$code" != "201" ] && [ "$code" != "200" ]; then
  echo "  登录失败（HTTP $code）。首次需先在浏览器打开 $CONSOLE 初始化管理员账号"
  echo "  （用户名 $ADMIN_USER、密码即 HIGRESS_ADMIN_PASS），再重跑本脚本。"
  exit 1
fi

echo "② 创建/更新服务来源 dronenacos（nacos3 → nacos:8848，开 MCP Server）"
curl -s -b "$CJ" -X POST "$CONSOLE/v1/service-sources" -H 'Content-Type: application/json' -d "{
  \"type\":\"nacos3\",\"name\":\"dronenacos\",\"domain\":\"nacos\",\"port\":8848,
  \"properties\":{\"nacosNamespaceId\":\"public\",\"nacosGroups\":[\"DEFAULT_GROUP\",\"mcp-endpoints\"],
    \"enableMCPServer\":true,\"mcpServerBaseUrl\":\"/mcp\"},
  \"authN\":{\"enabled\":true,\"properties\":{\"nacosUsername\":\"$NACOS_USER\",\"nacosPassword\":\"$NACOS_PW\"}}
}" >/dev/null && echo "  ✓ 服务来源已提交" || echo "  （已存在或需在控制台核对）"

echo "③ 创建消费者 $CK_NAME（Key Auth，Header X-API-Key）"
curl -s -b "$CJ" -X POST "$CONSOLE/v1/consumers" -H 'Content-Type: application/json' -d "{
  \"name\":\"$CK_NAME\",
  \"credentials\":[{\"type\":\"key-auth\",\"source\":\"HEADER\",\"key\":\"X-API-Key\",\"values\":[\"$CK_KEY\"]}]
}" >/dev/null && echo "  ✓ 消费者已提交" || echo "  （已存在）"

echo "④ 开启全局认证（key-auth global_auth=true，经容器内嵌 apiserver GET→改→PUT）"
docker exec uav-higress sh -c '
  set -e
  URL=https://localhost:18443/apis/extensions.higress.io/v1alpha1/namespaces/higress-system/wasmplugins/key-auth.internal
  curl -sk "$URL" > /tmp/ka.json
  python3 - <<PY
import json
d=json.load(open("/tmp/ka.json"))
d["spec"].setdefault("defaultConfig",{})["global_auth"]=True
json.dump(d,open("/tmp/ka2.json","w"))
PY
  curl -sk -X PUT "$URL" -H "Content-Type: application/json" -d @/tmp/ka2.json >/dev/null
' && echo "  ✓ global_auth=true" || echo "  ⚠ apiserver 写入失败，检查容器就绪"

echo "⑤ 按消费者限流（key-rate-limit，$CK_KEY → ${CK_QPM}/min）"
curl -s -b "$CJ" -X PUT "$CONSOLE/v1/global/plugin-instances/key-rate-limit" \
  -H 'Content-Type: application/json' -d "{
  \"pluginName\":\"key-rate-limit\",\"pluginVersion\":\"1.0.0\",\"scope\":\"GLOBAL\",\"enabled\":true,
  \"rawConfigurations\":\"limit_by_header: X-API-Key\\nlimit_keys:\\n- key: $CK_KEY\\n  query_per_minute: $CK_QPM\\n\"
}" >/dev/null && echo "  ✓ 限流已配" || echo "  （检查控制台）"

rm -f "$CJ"
echo
echo "完成。验证："
echo "  curl -s -o /dev/null -w '%{http_code}\\n' -X POST http://localhost:${HIGRESS_GATEWAY_HTTP_PORT:-8080}/mcp/uav-alert-mcp/mcp \\"
echo "    -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \\"
echo "    -H 'X-API-Key: $CK_KEY' -d '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/list\"}'   # 期望 200"
