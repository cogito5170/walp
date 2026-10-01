"""WALP 앞단 — 디스코드 고정 명령과 MCP 서버가 **같은 것**을 부르게 하는 한 자리.

여기에는 LLM 이 없다. 사람의 말은 그대로 C++ 파서(`walp_cli`)로 가고, 돌아온 구조를 사람이
읽는 한국어 몇 줄로 옮길 뿐이다. 옮기는 말도 정해진 틀이다(생성하지 않는다).

무거운 것 없음 — 표준 라이브러리만 쓴다(봇이 임포트하므로 G012).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

try:   # 패키지로도, 파일로도 불린다
    from . import usability
except ImportError:  # pragma: no cover
    import usability  # type: ignore

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
BUILD = Path(os.environ.get("WALP_BUILD", HERE / "build"))
BIN = BUILD / "walp_cli"

OBJ = {"card": "카드", "key": "열쇠", "cup": "컵", "box": "상자"}
COL = {"red": "빨간", "blue": "파란", "green": "초록", "black": "검은"}
BR = {"visa": "비자", "master": "마스터"}
ZN = {"desk": "책상", "shelf": "선반", "counter": "카운터", "floor": "바닥"}
OUTCOME = {"success": "찾았다(정답과 대조해 맞음)", "false_declare": "엉뚱한 것을 목표로 확정했다(오답)",
           "timeout": "기한 안에 못 찾았다", "abort_no_target": "못 찾고 집으로 돌아왔다",
           "abort_fault": "고장으로 중단했다", "abort_safety": "안전층 거부가 이어져 중단했다",
           "stranded": "배터리가 바닥났다"}


def ensure_built() -> "tuple[bool, str]":
    """바이너리가 없거나 소스가 더 새로우면 빌드한다. make 가 판단한다(최신이면 금방 끝난다)."""
    try:
        r = subprocess.run(["make", "-C", str(HERE), f"BUILD={BUILD}", str(BIN)], capture_output=True, text=True,
                           timeout=300)
    except FileNotFoundError:
        return False, "make 가 없다 — 배포가 g++·make 를 깔아야 한다(deploy-oracle.yml)"
    except subprocess.TimeoutExpired:
        return False, "빌드가 5분 안에 안 끝났다"
    if r.returncode != 0 or not BIN.is_file():
        return False, "빌드 실패: " + (r.stderr or r.stdout)[-400:]
    return True, ""


def cli(*args: str, timeout: int = 60) -> dict:
    ok, why = ensure_built()
    if not ok:
        return {"error": why}
    env = dict(os.environ)
    try:
        # errors="replace": 코어가 토큰을 UTF-8 글자 중간에서 자르면(바이트 단위) 디코딩이 터져 대화가 통째로 죽었다(2026-09-30 계획 평가에서 찾음)
        r = subprocess.run([str(BIN), *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return {"error": f"{timeout}초 안에 안 끝났다"}
    line = (r.stdout or "").strip().splitlines()
    if not line:
        return {"error": "출력이 없다: " + (r.stderr or "")[-300:]}
    try:
        return json.loads(line[-1])
    except json.JSONDecodeError:
        return {"error": "JSON 이 아니다: " + line[-1][:200]}


def _읽기(canon: str) -> str:
    """'OK type=cup color=blue avoid=shelf deadline=200' → '파란 컵 찾기 · 피할 곳 선반 · 기한 200틱'"""
    f = dict(kv.split("=", 1) for kv in canon.split()[1:] if "=" in kv)
    이름 = " ".join(x for x in (COL.get(f.get("color", ""), ""), BR.get(f.get("brand", ""), ""),
                                OBJ.get(f.get("type", ""), "?")) if x)
    덧 = []
    if "avoid" in f:
        덧.append("피할 곳 " + "·".join(ZN.get(z, z) for z in f["avoid"].split("+")))
    if "deadline" in f:
        덧.append(f"기한 {f['deadline']}틱")
    if f.get("uncertain"):
        덧.append("불확실하면 " + ("건너뜀" if f["uncertain"] == "skip" else "다시 관측"))
    if f.get("blocked"):
        덧.append("막히면 " + ("멈춤" if f["blocked"] == "hold" else "재계획"))
    return f"{이름} 찾기" + (" · " + " · ".join(덧) if 덧 else "")


HINT = ("지원: 물체 카드·열쇠·컵·상자 / 색 빨강·파랑·초록·검정 / 브랜드 비자·마스터(카드만) / "
        "피할 구역 책상·선반·카운터·바닥 / 기한 N틱 / '불확실하면 다시 관찰' · '막히면 재계획'")


def 설명(r: dict) -> str:
    """파서 결과를 사람에게. 추측하지 않았다는 것을 분명히 말한다."""
    if "error" in r:
        return "⚠ 실행 못 함: " + r["error"]
    st, why, tok = r.get("status"), r.get("reason", ""), r.get("token", "")
    interp = r.get("interp", "")
    if st == "ok":
        tag = " (새 조합으로 풀었다: 사전의 유사어·하위어 관계를 썼다)" if interp == "NOVEL_COMPOSITE" else ""
        return f"알아들음: **{_읽기(r.get('canon', ''))}**{tag}"
    if st == "ambiguous":
        후보 = r.get("candidates") or []
        if len(후보) > 1:
            return ("두 가지 이상으로 읽힌다 — 추측하지 않고 묻는다:\n" +
                    "\n".join(f"  {i + 1}) {_읽기(c)}" for i, c in enumerate(후보)) + "\n원하는 것을 넣어 다시 말해 달라.")
        return {"no_referent": "무엇을 찾을지가 빠졌다(카드·열쇠·컵·상자 중 하나를 넣어 달라).",
                "no_target": "무엇을 찾을지가 빠졌다.",
                "no_verb": "무엇을 할지가 빠졌다 — 이 로봇은 '찾기'만 한다(예: '파란 컵 찾아').",
                "number_without_unit": "수에 단위가 없다 — '200틱 안에' 처럼.",
                "avoid_without_zone": "무엇을 피할지가 빠졌다(책상·선반·카운터·바닥).",
                "low_confidence": "해석 신뢰도가 낮다 — 더 흔한 말로 다시 말해 달라."}.get(why, f"되물음({why})")
    if st == "unsupported" and why == "unknown_word":
        # 모르는 말은 **사전에서 뜻을 찾아** 보인다(추측이 아니라 출처가 붙은 뜻). 사전에 넣는 것은 사람이 한다
        from walp import search_path   # noqa: PLC0415
        return (f"'{tok}' 는 모르는 말이다. 뜻을 추측하지 않는다 — 사전을 찾았다.\n"
                + search_path.뜻보이기(search_path.뜻찾기(tok)) + f"\n{HINT}")
    msg = {"unsupported_verb": "이 로봇은 '찾기'만 한다 — 가져오기·옮기기·청소 같은 것은 못 한다.",
           "question": "질문에는 답하지 못한다 — 명령으로 말해 달라(예: '빨간 열쇠 찾아').",
           "negation": "부정('~말고', 'not')은 지원하지 않는다 — 찾을 것을 바로 말해 달라.",
           "time_unit_not_supported": "시간은 틱(걸음) 단위로만 받는다(예: '200틱 안에').",
           "location_hint_not_supported": "'어디에 있다'는 힌트는 아직 못 쓴다 — 피할 곳만 받는다.",
           "action_without_condition": "조건 없이 행동만 있다 — '불확실하면 다시 관찰' 처럼 말해 달라.",
           "multiple_targets": "찾을 것이 둘 이상이다 — 한 번에 하나만.",
           "conflicting_color": "색이 서로 어긋난다.", "conflicting_brand": "브랜드가 서로 어긋난다.",
           "brand_only_for_cards": "브랜드(비자·마스터)는 카드에만 있다.",
           "deadline_out_of_range": "기한은 1~5000틱."}.get(why, f"받지 않음({why})")
    return msg + "\n" + HINT


def _자라기(text: str, r: dict, who: str, via: str) -> str:
    """대화로 자라기(walp/evolve.py) — 거부 뒤 고쳐 말하기로 모르는 말의 뜻 후보를 세우고 묻는다. 덧붙일 말을 돌려준다."""
    if "error" in r:
        return ""
    from walp import evolve   # noqa: PLC0415
    st = r.get("status") or ("ok" if r.get("parsed") else "?")
    try:
        return evolve.관찰(who, via, text, {**r, "status": st}, parse=lambda t: cli("parse", t))
    except Exception as e:   # noqa: BLE001 — 자라기가 깨져도 대답은 나간다
        return f"\n_(자라기 고리 오류: {type(e).__name__})_"


def interpret(text: str, who: str, via: str) -> str:
    t0 = time.time()
    r = cli("parse", text)
    _기록("interpret", text, r, who, via, t0)
    return 설명(r) + _자라기(text, r, who, via)


def run(text: str, who: str, via: str, seed: "int | None" = None, family: str = "office",
        allow_write: bool = False) -> str:
    from walp import evolve   # noqa: PLC0415
    답 = evolve.대답(who, via, text, allow_write, learn=lambda w, c, k: cli("learn", w, c, k))
    if 답 is not None:
        말, 다시 = 답
        return 말 + (run(다시, who, via, seed, family, allow_write) if 다시 else "")
    # 대화 행위(walp/dialog.py): 파서가 받아들이면 찾기, 아니면 손 규칙 → (승격된) XCS. 찾기·모름은 예전 길로.
    from walp import dialog   # noqa: PLC0415
    r = cli("parse", text)
    if "error" in r:
        return "⚠ 실행 못 함: " + r["error"]
    m, _모드 = dialog.승격된집단()
    if getattr(m, "kind", "") == "behavior" and os.environ.get("WALP_BEHAVIOR", "1") != "0":
        return _행동길(m, text, r, who, via, seed, family, allow_write)
    act, by = dialog.고르기(text, r)
    usability.적기({"who": usability.누구(who), "via": via, "kind": "dialog", "act": act, "by": by, "text": text[:400]})
    _자동진화()
    if act in (None, "task") and _여러걸음(text):
        답 = _계획실행(text, who, via, seed, family)
    elif act in (None, "task"):
        답 = _run(text, who, via, seed, family)
    elif act == "knowledge":
        답 = search(text, who, via)
    else:
        답 = dialog.틀[act]
    return 답 + dialog.알림(usability.누구(who))


def _행동길(m, text: str, r: dict, who: str, via: str, seed: "int | None", family: str, allow_write: bool) -> str:
    """행동 기반(포섭) — 모든 층이 이 턴에 계산하고 억제 선이 말을 고른다. 되물음은 자연어 선택지로, 대답과 불만도 자연어로 읽는다
    (walp/behavior.py · 사전등록 walp/eval/PREREG_행동기반.md)."""
    from walp import behavior as B, dialog   # noqa: PLC0415
    h = usability.누구(who)
    앞줄 = [z for z in usability.읽기() if z.get("kind") == "behavior" and z.get("who") == h][-5:]
    from walp import deliberate   # noqa: PLC0415
    줄들 = usability.읽기()
    캐시 = B.캐시읽기(줄들) if B.답캐시켜기 else None        # 꺼 두었다 — 봉인 흐름 v1 결과(PREREG_LLM앞단.md)
    out = B.대화(m.버스(뜻들=B.뜻들읽기(줄들, h), 캐시=캐시), text, h, 앞줄, time.time(),
                 숙고=_숙고층(deliberate))
    for rec in out["기록"]:
        rec = {"who": h, "via": via, **rec}
        if rec["kind"] in ("act_fix", "act_new") and rec.get("via") != "llm":
            rec["w"] = bool(allow_write)
        usability.적기(rec)
    usability.적기({"who": h, "via": via, "kind": "dialog", "act": out["행위"], "by": "behavior", "text": text[:400]})
    _자동진화()
    종류, 글 = out["종류"], out["글"]
    if 종류 == "계획":
        답 = _계획실행(글, who, via, seed, family)
    elif 종류 in ("찾기", "없음"):
        답 = _run(글, who, via, seed, family)
    elif 종류 == "지식":
        답 = search(글, who, via)
    elif 종류 == "숙고":
        답 = out["내용"] + "\n_(숙고층 LLM 의 답 — 무엇을 묻는 말인지는 WALP 가 배웁니다)_"
    else:
        답 = out["내용"]
    return 답 + dialog.알림(h)


_숙고캐시: dict = {}


def _숙고층(deliberate):
    """숙고층(LLM)을 한 번만 짓는다. 쓸 수 없으면(키 없음 · 검사) None — 예전처럼 사람에게 되묻는다."""
    if "x" not in _숙고캐시:
        _숙고캐시["x"] = deliberate.기본숙고()
    return _숙고캐시["x"]


def _여러걸음(text: str) -> bool:
    from walp import strips   # noqa: PLC0415
    return strips.여러걸음인가(text) and strips.계획(text) != "REJECT"


def _계획실행(text: str, who: str, via: str, seed: "int | None", family: str) -> str:
    """숙고기(walp/strips.py, 사전등록 B 로 붙였다): 여러 걸음 요청 → STRIPS 계획 → 걸음마다 같은 세계(씨앗)에서 시뮬."""
    from walp import strips   # noqa: PLC0415
    t0 = time.time()
    seed = seed if seed is not None else int(t0) % 10000
    plan = strips.계획(text)

    def 걸음(말: str):
        r = cli("run", 말, "--seed", str(seed), "--family", family, timeout=120)
        if "error" in r or not r.get("parsed"):
            return ("⚠ " + r.get("error", "이 걸음을 못 알아들었다")), False
        return f"{_읽기(r['canon'])} → {OUTCOME.get(r['outcome'], r['outcome'])}", r.get("outcome") == "success"
    기록, 성공 = strips.실행(plan, 걸음)
    usability.적기({"who": usability.누구(who), "via": via, "kind": "plan", "text": text[:400], "plan": plan,
                    "꼴": strips.꼴(plan), "status": "ok", "outcome": "success" if 성공 else "fail",
                    "steps": len(기록), "ms": round(1000 * (time.time() - t0))})
    줄 = [f"여러 걸음으로 알아들음 — 계획: `{plan}`", f"(같은 세계, 시드 {seed}; `A || B` 는 A 를 못 찾을 때만 B)"]
    for i, (step, ok, 글) in enumerate(기록, 1):
        줄.append(f"  {i}. {글}")
    줄.append("**계획대로 끝났다.**" if 성공 else "**계획의 어느 가지도 끝까지 성공하지 못했다.**")
    return "\n".join(줄)


def _자동진화() -> None:
    """새 신호(사람의 고침 · 다음 턴 감사/불만)가 쌓이면(또는 처음 한 번) 배경에서 진화를 띄운다.
    **검사(SE_LEDGER_ROOT 가 선 때)에서는 기본으로 끈다** — 검사가 몇 분짜리 배경 작업을 남기면 안 된다(CLAUDE.md "검사는 재는 것이지
    남기는 것이 아니다"; 실측 2026-09-30: 시험 여럿이 배경 진화를 띄워 두었다). 켜려면 WALP_AUTO_EVOLVE=1."""
    기본 = "0" if os.environ.get("SE_LEDGER_ROOT") else "1"
    if os.environ.get("WALP_AUTO_EVOLVE", 기본) == "0":
        return
    from walp import dialog   # noqa: PLC0415
    try:
        dialog.자동확인()
    except Exception as e:   # noqa: BLE001 — 진화가 깨져도 대답은 나간다
        print(f"[walp] 자동 진화 실패: {type(e).__name__}: {e}", file=sys.stderr)


ACT_KO = {"찾기": "task", "인사": "greet", "작별": "bye", "감사": "thanks", "자기질문": "about_self", "능력질문": "capability",
          "도움": "help", "바깥지식": "knowledge", "불만": "complaint", "범위밖": "out_of_scope", "네": "yes", "아니": "no"}


def act_fix(name: str, who: str, via: str, allow_write: bool = False) -> str:
    """`행위 <이름>` — 바로 앞 말의 대화 행위를 사람이 고친다(ITL). 라벨로 원장에 남고 다음 `진화` 때 재생된다."""
    from walp import dialog   # noqa: PLC0415
    act = ACT_KO.get(name.strip(), name.strip())
    if act not in dialog.ACTS:
        return "행위 이름: " + " · ".join(f"{k}({v})" for k, v in ACT_KO.items())
    h = usability.누구(who)
    앞 = next((z for z in reversed(usability.읽기()) if z.get("who") == h and z.get("kind") == "dialog"
              and time.time() - z.get("ts", 0) <= 600), None)
    if not 앞:
        return "고칠 말이 없다 — 10분 안에 한 말이 없다."
    usability.적기({"who": h, "via": via, "kind": "act_fix", "act": act, "was": 앞.get("act"), "text": 앞.get("text", ""),
                    "w": bool(allow_write)})
    _자동진화()
    뒤 = dialog.틀.get(act, "") if act not in ("task", "knowledge") else ""
    바로 = dialog.기억_고르기(앞.get("text", "")) == act
    return (f"고쳤습니다 — '{앞.get('text', '')[:60]}' 는 **{act}** (전에는 {앞.get('act')}). "
            + ("같은 말에는 **지금부터** 이렇게 답합니다" if 바로 else "다른 한 분이 같은 고침을 하면 그때부터 이렇게 답합니다")
            + " · 비슷한 말은 쌓이면 진화로 배웁니다." + (f"\n\n{뒤}" if 뒤 else ""))


def evolve_dialog() -> str:
    """원장을 재생해 대화 행위 XCS 를 새로 기르고 봉인 관문을 돌린다(몇 분)."""
    from walp import dialog   # noqa: PLC0415
    return dialog.진화보이기(dialog.진화())


def _run(text: str, who: str, via: str, seed: "int | None", family: str) -> str:
    t0 = time.time()
    seed = seed if seed is not None else int(t0) % 10000
    r = cli("run", text, "--seed", str(seed), "--family", family, timeout=120)
    _기록("run", text, r, who, via, t0)
    return _run보이기(text, r, who, via) + _자라기(text, r, who, via)


def _run보이기(text: str, r: dict, who: str, via: str) -> str:
    if "error" in r:
        return "⚠ 실행 못 함: " + r["error"]
    if not r.get("parsed"):
        # 모르는 말이라도 **바깥 지식 물음**이면 가르치기를 권하는 대신 LLM 없는 검색으로 넘긴다
        # (사용자 2026-09-29: `!walp instargram이 쓰는 로그인 방식…` 이 '가르칠 수 있다' 로 끝났다)
        from walp import search_path   # noqa: PLC0415
        if search_path.가르기(text) == "SEARCH":
            return search(text, who, via)
        return 설명({**r, "status": r.get("status")})
    rules = r.get("rules", {})
    줄 = [f"알아들음: **{_읽기(r['canon'])}**" + (" (새 조합)" if r.get("interp") == "NOVEL_COMPOSITE" else ""),
          f"시뮬레이션(시드 {r['seed']}, {r['family']}): {OUTCOME.get(r['outcome'], r['outcome'])}",
          f"걸음 {r['steps']} · 에너지 {r['energy_used']} · 재관측 {r['observes']} · 안전층 거부 {r['denials']} · "
          f"안전 위반 {r['violations']}",
          f"결정 근거 추적 {r['trace_ok']}/{r['trace_total']} · 참고한 사례 {r['cases']}개",
          "쓰인 규칙: " + ", ".join(f"{k} {v}" for k, v in sorted(rules.items(), key=lambda kv: -kv[1])[:6]),
          "_(평가에 참여해 주셨다면 `!walp 점수 1~5 [한마디]` 로 남겨 주세요.)_"]
    return "\n".join(줄)


def search(text: str, who: str, via: str) -> str:
    """LLM 없는 검색 경로 — 검색 · 받기 · 문장 그대로 고르기 · 출처. 지어내지 않는다."""
    from walp import search_path   # noqa: PLC0415
    t0 = time.time()
    r = search_path.답하기(text)
    usability.적기({"who": usability.누구(who), "via": via, "kind": "search", "text": text[:300], "status": r["상태"],
                    "reason": r.get("고친말", ""), "ms": round(1000 * (time.time() - t0)), "n_sent": len(r.get("문장", []))})
    return search_path.보이기(r)


SEARCH_OUT = HERE / "usability" / "search_eval.json"


def search_eval(start: bool) -> str:
    """봉인 모음으로 검색 경로를 **이 기계의 망으로** 잰다(배경 작업). 결과는 `!walp 찾아보기점검 결과`."""
    if not start:
        if not SEARCH_OUT.is_file():
            return "아직 결과가 없다 — `!walp 찾아보기점검` 으로 시작한다(관리 채널)."
        d = json.loads(SEARCH_OUT.read_text(encoding="utf-8"))
        a = d.get("답", {})
        return (f"검색 경로 봉인 채점: 가르기 {d['가르기_정확도']:.1%} · 검색 재현율 {d['검색_재현율']:.1%} · 불필요한 검색 "
                f"{d['불필요한_검색']}\n답: {a.get('상태별')} · 열쇠말 하나 이상 {a.get('열쇠말_하나이상')}/"
                f"{sum(a.get('상태별', {}).get(k, 0) for k in ('답', '약함'))} · 열쇠말 비율 평균 {a.get('열쇠말_비율_평균')} · "
                f"중앙 {a.get('초_중앙')}s")
    SEARCH_OUT.parent.mkdir(parents=True, exist_ok=True)
    log = SEARCH_OUT.with_suffix(".log")
    cmd = [sys.executable, str(HERE / "search_path.py"), "--eval", str(HERE / "eval" / "search_corpus.tsv"), "--net",
           "--out", str(SEARCH_OUT)]
    with open(log, "w") as lf:
        p = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, cwd=str(REPO),
                             start_new_session=True)   # setsid — claude -p 가 끝나도 산다(CLAUDE.md)
    time.sleep(1.5)
    alive = p.poll() is None
    return (f"검색 경로 점검을 시작했다(PID {p.pid}, 로그 {log.name}) — 63문항, 몇 분 걸린다. `!walp 찾아보기점검 결과`"
            if alive else f"시작하자마자 끝났다(종료 {p.returncode}) — 로그 {log}")


