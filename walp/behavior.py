"""행동 기반(포섭) WALP — 사전등록 `walp/eval/PREREG_행동기반.md` 그대로.

분류기는 **센서**로 내려간다(성장망 · 손 규칙). 행동은 센서 비트를 보고 **직접** 행위를 고르고, 결과의 보상으로 **자기** 가치를 바꾼다.
상위 행동은 하위 행동의 출력을 억제 선으로 대체한다(Brooks 1986). 선은 자료라서 끊을 수 있다.

    L3 숙고(STRIPS) ──억제──┐
    L2 기억(사례)   ──억제──┤
    L1 되묻기(XCS 2)──억제──┤
    L0 반응(XCS 12) ────────┴──▶ 말(작동기)

선행: Dorigo & Colombetti 의 행동별 LCS · Mahadevan & Connell 의 행동별 보상 — 새 방법이 아니다
(`paper/선행조사/WALP_행동기반.md`).
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, field

from walp import dialog as D
from walp.xcs import XCS, Params

C = 0.3                       # 되물음 비용(사전등록 주 비용) — 효용: 맞음 +1 · 틀림 −1 · 되물음 1−c
L0_N, L1_N = 1600, 400
L1_EPOCHS = 20


def 효용(맞음: bool) -> float:
    return 1.0 if 맞음 else -1.0


def 보상(u: float) -> float:
    """효용(−1..1) → XCS 보상(0..1000)."""
    return 500.0 * (u + 1.0)


def _온도(v: float, 문턱: tuple) -> str:
    return "".join("1" if v >= t else "0" for t in 문턱)


def _원핫(act: "str | None") -> str:
    return "".join("1" if a == act else "0" for a in D.ACTS)


# ================================================================ 센서 — 분류기는 여기로 내려왔다
@dataclass
class 센서:
    성장: object = None           # dialog.성장모형(없으면 그 비트는 0)
    어휘: list = field(default_factory=list)
    손: bool = True               # False: 손 규칙 · 손으로 쓴 낱말 목록을 안 본다(사용자 2026-09-30 "손규칙 없애고")

    def 읽기(self, text: str, r: "dict | None" = None) -> dict:
        r = D._파스(text) if r is None else r
        ga, gc = D.학습기_예측(self.성장, text, r) if self.성장 is not None else (None, 0.0)
        h = D.손규칙(text, r) if self.손 else None
        # L0 은 **센서의 읽음** + 단서·파서 비트(29)만 본다. 글자조각(96)은 성장망 센서 안에서 이미 쓰인다 — 첫 판은 152 비트를 다 넣어
        # L0 이 자기가 읽는 센서보다 못했다(스모크 진단: 성장망 75% · L0 59%, 학습 안에서도 73% — 집단 1599/1600 포화, 2026-09-30)
        겉 = D.비트(D.특징(text, r), self.손)
        손비트 = (_원핫(h) + ("1" if h is None else "0")) if self.손 else ""
        return {"text": text, "r": r, "성장": ga, "성장확신": gc, "손": h, "파서ok": (r or {}).get("status") == "ok",
                "L0비트": 겉 + _원핫(ga) + _온도(gc, (0.5, 0.8)) + 손비트}


# ================================================================ 행동
class 행동:
    이름 = "?"
    층 = 0

    def 행(self, 상태: dict, 아래: dict) -> "dict | None":
        """이 층의 출력(없으면 None). 아래 = 이미 계산된 하위 층 출력들(상위 층은 하위의 출력을 **읽을** 수 있다)."""
        raise NotImplementedError


class 반응(행동):
    """L0 — 답 틀 하나를 골라 실행. 자기 XCS(12 행동)."""
    이름, 층 = "반응", 0

    def __init__(self, x: "XCS | None" = None, seed: int = 7):
        self.x = x or XCS(len(D.ACTS), Params(N=L0_N, p_hash=0.95, p_hash_one=0.4), seed=seed)

    def 예측(self, 상태: dict) -> "tuple[str, list]":
        a, pa = self.x.predict(상태["L0비트"])
        if a < 0:
            return (상태["손"] or "task"), []
        return D.ACTS[a], pa

    def 행(self, 상태, 아래):
        act, pa = self.예측(상태)
        return {"행위": act, "pa": pa}

    def 라벨(self, 상태: dict, act: str) -> None:          # 사람이 고친 말 — 전체 정보
        self.x.step_label_full(상태["L0비트"], D.ACTS.index(act))

    def 결과(self, 상태: dict, act: str, 좋음: bool) -> None:   # 다음 턴 감사/불만 — 밴딧
        self.x.step_bandit(상태["L0비트"], D.ACTS.index(act), 좋음)


def 불확실비트(상태: dict, l0: dict) -> str:
    """L1 의 센서: L0 의 최고 예측 · 1·2위 차 · 성장망 확신 · 일치 · 손 빔 · 파서 · 길이 · L0 가 고른 행위."""
    pa = sorted((p for p in (l0.get("pa") or []) if p is not None), reverse=True)
    top = (pa[0] if pa else 0.0) / 1000.0
    차 = ((pa[0] - pa[1]) if len(pa) > 1 else 0.0) / 1000.0
    n = len(D._정리(상태["text"]))
    return (_온도(top, (0.9, 0.7, 0.5, 0.3)) + _온도(차, (0.5, 0.3, 0.1)) + _온도(상태["성장확신"], (0.9, 0.7, 0.5))
            + ("1" if 상태["손"] is None else "0") + ("1" if 상태["손"] == l0["행위"] else "0")
            + ("1" if 상태["성장"] == l0["행위"] else "0") + ("1" if 상태["파서ok"] else "0")
            + ("1" if n <= 3 else "0") + ("1" if n <= 8 else "0") + ("1" if n <= 20 else "0") + _원핫(l0["행위"]))


class 되묻기(행동):
    """L1 — 되물을지(1) 통과할지(0). 자기 XCS(2 행동). 되물으면 L0 을 억제한다."""
    이름, 층 = "되묻기", 1

    def __init__(self, x: "XCS | None" = None, seed: int = 7):
        self.x = x or XCS(2, Params(N=L1_N, p_hash=0.66), seed=seed)

    def 행(self, 상태, 아래):
        l0 = 아래.get("반응")
        if not l0:
            return None
        a, _ = self.x.predict(불확실비트(상태, l0))
        return {"되물음": l0["행위"]} if a == 1 else None

    def 결과(self, 상태: dict, l0: dict, 물음: bool, u: float) -> None:
        self.x.step_reward(불확실비트(상태, l0), 1 if 물음 else 0, 보상(u))


class 기억(행동):
    """L2 — 사람이 고친 말(사례 기억). 학습 없음."""
    이름, 층 = "기억", 2

    def 행(self, 상태, 아래):
        a = D.기억_고르기(상태["text"])
        return {"행위": a} if a else None


class 숙고(행동):
    """L3 — 여러 걸음 요청이면 STRIPS 계획. 학습 없음."""
    이름, 층 = "숙고", 3

    def 행(self, 상태, 아래):
        from walp import strips
        t = 상태["text"]
        if strips.여러걸음인가(t):
            p = strips.계획(t)
            if p != "REJECT":
                return {"계획": p}
        return None


# ================================================================ 배운 행위 — 사람이 가르친 뜻마다 행동 하나(사전등록 walp/eval/PREREG_행위늘리기.md)
TEACH_θ = 0.5


def 포함률(말_: str, 예문: str) -> float:
    """예문의 글자조각 가운데 이 말에 든 비율 — 되물음 대답 해석(고른것)과 같은 꼴. 뜻이 아니라 **겉**을 본다."""
    h = D._조각(예문)
    return len(D._조각(말_) & h) / len(h) if h else 0.0


@dataclass
class 배운뜻:
    id: str
    예: list
    답: str
    설명: str = ""
    θ: float = TEACH_θ

    def 점수(self, text: str) -> float:
        return max((포함률(text, e) for e in self.예), default=0.0)


class 배움(행동):
    """층 1.5 — 가르친 뜻의 예문과 겉이 닮으면 L0 · L1 을 억제하고 가르친 답을 말한다. 기억 · 숙고가 이것을 억제한다."""
    이름, 층 = "배움", 1.5

    def __init__(self, 뜻들: list):
        self.뜻들 = list(뜻들)

    def 고르기(self, text: str) -> "tuple[배운뜻 | None, float]":
        best, 점 = None, 0.0
        for f in self.뜻들:
            s = f.점수(text)
            if s >= f.θ and s > 점:
                best, 점 = f, s
        return best, 점

    def 행(self, 상태, 아래):
        f, 점 = self.고르기(상태["text"])
        return {"행위": f"배움:{f.id}", "배운": f.id, "답": f.답, "점수": 점} if f else None


def 뜻들읽기(줄들: list, who: "str | None" = None) -> list:
    """원장 → 배운 뜻들. kind=act_new(가르침) · act_example(감사로 더한 예문) · act_tune(켜진 뒤 불만 → θ +0.05).
    쓰기 권한(w)으로 가르친 것은 모두에게, 아니면 가르친 사람에게만."""
    뜻: dict = {}
    for z in sorted(줄들, key=lambda z: z.get("ts", 0)):
        k = z.get("kind")
        if k == "act_new" and z.get("id") and z.get("text") and z.get("답"):
            if z.get("w") or who is None or z.get("who") == who:
                뜻[z["id"]] = 배운뜻(z["id"], [z["text"]], z["답"], z.get("설명", ""))
        elif k == "act_example" and z.get("id") in 뜻 and z.get("text") and z["text"] not in 뜻[z["id"]].예:
            뜻[z["id"]].예.append(z["text"])
        elif k == "act_tune" and z.get("id") in 뜻:
            뜻[z["id"]].θ = round(min(1.0, 뜻[z["id"]].θ + 0.05), 4)
    return list(뜻.values())


# ================================================================ 답 캐시 — LLM(숙고층)이 준 답을 다시 쓴다(사전등록 walp/eval/PREREG_LLM앞단.md)
답캐시켜기 = False           # 봉인 흐름 v1(PREREG_LLM앞단.md): H2 안 섬 — 이웃 묶음에 남의 답을 줬다(6/43). 등록한 규칙대로 끈다
CACHE_θ = 0.75              # 가르친 뜻(0.5)보다 엄하다 — LLM 답은 사실 질문이 많아 겉이 닮은 다른 질문에 틀린 답을 주면 안 된다


class 답캐시(배움):
    """층 1.6 — LLM 에게 물었던 말과 겉이 닮으면(포함률 ≥ θ) 그때의 답을 LLM 없이 다시 쓴다. 기억 · 숙고가 이것을 억제한다."""
    이름, 층 = "답캐시", 1.6

    def 행(self, 상태, 아래):
        f, 점 = self.고르기(상태["text"])
        return {"행위": f"캐시:{f.id}", "배운": f.id, "답": f.답, "점수": 점, "부류": f.설명} if f else None


def 캐시읽기(줄들: list, who: "str | None" = None) -> list:
    """원장 → 답 캐시. kind=llm_answer(물은 말 · 부류 · 답) · llm_tune(다시 쓴 답 뒤 불만 → θ +0.05).
    LLM 답은 사람이 아니라 모형이 준 것이라 사람 사이에 나눠 쓴다(같은 질문의 같은 답) — who 는 받지만 거르지 않는다."""
    표: dict = {}
    for z in sorted(줄들, key=lambda z: z.get("ts", 0)):
        k = z.get("kind")
        if k == "llm_answer" and z.get("id") and z.get("text") and z.get("답"):
            e = 표.get(z["id"])
            if e:
                if z["text"] not in e.예:
                    e.예.append(z["text"])
            else:
                표[z["id"]] = 배운뜻(z["id"], [z["text"]], z["답"], z.get("행위", ""), CACHE_θ)
        elif k == "llm_tune" and z.get("id") in 표:
            표[z["id"]].θ = round(min(1.0, 표[z["id"]].θ + 0.05), 4)
    return list(표.values())


# ================================================================ 버스 — 모두 계산하고, 억제 선이 작동기 입력을 대체한다
기본선 = {"되묻기": {"반응"}, "배움": {"반응", "되묻기"}, "답캐시": {"반응", "되묻기"},
         "기억": {"반응", "되묻기", "배움", "답캐시"}, "숙고": {"반응", "되묻기", "기억", "배움", "답캐시"}}


@dataclass
class 버스:
    센서: 센서
    행동들: list
    선: dict = field(default_factory=lambda: {k: set(v) for k, v in 기본선.items()})

    def 돌기(self, text: str, r: "dict | None" = None) -> dict:
        상태 = self.센서.읽기(text, r)
        출력: dict = {}
        for b in sorted(self.행동들, key=lambda b: b.층):      # 모든 층이 이 턴에 계산한다
            출력[b.이름] = b.행(상태, dict(출력))
        층 = {b.이름: b.층 for b in self.행동들}
        승자 = min((n for n, o in 출력.items() if o is not None), key=lambda n: 층[n], default=None)
        for n in sorted(출력, key=lambda n: 층[n]):            # 작동기 입력은 가장 낮은 층에서 시작 — 선이 이어진 상위만 대체한다
            if 출력[n] is not None and 승자 is not None and 승자 in self.선.get(n, set()):
                승자 = n
        return {"상태": 상태, "출력": 출력, "승자": 승자, "행한것": 출력.get(승자) if 승자 else None}

    def 끊기(self, 위: str, 아래: str) -> None:
        self.선.get(위, set()).discard(아래)

    def 더하기(self, b: 행동, 억제: "set | None" = None) -> None:
        self.행동들.append(b)
        if 억제:
            self.선[b.이름] = set(억제)


# ================================================================ 기르기 — 교차 적합(사전등록 '학습')
def _L0기르기(항목: list, seed: int, epochs: int = D.EPOCHS) -> 반응:
    b = 반응(seed=seed)
    rng = random.Random(seed)
    항목 = list(항목)
    for _ in range(epochs):
        rng.shuffle(항목)
        for 상태, act in 항목:
            b.라벨(상태, act)
    return b


def L1기르기(에피소드: list, c: float = C, seed: int = 7, epochs: int = L1_EPOCHS) -> 되묻기:
    """에피소드 = [(불확실비트, L0 맞음)]. 오프라인이라 두 행동의 효용을 다 안다 — 행동마다 갱신."""
    b = 되묻기(seed=seed)
    rng = random.Random(seed)
    ep = list(에피소드)
    for _ in range(epochs):
        rng.shuffle(ep)
        for bits, 맞음 in ep:
            b.x.step_rewards(bits, {0: 보상(효용(맞음)), 1: 보상(1.0 - c)})
    return b


def 기르기(표본: list, seed: int = 7, c: float = C, 성장만들기=None, 손: bool = True) -> dict:
    """표본 [(글, 행위)] → {센서, 반응, 되묻기, 에피소드, oof}. 2겹 교차 적합: 센서를 한쪽에서 길러 다른 쪽 비트를 만들고,
    L0 도 2겹으로 길러 OOF 예측으로 L1 의 에피소드를 만든다. 마지막 L0 은 OOF 비트 전부로, 시험용 센서는 전부로."""
    성장만들기 = 성장만들기 or (lambda s, sd: D.성장모형.만들기(s, [], sd, 손=손))
    어휘 = D.어휘기르기(표본)
    idx = list(range(len(표본)))
    random.Random(seed).shuffle(idx)
    반 = [idx[: len(idx) // 2], idx[len(idx) // 2:]]
    센서들 = [센서(성장만들기([표본[i] for i in 반[k]], seed), 어휘, 손) for k in (0, 1)]
    상태 = {}
    for k in (0, 1):                                  # 반 k 의 비트는 다른 반(1−k)에서 기른 센서로
        for i in 반[k]:
            상태[i] = 센서들[1 - k].읽기(표본[i][0])
    oof: dict = {}
    에피소드 = []
    for k in (0, 1):                                  # L0 을 반 k 로 길러 반 1−k 에서 예측
        l0 = _L0기르기([(상태[i], 표본[i][1]) for i in 반[k]], seed)
        for i in 반[1 - k]:
            o = l0.행(상태[i], {})
            맞음 = o["행위"] == 표본[i][1]
            oof[i] = {"행위": o["행위"], "맞음": 맞음, "성장확신": 상태[i]["성장확신"]}
            에피소드.append((불확실비트(상태[i], o), 맞음))
    l0 = _L0기르기([(상태[i], 표본[i][1]) for i in idx], seed)
    l1 = L1기르기(에피소드, c, seed)
    return {"센서": 센서(성장만들기(표본, seed), 어휘, 손), "반응": l0, "되묻기": l1, "에피소드": 에피소드,
            "oof": [oof[i] for i in sorted(oof)]}


def 버스짓기(체계: dict, 숙고켜기: bool = True, 기억켜기: bool = True, 뜻들: "list | None" = None,
           캐시: "list | None" = None) -> 버스:
    hs = ([체계["반응"], 체계["되묻기"]] + ([배움(뜻들)] if 뜻들 else []) + ([답캐시(캐시)] if 캐시 else [])
          + ([기억()] if 기억켜기 else []) + ([숙고()] if 숙고켜기 else []))
    return 버스(체계["센서"], hs)


def 문턱고르기(oof: list, c: float = C) -> float:
    """대조 D: 성장망 확신 < θ 이면 묻는다. θ 는 OOF 에서 효용 최대(같으면 작은 θ)."""
    후보 = sorted({0.0, 1.01} | {round(o["성장확신"], 4) for o in oof})
    def u(th):
        return sum((1.0 - c) if o["성장확신"] < th else 효용(o["맞음"]) for o in oof)
    return max(후보, key=lambda th: (u(th), -th))


# ================================================================ 저장
def to_json(체계: dict) -> dict:
    s = 체계["센서"]
    return {"성장": s.성장.to_json() if s.성장 is not None else None, "성장종류": getattr(s.성장, "kind", None), "어휘": s.어휘, "손": s.손,
            "반응": 체계["반응"].x.to_json(), "되묻기": 체계["되묻기"].x.to_json()}


def from_json(d: dict) -> dict:
    종류 = D.NB모형 if d.get("성장종류") == "nb" else D.성장모형
    return {"센서": 센서(종류.from_json(d["성장"]) if d.get("성장") else None, d["어휘"], d.get("손", True)),
            "반응": 반응(XCS.from_json(d["반응"])), "되묻기": 되묻기(XCS.from_json(d["되묻기"]))}


def dumps(체계: dict) -> str:
    return json.dumps(to_json(체계), ensure_ascii=False, sort_keys=True)


# ================================================================ 작동기 — 행한 것을 말로
ACT_설명 = {"task": "물건 찾기", "greet": "인사", "bye": "작별 인사", "thanks": "감사", "about_self": "저에 대한 질문",
            "capability": "제가 할 수 있는 것에 대한 질문", "help": "쓰는 법 묻기", "knowledge": "바깥 지식 질문",
            "complaint": "틀렸다는 말씀", "out_of_scope": "제가 못 하는 부탁", "yes": "'네' 라는 대답", "no": "'아니' 라는 대답"}


def 되물음말(act: str) -> str:
    return (f"혹시 **{ACT_설명.get(act, act)}** 이신가요? 맞으면 `네`, 아니면 `행위 <이름>` 으로 알려 주세요 "
            f"(행위 이름: 찾기 · 인사 · 작별 · 감사 · 자기질문 · 능력질문 · 도움 · 바깥지식 · 불만 · 범위밖 · 네 · 아니).")


def 말(결과: dict) -> "tuple[str, str | None]":
    """(종류, 내용) — 종류: 틀 · 찾기 · 계획 · 되물음 · 없음. 찾기·계획은 부르는 쪽(front)이 시뮬로 실행한다."""
    o = 결과.get("행한것")
    if not o:
        return "없음", None
    if "계획" in o:
        return "계획", o["계획"]
    if "되물음" in o:
        return "되물음", 되물음말(o["되물음"])
    if "배운" in o:
        return "틀", o["답"]
    act = o["행위"]
    if act == "task":
        return "찾기", None
    if act == "knowledge":
        return "지식", None
    return "틀", D.틀[act]


# ================================================================ 대화 — 형식 없이(사용자 2026-09-30 "내가 format 에 맞춰야 해? 내가 자연인데")
# 되물음은 자연어 선택지로 묻고, 사람이 편하게 한 대답을 WALP 가 **자기 센서로** 읽는다(네/아니도 배우는 행위다).
# 답한 뒤에 사람이 불만·아니를 말하면 다음 후보로 다시 묻는다. `행위 <이름>` 은 지름길로만 남는다.
# 선행: Hancock 등(ACL 2019) self-feeding chatbot — 불만이면 자연어 피드백을 청한다(paper/선행조사/WALP_행동기반.md 덧붙임).
ACT_말 = {"task": ["찾기", "찾아", "물건"], "greet": ["인사", "안부"], "bye": ["작별", "간다", "끝"], "thanks": ["감사", "고맙", "칭찬"],
         "about_self": ["너에 대해", "누구", "정체", "자기소개"], "capability": ["할 수 있는", "능력", "기능"],
         "help": ["쓰는 법", "사용법", "도움", "어떻게"], "knowledge": ["지식", "궁금", "물어본", "질문"],
         "complaint": ["불만", "틀렸", "잘못"], "out_of_scope": ["부탁", "해달라", "시킨"], "yes": ["네", "응"], "no": ["아니"]}
대기초 = 180


def 선택지말(acts: list) -> str:
    if len(acts) == 1:
        return f"혹시 **{ACT_설명.get(acts[0], acts[0])}** 이신가요?"
    return "혹시 " + ", 아니면 ".join(f"**{ACT_설명.get(a, a)}**" for a in acts) + " 중 어느 쪽이신가요?"


def 후보들(l0: dict, 뺄: "set | None" = None, k: int = 2) -> list:
    """L0 의 예측 칸 순서로 후보 행위 k 개(뺄 것 제외)."""
    pa = l0.get("pa") or []
    순 = sorted((i for i in range(len(pa)) if pa[i] is not None), key=lambda i: -pa[i])
    out = [l0["행위"]] + [D.ACTS[i] for i in 순]
    seen, res = set(뺄 or ()), []
    for a in out:
        if a not in seen:
            seen.add(a)
            res.append(a)
    return res[:k]


둘다말 = ("둘 다", "둘다", "다 아니", "전부 아니", "아무것도", "그런 거 아니", "그런거 아니", "neither", "none")


def _둘다아님(대답: str) -> bool:
    """'A, 아니면 B?' 라고 **WALP 가 물은 꼴**에 대한 '둘 다 아님' — 자기 질문의 구조를 되짚는 말 목록이다(세상을 해석하는 손 규칙이 아니다)."""
    t = D._정리(대답)
    return any(w in t for w in 둘다말)


def 고른것(대답: str, 선택지: list, l0행위: "str | None") -> "str | None":
    """사람의 자연어 대답 → 선택지 가운데 하나(모르면 None). ① 선택지를 가리키는 말(글자조각 겹침) ② 네 → 첫째 ③ 아니 → 둘째."""
    g = D._조각(대답)
    최고, 점 = None, 0.0
    for a in 선택지:
        for w in ACT_말.get(a, []) + [ACT_설명.get(a, "")]:
            h = D._조각(w)
            if h:
                s = len(g & h) / len(h)
                if s > 점:
                    최고, 점 = a, s
    if 최고 and 점 >= 0.5 and 최고 not in ("yes", "no"):
        return 최고
    if l0행위 == "yes":
        return 선택지[0]
    if l0행위 == "no" and len(선택지) > 1:
        return 선택지[1]
    return None


위로행위 = {"knowledge", "out_of_scope"}      # WALP 가 스스로 못 하는 것 — 답 캐시가 덮지 않으면 숙고층으로
틀행위 = {"greet", "bye", "thanks", "about_self", "capability", "help", "complaint", "yes", "no"}


def 모름(r: dict) -> bool:
    """순서층의 판정(사전등록 고정): 되묻기 층이 흔들려 L0 을 억제했거나, L0 이 WALP 가 못 하는 행위를 골랐다.
    위 층(배움 · 답 캐시 · 기억 · 숙고)이 가져간 말은 모르는 것이 아니다."""
    if r["승자"] == "되묻기":
        return True
    return r["승자"] == "반응" and (r["행한것"] or {}).get("행위") in 위로행위


def 숙고결과(d: dict, text: str, 기록: list) -> dict:
    """숙고층의 답 → 이 턴의 말 + 배울 것(원장 줄). 부류는 L0 · 센서의 라벨로(via=llm), 열린 답만 답 캐시로."""
    import hashlib
    act = d["행위"]
    qid = "q" + hashlib.sha256(D._알맹이(text).encode()).hexdigest()[:8]
    if act in D.ACTS:
        기록.append({"kind": "act_fix", "via": "llm", "act": act, "text": text})
    if act not in D.ACTS or act in 위로행위:
        기록.append({"kind": "llm_answer", "id": qid, "text": text, "행위": act, "답": d["답"],
                    "입력토큰": d.get("입력토큰", 0), "출력토큰": d.get("출력토큰", 0), "모형": d.get("모형", "")})
    기록.append({"kind": "behavior", "text": text, "승자": "숙고", "글": text, "행위": f"llm:{act}",
                "입력토큰": d.get("입력토큰", 0), "출력토큰": d.get("출력토큰", 0)})
    if act == "task":
        return {"종류": "찾기", "내용": None, "행위": "task", "글": text, "기록": 기록}
    if act in 틀행위:
        return {"종류": "틀", "내용": D.틀[act], "행위": act, "글": text, "기록": 기록}
    return {"종류": "숙고", "내용": d["답"], "행위": f"llm:{act}", "글": text, "기록": 기록}


def 대화(버스_: 버스, text: str, who: str, 원장: list, now: float, 숙고=None) -> dict:
    """한 턴. 돌려주는 것: {종류, 내용, 행위, 글(실행할 원래 말), 기록(원장 줄들)}.
    원장 = 이 사람의 최근 kind=behavior 줄들(시간순). 실행(찾기·계획·지식)은 부르는 쪽이 한다.
    숙고 = 숙고층(walp/deliberate.py) — 있으면 "모른다" 를 사람 대신 LLM 에게 묻고 그 답을 배운다. 없으면 예전처럼 되묻는다."""
    앞 = next((z for z in reversed(원장) if z.get("who") == who and now - z.get("ts", 0) <= 대기초), None)
    r = 버스_.돌기(text)
    l0 = r["출력"].get("반응") or {}
    기록 = []
    if 앞 and 앞.get("승자") == "가르침요청":                     # ④ 가르쳐 달라고 한 뒤의 말 = 그 뜻의 답
        if l0.get("행위") in ("no", "bye") and len(D._알맹이(text)) <= 6:
            기록.append({"kind": "behavior", "text": text, "승자": "가르침취소"})
            return {"종류": "틀", "내용": "알겠습니다 — 넘어갈게요.", "행위": None, "글": text, "기록": 기록}
        import hashlib
        nid = "n" + hashlib.sha256((앞["글"] + "\0" + text).encode()).hexdigest()[:8]
        기록.append({"kind": "act_new", "id": nid, "text": 앞["글"], "설명": 앞.get("설명", ""), "답": text})
        기록.append({"kind": "behavior", "text": text, "승자": "배움완료", "행위": f"배움:{nid}"})
        return {"종류": "틀", "내용": f"배웠습니다 — 앞으로 '{앞['글']}' 같은 말에는 \"{text}\" 라고 답할게요.",
                "행위": f"배움:{nid}", "글": text, "기록": 기록}
    if 앞 and 앞.get("선택지"):                                   # ① 되물음에 대한 자연어 대답
        a = None if _둘다아님(text) else 고른것(text, 앞["선택지"], l0.get("행위"))   # '둘 다 아님' 이 먼저('아니' 를 둘째로 읽지 않게)
        if a is None and (_둘다아님(text) or l0.get("행위") in ("no", "complaint") or r["승자"] == "되묻기"):
            기록.append({"kind": "behavior", "text": text, "승자": "가르침요청", "글": 앞["글"], "설명": text})
            return {"종류": "되물음", "내용": f"처음 듣는 뜻인가 봐요. '{앞['글']}' 같은 말에는 제가 뭐라고 답하면 될까요? "
                                               "알려 주시는 말 그대로 배울게요.", "행위": None, "글": 앞["글"], "기록": 기록}
        if a:
            기록.append({"kind": "act_fix", "via": "natural", "act": a, "text": 앞["글"]})
            기록.append({"kind": "behavior", "text": text, "승자": "대답해석", "글": 앞["글"], "행위": a, "되물음결과": "풀림"})
            결 = {"행한것": {"행위": a}}
            종류, 내용 = 말(결)
            return {"종류": 종류, "내용": 내용, "행위": a, "글": 앞["글"], "기록": 기록}
    # 위 층(배움 · 기억 · 숙고)이 이 말을 가져갔으면 불만·감사로 읽지 않는다 — 첫 판은 L0 의 읽음만 보고, 배운 뜻의 말을 불만으로 가로챘다
    아래층이 = r["승자"] in ("반응", "되묻기")
    답했던 = 앞 and 앞.get("글") and 앞.get("승자") != "배움완료" and 아래층이
    if 답했던 and str(앞.get("행위", "")).startswith("배움:") and l0.get("행위") == "thanks":   # 배운 답이 좋았다 → 예문을 더한다
        기록.append({"kind": "act_example", "id": 앞["행위"][3:], "text": 앞["글"]})
    if 답했던 and not 앞.get("선택지") and 앞.get("행위") and l0.get("행위") in ("complaint", "no"):   # ② 답한 뒤의 자연어 불만
        if str(앞["행위"]).startswith("배움:"):
            기록.append({"kind": "act_tune", "id": 앞["행위"][3:]})
        if str(앞["행위"]).startswith("캐시:"):
            기록.append({"kind": "llm_tune", "id": 앞["행위"][3:]})
        옛 = 버스_.돌기(앞["글"])
        선 = 후보들(옛["출력"].get("반응") or {}, 뺄={앞["행위"]}, k=2)
        기록.append({"kind": "behavior", "text": text, "승자": "고쳐묻기", "글": 앞["글"], "선택지": 선, "틀린행위": 앞["행위"]})
        return {"종류": "되물음", "내용": "제가 잘못 알아들었나 봐요. " + 선택지말(선) + " 편하게 말씀해 주세요.",
                "행위": None, "글": 앞["글"], "기록": 기록}
    if 숙고 is not None and 모름(r):                               # ⑤ 순서층: 모른다 → 숙고층(LLM) — 그 답에서 배운다
        d = 숙고.묻기(text)
        if d and "오류" not in d:
            return 숙고결과(d, text, 기록)
    종류, 내용 = 말(r)
    if 종류 == "되물음":                                          # ③ 되묻기 층이 L0 을 억제했다
        선 = 후보들(l0, k=2)
        기록.append({"kind": "behavior", "text": text, "승자": r["승자"], "글": text, "선택지": 선})
        return {"종류": "되물음", "내용": 선택지말(선) + " 편하게 말씀해 주세요.", "행위": None, "글": text, "기록": 기록}
    act = (r["행한것"] or {}).get("행위")
    기록.append({"kind": "behavior", "text": text, "승자": r["승자"], "글": text, "행위": act})
    return {"종류": 종류, "내용": 내용, "행위": act, "글": text, "기록": 기록}


# ================================================================ 배포 — dialog 의 학습기 자리에 들어간다(관문·래칫은 그대로)
class 행동모형:
    """dialog.학습기_기르기 / 승격된집단 이 다루는 꼴. 관문은 L0 의 답(예측)을 봉인 모음에서 잰다.
    되묻기 층(L1)은 봉인 v6 사전등록 실험으로 붙였다(walp/eval/PREREG_행동기반.md)."""
    kind = "behavior"

    def __init__(self, 체: dict):
        self.체 = 체
        self.어휘 = 체["센서"].어휘
        self.규칙수 = len(체["반응"].x.pop)

    @classmethod
    def 만들기(cls, 표본: list, 밴딧: list, seed: int = 7, 손: bool = True) -> "행동모형":
        체 = 기르기(표본, seed=seed, 손=손)
        for t, a, good in 밴딧:                        # 다음 턴 감사/불만 — L0 자기 가치에만(V2)
            if a in D.ACTS:
                체["반응"].결과(체["센서"].읽기(t), a, good)
        return cls(체)

    def 예측(self, text: str, r: "dict | None" = None) -> "tuple[str, float]":
        o = self.체["반응"].행(self.체["센서"].읽기(text, r), {})
        pa = [p for p in (o.get("pa") or []) if p is not None]
        return o["행위"], (max(pa) / 1000.0 if pa else 0.0)

    def 버스(self, 뜻들: "list | None" = None, 캐시: "list | None" = None) -> 버스:
        return 버스짓기(self.체, 뜻들=뜻들, 캐시=캐시)

    def to_json(self) -> dict:
        return to_json(self.체)

    @classmethod
    def from_json(cls, d: dict) -> "행동모형":
        return cls(from_json(d))
