"""WALP 대화 행위 — 찾기 말고도 인사·질문·불만·범위밖 요청을 알아보고 **정직한 틀**로 답한다.

사용자(2026-09-30): "대화 범위 넓히기: 찾기 외에 인사, 질문 같은 대화 행위를 늘립니다" + "XCS 로 진화 시작" — 동시에.
선행조사: `paper/선행조사/WALP_진화대화.md` 의 마지막 보탬(DAMSL · ISO 24617-2 · 단서 규칙 · XCS/UCS).

층(Brooks 식 — 위층이 아래층을 누른다):
    1. 파서가 받아들이면 → 찾기(task). 파서가 가장 믿을 만한 센서다.
    2. **손 규칙(W0)** — 단서 낱말 부류 + 파서 상태. 이 파일의 `손규칙` 은 **모음을 보기 전에** 썼고 먼저 커밋했다.
    3. 손 규칙이 아무것도 못 고르면 → **XCS 집단**(walp/xcs.py)이 고른다 — 봉인 모음 관문을 지나 승격된 것만.
    4. 그래도 없으면 → 예전처럼 파서의 설명(모르는 말 · 되물음).

답은 행위마다 **손으로 쓴 틀**이다 — 말을 지어내지 않는다. 바깥지식은 LLM 없는 검색 경로로 넘긴다.

행위 12개: task greet bye thanks about_self capability help knowledge complaint out_of_scope yes no
"""
from __future__ import annotations

import re

ACTS = ["task", "greet", "bye", "thanks", "about_self", "capability", "help", "knowledge", "complaint",
        "out_of_scope", "yes", "no"]

# ---------------------------------------------------------------- 특징(이진) — 단서 낱말 부류 + 파서 상태 + 겉모양
# 부류 이름 → 부분 문자열들(소문자 · 공백 뺀 글에서도 찾는다). 손으로 적었다(W0). XCS 는 이 비트들 위에서 규칙을 진화시킨다.
단서 = {
    "greet": ["안녕", "하이", "헬로", "반가", "좋은아침", "굿모닝", "hello", "hey", "방가", "ㅎㅇ", "하잉", "좋은 아침"],
    "bye": ["잘가", "잘 가", "잘있", "잘 있", "바이", "다음에", "또봐", "또 봐", "수고", "bye", "굿나잇", "잘자", "see you",
            "ㅂㅂ", "나갈게", "간다", "들어가"],
    "thanks": ["고마", "감사", "땡큐", "thank", "thx", "잘했", "최고", "굿", "nice", "good", "짱", "훌륭", "대박", "좋네",
               "좋아요", "좋았"],
    "you": ["너", "넌", "니가", "네가", "당신", "you", "walp", "왈프", "봇", "bot"],
    "who": ["누구", "이름", "정체", "만들었", "만든", "개발", "who", "name", "llm", "gpt", "인공지능", " ai", "ai야", "사람이",
            "로봇이", "made", "chatgpt", "claude"],
    "can": ["할 수", "할수", "할 줄", "할줄", "가능", "되나", "돼?", "되니", "can you", "able", "뭘 할", "뭐 할", "뭐할",
            "뭘할", "기능", "무슨 일", "할 줄 아", "could you", "can i"],
    "help": ["어떻게 써", "어떻게써", "사용법", "쓰는 법", "쓰는법", "명령어", "도움말", "도와", "help", "how to use",
             "how do i", "사용 방법", "사용방법", "뭐라고 말", "뭐라고 치", "뭐라고 해", "예시", "usage"],
    "what": ["뭐야", "뭔가", "무엇", "뭐지", "뭐임", "what is", "what's", "whats", "알려줘", "알려 줘", "설명", "이란", "란?",
             "의미", "뜻이", "뜻은", "뭔데"],
    "wh": ["왜", "어떻게", "언제", "어디", "얼마", "몇", "how", "why", "when", "where", "which", "누가"],
    "complain": ["틀렸", "틀린", "틀리", "아니잖", "그게 아니", "그게아니", "이상해", "이상한", "답답", "뭔소리", "뭔 소리", "엉뚱",
                 "별로", "짜증", "wrong", "안되잖", "제대로", "에휴", "하아", "못 알아", "못알아", "멍청", "바보", "쓰레기", "왜 이래",
                 "왜이래", "useless", "stupid"],
    "find": ["찾아", "찾기", "찾을", "찾고", "찾어", "찾자", "find", "locate", "look for", "어디 있", "어딨", "어디있", "가져와",
             "찾아봐", "search for"],
    "obj": ["카드", "열쇠", "키 ", "컵", "머그", "상자", "박스", "card", "key", "cup", "mug", "box", "지갑", "잔"],
    "color": ["빨간", "파란", "초록", "검은", "빨강", "파랑", "검정", "red", "blue", "green", "black"],
    "recommend": ["추천", "골라", "뭐 먹", "뭐먹", "메뉴", "불러", "노래", "시 써", "써줘", "써 줘", "그려", "만들어줘", "예약",
                  "정리해", "농담", "joke", "위로", "recommend", "write", "사줘", "주문", "계산해", "번역", "요약해", "짜줘",
                  "sing", "draw", "book a"],
    "world": ["날씨", "뉴스", "인구", "수도", "주가", "역사", "원리", "방식", "정책", "개념", "weather", "capital", "population",
              "뜻", "정의", "차이"],
    "request": ["해줘", "해 줘", "해주세요", "해줄래", "줘", "주세요", "please", "부탁", "plz"],
}
네말 = {"네", "넵", "예", "응", "어", "맞아", "맞아요", "맞다", "그래", "그래요", "yes", "yep", "yeah", "y", "ok", "okay", "오케이",
       "ㅇㅇ", "ㅇ", "웅", "옙", "좋아", "그럼", "당연"}
아니말 = {"아니", "아니요", "아뇨", "아니야", "아니오", "틀려", "no", "nope", "n", "ㄴㄴ", "ㄴ", "놉", "노", "싫어", "아냐"}

_파서상태 = ["p_ok", "p_unknown", "p_question", "p_noverb", "p_noref", "p_unsupported_verb", "p_negation", "p_ambiguous"]
_겉 = ["ends_q", "short", "ascii", "exact_yes", "exact_no"]
FEATURES = list(단서) + _파서상태 + _겉
# 손 없는 특징(사용자 2026-09-30 "손규칙 없애고 … 내가 자연인데"): 손으로 쓴 낱말 목록(단서 · 네말 · 아니말 · 물음 어미)을 뺀다.
# 남는 것은 작동기(찾기 파서)의 읽음 · '?' 글자 · 길이 · 영문 비율뿐이고, 나머지는 대화에서 배운 글자조각(어휘기르기)과 ESN 이 맡는다.
FEATURES_무손 = _파서상태 + ["q_mark", "short", "ascii"]