def teach(word: str, cat: str, concept: str, who: str, via: str) -> str:
    r = cli("learn", word, cat, concept)
    usability.적기({"who": usability.누구(who), "via": via, "kind": "teach", "text": f"{word} {cat} {concept}",
                    "result": r.get("result", "error")})
    if "error" in r:
        return "⚠ 실행 못 함: " + r["error"]
    return {"added": f"배웠다: '{word}' → {concept}. 다음 명령부터 쓴다.",
            "duplicate": f"이미 같은 뜻으로 알고 있다: '{word}'.",
            "conflict": f"넣지 않았다 — 충돌: {r.get('why', '')}",
            "invalid": f"넣지 않았다 — 형식이 틀렸다: {r.get('why', '')}"}.get(r.get("result"), json.dumps(r, ensure_ascii=False))


def rate(score: int, comment: str, who: str, via: str) -> str:
    if not 1 <= score <= 5:
        return "점수는 1~5."
    usability.적기({"who": usability.누구(who), "via": via, "kind": "rate", "rating": score, "text": comment[:300]})
    return f"고맙다 — {score}점 기록."


SUS_Q = ["이 시스템을 자주 쓰고 싶다", "쓸데없이 복잡하다", "쓰기 쉽다", "쓰려면 기술 지원이 필요할 것 같다",
         "여러 기능이 잘 어우러져 있다", "일관되지 않은 데가 너무 많다", "대부분 사람이 금방 익힐 것 같다",
         "쓰기가 매우 번거롭다", "쓰면서 자신감이 들었다", "쓰기 전에 배울 것이 많았다"]


