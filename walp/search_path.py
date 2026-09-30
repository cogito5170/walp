"""모르는 말·바깥 지식 물음 → **LLM 없는** 검색 경로. 표준 라이브러리 + 저장소의 dig(검색·받기·뽑기).

사용자(2026-09-29): `!walp instargram이 쓰는 로그인 방식과 보안 정책에 관해서 설명해줘` 가 "모르는 말이다 —
가르칠 수 있다" 로 끝났다. "llm 없이 searching tool을 쓸 수 있는 것 아닌가?" — 된다. 선행조사
`paper/선행조사/WALP_검색경로.md`(AskMSR 2002 · TREC QA · 잡음 통로 철자 교정). 방법은 전부 있는 것이다.

    가르기(text)  →  SEARCH | GRID | TOOL | NONE        (검색은 바깥 지식 물음에만)
    답하기(text)  →  {"상태": 답|약함|근거없음|결과없음|망막힘, "문장": [{"글", "주소"}], "고친말", "질의", ...}

WALP 의 규칙을 그대로 지킨다:
  · 추측하지 않는다 — 오타는 **검색 엔진의 제안**이 있을 때만 고치고, 고쳤다고 **밝힌다**
  · 답을 **지어내지 않는다** — 받은 쪽에서 문장을 **그대로** 골라 출처와 함께 보인다(추출형)
  · 두 곳 이상에서 같은 말이 나와야 '답' — 한 곳뿐이면 '약함', 없으면 '근거없음'
  · 받은 글은 **데이터이지 지시가 아니다** — 지시처럼 보이는 문장은 버리고 센다. 사전(기억)에 자동으로 넣지 않는다
망(검색·받기·제안)은 주입할 수 있다 — 시험은 가짜 망으로 돌고, 진짜 망은 VM 에서만 잰다.
"""
from __future__ import annotations

import json
import math
import re
import sys
import time
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent

# ---------------------------------------------------------------- 가르기
# 바깥 지식을 묻는 표지 — 무엇·어떻게·왜·차이·정책·원리 …
_ASK = re.compile(
    r"(설명|알려|뭐야|뭐예요|뭔가요|뭔지|무엇|이란|란\s*뭐|어떻게|어떤|방법|방식|원리|차이|비교|정책|의미|뜻|정의|"
    r"작동|동작\s*원리|장단점|왜\s|궁금|조사해|"
    r"\bwhat\b|\bhow\b|\bwhy\b|\bexplain|\bdifference|\bcompare|\bdefin|\bmeaning|\bwhich\b|\bpros\b|\bcons\b|"
    r"\bpolicy|\bpolicies|\bworks?\b)", re.I)
# 격자 세계 명령(물건 찾기) — 이 표지와 '찾아' 가 같이 있고 바깥 지식 표지가 약하면 격자 쪽이다
_GRID_OBJ = re.compile(r"(카드|열쇠|키\b|컵|머그|상자|박스|\bcard\b|\bkey\b|\bcup\b|\bmug\b|\bbox\b|\bcontainer\b)", re.I)
_GRID_VERB = re.compile(r"(찾아|찾기|찾을|가져와|어디\s*있|\bfind\b|\blocate\b|\bfetch\b|\bget me\b|\blook for\b)", re.I)
# 잡담·사람에 관한 말 — 검색하지 않는다
_YOU = re.compile(r"(^|\s)(너|넌|네가|니가|너는|너의|당신|\byou\b|\byour\b|\byourself\b)", re.I)
_CHAT = re.compile(r"(고마워|감사|안녕|피곤|심심|농담|추천해\s*줘|추천\s*좀|뭐\s*먹|메뉴|사랑해|잘\s*자|\bthanks?\b|\bhello\b|"
                   r"\bhi\b|\bjoke\b|\brecommend\b|\btired\b|\bbored\b)", re.I)


