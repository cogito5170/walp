"""WALP 사용자 친화성 — **저자(에이전트)가 끼지 않은** 상태에서 잰다.

사용자(2026-09-29): "성능 평가는 '사용자 친화성'을 제외한 나머지는 SE 내부에서 자체적으로
처리하고, 사용자 친화성을 (너의 개입 없는 상황)에서 평가한다."

그래서 이 모듈은 **말을 판정하지 않는다.** 사람이 `!walp`(디스코드 고정 명령) 또는 MCP
도구로 WALP 를 직접 부를 때마다 무슨 일이 났는지를 원장에 한 줄 적고, 나중에 그 줄들을
**센다.** 해석도 LLM 을 거치지 않는다(고정 명령은 에이전트 앞에서 가로챈다) — 에이전트가
사람의 말을 다듬어 넘기면 파서가 아니라 에이전트의 친화성을 재게 된다.

원장 한 줄(JSON):
    ts · who(해시) · via(discord|mcp) · kind(run|interpret|teach|rate|sus) · text
    status · interp · reason · token · outcome · ms
    kind=reaction|reaction_remove|edit 은 walp/sensors.py 가 적는다(반응 이모지 · 메시지 고침)

재는 것(전부 원장에서):
    첫 시도 성공률     세션의 첫 명령이 바로 받아들여졌나
    받아들여질 때까지  세션에서 처음 받아들여진 명령까지 몇 번 고쳐 말했나(중앙값)
    되묻기 회복률      AMBIGUOUS 다음 명령이 받아들여졌나
    포기율             거부로 끝난 세션(받아들여진 명령이 끝내 없음)
    모르는 낱말 상위    사람이 실제로 쓰는데 사전에 없는 말 — 사전을 키울 자리
    임무 성공률        run 이 받아들여진 뒤 시뮬 임무가 성공했나
    가르치기           teach 가 추가·충돌·중복된 수
    만족도 1~5 · SUS   사람이 직접 준 점수(없으면 없다고 적는다)

세션: 같은 사람의 줄이 30분 넘게 끊기면 새 세션.
"""
from __future__ import annotations

import hashlib
import json
import os
import statistics
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SESSION_GAP_S = 30 * 60


def 원장경로() -> Path:
    """SE_LEDGER_ROOT 가 서 있으면 거기(검사는 여기로 돌린다) — 아니면 walp/usability/."""
    뿌리 = os.environ.get("SE_LEDGER_ROOT")
    if 뿌리:
        return Path(뿌리) / "walp_usability.jsonl"
    return HERE / "usability" / "ledger.jsonl"


def 상태자리() -> Path:
    """비밀이 든 상태(소금 · 확인 대기 표)를 두는 곳 — **저장소 밖**. read_file 은 저장소 안만 읽으므로 여기는 못 닿는다.
    첫 판은 walp/usability/ 에 두어, 공개 채널에서 `!walp 도구 walp/usability/pending.json 파일 보여줘` 로
    남의 확인 대기 set_key 값이 읽혔다(실측 2026-09-29). SE_LEDGER_ROOT 가 서 있으면(검사) 그 임시 자리."""
    뿌리 = os.environ.get("SE_LEDGER_ROOT")
    if 뿌리:
        return Path(뿌리)
    d = Path(os.environ.get("WALP_STATE_DIR") or (Path.home() / ".local" / "state" / "walp"))
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d


def _소금경로() -> Path:
    return 상태자리() / ".salt"


def _소금() -> bytes:
    """호출자 해시의 비밀 소금. WALP_HASH_SALT 가 있으면 그것, 없으면 원장 옆 `.salt`(처음 한 번 만들고 0600).
    소금이 없으면 디스코드 ID 를 아는 사람이 해시를 대조할 수 있다 — 첫 판이 그랬다(가명이었지 익명이 아니었다).
    소금 파일은 저장소 밖(상태자리)에 둔다 — 저장소 안이면 read_file 로 읽혀 소금이 소용없다."""
    env = os.environ.get("WALP_HASH_SALT")
    if env:
        return env.encode()
    p = _소금경로()
    try:
        return p.read_bytes()
    except OSError:
        pass
    import secrets
    p.parent.mkdir(parents=True, exist_ok=True)
    v = secrets.token_hex(16).encode()
    try:
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(v)
    except FileExistsError:
        return p.read_bytes()
    return v