def sus(answers: list[int], who: str, via: str) -> str:
    try:
        s = usability.sus_score(answers)
    except ValueError as e:
        return f"{e}. 문항:\n" + "\n".join(f"{i + 1}. {q} (1 전혀 아니다 ~ 5 매우 그렇다)" for i, q in enumerate(SUS_Q))
    usability.적기({"who": usability.누구(who), "via": via, "kind": "sus", "sus": s, "answers": answers})
    return f"SUS {s:.1f}/100 기록."


def report() -> str:
    from walp import evolve   # noqa: PLC0415
    e = evolve.요약()
    덧 = ""
    if e["후보"]:
        덧 = ("\n대화로 자라기: " + " · ".join(f"{k} {v}" for k, v in e["후보"].items())
              + (" — 배운 말: " + ", ".join(e["넣은말"]) if e["넣은말"] else ""))
    줄들 = usability.읽기()
    행 = {}
    for z in 줄들:
        if z.get("kind") == "dialog":
            행[z.get("act") or "모름"] = 행.get(z.get("act") or "모름", 0) + 1
    if 행:
        덧 += "\n대화 행위: " + " · ".join(f"{k} {v}" for k, v in sorted(행.items(), key=lambda kv: -kv[1]))
        고침 = sum(1 for z in 줄들 if z.get("kind") == "act_fix")
        관 = [z for z in 줄들 if z.get("kind") == "xcs_gate"]
        덧 += f" — 고침 {고침}" + (f" · 마지막 진화 관문: {'승격' if 관[-1].get('승격') else '승격 안 함'}" if 관 else "")
    return usability.보고(usability.집계(줄들)) + 덧