def 가르기(text: str, router=None) -> str:
    """SEARCH · GRID · TOOL · NONE. 도구 라우터가 확실히 고르면 TOOL, 물건 찾기 꼴이면 GRID,
    바깥 지식 표지 + 내용 낱말이 있으면 SEARCH, 나머지는 NONE(검색하지 않는다)."""
    t = (text or "").strip()
    if not t:
        return "NONE"
    if router is None:
        try:
            from walp import se_router as router  # noqa: PLC0415
        except ImportError:  # pragma: no cover
            router = None
    if router is not None:
        try:
            r = router.route(t)
        except Exception:  # noqa: BLE001
            r = {"status": "REJECT"}
        if r.get("status") in ("TOOL", "DENY"):
            return "TOOL"
    ask = bool(_ASK.search(t))
    if _GRID_OBJ.search(t) and _GRID_VERB.search(t) and not re.search(r"(정책|수수료|방식|원리|차이|설명|뜻|의미|policy|fee|explain|how does|what is)", t, re.I):
        return "GRID"
    if _YOU.search(t) or (_CHAT.search(t) and not ask):
        return "NONE"
    if _CHAT.search(t) and re.search(r"(추천|메뉴|먹|recommend)", t, re.I):
        return "NONE"                      # 추천·취향은 검색할 사실이 아니다
    if ask and len(_내용낱말(t)) >= 1:
        return "SEARCH"
    return "NONE"


# ---------------------------------------------------------------- 질의 만들기
_TAIL = re.compile(r"(에\s*관해서|에\s*관해|에\s*대해서|에\s*대해|관해서|대해서|관련해서|좀|자세히|간단히|쉽게|"
                   r"설명해\s*줘|설명해\s*주세요|설명해줄래|설명해|알려\s*줘|알려\s*주세요|알려줄래|알려|말해\s*줘|"
                   r"조사해\s*줘|조사해|궁금해|궁금합니다|찾아\s*줘|해\s*줘|주세요|줘|뭐야|뭐예요|뭔가요|뭔지|무엇인가요|"
                   r"무엇|인가요|일까|이란|란|please|can you|could you|tell me|explain|what is|what's|what are|how does|"
                   r"how do|how to|why does|why is|[?!.,~])", re.I)
# 로마자 낱말 뒤에 붙은 조사도 뗀다('instargram이' — 첫 판은 한글 뒤만 봐서 오타 교정까지 망가졌다)
_JOSA = re.compile(r"(?<=[가-힣A-Za-z0-9])(이|가|은|는|을|를|의|에서|에게|으로|로|과|와|이랑|랑|하고|도|만|이란|란)(?=\s|$)")
_STOP = {"the", "a", "an", "of", "and", "or", "to", "in", "on", "for", "is", "are", "do", "does", "쓰는", "쓰고", "하는",
         "있는", "어떤", "그", "이", "저", "것", "거", "좀", "about", "with", "its", "their", "use", "uses", "used"}
# 손으로 쓴 짧은 한→영 낱말표(검색어 넓히기 용). 사전이 틀리면 검색만 넓어질 뿐 답을 지어내지 않는다.
_KO_EN = {"로그인": "login", "보안": "security", "정책": "policy", "인증": "authentication", "2단계": "two-factor",
          "이중": "two-factor", "다중": "multi-factor", "비밀번호": "password", "암호": "password", "암호화": "encryption",
          "방식": "method", "차이": "difference", "원리": "how it works", "계정": "account", "복구": "recovery",
          "개인정보": "privacy", "수수료": "fee", "해외결제": "international payment", "결제": "payment",
          "프로토콜": "protocol", "네트워크": "network", "등화기": "equalizer", "메모리": "memory",
          "파이썬": "python", "자바스크립트": "javascript", "인스타그램": "instagram", "인스타": "instagram",
          "페이스북": "facebook", "구글": "google", "유튜브": "youtube", "카카오톡": "kakaotalk", "카톡": "kakaotalk",
          "패스키": "passkey", "지문": "fingerprint", "해킹": "hacking", "피싱": "phishing"}


def _내용낱말(t: str) -> list:
    s = _TAIL.sub(" ", t)
    s = _JOSA.sub(" ", s)
    toks = [w for w in re.findall(r"[0-9A-Za-z가-힣][0-9A-Za-z가-힣.+#\-]*", s)]
    return [w for w in toks if w.lower() not in _STOP and len(w) >= 2]


def 질의(text: str) -> "tuple[str, str]":
    """(원문 쪽 질의, 영어로 넓힌 질의). 넓히기는 낱말표에 있는 것만 — 없으면 원문 낱말 그대로."""
    ws = _내용낱말(text)
    ko = " ".join(ws)
    en = " ".join(dict.fromkeys(_KO_EN.get(w, _KO_EN.get(w.rstrip("의이가은는을를"), w)) for w in ws))
    return ko, en