def _정리(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "").strip().lower())


def _알맹이(t: str) -> str:
    return re.sub(r"[\s.!~?,;:'\"`^*()\[\]…ㅋㅎㅠㅜ]+", "", _정리(t)).replace("😊", "")


def 특징(text: str, r: "dict | None" = None) -> dict:
    """글과 파서 결과 → {특징이름: 0/1}. 파서 결과가 없으면 파서 비트는 0."""
    t = _정리(text)
    tt = t.replace(" ", "")
    f = {k: int(any((w in t) or (w.replace(" ", "") in tt and len(w.replace(" ", "")) > 1) for w in ws))
         for k, ws in 단서.items()}
    # 'hi' · 'ai' 처럼 짧은 영어는 낱말 경계로만
    if re.search(r"\bhi\b", t):
        f["greet"] = 1
    if re.search(r"\bai\b", t):
        f["who"] = 1
    r = r or {}
    st, why = r.get("status"), r.get("reason", "")
    f["p_ok"] = int(st == "ok" or bool(r.get("parsed")))
    f["p_unknown"] = int(why == "unknown_word")
    f["p_question"] = int(why == "question")
    f["p_noverb"] = int(why == "no_verb")
    f["p_noref"] = int(why in ("no_referent", "no_target"))
    f["p_unsupported_verb"] = int(why == "unsupported_verb")
    f["p_negation"] = int(why == "negation")
    f["p_ambiguous"] = int(st == "ambiguous")
    알 = _알맹이(text)
    f["q_mark"] = int("?" in t)
    f["ends_q"] = int("?" in t or bool(re.search(r"(까|니|냐|나요|는지|ㄴ가|인가)$", t)))
    f["short"] = int(len(알) <= 4)
    글자 = [c for c in 알 if c.isalpha()]
    f["ascii"] = int(bool(글자) and sum(c.isascii() for c in 글자) > len(글자) / 2)
    f["exact_yes"] = int(알 in 네말)
    f["exact_no"] = int(알 in 아니말)
    return f


def 비트(f: dict, 손: bool = True) -> str:
    return "".join(str(f.get(k, 0)) for k in (FEATURES if 손 else FEATURES_무손))


# ---------------------------------------------------------------- 손 규칙(W0) — 모음을 보기 전에 썼다
def 손규칙(text: str, r: "dict | None" = None) -> "str | None":
    """우선순위 차례로. 아무것도 못 고르면 None(그 자리를 XCS 가 채운다)."""
    f = 특징(text, r)
    if f["p_ok"]:
        return "task"
    if f["exact_yes"]:
        return "yes"
    if f["exact_no"]:
        return "no"
    if f["complain"]:
        return "complaint"
    if f["help"]:
        return "help"
    if f["thanks"] and not f["find"] and len(_알맹이(text)) <= 12:
        return "thanks"
    if f["greet"] and not f["find"]:
        return "greet"
    if f["bye"] and not f["find"]:
        return "bye"
    if f["who"] and (f["you"] or f["ends_q"]):
        return "about_self"
    if f["can"]:
        return "capability"
    if f["find"] or (f["obj"] and (f["p_unknown"] or f["color"])):
        return "task"
    if f["recommend"]:
        return "out_of_scope"
    if (f["what"] or f["world"]) and (f["wh"] or f["ends_q"] or f["what"]):
        return "knowledge"
    return None


# ---------------------------------------------------------------- 답 틀 — 지어내지 않는다
HINT = "예: `파란 비자카드 찾아줘` · `빨간 컵 찾아줘, 선반은 피해서` · `find the black key within 200 ticks`"
틀 = {
    "greet": "안녕하세요 — WALP 입니다. LLM 없이 규칙과 탐색으로 도는 작은 로봇 두뇌예요. 시뮬레이션 사무실에서 물건을 찾아 드립니다.\n" + HINT,
    "bye": "안녕히 가세요. 나눈 말은 가명으로 기록되어, WALP 가 모르는 말을 배우고 규칙을 고르는 데 쓰입니다.",
    "thanks": "고맙습니다 — 좋았다는 신호로 적어 둘게요.",
    "about_self": ("저는 **WALP** 입니다. 학습된 가중치도 LLM 도 GPU 도 쓰지 않고, 손으로 쓴 규칙 · 탐색 · 대화에서 배운 사전으로 돕니다.\n"
                   "시뮬레이션 사무실에서 물건을 찾고, 모르는 말은 고쳐 말하신 것을 보고 뜻을 물어 배웁니다."),
    "capability": ("할 수 있는 것: **물건 찾기**(카드·열쇠·컵·상자 / 색 / 비자·마스터 / 피할 곳 / 기한) · 모르는 말 배우기 · "
                   "바깥 지식은 출처가 붙은 검색(`!walp 찾아보기`).\n"
                   "못 하는 것: 추천 · 글쓰기 · 의견 · 실제 기기 조작 — LLM 없이 지어낼 수 없어서 하지 않습니다."),
    "help": ("찾을 물건을 한 문장으로 말씀해 주세요. " + HINT + "\n"
             "모르는 말이 나오면 다른 말로 고쳐 말해 주시면, 뜻을 여쭤보고 배웁니다. `결과` 로 지금까지의 사용성 집계를 봅니다."),
    "complaint": ("죄송합니다 — 무엇이 틀렸는지 한 줄로 알려 주시면(예: '빨간 게 아니라 파란 컵') 다시 하겠습니다.\n"
                  "틀렸다는 신호로 적어 두었습니다. 제가 행위를 잘못 알아들었다면 `행위 <이름>` 으로 고쳐 주세요."),
    "out_of_scope": ("그건 못 합니다 — 저는 물건 찾기만 하고, 추천 · 창작 · 의견은 LLM 없이 지어낼 수 없어 하지 않습니다.\n" + HINT),
    "yes": "무엇에 대한 대답인지 모르겠어요 — 지금 제가 여쭤본 것이 없습니다.",
    "no": "무엇에 대한 대답인지 모르겠어요 — 지금 제가 여쭤본 것이 없습니다.",
}


# ================================================================ XCS 로 빈자리 채우기 — 원장 재생 · 관문 · 승격
import json as _json          # noqa: E402
import math as _math          # noqa: E402
import os as _os              # noqa: E402
import time as _time          # noqa: E402
from pathlib import Path as _Path   # noqa: E402

HERE = _Path(__file__).resolve().parent
# 학습 = 흉내 원장 + **열린** v1 시험 모음(관문 v1 에서 틀린 예를 보았으므로 더는 봉인이 아니다 → 학습 쪽으로 옮긴다).
# 2026-09-30 성장망 실험(PREREG_성장망.md) 뒤: 열린 v2 · v3 도 학습 쪽, 관문은 봉인 v4(그 실험이 한 번 보았다).
# 층 순서·ESN 실험(PREREG_층순서_ESN_숙고기.md) 뒤: 열린 v4 도 학습 쪽, 관문은 봉인 v5(그 실험이 한 번 보았다).
TRAINS = [HERE / "eval" / f for f in ("dialog_act_train.tsv", "dialog_act_test.tsv", "dialog_act_test_v2.tsv",
                                      "dialog_act_test_v3.tsv", "dialog_act_test_v4.tsv")]