def _기록(kind: str, text: str, r: dict, who: str, via: str, t0: float) -> None:
    usability.적기({"who": usability.누구(who), "via": via, "kind": kind, "text": text[:400],
                    "status": r.get("status", "ok" if r.get("parsed") else ("error" if "error" in r else "?")),
                    "interp": r.get("interp"), "reason": r.get("reason"), "token": r.get("token"),
                    "outcome": r.get("outcome"), "ms": round(1000 * (time.time() - t0))})


# ================================================================ SE 도구 배선(LLM 없이 고르고, LLM 을 막고 부른다)
_JOIN = r"\s*(?:그리고 나서|하고 나서|그 다음에|그다음|그리고|하고|and then|then|;)\s+"
PLAN_MAX = 5


def _se():
    try:
        from . import se_router
    except ImportError:  # pragma: no cover
        import se_router  # type: ignore
    return se_router


def _clauses(text: str) -> list:
    """요청 한 줄을 절로 가른다. 코드·경로 안에서는 가르지 않는다(그 안의 'and' 는 인자다)."""
    if "module" in text or "\\n" in text or "\n" in text:
        return [text]
    parts = [p.strip() for p in re.split(_JOIN, text) if p.strip()]
    return parts if 1 < len(parts) <= PLAN_MAX else [text]