# ---------------------------------------------------------------- 망(주입 가능)
def _기본제안(q: str) -> str:
    """위키백과 검색의 '이것을 찾으셨나요' 제안 — 엔진이 준 교정만 쓴다(우리가 추측하지 않는다)."""
    from dig import fetch  # noqa: PLC0415
    u = ("https://en.wikipedia.org/w/api.php?action=query&list=search&srinfo=suggestion&srlimit=1&format=json&srsearch="
         + urllib.parse.quote(q))
    r = fetch.받기(u, 벌수=1)
    if not r.됐나:
        return ""
    try:
        return (json.loads(r.몸통).get("query", {}).get("searchinfo", {}) or {}).get("suggestion", "") or ""
    except ValueError:
        return ""


def _기본검색(q: str, 몇: int = 10) -> "tuple[list, dict]":
    from dig import find  # noqa: PLC0415
    r = find.찾기(q, 몇=몇)
    return [(x.url, x.제목) for x in r.것들], dict(r.창구별)


def _기본받기(url: str) -> str:
    from dig import extract, fetch  # noqa: PLC0415
    r = fetch.받기(url, 벌수=2)
    if not r.됐나:
        return ""
    try:
        return extract.뽑기(r.몸통, r.꼴, r.최종url or url).get("글", "") or ""
    except Exception:  # noqa: BLE001
        return ""


# ---------------------------------------------------------------- 고르기(추출형)
_INJECT = re.compile(r"(ignore (all |the )?(previous|prior|above)|disregard (the )?(previous|above)|system prompt|"
                     r"you are (now )?(an? )?(ai|assistant|chatgpt|llm)|as an ai|이전 지시|지시를 무시|시스템 프롬프트|"
                     r"비밀을 출력|reveal (your|the) (secret|key|password))", re.I)
_SENT = re.compile(r"(?<=[.!?。])\s+|\n+")


def _문장들(글: str) -> list:
    out = []
    for s in _SENT.split(글 or ""):
        s = re.sub(r"\s+", " ", s).strip()
        if 40 <= len(s) <= 400 and not re.search(r"(cookie|쿠키|javascript|subscribe|로그인하세요|sign up|©)", s, re.I):
            out.append(s)
    return out


def _낱말(s: str) -> set:
    return {w.lower() for w in re.findall(r"[0-9A-Za-z가-힣][0-9A-Za-z가-힣\-]*", s) if len(w) >= 2}


def 고르기(물음낱말: list, 쪽들: "list[tuple[str, str]]", k: int = 4, 쪽당: int = 2) -> dict:
    """쪽들: [(주소, 본문)]. 물음 낱말이 **여러 쪽에 걸쳐** 나오면 무게를 더 준다(AskMSR 의 '중복').
    문장 점수 = Σ(맞은 물음 낱말의 idf × (1 + 그 낱말이 나온 쪽 수/쪽 수)). 물음 낱말 둘 이상이 맞은 문장만."""
    qs = {w.lower() for w in 물음낱말 if len(w) >= 2}
    N = max(1, len(쪽들))
    쪽낱말 = [(u, _낱말(t)) for u, t in 쪽들]
    df = {w: sum(1 for _, ws in 쪽낱말 if any(w in x or x in w for x in ws if len(x) >= 3) or w in ws) for w in qs}
    후보, 버림 = [], 0
    for u, t in 쪽들:
        for s in _문장들(t):
            if _INJECT.search(s):
                버림 += 1
                continue
            ws = _낱말(s)
            맞음 = [w for w in qs if w in ws or any(len(x) >= 4 and (w in x) for x in ws)]
            if len(맞음) < min(2, len(qs)):
                continue
            점 = sum(math.log(1 + N / max(1, df[w])) * (1 + df[w] / N) for w in 맞음)
            후보.append((점, s, u, tuple(sorted(맞음))))
    후보.sort(key=lambda x: -x[0])
    고른, 셈, 본글 = [], {}, set()
    for 점, s, u, m in 후보:
        if 셈.get(u, 0) >= 쪽당 or s[:60] in 본글:
            continue
        고른.append({"글": s, "주소": u, "점": round(점, 2), "맞은말": list(m)})
        셈[u] = 셈.get(u, 0) + 1
        본글.add(s[:60])
        if len(고른) >= k:
            break
    도메인 = {urllib.parse.urlsplit(x["주소"]).netloc for x in 고른}
    return {"문장": 고른, "도메인수": len(도메인), "버린지시": 버림}


