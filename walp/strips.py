"""WALP 숙고기 — Shakey/SPA 의 STRIPS 식으로 여러 걸음 요청을 계획한다. LLM 없음.

사용자(2026-09-30): 처음 설계 "2. 숙고기 = 숙고형의 원형 — Shakey 와 SPA". 선행조사 `paper/선행조사/WALP_숙고기_STRIPS.md`,
사전등록 `walp/eval/PREREG_층순서_ESN_숙고기.md`(B).

지금 파서는 여러 걸음을 `multiple_targets` 로 거부한다. 숙고기가 그 앞에서:
    "선반은 피해서 파란 비자카드랑 열쇠 찾고, 없으면 검은 컵"
      → (find(card,blue,visa,avoid=shelf) && find(key,avoid=shelf)) || find(cup,black,avoid=shelf)

STRIPS 로 적으면 연산자 하나 `find(o)`: 전제 없음 · 결과가 불확실 → 더할 목록 {found(o)} 또는 {notfound(o)}.
`A && B` 는 차례 계획, `A || B` 는 **우발 가지**(A 가 notfound 일 때만 B). 걸음마다 지금의 C++ 실행기(시뮬)를 부른다 —
숙고기는 순서만 정한다(Sense-Plan-Act 의 P).

지키는 것: 모르는 연결 · 가져오기/옮기기/청소는 **거부**(추측 안 함). 피할 곳은 한 번 말하면 모든 걸음에, 색·브랜드는 붙은 물건에만.
성공한 계획의 **꼴**(삼각 표 · Fikes 1972 의 매크로처럼 일반화한 모양)을 원장에 남긴다.
"""
from __future__ import annotations

import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
ZONES = ["desk", "shelf", "counter", "floor"]
_조사 = "은는이가을를도에의로와과랑요"
# 연결 표지 — 우발(없으면) 이 차례보다 먼저 가른다
우발표지 = ["못 찾으면", "못찾으면", "없으면", "없음", "없을 경우", "없을경우", "안 보이면", "안보이면", "아니면", "없다면", "못찾겠으면",
         "if not found", "if you can't find", "if you cant find", "if there's no", "if there is no", "if not", "otherwise",
         "or else", "failing that", "if none", "else"]
차례표지 = ["그리고 나서", "그리고나서", "그 다음에", "그다음에", "그 다음", "그다음", "다음에", "찾고 나서", "찾고나서", "하고 나서",
         "그리고", "and then", "then", "after that", "찾고", "and", "&", ",", "+", "랑", "이랑", "하고", "및", "plus", "also"]
거부표지 = ["가져", "갖다", "갖고 와", "옮겨", "옮기", "치워", "정리", "사와", "사다", "청소", "버려", "닦아", "열어", "bring", "carry",
         "fetch", "move ", "clean", "throw", "buy", "pick up", "deliver", "open "]
피함표지 = ["피해", "피하", "빼고", "말고", "제외", "avoid", "except", "without", "not the", "stay away", "skip", "안 가", "가지 말"]


def _사전() -> dict:
    """낱말 → (범주, 개념). lexicon.csv + 배운 낱말. 뜻이 여럿(a|b)인 것은 뺀다(추측 안 함)."""
    import os
    d: dict = {}
    for p in (HERE / "data" / "lexicon.csv", Path(os.environ.get("WALP_LEARNED") or HERE / "data" / "learned.csv")):
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                if not line or line.startswith("#"):
                    continue
                f = line.split(",")
                if len(f) < 3 or "|" in f[2] or f[1] not in ("color", "object", "brand", "brandcard", "zone"):
                    continue
                d[f[0].strip().lower()] = (f[1], f[2])
        except OSError:
            pass
    return d


_캐시 = {"d": None}


def 사전() -> dict:
    if _캐시["d"] is None:
        _캐시["d"] = _사전()
    return _캐시["d"]


