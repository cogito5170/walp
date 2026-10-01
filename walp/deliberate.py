"""숙고층(Deliberative) — WALP 가 "모른다" 고 판정한 말만 LLM 에게 묻는다(사전등록 walp/eval/PREREG_LLM앞단.md).

LLM 은 이 층에만 있다. 받은 말의 **부류**(12 행위 가운데 하나 또는 "other")와 **답**을 JSON 으로 받는다 —
부류는 아래 층(L0 · 센서)이 배우고, 답은 답 캐시가 다시 쓴다. 부를 때마다 토큰을 센다(줄인 양을 재려고).

    Claude숙고()   Claude API(claude-opus-5-5). 키는 환경에서(ANTHROPIC_API_KEY 등) — 없으면 기본숙고() 가 None
    신탁숙고(표)   검사 · 오프라인 실험용 — 정답 표를 돌려주고 부른 횟수 · 토큰(어림)을 센다
"""
from __future__ import annotations

import json
import os

MODEL = "claude-opus-5-5"
EFFORT = "low"            # 부류 + 짧은 답 — 분류 · 짧은 질의응답 길은 low 로 충분하다(효과는 실사용 원장으로 다시 잰다)
부류들 = ["task", "greet", "bye", "thanks", "about_self", "capability", "help", "knowledge", "complaint",
        "out_of_scope", "yes", "no", "other"]

SYSTEM = (
    "You are the deliberative layer behind a tiny Korean assistant called WALP. WALP forwards to you only the messages "
    "it could not handle itself. WALP can itself: find objects (cups, cards, keys, boxes; colors; places) in a simulated "
    "room, and do small talk. For the user's message, return JSON with two fields:\n"
    "- act: which of these the message is: task (asks to find an object), greet, bye, thanks, about_self (about the "
    "assistant itself), capability, help (how to use it), knowledge (a factual question), complaint, out_of_scope (a "
    "request for something other than finding objects or answering facts), yes, no, or other.\n"
    "- answer: the reply to show the user, in the user's language, at most 2 short sentences. For a factual question, "
    "answer it directly. If you are not sure of a fact, say so instead of guessing."
)
SCHEMA = {
    "type": "object",
    "properties": {"act": {"type": "string", "enum": 부류들}, "answer": {"type": "string"}},
    "required": ["act", "answer"],
    "additionalProperties": False,
}


class Claude숙고:
    """Claude API 한 번 = 한 숙고. 거절(refusal)이면 None — 아래 층이 사람에게 되묻는다."""

    def __init__(self, model: str = MODEL, effort: str = EFFORT):
        import anthropic
        ws = os.environ.get("ANTHROPIC_WORKSPACE_ID") or ""
        self.client = anthropic.Anthropic(default_headers={"anthropic-workspace-id": ws} if ws else None)
        self.model, self.effort = model, effort
        self.통계 = {"호출": 0, "입력토큰": 0, "출력토큰": 0, "거절": 0, "오류": 0}

    def 묻기(self, text: str) -> "dict | None":
        import anthropic
        self.통계["호출"] += 1
        try:
            r = self.client.beta.messages.create(
                model=self.model, max_tokens=2000,
                # 거절되면 서버가 알맞은 모형으로 다시 돌린다(Opus 5.5 기본 권장 — 끄려면 이 두 줄을 뺀다)
                betas=["server-side-fallback-2026-07-01"], fallbacks="default",
                system=SYSTEM,
                messages=[{"role": "user", "content": text}],
                output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": SCHEMA}},
            )
        except anthropic.APIError as e:              # 망 · 한도 · 서버 — 숙고 없이 아래 층으로 돌아간다
            self.통계["오류"] += 1
            return {"오류": type(e).__name__}
        u = r.usage
        self.통계["입력토큰"] += u.input_tokens or 0
        self.통계["출력토큰"] += u.output_tokens or 0
        if r.stop_reason == "refusal":
            self.통계["거절"] += 1
            return None
        글 = next((b.text for b in r.content if b.type == "text"), "")
        try:
            d = json.loads(글)
        except json.JSONDecodeError:
            self.통계["오류"] += 1
            return {"오류": "JSON"}
        return {"행위": d["act"], "답": d["answer"], "입력토큰": u.input_tokens or 0, "출력토큰": u.output_tokens or 0,
                "모형": r.model}


class 신탁숙고:
    """정답 표 { 말: (행위, 답) } 를 돌려준다. 토큰은 글자 수로 어림한다(시스템 프롬프트 몫 포함) — 실제 수가 아니다."""

    def __init__(self, 표: dict):
        self.표 = 표
        self.통계 = {"호출": 0, "입력토큰": 0, "출력토큰": 0, "거절": 0, "오류": 0}

    def 묻기(self, text: str) -> "dict | None":
        self.통계["호출"] += 1
        act, 답 = self.표.get(text, ("other", "모르겠습니다."))
        i, o = len(SYSTEM) // 4 + len(text) // 2 + 1, len(답) // 2 + 8
        self.통계["입력토큰"] += i
        self.통계["출력토큰"] += o
        return {"행위": act, "답": 답, "입력토큰": i, "출력토큰": o, "모형": "신탁"}


def 기본숙고() -> "Claude숙고 | None":
    """쓸 수 있으면 Claude숙고, 아니면 None(예전처럼 사람에게 되묻는다). WALP_LLM=0 이면 끈다. 검사(SE_LEDGER_ROOT)에서는 기본으로 끈다."""
    기본 = "0" if os.environ.get("SE_LEDGER_ROOT") else "1"
    if os.environ.get("WALP_LLM", 기본) == "0":
        return None
    from pathlib import Path
    프로필 = Path.home() / ".config" / "anthropic"            # `ant auth login` 이 남기는 자리 — 환경 변수가 없어도 SDK 가 읽는다
    if not any(os.environ.get(k) for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE")) \
            and not 프로필.is_dir():
        return None
    try:
        return Claude숙고()
    except ImportError:
        return None
