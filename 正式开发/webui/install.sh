#!/usr/bin/env bash
# 把 webui HITL 补丁装进 deerflow/frontend（幂等）。
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DF="$HERE/../deerflow"
FE="$DF/frontend"

[ -d "$FE/src" ] || { echo "未找到 $FE/src——先克隆 DeerFlow 到 正式开发/deerflow"; exit 1; }

ln -sf "$HERE/uav-confirm-card.tsx" "$FE/src/components/workspace/messages/uav-confirm-card.tsx"
mkdir -p "$FE/src/app/api/uav/approval"
# route 文件必须实拷贝：App Router 路由扫描不收指向项目根之外的软链
# （组件走 webpack 模块解析，软链可用）。静态路由 + body 传参,勿改回动态段（见 route 文件头注释）
cp "$HERE/approval-proxy-route.ts" "$FE/src/app/api/uav/approval/route.ts"
echo "✓ 组件软链 + 路由拷贝就绪"

cd "$DF"
if git apply --check "$HERE/deerflow-webui-hitl.patch" 2>/dev/null; then
  git apply "$HERE/deerflow-webui-hitl.patch"
  echo "✓ 补丁已应用"
elif git apply --reverse --check "$HERE/deerflow-webui-hitl.patch" 2>/dev/null; then
  echo "✓ 补丁此前已应用,跳过"
else
  echo "✗ 补丁与当前 DeerFlow 版本冲突,请手动合并 $HERE/deerflow-webui-hitl.patch"; exit 1
fi