def _낱말들(글: str) -> list:
    """글 → [(범주, 개념, 위치)]. 앞에서부터 가장 긴 사전 낱말을 붙잡는다(조사·'거' 가 붙어도)."""
    d = 사전()
    t = 글.lower()
    out = []
    for m in re.finditer(r"[0-9a-z가-힣\-]+", t):
        w = m.group(0)
        i = 0
        while i < len(w):
            hit = None
            for j in range(len(w), i, -1):
                if w[i:j] in d:
                    hit = (w[i:j], d[w[i:j]])
                    break
            if hit:
                cat, con = hit[1]
                if cat == "brandcard":
                    out.append(("brand", con, m.start() + i))
                    out.append(("object", "card", m.start() + i))
                else:
                    out.append((cat, con, m.start() + i))
                i += len(hit[0])
                if not w[:i].isascii():
                    # 한국어: 뒤에 **사전 낱말이 바로 이어지면**(띄어쓰기 없이 "검정컵") 계속 읽고, 아니면 멈춘다(뒤는 조사·어미).
                    # 봉인 평가(92.9%)는 이 고침 **전** 판으로 쟀다 — 이 고침은 저자 예문 "검정컵없으면파랑컵찾아" 에서 찾았다.
                    나머지 = w[i:]
                    for 표 in ("이랑", "랑", "하고", "과", "와", "고", "없으면", "아니면", "그리고", "다음"):
                        if 나머지.startswith(표):
                            i += len(표)
                            나머지 = w[i:]
                            break
                    if not any(나머지.startswith(k) for k in d if len(k) >= 1 and not k.isascii()):
                        break
            else:
                if w.isascii():
                    break
                i += 1
    return out


def _자르기(글: str, 표지: list) -> list:
    # 표지를 품은 사전 낱말은 먼저 가린다 — "랑" 이 "파랑" 을, "하고" 가 낱말 속을 자르던 결함(저자 예문에서 찾음).
    가림 = {}
    t = 글
    for w in sorted((w for w in 사전() if not w.isascii() and any(k in w for k in 표지 if not k.isascii())),
                    key=len, reverse=True):
        if w in t:
            ph = f"\x01{len(가림)}\x01"
            가림[ph] = w
            t = t.replace(w, ph)
    for k in sorted(표지, key=len, reverse=True):
        t = re.sub(re.escape(k), "\x00", t, flags=re.I) if not k.isascii() or not k.isalpha() else \
            re.sub(r"\b" + re.escape(k) + r"\b", "\x00", t, flags=re.I)
    out = []
    for p in t.split("\x00"):
        for ph, w in 가림.items():
            p = p.replace(ph, w)
        out.append(p)
    return out


def _피할곳(글: str) -> list:
    """피함 표지와 같은 절에 나온 구역들(전체에 공유)."""
    t = 글.lower()
    if not any(k in t for k in 피함표지):
        return []
    zs = [con for cat, con, _ in _낱말들(글) if cat == "zone"]
    return [z for z in ZONES if z in zs]


def _걸음들(조각: str) -> list:
    """한 차례 조각 → [ {object, color, brand} ]. 색·브랜드는 **다음** 물건에 붙고, 물건 뒤에만 오면 앞 물건에 붙는다."""
    걸음 = []
    대기: dict = {}
    for cat, con, _ in _낱말들(조각):
        if cat in ("color", "brand"):
            if 걸음 and not 대기 and cat not in 걸음[-1] and 걸음[-1].get("_뒤", True):
                # "카드 파란거" — 물건 뒤의 색. 다음 물건이 오면 거기로 옮긴다
                걸음[-1][cat] = con
                걸음[-1].setdefault("_뒤붙음", []).append(cat)
            else:
                대기[cat] = con
        elif cat == "object":
            if 걸음 and 걸음[-1].get("_뒤붙음"):
                for c in 걸음[-1].pop("_뒤붙음"):          # 앞 물건 뒤에 붙었던 색이 사실은 이 물건 앞의 것이었다
                    if c not in 대기:
                        대기[c] = 걸음[-1].pop(c)
            걸음.append({"object": con, **대기})
            대기 = {}
    for s in 걸음:
        s.pop("_뒤붙음", None)
        s.pop("_뒤", None)
    if 대기 and 걸음:
        for k, v in 대기.items():
            걸음[-1].setdefault(k, v)
    return 걸음


