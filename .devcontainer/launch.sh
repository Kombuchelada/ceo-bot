#!/usr/bin/env bash
# Build & enter the dev container without VS Code.
# Usage: ./.devcontainer/launch.sh [shell|claude]
#   shell  (default) drops you into zsh
#   claude runs Claude Code with --dangerously-skip-permissions
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE="ceo-bot-dev:latest"
NAME="ceo-bot-dev"

cd "$ROOT"

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo ">> building $IMAGE"
  docker build -t "$IMAGE" -f .devcontainer/Dockerfile .devcontainer
fi

# Named volumes preserve Claude auth / shell history across container restarts
docker volume create ceo-bot-claude >/dev/null
docker volume create ceo-bot-config >/dev/null
docker volume create ceo-bot-cache  >/dev/null

# Forward host env if present (handy for non-interactive Anthropic key)
ENV_ARGS=()
[ -n "${ANTHROPIC_API_KEY:-}" ] && ENV_ARGS+=(-e "ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY")

CMD="${1:-shell}"
case "$CMD" in
  shell)  RUN_CMD=(zsh) ;;
  claude) RUN_CMD=(claude --dangerously-skip-permissions) ;;
  *)      RUN_CMD=("$@") ;;
esac

exec docker run --rm -it \
  --name "$NAME" \
  --hostname ceo-bot-dev \
  -v "$ROOT":/workspace \
  -v ceo-bot-claude:/home/dev/.claude \
  -v ceo-bot-config:/home/dev/.config \
  -v ceo-bot-cache:/home/dev/.cache \
  -w /workspace \
  -u dev \
  "${ENV_ARGS[@]}" \
  "$IMAGE" \
  "${RUN_CMD[@]}"