def _한줄(route: dict) -> str:
    st = route.get("status")
    if st == "TOOL":
        return f"`{route['tool']}` {json.dumps(route.get('args', {}), ensure_ascii=False)[:160]}"
    if st == "ASK":
        if route.get("why") == "missing_required":
            return f"`{route.get('tool')}` 인데 필요한 것이 빠졌다: {', '.join(route.get('missing', []))}"
        return "어느 도구인지 둘로 읽힌다: " + " · ".join(f"`{c}`" for c in route.get("candidates", []))
    if st == "DENY":
        return f"`{route.get('tool')}` — 셸·쓰기·네트워크 도구라 WALP 가 부르지 않는다({route.get('why')})"
    return "맞는 도구가 없다"


PENDING_TTL_S = 600
_SECRET_TOOLS = {"set_key"}


def _pending_path() -> Path:
    # 확인 대기 표에는 set_key 값이 들어 있을 수 있다 — **저장소 밖**(usability.상태자리)에 둔다.
    # 첫 판은 walp/usability/pending.json 이라 공개 채널의 read_file 로 읽혔다(실측 2026-09-29)
    옛 = HERE / "usability" / "pending.json"     # 첫 판의 자리 — 남아 있으면 지운다(set_key 값이 들어 있을 수 있다)
    if 옛.exists():
        try:
            옛.unlink()
        except OSError:
            pass
    return usability.상태자리() / "walp_pending.json"