TEST = HERE / "eval" / "dialog_act_test_v5.tsv"
TEST_SHA = "fed5eb66e38d897ea0ea86580b4c27c41ace65404ae50e110fe8110e6580cb8d"
EPOCHS = 60                  # 학습 모음 2겹 교차검증으로 골랐다(봉인 모음 안 봄): 지도형 전체 갱신 · 60 · N=1600
K_어휘 = 96                  # 원장에서 기르는 글자 조각 특징 수
CONF = 500.0                 # XCS 가 고른 행동의 예측 보상이 이보다 낮으면 고르지 않는다(모른다고 한다)
CONF_OVR = 900.0             # 손 규칙을 **덮으려면** 이만큼 확신해야 한다(덮기 모드 — 관문에서 따로 잰다)
ALPHA = 0.05
# 같은 봉인 모음으로 관문을 **되풀이해** 보면(자동 진화) 언젠가 운 좋은 집단이 통과한다 — α 소비로 막는다:
# k 번째 관문의 α_k = 0.05·(6/π²)/k² → 무한히 봐도 거짓 승격 확률 합 ≤ 0.05. 이 모음은 저자가 이미 두 번 보았다(v2 관문 ·
# 자동 진화 배선 확인 2026-09-30) — 그 둘도 센다.
BASE_LOOKS = 2               # 봉인 v5: 층 순서·ESN 사전등록 실험 1 + 저자의 설치 시험(첫 실행 자동 진화) 1
USER_HOLD = 3                # 사람이 고친 말의 1/3 은 관문용으로 떼어 둔다(학습에 안 씀) — 사용자 말투에서 나아졌나
AUTO_N = 20                  # 마지막 관문 뒤 새 신호(고침 · 다음 턴 감사/불만)가 이만큼 쌓이면 진화를 저절로 돌린다
SIM = 0.75                   # 고친 말과 이만큼 비슷하면(글자조각 자카드) 같은 말로 본다
_캐시: dict = {}


def _상태():
    from walp import usability
    return usability.상태자리() / "walp_xcs.json"


def _파스(text: str) -> dict:
    if text not in _캐시:
        from walp import front
        _캐시[text] = front.cli("parse", text)
    return _캐시[text]


def 모음(path) -> "list[tuple[str, str]]":
    out = []
    for line in open(path, encoding="utf-8"):
        if not line.strip() or line.startswith("#"):
            continue
        act, _, text = line.rstrip("\n").partition("\t")
        if act in ACTS and text:
            out.append((act, text))
    return out


def _조각(text: str) -> set:
    """글자 조각: 공백·문장부호 뺀 소문자의 글자 2-그램 + 3글자 이하 글 전체. 한국어 조사·오타에 덜 흔들린다."""
    a = _알맹이(text)
    g = {a[i:i + 2] for i in range(len(a) - 1)}
    if 0 < len(a) <= 3:
        g.add("=" + a)
    return g


def 어휘기르기(표본: list, k: int = K_어휘) -> list:
    """재생 표본에서 행위를 가장 잘 가르는 조각 k 개 — 상호정보량(계수의 닫힌꼴, W1). 두 글 이상에 나온 것만."""
    n = len(표본)
    if not n:
        return []
    라 = {}
    for _, a in 표본:
        라[a] = 라.get(a, 0) + 1
    칸: dict = {}
    for t, a in 표본:
        for g in _조각(t):
            칸.setdefault(g, {}).setdefault(a, 0)
            칸[g][a] += 1
    점수 = []
    for g, by in 칸.items():
        df = sum(by.values())
        if df < 2:
            continue
        mi = 0.0
        for a, na in 라.items():
            for has in (1, 0):
                nga = by.get(a, 0) if has else na - by.get(a, 0)
                ng = df if has else n - df
                if nga > 0:
                    mi += nga / n * _math.log(nga * n / (ng * na))
        점수.append((mi, g))
    점수.sort(key=lambda z: (-z[0], z[1]))
    return [g for _, g in 점수[:k]]


def 입력(text: str, r: "dict | None", 어휘: list, 손: bool = True) -> str:
    g = _조각(text)
    return 비트(특징(text, r), 손) + "".join("1" if w in g else "0" for w in 어휘)


def 사용자봉인(text: str) -> bool:
    """사람이 고친 말 가운데 관문용으로 떼어 둘 것 — 글의 알맹이 해시로 정한다(결정적, 학습·관문이 같은 답을 낸다)."""
    import hashlib
    return int(hashlib.sha256(_알맹이(text).encode()).hexdigest(), 16) % USER_HOLD == 0


def 재생표본(줄들: list) -> "tuple[list, list, list]":
    """원장 → (라벨 표본 [(글, 행위)], 밴딧 표본 [(글, 행위, 좋음)], 사용자 관문 [(행위, 글)]). 센서–센서:
      · run/interpret 이 받아들여짐            → 그 글은 task          (파서가 라벨)
      · act_fix(`행위 <이름>`)                 → 그 글은 그 행위        (사람이 라벨 — ITL). 1/3 은 관문용으로 뗀다
      · dialog 다음 3분 안의 같은 사람의 감사  → 앞 행위 좋음(밴딧)
      · dialog 다음 3분 안의 같은 사람의 불만  → 앞 행위 나쁨(밴딧)"""
    라벨: dict = {}
    고침: dict = {}
    밴딧 = []
    by: dict = {}
    for z in sorted(줄들, key=lambda z: z.get("ts", 0)):
        k = z.get("kind")
        if k in ("run", "interpret") and z.get("status") == "ok" and z.get("text"):
            라벨[z["text"]] = "task"
        elif k == "act_fix" and z.get("act") in ACTS and z.get("text"):
            고침[z["text"]] = z["act"]
        elif k == "dialog" and z.get("act") in ACTS:
            앞 = by.get(z.get("who"))
            if 앞 and z.get("ts", 0) - 앞.get("ts", 0) <= 180 and 앞.get("text") and 앞.get("act") in ACTS:
                if z["act"] == "thanks":
                    밴딧.append((앞["text"], 앞["act"], True))
                elif z["act"] == "complaint":
                    밴딧.append((앞["text"], 앞["act"], False))
            by[z.get("who")] = z
    사용자시험 = [(a, t) for t, a in 고침.items() if 사용자봉인(t)]
    떼 = {t for _, t in 사용자시험}
    for t, a in 고침.items():
        if t not in 떼:
            라벨[t] = a
    밴딧 = [b for b in 밴딧 if b[0] not in 떼]
    return list(라벨.items()), 밴딧, 사용자시험


