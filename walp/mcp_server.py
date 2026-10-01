"""WALP MCP 서버(stdio, JSON-RPC 2.0) — 선택한 MCP 층의 **바깥쪽**.

사람이든 다른 에이전트든 MCP 클라이언트로 WALP 를 부른다. 도구 아홉 개는 `front.py` 의 같은 함수를
부르고(디스코드 `!walp` 와 같은 길), `walp_publish` · `walp_wait` 은 실행 정책층(`execpolicy.py`)의 P2 · P3 를 부른다. 표준 라이브러리만 쓴다.

    python3 walp/mcp_server.py        # 클라이언트 설정에 이 줄을 command 로 넣는다

**안쪽(WALP 가 MCP 클라이언트로 환경 서버를 부르는 쪽)은 아직 짓지 않았다** — 설계는
`walp/docs/MCP.md`. 지금 환경은 코어와 같은 프로세스의 시뮬레이터다.

도구 결과는 사람이 읽는 글이다. 해석이 모호하면 실행하지 않고 후보를 돌려준다 — 부르는
쪽(LLM 이어도)이 고르게 두고, WALP 는 고르지 않는다.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from walp import front  # noqa: E402

PROTOCOL = "2025-06-18"
WHO = os.environ.get("WALP_MCP_USER", "mcp")

TOOLS = [
    {"name": "walp_interpret", "description": "명령 한 줄을 목표(GoalSpec)로 해석만 한다. 모르는 말·모호함은 추측하지 않고 이유를 돌려준다.",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "walp_run", "description": "명령을 해석하고 결정론적 시뮬레이터에서 물체 찾기 임무를 한 판 돌린다. 결과·안전 위반·결정 추적을 돌려준다.",
     "inputSchema": {"type": "object", "properties": {
         "text": {"type": "string"}, "seed": {"type": "integer"},
         "family": {"type": "string", "enum": ["office", "shop", "home", "shift"]}}, "required": ["text"]}},
    {"name": "walp_teach", "description": "모르는 낱말을 기존 개념에 잇는다. 문법어·기존 뜻과 충돌하면 넣지 않는다.",
     "inputSchema": {"type": "object", "properties": {
         "word": {"type": "string"}, "category": {"type": "string", "enum": ["color", "object", "brand", "zone", "modifier"]},
         "concept": {"type": "string"}}, "required": ["word", "category", "concept"]}},
    {"name": "walp_rate", "description": "써 본 느낌 1~5 와 한마디(사용성 평가 원장).",
     "inputSchema": {"type": "object", "properties": {"score": {"type": "integer", "minimum": 1, "maximum": 5},
                                                      "comment": {"type": "string"}}, "required": ["score"]}},
    {"name": "walp_sus", "description": "SUS 설문 10문항 점수(1~5). 빈 배열이면 문항을 돌려준다.",
     "inputSchema": {"type": "object", "properties": {"answers": {"type": "array", "items": {"type": "integer"}}},
                     "required": ["answers"]}},
    {"name": "walp_loop", "description": "LoopAct: 명령 하나를 끝에서 끝까지 점검하는 고리(해석 → 후보마다 시뮬 k 판). 최상위 행동(ANSWER/LOOP/REFUSE)·바퀴·호출 수·못 채운 것을 돌려준다. 모호하면 고르지 않고 후보별 결과를 나란히 둔다. 예산: 4바퀴·30호출.",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}, "k": {"type": "integer", "minimum": 1, "maximum": 5}},
                     "required": ["text"]}},
    {"name": "walp_se_tool", "description": "SE 에이전트의 도구(시뮬·EDA·SPICE·SerDes·교재·개념 등)를 LLM 없이 골라 LLM 을 막은 채 부른다. 확실하지 않으면 실행하지 않고 되묻는다. 절이 여럿이면 전부 확실할 때만 차례로.",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "walp_se_catalog", "description": "배선된 SE 도구 목록(부작용 분류·LLM 추정·목록 해시).",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "walp_usability_report", "description": "사용성 원장 집계(첫 시도 성공률·되묻기 회복·모르는 낱말 상위 등).",
     "inputSchema": {"type": "object", "properties": {}}},
    # 실행 정책층 P2 · P3 (execpolicy.py) -- LLM 이 **스스로 부를 때만** 돈다. 자동으로 켜지지 않는다
    {"name": "walp_publish", "description": "게시 한 번: 커밋(추적되는 파일의 변경 + files 로 이름을 댄 새 파일만) -> 깨끗한 워크트리 검사 -> (push=true) 밀기 -> (merge=true) PR · 머지 · 머지 조회. "
     "걸음마다 결정론적으로 확인하고 하나라도 안 되면 그 자리에서 멈춰 stopped_at 과 까닭을 돌려준다. 밀기 · 머지는 인자로 명시하고, "
     "서버를 WALP_MCP_ALLOW_PUBLISH=1 로 띄운 경우만 한다.",
     "inputSchema": {"type": "object", "properties": {"repo": {"type": "string"}, "message": {"type": "string"},
                     "test": {"type": "string"}, "push": {"type": "boolean"}, "merge": {"type": "boolean"},
                     "base": {"type": "string"}, "files": {"type": "array", "items": {"type": "string"},
                     "description": "담을 새 파일(추적되는 파일의 변경은 늘 담는다). 이름을 댄 새 파일만 담는다"}},
                     "required": ["repo", "message"]}},
    {"name": "walp_wait", "description": "백그라운드 작업을 PID 로 끝날 때까지 기다리고 한 번 돌려준다(폴링 턴 대신). "
     "status: ended · error(로그에 오류 흔적) · timeout · not_found. progress_every 초마다 진행을 남긴다(progressToken 이 있으면 알림으로도).",
     "inputSchema": {"type": "object", "properties": {"pid": {"type": "integer"}, "log": {"type": "string"},
                     "timeout": {"type": "number"}, "progress_every": {"type": "number"}}, "required": ["pid"]}},
]


def call(name: str, a: dict, notify=None) -> str:
    if name == "walp_interpret":
        return front.interpret(str(a["text"]), WHO, "mcp")
    if name == "walp_run":
        return front.run(str(a["text"]), WHO, "mcp", a.get("seed"), a.get("family", "office"))
    if name == "walp_teach":
        # 사전을 바꾸는 일 — 디스코드에서는 관리 채널로 막는다. MCP 는 호출자를 모르므로 **기본으로 닫고**,
        # 서버를 띄운 사람이 WALP_MCP_ALLOW_TEACH=1 로 연 경우만 받는다(HTTP 로 열어도 구멍이 안 되게)
        if os.environ.get("WALP_MCP_ALLOW_TEACH") != "1":
            return "가르치기는 이 MCP 서버에서 닫혀 있다 — 서버를 WALP_MCP_ALLOW_TEACH=1 로 띄운 경우만 받는다."
        return front.teach(str(a["word"]), str(a["category"]), str(a["concept"]), WHO, "mcp")
    if name == "walp_rate":
        return front.rate(int(a["score"]), str(a.get("comment", "")), WHO, "mcp")
    if name == "walp_sus":
        return front.sus([int(x) for x in a.get("answers", [])], WHO, "mcp")
    if name == "walp_loop":
        from walp.loop import walp_campaign
        # k 는 호출자가 정한다 — 상한이 없으면 한 번의 호출로 자원을 오래 붙잡는다. 1~5 로 자른다
        return walp_campaign(str(a["text"]), k=max(1, min(5, int(a.get("k", 3)))))
    if name == "walp_se_tool":
        return front.se_tool(str(a["text"]), WHO, "mcp")
    if name == "walp_se_catalog":
        return front.se_catalog()
    if name == "walp_usability_report":
        return front.report()
    if name == "walp_publish":
        from walp import execpolicy
        push, merge = bool(a.get("push")), bool(a.get("merge"))
        # 바깥 동작(밀기 · 머지) -- MCP 는 호출자를 모르므로 **기본으로 닫는다**(walp_teach 와 같은 규칙)
        if (push or merge) and os.environ.get("WALP_MCP_ALLOW_PUBLISH") != "1":
            return "⚠ 밀기 · 머지는 이 MCP 서버에서 닫혀 있다 — 서버를 WALP_MCP_ALLOW_PUBLISH=1 로 띄운 경우만 한다. (push · merge 없이 부르면 커밋 · 검사까지 한다)"
        r = execpolicy.publish(str(a["repo"]), str(a["message"]), a.get("test"), push, merge, str(a.get("base", "main")),
                               files=[str(f) for f in a.get("files") or []])
        return ("" if r.get("ok") else "⚠ ") + json.dumps(r, ensure_ascii=False)
    if name == "walp_wait":
        from walp import execpolicy
        r = execpolicy.wait(int(a["pid"]), a.get("log"), float(a.get("timeout", 3600)),
                            progress_every=float(a.get("progress_every", 0) or 0), on_progress=notify)
        return ("" if r["status"] == "ended" else "⚠ ") + json.dumps(r, ensure_ascii=False)
    raise KeyError(name)


def handle(msg: dict, send=None) -> "dict | None":
    """send: 진행 알림(notifications/progress)을 바로 내보낼 함수 -- 부른 쪽이 progressToken 을 줬을 때만 쓴다."""
    mid = msg.get("id")
    method = msg.get("method", "")
    if mid is None:            # 알림(notifications/initialized 등)에는 답하지 않는다
        return None
    try:
        if method == "initialize":
            res = {"protocolVersion": PROTOCOL, "capabilities": {"tools": {"listChanged": False}},
                   "serverInfo": {"name": "walp", "version": "0.2"}}
        elif method == "ping":
            res = {}
        elif method == "tools/list":
            res = {"tools": TOOLS}
        elif method == "tools/call":
            p = msg.get("params") or {}
            name = p.get("name", "")
            if name not in {t["name"] for t in TOOLS}:
                return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"모르는 도구: {name}"}}
            token = (p.get("_meta") or {}).get("progressToken")
            notify = None
            if token is not None and send:
                notify = lambda pr: send({"jsonrpc": "2.0", "method": "notifications/progress",  # noqa: E731
                                          "params": {"progressToken": token, "progress": pr.get("seconds", 0),
                                                     "message": json.dumps(pr, ensure_ascii=False)}})
            try:
                text = call(name, p.get("arguments") or {}, notify)
                res = {"content": [{"type": "text", "text": text}], "isError": text.startswith("⚠")}
            except (KeyError, ValueError, TypeError) as e:
                res = {"content": [{"type": "text", "text": f"인자 오류: {e}"}], "isError": True}
        else:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"모르는 메서드: {method}"}}
        return {"jsonrpc": "2.0", "id": mid, "result": res}
    except Exception as e:  # noqa: BLE001 — 서버는 죽지 않고 오류로 답한다
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": f"{type(e).__name__}: {e}"}}


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            out = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "JSON 이 아니다"}}
        else:
            out = handle(msg, _write)
        if out is not None:
            _write(out)


def _write(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