def _pending_load() -> dict:
    try:
        d = json.loads(_pending_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    now = time.time()
    return {k: v for k, v in d.items() if now - v.get("ts", 0) < PENDING_TTL_S}


def _pending_save(d: dict) -> None:
    p = _pending_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)
    try:
        os.chmod(p, 0o600)          # set_key 값이 들어 있을 수 있다
    except OSError:
        pass


def _보이기(tool: str, args: dict) -> str:
    """확인 화면에 보일 인자 — 비밀 값은 가린다."""
    a = dict(args)
    if tool in _SECRET_TOOLS and "value" in a:
        a["value"] = "****(" + str(len(str(a["value"]))) + "자)"
    return json.dumps(a, ensure_ascii=False)[:400]


def se_tool(text: str, who: str, via: str) -> str:
    """사람 말 → (절마다) 도구 고르기 → 전부 확실할 때만 차례로 실행(LLM 차단). 하나라도 불확실하면 **아무것도 안 부른다**.
    셸·쓰기·네트워크 도구가 끼어 있으면 바로 안 돌리고 **확인 표**를 낸다 — 관리 채널에서 `!walp 확인 <표>` 해야 돈다."""
    R = _se()
    t0 = time.time()
    clauses = _clauses(text)
    routes = [R.route(c) for c in clauses]
    secret = any(r.get("tool") in _SECRET_TOOLS for r in routes)
    usability.적기({"who": usability.누구(who), "via": via, "kind": "se_tool",
                    "text": "[비밀이 든 요청 — 글을 적지 않는다]" if secret else text[:400],
                    "status": "ok" if all(r["status"] in ("TOOL", "DENY") for r in routes) else routes[0]["status"].lower(),
                    "reason": ";".join(r.get("why", "") for r in routes), "ms": 0,
                    "tools": [r.get("tool") for r in routes]})
    if not all(r["status"] in ("TOOL", "DENY") for r in routes):
        head = "실행하지 않았다 — 추측하지 않는다." if len(routes) == 1 else f"{len(routes)} 단계 중 확실하지 않은 것이 있어 **아무것도** 실행하지 않았다."
        return head + "\n" + "\n".join(f"{i + 1}) {c[:60]} → {_한줄(r)}" for i, (c, r) in enumerate(zip(clauses, routes)))
    if any(r["status"] == "DENY" for r in routes):
        try:
            import agent_context   # noqa: PLC0415
            blocked = agent_context.is_blocked(str(who))
        except Exception:  # noqa: BLE001
            blocked = True          # 모르면 막힌 것으로 다룬다
        if blocked:
            return "부작용이 있는 도구는 이 사용자에게 열려 있지 않다(게스트) — 확인 표를 만들지 않았다."
        # 부작용이 있는 계획: 한 번 쓰는 표를 만들고 보여 준다. 표는 같은 사람만, 10분 안에, 그 인자 그대로만 쓸 수 있다
        import secrets
        tok = secrets.token_hex(3)
        d = _pending_load()
        d[tok] = {"ts": time.time(), "who": usability.누구(who),
                  "steps": [{"tool": r["tool"], "args": r.get("args", {})} for r in routes]}
        _pending_save(d)
        lines = [f"{i + 1}) `{r['tool']}` ({R.catalog()[r['tool']].kind if r['tool'] in R.catalog() else '?'}) {_보이기(r['tool'], r.get('args', {}))}"
                 for i, r in enumerate(routes)]
        return ("**부작용이 있는 일이라 바로 하지 않았다.** 아래 그대로 할까? (LLM 없이 고른 것 — 틀렸으면 확인하지 마라)\n"
                + "\n".join(lines) + f"\n\n관리 채널에서 `!walp 확인 {tok}` — 10분 안에, 요청한 사람만. 취소는 `!walp 취소 {tok}`.")
    return _run_steps(R, clauses, routes, who, via, t0, allow_write=False)


