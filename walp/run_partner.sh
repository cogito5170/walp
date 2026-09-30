#!/usr/bin/env bash
# WALP 대화 상대(Gemini)를 배경에서 몇 시간이고 돌린다 — 터미널을 닫아도 산다.
#   bash walp/run_partner.sh            # 24시간
#   bash walp/run_partner.sh 6 --rpm 5  # 6시간, 나머지 인자는 walp.partner 로
# 셸 변수는 아스키만 쓴다(한글 변수명은 bash 가 명령어로 읽는다 — CLAUDE.md).
set -euo pipefail
HOURS="${1:-24}"
shift || true
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE="${WALP_STATE_DIR:-$HOME/.local/state/walp}"
mkdir -p "$STATE"
LOG="$STATE/partner.log"
cd "$ROOT"
export PYTHONUNBUFFERED=1
if command -v setsid >/dev/null 2>&1; then
  setsid nohup python3 -m walp.partner --hours "$HOURS" "$@" > "$LOG" 2>&1 < /dev/null &
else
  nohup python3 -m walp.partner --hours "$HOURS" "$@" > "$LOG" 2>&1 < /dev/null &   # macOS 에는 setsid 가 없다
fi
disown || true
sleep 8
if grep -q "Traceback\|SystemExit\|GEMINI_API_KEY 가 없다" "$LOG" 2>/dev/null; then
  echo "failed to start — last lines of $LOG:"
  tail -20 "$LOG"
  exit 1
fi
# setsid 는 새 PID 로 갈라지므로 $! 를 믿지 않는다 — 아스키 패턴으로 찾는다(CLAUDE.md)
PID="$(pgrep -f "walp.partner --hours $HOURS" | head -1 || true)"
if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
  echo "started PID=$PID"
  echo "log: $LOG   (tail -f \"$LOG\")"
  echo "stop: kill $PID"
elif grep -q "partner\]" "$LOG" 2>/dev/null; then
  echo "finished already (short run) — log: $LOG"
  tail -5 "$LOG"
else
  echo "failed to start — last lines of $LOG:"
  tail -20 "$LOG" || true
  exit 1
fi