def 누구(원래: str) -> str:
    """디스코드 ID 를 그대로 적지 않는다 — 세션을 가를 만큼만. **가명**이다(같은 사람은 같은 값): 소금이 비밀인
    동안만 ID 로 되짚을 수 없다. 2026-09-29 이전 원장 줄은 소금 없이 적혔다 — 그 줄과 새 줄은 이어지지 않는다."""
    return hashlib.sha256(_소금() + b"walp:" + (원래 or "anon").encode()).hexdigest()[:12]


def 적기(줄: dict) -> None:
    p = 원장경로()
    p.parent.mkdir(parents=True, exist_ok=True)
    줄 = {"ts": round(time.time(), 3), **줄}
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(줄, ensure_ascii=False) + "\n")


def 읽기(path: "Path | None" = None) -> list[dict]:
    p = path or 원장경로()
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue   # 깨진 줄은 세지 않는다(세면 거짓 수가 된다)
    return out


def _세션들(줄들: list[dict]) -> list[list[dict]]:
    by: dict[str, list[dict]] = {}
    for z in sorted(줄들, key=lambda z: z.get("ts", 0)):
        by.setdefault(z.get("who", "?"), []).append(z)
    out = []
    for zs in by.values():
        cur: list[dict] = []
        for z in zs:
            if cur and z.get("ts", 0) - cur[-1].get("ts", 0) > SESSION_GAP_S:
                out.append(cur)
                cur = []
            cur.append(z)
        if cur:
            out.append(cur)
    return out


def sus_score(ans: list[int]) -> float:
    """SUS(Brooke 1996): 홀수 문항은 (점수-1), 짝수 문항은 (5-점수), 합×2.5 → 0..100."""
    if len(ans) != 10 or any(a < 1 or a > 5 for a in ans):
        raise ValueError("SUS 는 1..5 점 열 개")
    s = sum((a - 1) if i % 2 == 0 else (5 - a) for i, a in enumerate(ans))
    return s * 2.5


def 집계(줄들: list[dict]) -> dict:
    명령 = [z for z in 줄들 if z.get("kind") in ("run", "interpret")]
    세션 = _세션들(명령)
    첫성공 = sum(1 for s in 세션 if s and s[0].get("status") == "ok")
    까지 = []
    포기 = 0
    for s in 세션:
        idx = next((i for i, z in enumerate(s) if z.get("status") == "ok"), None)
        if idx is None:
            포기 += 1
        else:
            까지.append(idx)
    되묻기 = 회복 = 0
    for s in 세션:
        for a, b in zip(s, s[1:]):
            if a.get("status") == "ambiguous":
                되묻기 += 1
                회복 += b.get("status") == "ok"
    모르는: dict[str, int] = {}
    사유: dict[str, int] = {}
    해석: dict[str, int] = {}
    for z in 명령:
        if z.get("status") != "ok":
            사유[z.get("reason", "?")] = 사유.get(z.get("reason", "?"), 0) + 1
            if z.get("reason") == "unknown_word" and z.get("token"):
                모르는[z["token"]] = 모르는.get(z["token"], 0) + 1
        if z.get("interp"):
            해석[z["interp"]] = 해석.get(z["interp"], 0) + 1
    runs = [z for z in 줄들 if z.get("kind") == "run" and z.get("status") == "ok"]
    임무성공 = sum(1 for z in runs if z.get("outcome") == "success")
    가르침 = [z for z in 줄들 if z.get("kind") == "teach"]
    점수 = [z["rating"] for z in 줄들 if z.get("kind") == "rate" and isinstance(z.get("rating"), int)]
    sus = [z["sus"] for z in 줄들 if z.get("kind") == "sus" and isinstance(z.get("sus"), (int, float))]
    ms = [z["ms"] for z in 명령 if isinstance(z.get("ms"), (int, float))]
    사람 = {z.get("who") for z in 줄들}
    return {
        "사람": len(사람),
        "세션": len(세션),
        "명령": len(명령),
        "첫시도_성공": [첫성공, len(세션)],
        "받아들여질때까지_고쳐말한횟수_중앙값": statistics.median(까지) if 까지 else None,
        "포기한_세션": [포기, len(세션)],
        "되묻기_뒤_회복": [회복, 되묻기],
        "거부_사유": dict(sorted(사유.items(), key=lambda kv: -kv[1])),
        "해석_상태": 해석,
        "모르는_낱말_상위": sorted(모르는.items(), key=lambda kv: -kv[1])[:15],
        "임무_성공": [임무성공, len(runs)],
        "가르치기": {r: sum(1 for z in 가르침 if z.get("result") == r) for r in ("added", "duplicate", "conflict", "invalid")},
        "만족도_1to5": {"n": len(점수), "평균": round(statistics.mean(점수), 2) if 점수 else None},
        "SUS": {"n": len(sus), "평균": round(statistics.mean(sus), 1) if sus else None},
        "응답_ms_중앙값": statistics.median(ms) if ms else None,
        "반응": _반응집계(줄들),
        "고침": {"n": sum(1 for z in 줄들 if z.get("kind") == "edit"),
                 "새글도_walp": sum(1 for z in 줄들 if z.get("kind") == "edit" and z.get("walp"))},
    }