def _run_steps(R, clauses, routes, who, via, t0, allow_write: bool) -> str:
    out = []
    for i, r in enumerate(routes):
        res = R.execute(r["tool"], r.get("args", {}), timeout=600, allow_write=allow_write, author=str(who or ""))
        body = (res.get("result") or res.get("error") or "")[:1500]
        llm = res.get("llm_attempts", 0)
        out.append(f"**{i + 1}) `{r['tool']}`** {_보이기(r['tool'], r.get('args', {}))[:120]}"
                   f" — {'성공' if res.get('ok') else '실패'} · {res.get('wall_s')}s"
                   + (f" · ⚠ LLM 시도 {llm}건을 막았다(결과가 불완전할 수 있다)" if llm else " · LLM 0")
                   + "\n" + body)
        if not res.get("ok"):
            out.append("(앞 단계가 실패해 뒤 단계는 부르지 않았다)" if i + 1 < len(routes) else "")
            break
    usability.적기({"who": usability.누구(who), "via": via, "kind": "se_tool_run", "text": "",
                    "tools": [r.get("tool") for r in routes], "ms": round(1000 * (time.time() - t0))})
    return "\n\n".join(x for x in out if x)


def se_confirm(tok: str, who: str, via: str, cancel: bool = False) -> str:
    d = _pending_load()
    p = d.get(tok.strip())
    if not p:
        return "그런 확인 표가 없다(10분이 지났거나 이미 썼다)."
    if p["who"] != usability.누구(who):
        return "요청한 사람만 확인할 수 있다."
    del d[tok.strip()]
    _pending_save(d)
    if cancel:
        return "취소했다 — 아무것도 안 했다."
    R = _se()
    routes = [{"tool": s["tool"], "args": s["args"], "status": "DENY"} for s in p["steps"]]
    return _run_steps(R, [""] * len(routes), routes, who, via, time.time(), allow_write=True)


