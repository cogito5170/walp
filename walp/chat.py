"""WALP 터미널 대화 — 디스코드 WALP 전용 서버와 같은 길을 터미널에서.

사용자(2026-09-30): "git 이 안 되니깐 배포 형태로 다운받아서 사용해볼게. 터미널에서 작업할거고."

    python3 -m walp.chat            # 저장소 뿌리에서. 한 줄에 한 말, `끝` / Ctrl-D 로 나간다
    echo "파란 비자카드 찾아줘" | python3 -m walp.chat     # 파이프도 된다

한 줄마다 `!walp` 를 안 붙여도 붙인 것으로 받는다(WALP 전용 서버와 같다). **LLM 은 부르지 않는다.**
내 컴퓨터이므로 쓰기(가르치기 · 확인)도 된다. 쓴 말은 사용성 원장(walp/usability/ledger.jsonl)에
via=terminal 로 적힌다 — 디스코드와 섞이지 않게.
"""
from __future__ import annotations

import sys

NAME = "terminal"


def 한마디(글: str, who: str = NAME, via: str = NAME, allow_write: bool = True) -> str:
    from walp import discord_cmd
    글 = (글 or "").strip()
    if not 글:
        return ""
    본문 = 글 if (글 == "!walp" or 글.startswith("!walp ")) else "!walp " + 글
    return discord_cmd.run(본문, None, allow_write, who=who, via=via) or ""


def main() -> int:
    try:
        import agent_context                      # 저장소 뿌리에서 돌 때만 있다
        agent_context.current_author.set(NAME)
    except Exception:                             # noqa: BLE001
        pass
    대화형 = sys.stdin.isatty()
    if 대화형:
        print("WALP 터미널 — LLM 없음. `도움` · `결과` · `끝`(또는 Ctrl-D)")
    while True:
        try:
            줄 = input("walp> " if 대화형 else "")
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if 줄.strip() in ("끝", "quit", "exit"):
            return 0
        답 = 한마디(줄)
        if 답:
            print(답 + "\n", flush=True)


if __name__ == "__main__":
    sys.exit(main())