def _반응집계(줄들: list[dict]) -> dict:
    """반응은 극성 표(손으로 적음)로 셀 뿐 판정하지 않는다 — 0 은 '모르는 이모지' 이지 '중립' 이 아니다."""
    달기 = [z for z in 줄들 if z.get("kind") == "reaction"]
    이모지: dict[str, int] = {}
    for z in 달기:
        이모지[z.get("emoji", "?")] = 이모지.get(z.get("emoji", "?"), 0) + 1
    return {"n": len(달기), "뺌": sum(1 for z in 줄들 if z.get("kind") == "reaction_remove"),
            "본인": sum(1 for z in 달기 if z.get("본인")),
            "+": sum(1 for z in 달기 if z.get("극성") == 1), "-": sum(1 for z in 달기 if z.get("극성") == -1),
            "모름": sum(1 for z in 달기 if z.get("극성") == 0),
            "상위": sorted(이모지.items(), key=lambda kv: -kv[1])[:8]}


def 보고(요약: dict) -> str:
    """사람이 읽는 몇 줄. 표본이 적으면 적다고 먼저 말한다."""
    def frac(a):
        k, n = a
        return f"{k}/{n}" + (f" ({100 * k / n:.0f}%)" if n else "")
    줄 = []
    if 요약["세션"] < 10:
        줄.append(f"⚠ 표본이 작다 — 세션 {요약['세션']}개. 아래 비율은 경향도 못 된다.")
    줄 += [
        f"사람 {요약['사람']} · 세션 {요약['세션']} · 명령 {요약['명령']}",
        f"첫 시도에 받아들여짐: {frac(요약['첫시도_성공'])}",
        f"받아들여질 때까지 고쳐 말한 횟수(중앙값): {요약['받아들여질때까지_고쳐말한횟수_중앙값']}",
        f"끝내 못 알아들은 세션: {frac(요약['포기한_세션'])}",
        f"되물은 뒤 다음 말로 풀림: {frac(요약['되묻기_뒤_회복'])}",
        f"시뮬 임무 성공(받아들여진 run 중): {frac(요약['임무_성공'])}",
        f"만족도: {요약['만족도_1to5']} · SUS: {요약['SUS']}",
    ]
    반 = 요약.get("반응") or {}
    if 반.get("n") or 반.get("뺌") or (요약.get("고침") or {}).get("n"):
        줄.append(f"답에 단 반응 {반['n']}(본인 {반['본인']} · 👍류 {반['+']} · 👎류 {반['-']} · 표에 없는 것 {반['모름']} · 뺌 {반['뺌']})"
                  + (" — " + " ".join(f"{e}×{n}" for e, n in 반["상위"]) if 반.get("상위") else "")
                  + f" · 요청 고침 {요약['고침']['n']}")
    if 요약["모르는_낱말_상위"]:
        줄.append("사람이 썼는데 사전에 없던 말: " + ", ".join(f"{w}×{n}" for w, n in 요약["모르는_낱말_상위"]))
    if 요약["거부_사유"]:
        줄.append("거부 사유: " + ", ".join(f"{k} {v}" for k, v in list(요약["거부_사유"].items())[:8]))
    return "\n".join(줄)


if __name__ == "__main__":
    import sys
    요약 = 집계(읽기(Path(sys.argv[1]) if len(sys.argv) > 1 else None))
    if "--json" in sys.argv:
        print(json.dumps(요약, ensure_ascii=False, indent=1))
    else:
        print(보고(요약))
