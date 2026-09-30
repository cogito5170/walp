"""WALP 대화로 자라기 — 최소항. **고쳐 말하기가 모르는 말을 가르친다**(센서–센서 자기지도).

사용자(2026-09-30): "사용자와 대화하면서 스스로 발전하는 형태의 최소항부터 구현하자."
선행조사: `paper/선행조사/WALP_진화대화.md` — Stanley 의 자기지도(가까운 믿을 센서가 먼 싼 센서를 가르친다) ·
Alexa 의 고쳐 말하기 학습 · ITL(사람이 대화로 가르친다) · XCS(후보 규칙의 경쟁)의 **가장 작은 꼴**.

고리 하나(대화 한 번 안에 닫힌다):

    1) 사람이 말한다      "주홍 컵 찾아줘"            → 모르는 말 '주홍' (거부 — 싼 센서)
    2) 사람이 고쳐 말한다  "빨간 컵 찾아줘"            → 받아들여짐 (믿을 센서 — 해석이 섰다)
    3) 맞대기(변이)        '주홍' 자리에 아는 개념을 하나씩 넣어 본다(반사실 대입, 개념 14개).
                          고쳐 말한 것과 **해석(canon)이 꼭 같아지는 개념이 하나뿐**이면 후보: 주홍 → red
    4) 묻기(시퀀서)        "방금 '주홍' 을 '빨강' 뜻으로 쓰셨나요? 맞으면 `네`"
    5) 고르기(선택)        `네`(쓰기 권한) → 사전에 넣고 **처음 말을 다시 돌린다**. `아니` → 후보를 버린다.
                          권한이 없는 사람의 `네` 는 증거 1 로만 센다 — **서로 다른 사람 둘**의 증거가 모이면 넣는다
                          (누구나 들어오는 서버에서 한 사람이 사전을 더럽히지 못하게).

지키는 것:
  · **추측하지 않는다.** 맞대기가 둘 이상이거나 없으면 후보를 안 낸다. 고쳐 말한 것이 **다른 요청**이면
    (다른 자리도 바뀌었으면) canon 이 안 같아지므로 후보가 안 생긴다.
  · 학습된 가중치 0 · GPU 0 · LLM 0. 배우는 것은 사전 한 줄(사람이 읽을 수 있다)과 그 근거(원장).
  · 사전에 들어가는 길은 `walp_cli learn` 하나 — 사람이 `가르치기` 할 때와 같은 충돌 검사를 지난다.

상태(저장소 밖, `usability.상태자리()/walp_evolve.json`):
    fail[who]   = 그 사람의 최근 거부 몇 개 (글 · 모르는 말 · 시각)
    ask[who]    = 물어 둔 것 (낱말 · 범주 · 개념 · 처음 말 · 시각)
    cand[낱말]  = {범주, 개념, 증거:[who…], 반대:[who…], 상태: 후보|넣음|버림|충돌}
원장(kind=evolve): 후보 · 물음 · 네 · 아니 · 넣음 · 버림 — 무엇이 언제 왜 배워졌는지 전부 남는다.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from walp import usability

WINDOW_S = 10 * 60          # 거부와 고쳐 말하기 사이 · 물음과 대답 사이
KEEP_FAILS = 3
NEED_WHO = 2                # 권한 없이 넣으려면 서로 다른 사람 수

# 반사실 대입에 쓰는 개념과 그 **한국어 대표 낱말**(조사가 붙어도 파서가 읽는다). 범주는 walp_cli learn 의 것.
개념 = [("color", "red", "빨간", "빨강"), ("color", "blue", "파란", "파랑"), ("color", "green", "초록", "초록"),
       ("color", "black", "검은", "검정"),
       ("object", "card", "카드", "카드"), ("object", "key", "열쇠", "열쇠"), ("object", "cup", "컵", "컵"),
       ("object", "box", "상자", "상자"),
       ("brand", "visa", "비자", "비자"), ("brand", "master", "마스터", "마스터"),
       ("zone", "desk", "책상", "책상"), ("zone", "shelf", "선반", "선반"), ("zone", "counter", "카운터", "카운터"),
       ("zone", "floor", "바닥", "바닥")]
_조사 = ("은", "는", "이", "가", "을", "를", "도", "에", "의", "로", "와", "과")
네말 = {"네", "넵", "예", "응", "어", "맞아", "맞아요", "맞다", "그래", "yes", "y", "ok", "ㅇㅇ", "ㅇ"}
아니말 = {"아니", "아니요", "아뇨", "아니야", "틀려", "no", "n", "ㄴㄴ", "ㄴ"}


def _경로() -> Path:
    return usability.상태자리() / "walp_evolve.json"


def _읽기() -> dict:
    try:
        d = json.loads(_경로().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _쓰기(d: dict) -> None:
    p = _경로()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def _적기(who_h: str, via: str, 무엇: str, **칸) -> None:
    usability.적기({"who": who_h, "via": via, "kind": "evolve", "what": 무엇, **칸})


def _이름(con: str) -> str:
    return next((k[3] for k in 개념 if k[1] == con), con)


def 맞대기(실패글: str, 낱말: str, 목표canon: str, parse) -> "list[tuple[str, str, str]]":
    """실패한 말의 '낱말' 자리에 개념을 하나씩 넣어, 해석이 목표와 **꼭 같아지는** (낱말줄기, 범주, 개념) 목록."""
    if not 낱말 or 낱말 not in 실패글 or not 목표canon:
        return []
    줄기, 꼬리 = 낱말, ""
    if len(낱말) > 1 and 낱말[-1] in _조사:
        줄기, 꼬리 = 낱말[:-1], 낱말[-1]
    맞음 = []
    for cat, con, 대표, _ in 개념:
        r = parse(실패글.replace(낱말, 대표 + 꼬리, 1))
        if r.get("status") == "ok" and r.get("canon") == 목표canon:
            맞음.append((줄기, cat, con))
    return 맞음


def 관찰(who: str, via: str, 글: str, r: dict, parse, now: "float | None" = None) -> str:
    """front.run/interpret 가 해석 뒤에 부른다. 사람에게 덧붙일 말(물음)이 있으면 돌려준다, 없으면 ""."""
    now = time.time() if now is None else now
    h = usability.누구(who)
    d = _읽기()
    fails = d.setdefault("fail", {}).setdefault(h, [])
    fails[:] = [f for f in fails if now - f["ts"] <= WINDOW_S]
    st = r.get("status")
    if st == "unsupported" and r.get("reason") == "unknown_word" and r.get("token"):
        tok = r["token"]
        fails.append({"ts": now, "text": 글, "token": tok})
        del fails[:-KEEP_FAILS]
        c = d.get("cand", {}).get(tok) or d.get("cand", {}).get(tok[:-1] if tok[-1:] in _조사 else "")
        말 = ""
        if c and c.get("상태") == "후보":
            말 = _묻기(d, h, via, c["낱말"], c["범주"], c["개념"], 글, now)
        _쓰기(d)
        return 말
    if st != "ok" or not r.get("canon") or not fails:
        _쓰기(d)
        return ""
    # 받아들여졌다 — 바로 앞 거부들 가운데 이 말로 풀리는 것이 있나(최근 것부터).
    # 목표는 **같은 parse 로 다시 얻은 canon** 이다 — run 의 canon 은 기본 기한(deadline=300)을 덧붙여 parse 와 글자가 다르다.
    목표 = parse(글)
    if 목표.get("status") != "ok" or not 목표.get("canon"):
        _쓰기(d)
        return ""
    for f in reversed(fails):
        맞 = 맞대기(f["text"], f["token"], 목표["canon"], parse)
        if len(맞) != 1:
            if 맞:
                _적기(h, via, "모호", token=f["token"], n=len(맞))
            continue
        줄기, cat, con = 맞[0]
        fails.remove(f)
        c = d.setdefault("cand", {}).get(줄기)
        if c and (c["범주"], c["개념"]) != (cat, con):
            _적기(h, via, "어긋남", token=줄기, 전=f"{c['범주']}:{c['개념']}", 새=f"{cat}:{con}")
            c["반대"] = sorted(set(c.get("반대", [])) | {h})
            _쓰기(d)
            return ""
        if not c:
            c = d["cand"][줄기] = {"낱말": 줄기, "범주": cat, "개념": con, "증거": [], "반대": [], "상태": "후보"}
            _적기(h, via, "후보", token=줄기, cat=cat, concept=con, text=f["text"][:200], canon=목표["canon"])
        if c["상태"] != "후보":
            _쓰기(d)
            return ""
        말 = _묻기(d, h, via, 줄기, cat, con, f["text"], now)
        _쓰기(d)
        return 말
    _쓰기(d)
    return ""


def _묻기(d: dict, h: str, via: str, 줄기: str, cat: str, con: str, 처음: str, now: float) -> str:
    d.setdefault("ask", {})[h] = {"낱말": 줄기, "범주": cat, "개념": con, "처음": 처음, "ts": now}
    _적기(h, via, "물음", token=줄기, cat=cat, concept=con)
    return (f"\n\n혹시 '{줄기}' 라는 말을 **{_이름(con)}** 뜻으로 쓰셨나요? 맞으면 `네`, 아니면 `아니` — "
            "`네` 면 배워서 다음부터 알아듣는다.")


def 대답(who: str, via: str, 글: str, allow_write: bool, learn, now: "float | None" = None) -> "tuple[str, str] | None":
    """물어 둔 것에 대한 네/아니. 대답이 아니면 None. 대답이면 (사람에게 할 말, 다시 돌릴 처음 말 또는 "")."""
    now = time.time() if now is None else now
    t = (글 or "").strip().rstrip(".!~ ").lower()
    if t not in 네말 and t not in 아니말:
        return None
    h = usability.누구(who)
    d = _읽기()
    q = d.get("ask", {}).get(h)
    if not q or now - q["ts"] > WINDOW_S:
        return None
    del d["ask"][h]
    c = d.setdefault("cand", {}).setdefault(q["낱말"], {"낱말": q["낱말"], "범주": q["범주"], "개념": q["개념"],
                                                        "증거": [], "반대": [], "상태": "후보"})
    if t in 아니말:
        c["반대"] = sorted(set(c.get("반대", [])) | {h})
        if len(c["반대"]) >= max(1, len(c.get("증거", []))):
            c["상태"] = "버림"
        _적기(h, via, "아니", token=q["낱말"], 상태=c["상태"])
        _쓰기(d)
        return f"알겠다 — '{q['낱말']}' 를 {_이름(q['개념'])} 뜻으로 배우지 않는다.", ""
    c["증거"] = sorted(set(c.get("증거", [])) | {h})
    _적기(h, via, "네", token=q["낱말"], 권한=allow_write, 증거=len(c["증거"]))
    if not allow_write and len(c["증거"]) < NEED_WHO:
        _쓰기(d)
        return (f"고맙다 — 증거로 적었다('{q['낱말']}' → {_이름(q['개념'])}, 사람 {len(c['증거'])}/{NEED_WHO}). "
                "다른 분도 같은 뜻으로 쓰면 그때 배운다."), ""
    r = learn(q["낱말"], q["범주"], q["개념"])
    res = r.get("result", "error")
    c["상태"] = {"added": "넣음", "duplicate": "넣음", "conflict": "충돌"}.get(res, c["상태"])
    _적기(h, via, "넣음" if c["상태"] == "넣음" else res, token=q["낱말"], cat=q["범주"], concept=q["개념"],
         why=r.get("why", ""))
    _쓰기(d)
    if c["상태"] == "넣음":
        return f"배웠다: '{q['낱말']}' → {_이름(q['개념'])}. 처음 말을 다시 돌린다:\n\n", q["처음"]
    return f"넣지 못했다({res}: {r.get('why', '')}).", ""


def 요약() -> dict:
    d = _읽기()
    by: dict[str, int] = {}
    for c in d.get("cand", {}).values():
        by[c.get("상태", "?")] = by.get(c.get("상태", "?"), 0) + 1
    return {"후보": by, "넣은말": sorted(c["낱말"] + "→" + c["개념"] for c in d.get("cand", {}).values()
                                    if c.get("상태") == "넣음")}