def _표기(s: dict, 피할: list) -> str:
    parts = [s["object"]] + [s[k] for k in ("color", "brand") if k in s]
    if s.get("brand") and s["object"] != "card":
        parts = [p for p in parts if p != s["brand"]]
    if 피할:
        parts.append("avoid=" + "+".join(피할))
    return "find(" + ",".join(parts) + ")"


def 계획(글: str) -> str:
    """말 → 계획 표기(`find(..) && find(..) || find(..)` 또는 `REJECT`)."""
    t = (글 or "").strip()
    low = t.lower()
    if any(k in low for k in 거부표지) or not any(c == "object" for c, _, _ in _낱말들(t)):
        return "REJECT"
    피할 = _피할곳(t)
    가지 = []
    for 조각 in _자르기(t, 우발표지):
        걸음 = []
        for 작은 in _자르기(조각, 차례표지):
            걸음 += _걸음들(작은)
        if 걸음:
            가지.append(" && ".join(_표기(s, 피할) for s in 걸음))
    if not 가지:
        return "REJECT"
    return " || ".join(가지)


# ---------------------------------------------------------------- 표기 비교 · 대조군
def 정규(p: str) -> str:
    p = re.sub(r"\s+", "", (p or "").strip())
    def z(m):
        zs = sorted(m.group(1).split("+"), key=lambda x: ZONES.index(x) if x in ZONES else 9)
        return "avoid=" + "+".join(zs)
    return re.sub(r"avoid=([a-z+]+)", z, p)


def 파서계획(글: str, parse) -> str:
    """파서 한 번 — 받아들이면 한 걸음, 아니면 REJECT(지금의 WALP)."""
    r = parse(글)
    if r.get("status") != "ok":
        return "REJECT"
    f = dict(kv.split("=", 1) for kv in r.get("canon", "").split()[1:] if "=" in kv)
    s = {"object": f.get("type", "?")}
    if f.get("color"):
        s["color"] = f["color"]
    if f.get("brand"):
        s["brand"] = f["brand"]
    return _표기(s, [z for z in ZONES if z in f.get("avoid", "").split("+")])


def 자르기만(글: str, parse) -> str:
    """사소한 대조: 쉼표·그리고·랑·and 로만 자르고 조각마다 파서. 우발·공유 수식어 없음."""
    조각 = [p for p in re.split(r",|그리고|이랑|랑|하고|\band\b|\bthen\b", 글) if p.strip()]
    걸음 = [파서계획(p, parse) for p in 조각]
    걸음 = [s for s in 걸음 if s != "REJECT"]
    return " && ".join(걸음) if 걸음 else "REJECT"


# ---------------------------------------------------------------- 실행 — 걸음마다 시뮬(C++ 실행기)
_말 = {"card": "카드", "key": "열쇠", "cup": "컵", "box": "상자", "red": "빨간", "blue": "파란", "green": "초록", "black": "검은",
      "visa": "비자", "master": "마스터", "desk": "책상", "shelf": "선반", "counter": "카운터", "floor": "바닥"}


def 걸음말(step: str) -> str:
    """find(card,blue,visa,avoid=shelf) → '파란 비자 카드 찾아줘, 선반은 피해서' — 지금의 파서가 받는 말로."""
    m = re.match(r"find\(([^)]*)\)", step.strip())
    if not m:
        return ""
    parts = m.group(1).split(",")
    피 = [p[6:] for p in parts if p.startswith("avoid=")]
    속 = [p for p in parts if not p.startswith("avoid=")]
    obj, 앞 = 속[0], 속[1:]
    말 = " ".join(_말.get(a, a) for a in 앞) + (" " if 앞 else "") + _말.get(obj, obj) + " 찾아줘"
    if 피:
        말 += ", " + "·".join(_말.get(z, z) for z in 피[0].split("+")) + "은 피해서"
    return 말


def 여러걸음인가(글: str) -> bool:
    ws = _낱말들(글)
    t = (글 or "").lower()
    return sum(1 for c, _, _ in ws if c == "object") >= 2 or (any(k in t for k in 우발표지[:12]) and any(c == "object" for c, _, _ in ws))