def 훈련(라벨표본: list, 밴딧표본: list, seed: int = 7, epochs: int = EPOCHS, ga: bool = True, 어휘: "list | None" = None,
        손: bool = True):
    """XCS 집단(어휘 포함). 어휘도 재생 표본에서 자란다 — 대화가 늘면 특징도 는다.
    ga=False 는 진화 효과 실험의 대조(덮기·삭제만, GA 끔 — walp/eval/PREREG_진화효과.md)."""
    from walp.xcs import XCS, Params
    어휘 = 어휘기르기(라벨표본) if 어휘 is None else 어휘
    x = XCS(len(ACTS), Params(N=1600, p_hash=0.95, p_hash_one=0.4, theta_ga=25 if ga else 10 ** 12), seed=seed)
    rng = __import__("random").Random(seed)
    data = [(입력(t, _파스(t), 어휘, 손), ACTS.index(a)) for t, a in 라벨표본]
    for e in range(epochs):
        rng.shuffle(data)
        for b, a in data:
            x.step_label_full(b, a)
    for t, a, good in 밴딧표본:
        x.step_bandit(입력(t, _파스(t), 어휘, 손), ACTS.index(a), good)
    x.어휘 = 어휘
    x.손 = 손
    return x


def xcs_예측(x, text: str, r: "dict | None" = None) -> "tuple[str | None, float]":
    a, pa = x.predict(입력(text, r if r is not None else _파스(text), getattr(x, "어휘", []), getattr(x, "손", True)))
    if a < 0 or pa[a] is None:
        return None, 0.0
    return ACTS[a], pa[a]


# ---------------------------------------------------------------- 학습기 — 채움 자리에 무엇이 앉나(XCS · 성장망 · NB)
# 어느 것을 쓰는지는 사전등록 실험의 결정 규칙이 정한다(walp/eval/PREREG_성장망.md). 셋 다 같은 관문을 지나야 승격된다.
# 성장망 사전등록 실험(봉인 v4)의 결정 규칙 1: 손+성장망이 손만(36/0)·손+다수(30/5)를 이기고 NB 에 지지 않음(13/5) → 성장망.
# 정직하게: 진화의 몫(G2)도 성장의 몫(G3, 은닉 0~3개)도 서지 않았다 — 일하는 것은 사실상 특징 위의 소프트맥스 회귀다.
LEARNER = "behavior"          # 행동 기반(포섭) — 봉인 v6 사전등록 결과로 배포(walp/eval/PREREG_행동기반.md). 예전: "grow"
# 층 순서 · ESN — 사전등록 A(봉인 v5)의 결정 규칙이 정한다(walp/eval/PREREG_층순서_ESN_숙고기.md).
# 봉인 v5 결과: O1 25/1 · O2 62/12 · E1 9/0 · E2 6/0 — 넷 다 섬 → 학습기먼저 + ESN.
ORDER = "학습기먼저"        # 손먼저: 손 규칙 → 빈자리만 학습기 · 학습기먼저: 학습기(확신 ≥0.5) → 모자라면 손 규칙
USE_ESN = True


class 성장모형:
    """성장망(walp/grownet.py) — XCS(GA) 집단의 조건을 후보로, 남은 오차를 줄이는 것만 은닉 단위로 붙는다."""
    kind = "grow"

    def __init__(self, net, 어휘: list, esn_seed: "int | None" = None, 손: bool = True):
        from walp import grownet as G
        self.net, self.어휘, self.손 = net, 어휘, 손
        self.esn_seed = esn_seed
        self.esn = G.ESN(esn_seed) if esn_seed is not None else None
        self.규칙수 = len(net.단위)

    @classmethod
    def 만들기(cls, 표본: list, 밴딧: list, seed: int = 7, esn: "bool | None" = None, 손: bool = True) -> "성장모형":
        from walp import grownet as G
        esn = USE_ESN if esn is None else esn
        x = 훈련(표본, 밴딧, seed=seed, 손=손)
        후보 = G.후보뽑기(x, 300)
        n_bits = len(입력("x", {}, x.어휘, 손))
        E = G.ESN(seed) if esn else None
        data = [(G.비트수(입력(t, _파스(t), x.어휘, 손)), E.상태(t) if E else None, ACTS.index(a)) for t, a in 표본]
        net = G.성장망(n_bits, len(ACTS), 후보, seed, esn_n=E.n if E else 0).fit(data)
        net.후보 = [net.후보[u] for u in net.단위]          # 붙은 것만 남긴다(저장이 작아진다)
        net.단위 = list(range(len(net.후보)))
        return cls(net, x.어휘, seed if esn else None, 손)

    def 예측(self, text: str, r: "dict | None" = None) -> "tuple[str, float]":
        from walp import grownet as G
        e = self.esn.상태(text) if self.esn else None
        p = self.net.proba(G.비트수(입력(text, r if r is not None else _파스(text), self.어휘, getattr(self, "손", True))), e)
        k = max(range(len(ACTS)), key=lambda i: p[i])
        return ACTS[k], p[k]

    def 설명(self) -> list:
        return self.net.설명(FEATURES + self.어휘)

    def to_json(self) -> dict:
        n = self.net
        return {"n_bits": n.n_bits, "후보": [c for c, _, _ in n.후보], "W": n.W, "b": n.b, "어휘": self.어휘,
                "esn_seed": self.esn_seed, "esn_n": n.esn_n, "손": self.손}

    @classmethod
    def from_json(cls, d: dict) -> "성장모형":
        from walp import grownet as G
        후보 = [(c, *G.조건수(c)) for c in d["후보"]]
        net = G.성장망(d["n_bits"], len(ACTS), 후보, 0, 최대=0, esn_n=d.get("esn_n", 0))
        net.단위 = list(range(len(후보)))
        net.W, net.b = d["W"], d["b"]
        return cls(net, d["어휘"], d.get("esn_seed"), d.get("손", True))


class NB모형:
    """베르누이 나이브 베이즈 — 셈의 닫힌꼴(W1)."""
    kind = "nb"
    규칙수 = 0

    def __init__(self, nb, 어휘: list):
        self.nb, self.어휘 = nb, 어휘

    @classmethod
    def 만들기(cls, 표본: list, 밴딧: list, seed: int = 7) -> "NB모형":
        from walp.evotest import NB
        어휘 = 어휘기르기(표본)
        bits = [(입력(t, _파스(t), 어휘), ACTS.index(a)) for t, a in 표본]
        return cls(NB(bits, len(bits[0][0])), 어휘)

    def 예측(self, text: str, r: "dict | None" = None) -> "tuple[str, float]":
        p = self.nb.proba(입력(text, r if r is not None else _파스(text), self.어휘))
        k = max(range(len(ACTS)), key=lambda i: p[i])
        return ACTS[k], p[k]

    def to_json(self) -> dict:
        return {"lp": self.nb.lp, "l1": self.nb.l1, "l0": self.nb.l0, "어휘": self.어휘}

    @classmethod
    def from_json(cls, d: dict) -> "NB모형":
        from walp.evotest import NB
        nb = NB.__new__(NB)
        nb.lp, nb.l1, nb.l0 = d["lp"], d["l1"], d["l0"]
        return cls(nb, d["어휘"])