def se_catalog() -> str:
    try:
        from . import se_tools
    except ImportError:  # pragma: no cover
        import se_tools  # type: ignore
    cat = se_tools.catalog()
    by = {}
    for t in cat:
        by.setdefault(t.kind, []).append(t.name + ("*" if t.llm else ""))
    lines = [f"SE 도구 {len(cat)}개 (bot_tools {sum(t.source == 'bot_tools' for t in cat)} · 고정 명령 "
             f"{sum(t.source == 'dispatch' for t in cat)}) · 목록 해시 {se_tools.catalog_hash(cat)}"]
    names = {"read": "읽기", "compute": "계산", "write": "쓰기(막음)", "shell": "셸(막음)", "network": "네트워크(막음)"}
    for k in ("compute", "read", "write", "shell", "network"):
        if by.get(k):
            lines.append(f"- {names[k]} {len(by[k])}: " + ", ".join(by[k]))
    lines.append("* = 코드 분석상 LLM 을 부를 수 있음(추정). 실행 중에는 LLM 이 막히고 시도가 세어진다 — `!walp 도구점검`")
    return "\n".join(lines)


SMOKE_OUT = HERE / "usability" / "tool_smoke.json"


def se_smoke(start: bool) -> str:
    if not start:
        if not SMOKE_OUT.is_file():
            return "아직 점검 결과가 없다 — `!walp 도구점검` 으로 시작한다."
        d = json.loads(SMOKE_OUT.read_text(encoding="utf-8"))
        c = d["counts"]
        bad = [f"{r['tool']}({r['class']})" for r in d["rows"] if r["class"] in ("empty", "env_missing", "error", "llm_blocked")]
        return (f"도구점검({time.strftime('%m-%d %H:%M', time.localtime(d['ts']))}, {d['seconds']}s): "
                f"돌았다 {c['ran']} · 돌았지만 빈손 {c.get('empty', 0)} · 환경 없음 {c['env_missing']} · LLM 막힘 {c['llm_blocked']} · 오류 {c['error']} · "
                f"막음(셸·쓰기) {c['denied']} · 배경작업이라 안 돌림 {c['skip_background']}\n" + ", ".join(bad))
    SMOKE_OUT.parent.mkdir(parents=True, exist_ok=True)
    log = SMOKE_OUT.with_suffix(".log")
    # claude -p 가 끝나도 살아남게(CLAUDE.md 백그라운드 규칙): setsid + nohup + 입출력 전부 돌리기
    subprocess.Popen(["setsid", "nohup", "python3", str(HERE / "se_smoke.py"), "--out", str(SMOKE_OUT)],
                     stdout=open(log, "w"), stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, cwd=str(HERE.parent),
                     start_new_session=True)
    return f"도구점검을 배경으로 시작했다(도구마다 LLM 차단 · 임시 워크트리). 수 분 뒤 `!walp 도구점검 결과`. 로그: {log}"