def 실행(plan: str, run_step) -> "tuple[list, bool]":
    """계획을 걸음마다 돌린다. run_step(말) → (보일 글, 성공?). || 는 앞 가지가 모두 성공하면 뒤를 건너뛴다."""
    기록 = []
    for 가지 in plan.split(" || "):
        ok_all = True
        for step in 가지.split(" && "):
            글, ok = run_step(걸음말(step))
            기록.append((step, ok, 글))
            ok_all = ok_all and ok
        if ok_all:
            return 기록, True
    return 기록, False


def 꼴(plan: str) -> str:
    """삼각 표처럼 일반화한 계획의 모양 — 값은 지우고 구조만(예: find(o,c) && find(o) || find(o,c))."""
    def g(m):
        parts = m.group(1).split(",")
        return "find(o" + ",c" * sum(1 for p in parts[1:] if not p.startswith("avoid=") and p in ("red", "blue", "green", "black")) \
               + (",b" if any(p in ("visa", "master") for p in parts) else "") + (",avoid" if any(p.startswith("avoid=") for p in parts) else "") + ")"
    return re.sub(r"find\(([^)]*)\)", g, plan)


# ---------------------------------------------------------------- 사전등록 평가(B)
def 평가(path, parse) -> dict:
    import math
    rows = []
    for line in open(path, encoding="utf-8"):
        if not line.strip() or line.startswith("#"):
            continue
        exp, _, utt = line.rstrip("\n").partition("\t")
        rows.append((정규(exp), utt))

    def 부류(e):
        return "거부" if e == "REJECT" else "우발" if "||" in e else "차례" if "&&" in e else "한걸음"
    조건 = {"지금": lambda u: 파서계획(u, parse), "자르기만": lambda u: 자르기만(u, parse), "STRIPS": 계획}
    맞 = {k: [정규(f(u)) == e for e, u in rows] for k, f in 조건.items()}

    def 짝(a, b):
        w = sum(1 for x, y in zip(맞[a], 맞[b]) if x and not y)
        l = sum(1 for x, y in zip(맞[a], 맞[b]) if y and not x)
        n = w + l
        p = 1.0 if n == 0 else sum(math.comb(n, k) for k in range(w, n + 1)) / 2 ** n
        return [w, l, p]
    P1, P2 = 짝("STRIPS", "지금"), 짝("STRIPS", "자르기만")
    부류별 = {}
    for k in 조건:
        for (e, _), m in zip(rows, 맞[k]):
            부류별.setdefault(k, {}).setdefault(부류(e), [0, 0])
            부류별[k][부류(e)][0] += m
            부류별[k][부류(e)][1] += 1
    섬 = P1[0] > P1[1] and P1[2] < 0.05 and P2[0] > P2[1] and P2[2] < 0.05
    틀림 = [{"기대": e, "STRIPS": 정규(계획(u)), "말": u} for (e, u), m in zip(rows, 맞["STRIPS"]) if not m]
    return {"n": len(rows), "정확도": {k: round(sum(v) / len(v), 4) for k, v in 맞.items()}, "P1 STRIPS>지금": P1,
            "P2 STRIPS>자르기만": P2, "부류별": 부류별, "섬": 섬,
            "결정": "숙고기를 붙인다" if 섬 else "붙이지 않는다", "STRIPS_틀린수": len(틀림), "_틀림": 틀림}


if __name__ == "__main__":
    import argparse
    import json
    import sys
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval")
    ap.add_argument("--out")
    ap.add_argument("text", nargs="*")
    a = ap.parse_args()
    if a.eval:
        from walp import front
        r = 평가(a.eval, lambda t: front.cli("parse", t))
        if a.out:
            Path(a.out).write_text(json.dumps({k: v for k, v in r.items() if k != "_틀림"}, ensure_ascii=False, indent=1),
                                   encoding="utf-8")
        print(json.dumps({k: v for k, v in r.items() if k != "_틀림"}, ensure_ascii=False, indent=1))
        sys.exit(0)
    print(계획(" ".join(a.text)))