def 학습기_기르기(표본: list, 밴딧: list, seed: int = 7, kind: "str | None" = None):
    kind = kind or LEARNER
    if kind == "behavior":
        from walp import behavior as B
        return B.행동모형.만들기(표본, 밴딧, seed)
    if kind == "grow":
        return 성장모형.만들기(표본, 밴딧, seed)
    if kind == "nb":
        return NB모형.만들기(표본, 밴딧, seed)
    return 훈련(표본, 밴딧, seed=seed)


def 학습기_예측(m, text: str, r: "dict | None" = None) -> "tuple[str | None, float]":
    """(행위, 확신 0..1). XCS 는 예측 보상/1000."""
    if hasattr(m, "예측"):
        return m.예측(text, r)
    a, c = xcs_예측(m, text, r)
    return a, c / 1000.0


def xcs_고르기(x, text: str, r: "dict | None" = None) -> "str | None":
    a, c = xcs_예측(x, text, r)
    return a if a and c >= CONF else None


def _부호검정(이김: int, 짐: int) -> float:
    n = 이김 + 짐
    if n == 0:
        return 1.0
    return sum(_math.comb(n, k) for k in range(이김, n + 1)) / 2 ** n


def _모드들(x, text: str, 다수: str) -> dict:
    r = _파스(text)
    h = 손규칙(text, r)
    xa, xc = 학습기_예측(x, text, r)
    xs = xa if xa and xc >= CONF / 1000 else None
    if (r or {}).get("status") == "ok":
        h = h or "task"
    fill = (xs or h) if ORDER == "학습기먼저" else (h or xs)
    return {"손만": h, "손+다수": h or 다수, "채움": fill, "덮기": xa if (xa and xc >= CONF_OVR / 1000) else fill, "XCS만": xs}


def _재기(x, 시험: list, 다수: str) -> dict:
    맞 = {k: 0 for k in ("손만", "손+다수", "채움", "덮기", "XCS만")}
    쌍 = {k: [0, 0] for k in ("채움vs손만", "채움vs손+다수", "덮기vs손만", "덮기vs손+다수", "덮기vs채움")}
    빈자리 = 채움 = 채움맞음 = 덮음 = 덮음맞음 = 0
    틀림: list = []
    for act, text in 시험:
        m = _모드들(x, text, 다수)
        for k in 맞:
            맞[k] += m[k] == act
        for key in 쌍:
            a, b = key.split("vs")
            if (m[a] == act) != (m[b] == act):
                쌍[key][0 if m[a] == act else 1] += 1
        if m["손만"] is None:
            빈자리 += 1
            채움 += m["채움"] is not None
            채움맞음 += m["채움"] == act
        if m["덮기"] != m["채움"]:
            덮음 += 1
            덮음맞음 += m["덮기"] == act
        if m["채움"] != act and len(틀림) < 25:
            틀림.append({"글": text, "정답": act, "손": m["손만"], "XCS": m["XCS만"]})
    n = len(시험) or 1
    return {"n": len(시험), "정확도": {k: round(v / n, 3) for k, v in 맞.items()}, "맞은수": 맞, "쌍대_이김_짐": 쌍,
            "p_단측": {k: round(_부호검정(*v), 5) for k, v in 쌍.items()}, "손규칙_빈자리": 빈자리, "XCS_채움": 채움,
            "XCS_채움_맞음": 채움맞음, "XCS_덮음": 덮음, "XCS_덮음_맞음": 덮음맞음, "틀린_예": 틀림}


def α(k: int) -> float:
    return ALPHA * 6 / (_math.pi ** 2) / (k * k)


def 관문(x, 시험: list, 다수: str, 사용자시험: "list | None" = None, 유의: float = ALPHA) -> dict:
    """봉인 모음에서 모드 넷(손만 · 손+다수 · 채움 · 덮기)을 잰다.
      채움 승격 = 채움이 손만과 손+다수를 **둘 다** 유의하게(단측 부호검정 p<0.05) 이김
      덮기 승격 = 덮기가 손만과 손+다수를 둘 다 유의하게 이기고, 채움에 지지 않음
      그리고 사람이 고친 말 가운데 떼어 둔 것(사용자 관문, 5개 이상일 때)에서 고른 모드가 손만에 지지 않아야 한다."""
    g = _재기(x, 시험, 다수)
    ok = lambda k: g["쌍대_이김_짐"][k][0] > g["쌍대_이김_짐"][k][1] and g["p_단측"][k] < 유의   # noqa: E731
    채움승격 = ok("채움vs손만") and ok("채움vs손+다수")
    덮기승격 = ok("덮기vs손만") and ok("덮기vs손+다수") and g["쌍대_이김_짐"]["덮기vs채움"][0] >= g["쌍대_이김_짐"]["덮기vs채움"][1]
    u = _재기(x, 사용자시험 or [], 다수)
    u_ok = {}
    for 모드 in ("채움", "덮기"):
        w, l = u["쌍대_이김_짐"][f"{모드}vs손만"]
        u_ok[모드] = u["n"] < 5 or w >= l
    모드 = "덮기" if 덮기승격 and u_ok["덮기"] else ("채움" if 채움승격 and u_ok["채움"] else None)
    g.update({"유의수준": 유의, "다수행위": 다수, "채움승격": 채움승격, "덮기승격": 덮기승격, "모드": 모드, "승격": 모드 is not None,
              "사용자관문": {k: v for k, v in u.items() if k != "틀린_예"}, "사용자관문_통과": u_ok})
    return g


U_MIN = 5                    # 떼어 둔 사람 고침이 이만큼 있으면 래칫의 주 기준이 그것이 된다(PREREG_래칫_사용자기준.md)
V5_여유 = 0.01               # 사용자 기준으로 바꿀 때 봉인 v5 에서 허용하는 퇴행(정확도 1%p) — 퇴행을 '못 찾음' 이지 비열등 증명이 아니다


def _맞대기(새, 새모드: str, 옛, 옛모드: "str | None", 시험: list, 다수: str) -> list:
    """[새 이김, 새 짐] — 옛이 None 이면 손만과 맞댄다."""
    이김 = 짐 = 0
    for act, text in 시험:
        a = _모드들(새, text, 다수)[새모드] == act
        b = _모드들(옛, text, 다수)["덮기" if 옛모드 == "덮기" else "채움"] == act if 옛 is not None else _모드들(새, text, 다수)["손만"] == act
        이김 += a and not b
        짐 += b and not a
    return [이김, 짐]


