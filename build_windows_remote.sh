#!/usr/bin/env bash
# 触发 GitHub Actions 构建 Windows 便携版，并把产物下载到 dist/
#
# 用法:
#   ./build_windows_remote.sh [分支]       # 默认 main
#
# 依赖:
#   - gh CLI 已登录（gh auth login）或已设置 GH_TOKEN
#   - origin 指向 GitHub 仓库

set -euo pipefail

BRANCH="${1:-main}"
WORKFLOW="build-windows.yml"
ARTIFACT="VolCtrl-windows-portable"
OUT_DIR="${OUT_DIR:-dist}"

cd "$(dirname "$0")"

# --- 解析仓库 & 代理 ---
REMOTE_URL=$(git remote get-url origin)
REPO=$(printf '%s' "$REMOTE_URL" | sed -E 's#(git@|https://)github\.com[:/](.+?)(\.git)?$#\2#')
if [[ -z "$REPO" ]]; then
    echo "无法从 origin 解析 GitHub 仓库: $REMOTE_URL" >&2
    exit 1
fi

# 复用 git 配置的代理（如 https.proxy）
if [[ -z "${HTTPS_PROXY:-}" ]]; then
    proxy=$(git config --get https.proxy || true)
    if [[ -n "$proxy" ]]; then
        export HTTPS_PROXY="$proxy" HTTP_PROXY="$proxy"
    fi
fi

if ! gh auth status >/dev/null 2>&1; then
    echo "gh 未登录，请先执行: gh auth login" >&2
    exit 1
fi

# --- 触发构建 ---
prev_run=$(gh run list --repo "$REPO" --workflow "$WORKFLOW" \
    --branch "$BRANCH" --limit 1 --json databaseId --jq '.[0].databaseId // empty' 2>/dev/null || true)

echo "==> 触发构建: $REPO / $WORKFLOW (分支 $BRANCH)"
gh workflow run "$WORKFLOW" --repo "$REPO" --ref "$BRANCH"

echo "==> 等待任务启动..."
run_id=""
for _ in $(seq 1 30); do
    sleep 3
    run_id=$(gh run list --repo "$REPO" --workflow "$WORKFLOW" \
        --branch "$BRANCH" --limit 1 --json databaseId --jq '.[0].databaseId // empty' 2>/dev/null || true)
    if [[ -n "$run_id" && "$run_id" != "$prev_run" ]]; then
        break
    fi
    run_id=""
done
if [[ -z "$run_id" ]]; then
    echo "未找到刚触发的构建任务，请到 Actions 页面查看" >&2
    exit 1
fi

# --- 等待完成 ---
echo "==> 任务 $run_id 构建中（实时日志）"
gh run watch "$run_id" --repo "$REPO" --exit-status

# --- 下载产物 ---
echo "==> 下载产物..."
gh run download "$run_id" --repo "$REPO" -n "$ARTIFACT" -D "$OUT_DIR"

zip_path=$(find "$OUT_DIR" -name 'VolCtrl-portable.zip' -print -quit)
if [[ -z "$zip_path" ]]; then
    echo "未找到产物 VolCtrl-portable.zip" >&2
    exit 1
fi

echo "✅ 构建成功: $zip_path"