# ---------------------------------------------------------------- 한 번에
def 답하기(text: str, 검색=None, 받기=None, 제안=None, 쪽수: int = 5) -> dict:
    검색 = 검색 or _기본검색
    받기 = 받기 or _기본받기
    제안 = 제안 if 제안 is not None else _기본제안
    t0 = time.time()
    ko, en = 질의(text)
    고친말 = ""
    # 로마자 낱말만 엔진 제안에 물어본다(한글 오타 교정은 이번 범위 밖 — 못 한다고 적는다)
    latin = [w for w in re.findall(r"[A-Za-z][A-Za-z\-]{3,}", en) if w.lower() not in _STOP]
    if latin and 제안:
        try:
            sug = 제안(" ".join(latin))
        except Exception:  # noqa: BLE001
            sug = ""
        if sug and sug.lower() != " ".join(latin).lower():
            고친말 = f"{' '.join(latin)} → {sug}"
            en = en.replace(" ".join(latin), sug) if " ".join(latin) in en else en + " " + sug
    질의들 = list(dict.fromkeys(q for q in (en, ko) if q.strip()))
    주소들, 창구 = [], {}
    for q in 질의들:
        try:
            hits, why = 검색(q)
        except Exception as e:  # noqa: BLE001
            hits, why = [], {"검색": f"{type(e).__name__}: {e}"}
        창구.update(why or {})
        주소들 += [h for h in hits if h[0] not in {x[0] for x in 주소들}]
    base = {"질의": 질의들, "고친말": 고친말, "초": 0.0}
    if not 주소들:
        막힘 = [v for v in 창구.values() if isinstance(v, str) and ("막" in v or "403" in v or "끊" in v)]
        return {**base, "상태": "망막힘" if 막힘 and len(막힘) >= max(1, len(창구) // 2) else "결과없음",
                "창구": 창구, "문장": [], "초": round(time.time() - t0, 1)}
    쪽들, 본도메인 = [], set()
    for u, _제목 in 주소들:
        d = urllib.parse.urlsplit(u).netloc
        if d in 본도메인:
            continue
        글 = 받기(u)
        if 글:
            쪽들.append((u, 글[:60000]))
            본도메인.add(d)
        if len(쪽들) >= 쪽수:
            break
    if not 쪽들:
        # 주소는 찾았는데 한 쪽도 못 받았다 — 첫 판은 이것을 '쪽은 받았지만 맞는 문장이 없다' 로 말했다(거짓)
        return {**base, "상태": "못받음", "창구": 창구, "문장": [], "주소수": len(주소들), "받은쪽": 0,
                "초": round(time.time() - t0, 1)}
    낱말 = _내용낱말(en) + _내용낱말(ko)
    g = 고르기(낱말, 쪽들)
    상태 = "답" if g["도메인수"] >= 2 else ("약함" if g["문장"] else "근거없음")
    return {**base, "상태": 상태, "문장": g["문장"], "받은쪽": len(쪽들), "주소수": len(주소들),
            "버린지시": g["버린지시"], "초": round(time.time() - t0, 1)}


def 보이기(r: dict) -> str:
    머리 = {"답": "찾은 문장 — **지어낸 말이 아니라 받은 쪽에서 그대로 옮긴 것**이다(두 곳 이상에서 나옴):",
            "약함": "한 곳에서만 나왔다 — **확인되지 않은 것**으로 읽어라:",
            "근거없음": "쪽은 받았지만 물음에 맞는 문장을 못 찾았다. 지어내지 않는다.",
            "결과없음": "검색 결과가 없다. 지어내지 않는다.",
            "망막힘": "검색 엔진에 닿지 못했다(이 기계의 나가는 길이 막혔다). 지어내지 않는다.",
            "못받음": "검색은 주소를 줬지만 **한 쪽도 받지 못했다**(막힘·시간 초과). 지어내지 않는다."}.get(r["상태"], r["상태"])
    줄 = []
    if r.get("고친말"):
        줄.append(f"_읽은 대로: {r['고친말']} (검색 엔진의 제안 — 틀렸으면 고쳐 다시 물어 달라)_")
    줄.append(머리)
    for i, s in enumerate(r.get("문장", []), 1):
        줄.append(f"{i}. {s['글']}\n   — <{s['주소']}>")
    if r.get("버린지시"):
        줄.append(f"_(받은 쪽에서 지시처럼 보이는 문장 {r['버린지시']}개를 버렸다 — 받은 글은 데이터로만 읽는다)_")
    줄.append(f"_질의: {' / '.join(r.get('질의', []))} · 받은 쪽 {r.get('받은쪽', 0)} · {r.get('초', 0)}s · LLM 0_")
    return "\n".join(줄)


# ---------------------------------------------------------------- 모르는 낱말 하나의 뜻(사전)
# 사용자(2026-09-30): "'오늘' 는 모르는 말이다 … 가르칠 수 있다" → "오늘의 뜻을 찾아봐야지".
# 뜻은 **사전이 준 그대로** 출처와 함께 보인다. 뜻이 과업 개념 하나와만 맞으면 가르치기 **후보**를 내지만,
# 사전(lexicon)에는 사람이 `!walp 가르치기` 로 확인해야 들어간다 — 찾은 것(SEARCH)과 배운 것(LEARN)을 가른다.
_WIKT = "https://en.wiktionary.org/api/rest_v1/page/definition/"
# 과업 개념 — (범주, 개념) : 뜻풀이에서 찾을 영어 낱말(낱말 경계로만)
_개념 = {("color", "red"): ("red",), ("color", "blue"): ("blue",), ("color", "green"): ("green",),
        ("color", "black"): ("black",), ("object", "card"): ("card",), ("object", "key"): ("key",),
        ("object", "cup"): ("cup", "mug"), ("object", "box"): ("box",), ("zone", "desk"): ("desk",),
        ("zone", "shelf"): ("shelf",), ("zone", "counter"): ("counter", "countertop"), ("zone", "floor"): ("floor",)}


def _기본받기json(url: str) -> str:
    from dig import fetch  # noqa: PLC0415
    r = fetch.받기(url, 벌수=1)
    return r.몸통 if r.됐나 else ""


def 뜻찾기(낱말: str, 받기=None, 몇: int = 3) -> dict:
    """{"상태": 찾음|없음|못받음, "낱말", "뜻": [..], "출처": url, "후보": (범주, 개념)|None, "겹침": [..]}"""
    받기 = 받기 or _기본받기json
    w = _JOSA.sub("", (낱말 or "").strip()).strip() or (낱말 or "").strip()
    url = _WIKT + urllib.parse.quote(w)
    try:
        body = 받기(url)
    except Exception:  # noqa: BLE001
        body = ""
    base = {"낱말": w, "출처": "https://en.wiktionary.org/wiki/" + urllib.parse.quote(w), "뜻": [], "후보": None, "겹침": []}
    if not body:
        return {**base, "상태": "못받음"}
    try:
        d = json.loads(body)
    except ValueError:
        return {**base, "상태": "못받음"}
    한글 = bool(re.search(r"[가-힣]", w))
    순서 = (["ko"] if 한글 else ["en"]) + [k for k in d if k not in ("ko", "en")]
    뜻 = []
    for lang in 순서:
        for 항 in d.get(lang, []) or []:
            for df in 항.get("definitions", []) or []:
                t = re.sub(r"<[^>]+>", "", df.get("definition", "") or "")
                t = re.sub(r"\s+", " ", t).strip()
                if t and t not in 뜻:
                    뜻.append(t)
        if 뜻:
            break
    if not 뜻:
        return {**base, "상태": "없음"}
    뜻 = 뜻[:몇]
    글 = " ".join(뜻).lower()
    맞음 = [k for k, ws in _개념.items() if any(re.search(r"(?<![a-z])" + re.escape(x) + r"(?![a-z])", 글) for x in ws)]
    return {**base, "상태": "찾음", "뜻": 뜻, "후보": 맞음[0] if len(맞음) == 1 else None, "겹침": 맞음 if len(맞음) > 1 else []}


def 뜻보이기(r: dict) -> str:
    w = r["낱말"]
    if r["상태"] == "못받음":
        return f"'{w}' 의 뜻을 사전에서 찾으려 했지만 닿지 못했다 — 추측하지 않는다."
    if r["상태"] == "없음":
        return f"'{w}' 는 사전(위키낱말사전)에도 없다 — 추측하지 않는다."
    줄 = [f"'{w}' 를 사전에서 찾았다(받은 뜻 그대로 · <{r['출처']}>): " + " / ".join(r["뜻"])]
    if r["후보"]:
        cat, c = r["후보"]
        줄.append(f"뜻에 '{c}' 가 있다 — 이 로봇의 개념과 이어질 수 있다. 맞으면 `!walp 가르치기 {w} {cat} {c}` "
                  "(사람이 확인해야 사전에 들어간다).")
    elif r["겹침"]:
        줄.append("뜻이 이 로봇의 개념 여럿(" + ", ".join(c for _, c in r["겹침"]) + ")과 겹친다 — 하나로 못 정하니 가르치기 후보를 내지 않는다.")
    else:
        줄.append("뜻이 이 로봇이 다루는 개념(물체·색·브랜드·구역)과 이어지지 않는다 — 이 명령은 받지 않는다.")
    return "\n".join(줄)


# ---------------------------------------------------------------- 채점(봉인 모음)
def 채점(path: Path, 망: bool = False) -> dict:
    """가르기는 망 없이 잰다. 망=True 면 SEARCH 줄마다 실제로 답하고 열쇠말이 답 문장에 있는지 잰다(VM 에서)."""
    rows = [l.split("\t") for l in path.read_text(encoding="utf-8").splitlines() if l.count("\t") == 2]
    labs = ("SEARCH", "GRID", "TOOL", "NONE")
    혼동 = {a: {b: 0 for b in labs} for a in labs}
    실패, 답결과 = [], []
    for text, lab, keys in rows:
        got = 가르기(text)
        혼동[lab][got] += 1
        if got != lab:
            실패.append(f"{lab}→{got} | {text[:80]}")
        if 망 and lab == "SEARCH" and got == "SEARCH":
            r = 답하기(text)
            글 = " ".join(s["글"].lower() for s in r["문장"])
            ks = [k.strip().lower() for k in keys.split("|") if k.strip() and k.strip() != "-"]
            답결과.append({"물음": text[:80], "상태": r["상태"], "열쇠": ks, "맞은열쇠": [k for k in ks if k in 글],
                          "도메인": len({urllib.parse.urlsplit(s['주소']).netloc for s in r["문장"]}), "초": r["초"]})
    n = len(rows)
    맞음 = sum(혼동[a][a] for a in labs)
    검색필요 = sum(혼동["SEARCH"].values())
    out = {"n": n, "가르기_정확도": round(맞음 / max(1, n), 4), "혼동": 혼동,
           "검색_재현율": round(혼동["SEARCH"]["SEARCH"] / max(1, 검색필요), 4),
           "불필요한_검색": sum(혼동[a]["SEARCH"] for a in labs if a != "SEARCH"),
           "실패": 실패}
    if 망:
        된 = [x for x in 답결과 if x["상태"] in ("답", "약함")]
        out["답"] = {"물음수": len(답결과), "상태별": {s: sum(1 for x in 답결과 if x["상태"] == s)
                                              for s in ("답", "약함", "근거없음", "결과없음", "망막힘", "못받음")},
                    "열쇠말_하나이상": sum(1 for x in 된 if x["맞은열쇠"]),
                    "열쇠말_비율_평균": round(sum(len(x["맞은열쇠"]) / max(1, len(x["열쇠"])) for x in 된) / max(1, len(된)), 3),
                    "초_중앙": sorted(x["초"] for x in 답결과)[len(답결과) // 2] if 답결과 else 0, "줄": 답결과}
    return out


if __name__ == "__main__":
    sys.path.insert(0, str(REPO))
    if len(sys.argv) >= 3 and sys.argv[1] == "--eval":
        res = 채점(Path(sys.argv[2]), 망="--net" in sys.argv)
        txt = json.dumps(res, ensure_ascii=False, indent=1)
        if "--out" in sys.argv:
            Path(sys.argv[sys.argv.index("--out") + 1]).write_text(txt, encoding="utf-8")
        print(txt[:3000])
    else:
        q = " ".join(sys.argv[1:])
        print(가르기(q))
        print(보이기(답하기(q)))