def 교체할까(g: dict, 새, 옛, 옛모드: "str | None", 봉인: list, 다수: str,
            사용자시험: "list | None" = None) -> "tuple[bool, str, list, str | None]":
    """래칫. 돌려주는 것: (바꾸나, 까닭, [새 이김, 새 짐](봉인, 옛에 대해), 새 모드).
      · 승격본 없음 → 관문을 지나야 처음 승격(사람 고침만으로는 처음 승격하지 않는다)
      · 승격본 있음 + 떼어 둔 사람 고침 ≥ U_MIN → **사람 고침에서 이기고**(R1) · 봉인 v5 에서 1%p 넘게 안 떨어지고(R2) ·
        손만보다 못하지 않을 때(R3) 바꾼다. 관문(α_k 유의)은 요구하지 않는다 — 사람 신호만으로는 거의 안 열리기 때문
        (사용자 결정 2026-09-30 "2번으로 지어", walp/eval/PREREG_래칫_사용자기준.md)
      · 승격본 있음 + 사람 고침 < U_MIN → 관문 통과 + 봉인에서 승격본에 지지 않음
    첫 판은 관문 결과와 상관없이 상태 파일을 덮어써서, 떨어지면 잘 쓰던 승격본까지 꺼졌다."""
    if 옛 is None:
        if not g.get("승격"):
            return False, "관문 미통과", [0, 0], None
        return True, "관문 통과 — 처음 승격", [0, 0], g["모드"]
    사용자시험 = 사용자시험 or []
    if len(사용자시험) >= U_MIN:
        모드 = g.get("모드") or ("덮기" if 옛모드 == "덮기" else "채움")
        u = _맞대기(새, 모드, 옛, 옛모드, 사용자시험, 다수)
        v = _맞대기(새, 모드, 옛, 옛모드, 봉인, 다수)
        h = _맞대기(새, 모드, None, None, 봉인, 다수)
        여유 = -(-len(봉인) * int(V5_여유 * 1000) // 1000)          # ⌈0.01·n⌉ — 부동소수 없이
        글 = f"사용자 고침 {len(사용자시험)}개에서 {u[0]}승 {u[1]}패(유의 아님) · 봉인 {v[0]}승 {v[1]}패(여유 {여유}) · 손만 대비 {h[0]}승 {h[1]}패"
        if not u[0] > u[1]:
            return False, f"사용자 고침에서 이기지 못함 — 지금 것을 그대로 쓴다 ({글})", v, 모드
        if v[1] - v[0] > 여유:
            return False, f"봉인에서 1%p 넘게 떨어짐 — 지금 것을 그대로 쓴다 ({글})", v, 모드
        if h[1] > h[0]:
            return False, f"손 규칙보다 못함 — 지금 것을 그대로 쓴다 ({글})", v, 모드
        return True, f"사용자 기준으로 바꾼다 ({글})", v, 모드
    if not g.get("승격"):
        return False, f"관문 미통과 — 지금 것을 그대로 쓴다(사용자 고침 {len(사용자시험)}개 < {U_MIN})", [0, 0], None
    v = _맞대기(새, g["모드"], 옛, 옛모드, 봉인, 다수)
    if v[1] > v[0]:
        return False, f"관문은 지났지만 지금 승격본보다 못하다({v[0]}승 {v[1]}패) — 지금 것을 그대로 쓴다", v, g["모드"]
    return True, f"관문 통과 · 지금 승격본에 지지 않음({v[0]}승 {v[1]}패) — 바꾼다", v, g["모드"]


def 진화(줄들: "list | None" = None, seed: int = 7, 저장: bool = True) -> dict:
    """원장 + 학습 모음을 재생해 XCS 를 새로 기르고, 봉인 모음(+사용자 관문)을 지나면 승격한다."""
    import hashlib
    from walp import usability
    if hashlib.sha256(TEST.read_bytes()).hexdigest() != TEST_SHA:
        return {"오류": "봉인 모음이 바뀌었다 — 관문을 돌리지 않는다"}
    t0 = _time.time()
    줄들 = usability.읽기() if 줄들 is None else 줄들
    원장라벨, 밴딧, 사용자시험 = 재생표본(줄들)
    학습 = [(t, a) for p in TRAINS for a, t in 모음(p)]
    시험글 = {t for _, t in 모음(TEST)}
    섞임 = [t for t, _ in 원장라벨 if t in 시험글]
    원장라벨 = [(t, a) for t, a in 원장라벨 if t not in 시험글]      # 봉인 글이 원장에 있으면 학습에서 뺀다(누수 방지)
    표본 = 학습 + 원장라벨
    빈 = [a for t, a in 표본 if 손규칙(t, _파스(t)) is None]
    다수 = max(set(빈), key=빈.count) if 빈 else "task"
    x = 학습기_기르기(표본, 밴딧, seed=seed)
    몇번째 = BASE_LOOKS + 1 + sum(1 for z in 줄들 if z.get("kind") == "xcs_gate" and z.get("via") == "evolve")
    # 학습 글과 알맹이가 같은 봉인 문장은 뺀다 — 실험(evotest/growtest)과 같은 규칙. 첫 판은 이걸 빠뜨려 363 가운데
    # 85 문장이 학습과 겹친 채로 관문을 쟀다(승격 판단이 부풀었다, 2026-09-30 배포 경로 확인에서 찾음).
    학습알 = {_알맹이(t) for t, _ in 표본}
    봉인 = [(a, t) for a, t in 모음(TEST) if _알맹이(t) not in 학습알]
    g = 관문(x, 봉인, 다수, 사용자시험, 유의=α(몇번째))
    g["봉인_누수_뺌"] = len(모음(TEST)) - len(봉인)
    g["몇번째_관문"] = 몇번째
    g.update({"학습기": getattr(x, "kind", "xcs"), "어휘": len(x.어휘), "어휘_앞": x.어휘[:20], "학습_모음": len(학습), "원장_라벨": len(원장라벨),
              "원장_밴딧": len(밴딧), "사용자_관문용": len(사용자시험), "봉인누수_뺌": len(섞임),
              "집단_규칙수": len(x.pop) if hasattr(x, "pop") else getattr(x, "규칙수", 0), "초": round(_time.time() - t0, 1), "씨앗": seed})
    _실행집단["mtime"] = None                                  # 옛 승격본을 새로 읽는다
    옛, 옛모드 = 승격된집단()
    바꿈, 까닭, 대옛, 새모드 = 교체할까(g, x, 옛, 옛모드, 봉인, 다수, 사용자시험)
    g.update({"교체": 바꿈, "래칫": 까닭, "새_대_옛(이김,짐)": 대옛, "쓰는_모드": 새모드 if 바꿈 else 옛모드})
    if 저장 and not 바꿈 and 옛 is not None:
        # 승격본을 지킨다 — 상태 파일은 그대로, 떨어진 후보의 관문 결과만 원장에 남긴다
        usability.적기({"who": "system", "via": "evolve", "kind": "xcs_gate", "승격": g["승격"], "모드": g["모드"],
                        "교체": False, "래칫": 까닭, "쓰는_모드": g["쓰는_모드"], "몇번째": g["몇번째_관문"], "유의수준": g["유의수준"],
                        "정확도": g["정확도"], "p": g["p_단측"], "원장_라벨": len(원장라벨), "원장_밴딧": len(밴딧),
                        "사용자_관문용": len(사용자시험)})
        return g
    if 저장:
        p = _상태()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(_json.dumps({"학습기": getattr(x, "kind", "xcs"), "xcs": x.to_json(), "어휘": x.어휘, "관문": {k: v for k, v in g.items() if k != "틀린_예"},
                                    "승격": 바꿈, "모드": 새모드 if 바꿈 else None, "ts": _time.time()}, ensure_ascii=False),
                       encoding="utf-8")
        _os.replace(tmp, p)
        usability.적기({"who": "system", "via": "evolve", "kind": "xcs_gate", "승격": g["승격"], "모드": g["모드"],
                        "교체": 바꿈, "래칫": 까닭, "쓰는_모드": g["쓰는_모드"], "몇번째": g["몇번째_관문"], "유의수준": g["유의수준"],
                        "정확도": g["정확도"], "p": g["p_단측"], "원장_라벨": len(원장라벨), "원장_밴딧": len(밴딧),
                        "사용자_관문용": len(사용자시험)})
    return g


_실행집단 = {"mtime": None, "x": None, "모드": None}


def 승격된집단() -> "tuple[object, str | None]":
    """(집단, 모드: 채움|덮기) — 승격 안 됐으면 (None, None)."""
    p = _상태()
    try:
        m = p.stat().st_mtime
    except OSError:
        return None, None
    if _실행집단["mtime"] != m:
        try:
            d = _json.loads(p.read_text(encoding="utf-8"))
            from walp.xcs import XCS
            kind = d.get("학습기", "xcs")
            if not d.get("승격"):
                _실행집단["x"] = None
            elif kind == "behavior":
                from walp import behavior as B
                _실행집단["x"] = B.행동모형.from_json(d["xcs"])
            elif kind == "grow":
                _실행집단["x"] = 성장모형.from_json(d["xcs"])
            elif kind == "nb":
                _실행집단["x"] = NB모형.from_json(d["xcs"])
            else:
                _실행집단["x"] = XCS.from_json(d["xcs"])
                _실행집단["x"].어휘 = d.get("어휘", [])
            _실행집단["모드"] = (d.get("모드") or "채움") if d.get("승격") else None
        except (OSError, ValueError, KeyError, TypeError):
            _실행집단["x"], _실행집단["모드"] = None, None
        _실행집단["mtime"] = m
    return _실행집단["x"], _실행집단["모드"]


# ---------------------------------------------------------------- 사례 기억 — 사람이 고친 말은 손 규칙보다 앞선다
_기억캐시 = {"key": None, "표": {}}


def 고침표(줄들: "list | None" = None) -> dict:
    """알맹이 → {행위, 글, 사람들, 권한}. 같은 말을 다른 행위로 다시 고치면 새 고침이 이긴다(사람 목록도 새로)."""
    from walp import usability
    if 줄들 is None:
        p = usability.원장경로()
        try:
            st = p.stat()
            key = (str(p), st.st_mtime, st.st_size)
        except OSError:
            return {}
        if _기억캐시["key"] == key:
            return _기억캐시["표"]
        줄들 = usability.읽기()
    else:
        key = None
    표: dict = {}
    for z in sorted(줄들, key=lambda z: z.get("ts", 0)):
        if z.get("kind") != "act_fix" or z.get("act") not in ACTS or not z.get("text"):
            continue
        k = _알맹이(z["text"])
        e = 표.get(k)
        if not e or e["행위"] != z["act"]:
            e = 표[k] = {"행위": z["act"], "글": z["text"], "사람들": set(), "권한": False}
        e["사람들"].add(z.get("who"))
        e["권한"] = e["권한"] or bool(z.get("w"))
    if key is not None:
        _기억캐시.update(key=key, 표=표)
    return 표


def _켜짐(e: dict) -> bool:
    return e["권한"] or len(e["사람들"]) >= 2          # 권한 없는 한 사람의 고침은 모두의 답을 바꾸지 않는다


def 기억_고르기(text: str, 표: "dict | None" = None) -> "str | None":
    표 = 고침표() if 표 is None else 표
    if not 표:
        return None
    k = _알맹이(text)
    e = 표.get(k)
    if e and _켜짐(e):
        return e["행위"]
    g = _조각(text)
    if len(g) < 2:
        return None
    best, 점 = None, 0.0
    for kk, e in 표.items():
        if not _켜짐(e):
            continue
        h = _조각(e["글"])
        if len(h) < 2:
            continue
        j = len(g & h) / len(g | h)
        if j > 점:
            best, 점 = e["행위"], j
    return best if 점 >= SIM else None


def 고르기(text: str, r: "dict | None" = None) -> "tuple[str | None, str]":
    """(행위, 누가 골랐나: parser|memory|xcs-override|rule|xcs|none). 층 차례:
    파서(찾기) → 사람이 고친 말(사례 기억) → 승격된 XCS 가 '덮기' 모드로 아주 확신할 때 → 손 규칙 → XCS 채움."""
    r = r if r is not None else {}
    if r.get("status") == "ok" or r.get("parsed"):
        return "task", "parser"
    m = 기억_고르기(text)
    if m:
        return m, "memory"
    x, 모드 = 승격된집단()
    if x is not None and 모드 == "덮기":
        a, c = 학습기_예측(x, text, r)
        if a and c >= CONF_OVR / 1000:
            return a, "xcs-override"
    if ORDER == "학습기먼저" and x is not None:
        a, c = 학습기_예측(x, text, r)
        if a and c >= CONF / 1000:
            return a, "xcs"
    h = 손규칙(text, r)
    if h:
        return h, "rule"
    if x is not None and ORDER != "학습기먼저":
        a, c = 학습기_예측(x, text, r)
        if a and c >= CONF / 1000:
            return a, "xcs"
    return None, "none"


# ---------------------------------------------------------------- 저절로 진화 — 신호가 쌓이면 배경에서 돌리고, 결과만 알린다
def _잠금():
    return _상태().with_name("walp_xcs.lock")


def _도는중() -> bool:
    try:
        pid = int(_잠금().read_text().strip())
        _os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def 새신호(줄들: list) -> int:
    """마지막 관문 뒤에 쌓인 새 신호 수: 사람의 고침 + 다음 턴 감사/불만(대화 행위 줄)."""
    끝 = max((z.get("ts", 0) for z in 줄들 if z.get("kind") == "xcs_gate"), default=0)
    return sum(1 for z in 줄들 if z.get("ts", 0) > 끝 and (z.get("kind") == "act_fix"
                                                           or (z.get("kind") == "dialog" and z.get("act") in ("thanks", "complaint"))))


def _띄우기() -> "int | None":
    import subprocess
    import sys
    log = _상태().with_name("walp_xcs_evolve.log")
    with open(log, "a") as lf:
        p = subprocess.Popen([sys.executable, "-m", "walp.dialog", "--evolve"], cwd=str(HERE.parent), stdout=lf,
                             stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)   # setsid(CLAUDE.md)
    return p.pid


def 자동확인(줄들: "list | None" = None, 띄우기=None) -> "int | None":
    """새 신호가 AUTO_N 이상이고 돌고 있는 진화가 없으면 배경에서 진화를 띄운다. 띄웠으면 PID."""
    from walp import usability
    줄들 = usability.읽기() if 줄들 is None else 줄들
    처음 = not _상태().exists() and not any(z.get("kind") in ("xcs_gate", "xcs_auto") for z in 줄들)
    if (새신호(줄들) < AUTO_N and not 처음) or _도는중():
        return None                        # 처음(길러 둔 학습기가 없을 때)은 신호를 기다리지 않고 한 번 기른다
    pid = (띄우기 or _띄우기)()
    if pid:
        _잠금().write_text(str(pid))
        usability.적기({"who": "system", "via": "evolve", "kind": "xcs_auto", "pid": pid, "신호": 새신호(줄들)})
    return pid


def 알림(who_h: str, 줄들: "list | None" = None) -> str:
    """새 진화 관문 결과를 그 사람에게 **한 번만** 한 줄로 알린다."""
    from walp import usability
    줄들 = usability.읽기() if 줄들 is None else 줄들
    관 = [z for z in 줄들 if z.get("kind") == "xcs_gate" and z.get("via") == "evolve"]
    if not 관:
        return ""
    끝 = 관[-1]
    p = _상태().with_name("walp_xcs_notice.json")
    try:
        본 = _json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        본 = {}
    if 본.get(who_h, 0) >= 끝.get("ts", 0):
        return ""
    본[who_h] = 끝.get("ts", 0)
    p.write_text(_json.dumps(본), encoding="utf-8")
    a = 끝.get("정확도", {})
    return (f"\n\n_(대화로 쌓인 신호로 진화를 돌렸습니다 — "
            + (f"**{끝.get('쓰는_모드') or 끝.get('모드')} 모드로 바꿈** ({끝.get('래칫', '')})" if 끝.get("교체", 끝.get("승격"))
               else f"**그대로 둠** — {끝['래칫']}" if 끝.get("래칫")
               else "승격하지 않음 — 지금 쓰던 학습기는 그대로")
            + f" · 봉인 모음 손만 {a.get('손만', 0):.0%} → 채움 {a.get('채움', 0):.0%} · 덮기 {a.get('덮기', 0):.0%}. `결과` 로 자세히.)_")


def 진화보이기(g: dict) -> str:
    if "오류" in g:
        return "⚠ " + g["오류"]
    a, 쌍, p = g["정확도"], g["쌍대_이김_짐"], g["p_단측"]
    u = g.get("사용자관문", {})
    return (f"대화 행위 진화(XCS) — 학습 모음 {g['학습_모음']} + 원장 라벨 {g['원장_라벨']} · 밴딧 {g['원장_밴딧']} 을 재생 "
            f"(사람 고침 {g.get('사용자_관문용', 0)}개는 관문용으로 뗌), 규칙 {g['집단_규칙수']}개 · 글자조각 특징 {g.get('어휘', 0)}개 · "
            f"{g['초']}초\n"
            f"봉인 모음 {g['n']}문장(학습과 겹친 {g.get('봉인_누수_뺌', 0)} 뺌): 손만 {a['손만']:.1%} · 손+다수({g['다수행위']}) {a['손+다수']:.1%} · "
            f"**채움 {a['채움']:.1%}** · **덮기 {a['덮기']:.1%}** · XCS만 {a['XCS만']:.1%}\n"
            f"빈자리 {g['손규칙_빈자리']} 가운데 XCS 채움 {g['XCS_채움']}(맞음 {g['XCS_채움_맞음']}) · "
            f"덮기가 손 규칙을 바꾼 것 {g['XCS_덮음']}(맞음 {g['XCS_덮음_맞음']})\n"
            f"쌍대(이김/짐, p): 채움 vs손만 {쌍['채움vs손만']} {p['채움vs손만']} · vs손+다수 {쌍['채움vs손+다수']} {p['채움vs손+다수']} | "
            f"덮기 vs손만 {쌍['덮기vs손만']} {p['덮기vs손만']} · vs손+다수 {쌍['덮기vs손+다수']} {p['덮기vs손+다수']} · vs채움 {쌍['덮기vs채움']}\n"
            + (f"사용자 관문(사람이 고친 말 {u.get('n', 0)}개): 손만 {u.get('정확도', {}).get('손만', 0):.0%} · "
               f"채움 {u.get('정확도', {}).get('채움', 0):.0%} · 덮기 {u.get('정확도', {}).get('덮기', 0):.0%}\n" if u.get("n") else "")
            + f"이 봉인 모음을 {g.get('몇번째_관문', 1)}번째 보는 관문 — 유의수준 {g.get('유의수준', ALPHA):.4f}(α 소비: 되풀이해 볼수록 엄격하다)\n"
            + (f"래칫: {g['래칫']}\n" if g.get("래칫") else "")
            + (f"**{g.get('쓰는_모드') or g['모드']} 모드로 바꿨다.**" if g.get("교체", g["승격"])
               else "지금 승격본을 그대로 쓴다." if g.get("쓰는_모드")
               else "승격하지 않았다 — 봉인 모음에서 손만과 손+다수를 둘 다 유의하게 이기지 못했다. 손 규칙(+사람이 고친 말)만 쓴다."))


if __name__ == "__main__":
    import sys
    if "--eval" in sys.argv:
        g = 진화(줄들=[], 저장=False)
        out = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else None
        if out:
            _Path(out).write_text(_json.dumps(g, ensure_ascii=False, indent=1), encoding="utf-8")
        print(진화보이기(g))
    elif "--evolve" in sys.argv:          # 자동확인이 배경에서 띄운다
        try:
            _잠금().write_text(str(_os.getpid()))
            print(진화보이기(진화()), flush=True)
        finally:
            try:
                _잠금().unlink()
            except OSError:
                pass
