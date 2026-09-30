"""WALP 대화 상대 구동기 — Gemini(무료 호출)가 **사람 역할**로 WALP 와 몇 시간이고 대화하고, WALP 는 그 대화로 자란다.

사용자(2026-09-30): "이 모델을 내 PC 에 이식한 다음에, LLM(gemini 무료호출)을 사용해서 24시간 동안 계속해서 상호-대화를 하도록 할거야."

    GEMINI_API_KEY=... python3 -m walp.partner --hours 24
    python3 -m walp.partner --fake --turns 30          # 키 없이 배선만 본다(정해 둔 말 몇 개를 돌린다)

한 턴:
    1. Gemini 가 다음 말을 짓는다 — JSON {"say": 말, "act": 그 말의 의도 행위(12개 중)}. 구동기가 이번 턴에 권할 행위를
       고르지만(골고루 나오게), WALP 가 뜻을 물었으면 네/아니로, 모르는 말이라 했으면 고쳐 말하게 한다.
    2. 그 말을 WALP 에 넣는다(`!walp` 없이 — WALP 전용 서버와 같은 길). WALP 는 LLM 을 부르지 않는다.
    3. WALP 가 고른 행위(원장의 dialog 줄)가 의도와 다르면 **구동기가** `행위 <의도>` 로 고친다 — Gemini 호출 없이.
       (찾기인데 WALP 가 '모르는 말' 이라 한 것은 고치지 않는다 — 다음 턴에 Gemini 가 고쳐 말한다. 그것이 낱말 배우기 고리다.)
    4. 원장에 쌓인 고침 · 감사/불만이 AUTO_N 개를 넘으면 WALP 가 배경에서 스스로 진화를 돌린다(walp/dialog.py).

재는 것(한 시간마다 찍는다): **WALP 가 의도 행위를 맞힌 비율** — 이것이 오르면 대화로 진화하고 있는 것이다.
기록: 상태자리/walp_partner.jsonl (턴마다 한 줄). 사용성 원장에는 via=gemini 로 적힌다 — 사람의 대화와 가를 수 있다.

**정직하게 적어 둘 것:** WALP 안에는 가중치가 없지만, 이 구동기로 배운 라벨은 **사전학습된 LLM(Gemini)이 단 것**이다.
via=gemini 로 남기므로 나중에 사람 신호만으로 다시 기를 수 있다. 쓰기 권한(--write)을 주지 않으면 사례 기억과 낱말 배우기는
서로 다른 '사람'(페르소나) 둘의 증거가 필요하다 — 페르소나는 같은 LLM 이라 **독립이 아니다.**

무료 한도: 분당 호출(--rpm, 기본 10)과 하루 호출(--rpd, 기본 1000)을 **둘 다** 지킨다 — 간격 = max(60/rpm, 86400/rpd).
429 가 오면 응답의 retryDelay(없으면 60초)만큼 쉰다. 키는 헤더로만 보낸다(URL·로그에 안 남는다).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
MODEL = os.environ.get("WALP_PARTNER_MODEL", "gemini-3.5-flash-lite")
ACT_KO = {"task": "찾기", "greet": "인사", "bye": "작별", "thanks": "감사", "about_self": "자기질문", "capability": "능력질문",
          "help": "도움", "knowledge": "바깥지식", "complaint": "불만", "out_of_scope": "범위밖", "yes": "네", "no": "아니"}
# 권하는 행위의 비율 — 찾기를 가장 많이, 나머지는 골고루(네/아니는 WALP 가 물을 때만)
권함 = [("task", 34), ("greet", 7), ("bye", 5), ("thanks", 7), ("about_self", 6), ("capability", 7), ("help", 6),
       ("knowledge", 8), ("complaint", 7), ("out_of_scope", 9)]
페르소나 = [
    "20대 대학생. 반말, 줄임말과 ㅋㅋ를 자주 쓰고 오타가 가끔 있다.",
    "40대 회사원. 존댓말을 쓰고 문장이 길고 공손하다.",
    "영어와 한국어를 섞어 쓰는 개발자. 짧게 말하고 영어 낱말을 자주 쓴다.",
    "성격이 급한 사용자. 짧고 무뚝뚝하며 잘 안 되면 바로 불평한다.",
    "처음 써 보는 어르신. 천천히, 사투리와 옛 낱말을 섞어 말한다.",
]
SYSTEM = """너는 WALP 라는 작은 로봇 비서와 디스코드에서 대화하는 **사람**을 연기한다. 너는 AI 라는 것을 드러내지 않는다.
WALP 는 LLM 이 아니다. 할 수 있는 것은 시뮬레이션 사무실에서 물건(카드·열쇠·컵·상자)을 찾는 것뿐이고, 색(빨강·파랑·초록·검정),
카드 브랜드(비자·마스터), 피할 곳(책상·선반·카운터·바닥), 기한(틱)을 붙일 수 있다. 모르는 낱말은 모른다고 하고, 사람이 다른 말로
고쳐 말하면 "혹시 'X' 라는 말을 Y 뜻으로 쓰셨나요?" 하고 묻는다.

규칙:
- 한 번에 **한 메시지**만 쓴다. 실제 사람처럼 자연스럽고 다양하게(짧게도 길게도, 비슷한 말·낯선 낱말·오타도).
- 찾기 요청을 할 때는 가끔 WALP 가 모를 법한 낱말(예: 비슷한 색 이름, 물건의 다른 이름, 구역의 다른 이름)을 섞는다.
- WALP 가 모르는 말이라고 하면, 다음에는 **같은 요청을 더 쉬운 말로** 고쳐 말한다.
- WALP 가 "혹시 'X' 라는 말을 Y 뜻으로 쓰셨나요?" 하고 물으면, 네가 실제로 그 뜻이었으면 `네`, 아니면 `아니` 라고만 답한다.
- 출력은 JSON 하나: {"say": "<보낼 메시지>", "act": "<그 메시지의 의도>"}.
  act 는 다음 중 하나: task(물건 찾기 요청) greet(인사) bye(작별) thanks(감사·칭찬) about_self(WALP 가 누구/무엇인지)
  capability(무엇을 할 수 있는지) help(사용법) knowledge(바깥 세상 사실 질문) complaint(불만·틀렸다) out_of_scope(추천·창작·의견·
  WALP 가 못 하는 일 부탁) yes(긍정 대답) no(부정 대답)."""


def _상태자리() -> Path:
    from walp import usability
    return usability.상태자리()


def _열쇠() -> str:
    k = os.environ.get("GEMINI_API_KEY", "").strip()
    if k:
        return k
    for p in (Path.cwd() / ".env", Path(__file__).resolve().parent.parent / ".env"):
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("GEMINI_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError:
            pass
    return ""


class 한도초과(Exception):
    def __init__(self, 쉼: float, 본문: str):
        super().__init__(본문)
        self.쉼 = 쉼


def gemini(prompt: str, key: str, model: "str | None" = None, timeout: float = 60.0) -> str:
    model = model or MODEL
    body = {"systemInstruction": {"parts": [{"text": SYSTEM}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 1.0, "maxOutputTokens": 300, "responseMimeType": "application/json"}}
    req = urllib.request.Request(API.format(model=model), data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "x-goog-api-key": key})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        본문 = e.read().decode(errors="replace")[:600]
        if e.code == 429:
            m = re.search(r'"retryDelay":\s*"(\d+)', 본문)
            raise 한도초과(float(m.group(1)) + 2 if m else 60.0, 본문)
        raise RuntimeError(f"HTTP {e.code}: {본문[:300]}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RuntimeError(f"연결 실패: {type(e).__name__}")          # 예외 문자열을 옮기지 않는다(키 방어 — gemini_http.py 와 같은 규율)
    try:
        return d["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("빈 응답: " + json.dumps(d, ensure_ascii=False)[:200])


def _뽑기(글: str) -> "dict | None":
    m = re.search(r"\{.*\}", 글 or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(d.get("say"), str) or not d["say"].strip() or d.get("act") not in ACT_KO:
        return None
    d["say"] = d["say"].strip().replace("\n", " ")[:300]
    return d


def _줄임(글: str, n: int = 700) -> str:
    """앞과 **끝**을 남긴다 — WALP 의 물음('쓰셨나요?')은 긴 답의 끝에 붙는다. 앞만 자르면 상대가 물음을 못 본다."""
    글 = 글 or ""
    return 글 if len(글) <= n else 글[: n // 3] + " … " + 글[-(2 * n // 3):]


def 권할행위(rng: random.Random, walp_말: str) -> str:
    if "쓰셨나요?" in walp_말 and "`네`" in walp_말:
        return "yes/no"
    if "모르는 말" in walp_말:
        return "rephrase"
    return rng.choices([a for a, _ in 권함], [w for _, w in 권함])[0]


def 프롬프트(페르소나설명: str, 역사: list, 권: str) -> str:
    대화 = "\n".join(f"{'나' if who == 'me' else 'WALP'}: {t}" for who, t in 역사[-12:]) or "(아직 아무 말도 안 했다 — 처음 말을 건다)"
    if 권 == "yes/no":
        지시 = "WALP 가 방금 뜻을 물었다. 네가 실제로 그 뜻이었으면 `네`(act=yes), 아니면 `아니`(act=no)."
    elif 권 == "rephrase":
        지시 = "WALP 가 모르는 말이라고 했다. 같은 요청을 **더 쉬운 말로** 고쳐 말하라(act=task)."
    else:
        지시 = (f"이번에는 되도록 '{ACT_KO[권]}'({권}) 에 해당하는 말을 하라. 다만 대화 흐름상 어색하면 자연스러운 말을 하고, "
              "act 는 **실제로 한 말의 의도**를 적는다.")
    return f"너의 성격: {페르소나설명}\n\n지금까지 대화:\n{대화}\n\n{지시}\nJSON 하나만 출력."


class 가짜:
    """--fake: 키 없이 배선만 본다. 정해 둔 말들을 차례로 낸다(의도 행위를 달고)."""
    말들 = [("안녕하세요", "greet"), ("너는 누구야?", "about_self"), ("주홍 컵 찾아줘", "task"), ("빨간 컵 찾아줘", "task"),
           ("네", "yes"), ("yo", "greet"), ("뭐 할 수 있어?", "capability"), ("오늘 저녁 메뉴 추천해줘", "out_of_scope"),
           ("고마워", "thanks"), ("이만 가볼게요 안녕히 계세요", "bye"), ("틀렸잖아", "complaint"), ("WebAuthn이 뭐야?", "knowledge")]

    def __init__(self):
        self.i = 0

    def __call__(self, prompt: str) -> str:
        s, a = self.말들[self.i % len(self.말들)]
        self.i += 1
        return json.dumps({"say": s, "act": a}, ensure_ascii=False)


def _walp_행위(who_h: str, since: float) -> "tuple[str | None, str | None]":
    from walp import usability
    for z in reversed(usability.읽기()):
        if z.get("ts", 0) < since:
            break
        if z.get("who") == who_h and z.get("kind") == "dialog":
            return z.get("act"), z.get("by")
    return None, None


def _찍기(s: str) -> None:
    print(time.strftime("%m-%d %H:%M ") + s, flush=True)


def 돌리기(시간: float = 24.0, 턴상한: int = 0, rpm: int = 10, rpd: int = 1000, 페르소나수: int = 3, 쓰기: bool = False,
         llm=None, 씨앗: int = 0, 출력=_찍기, 쉬기=time.sleep, 시계=time.time) -> dict:
    """대화를 돌린다. llm(prompt)->str 을 주면 그것을(시험 · --fake), 아니면 Gemini 를 부른다.
    시계 · 쉬기는 시험이 가짜로 바꾼다(쉬기가 시계를 앞으로 민다) — 진짜로는 time.time · time.sleep."""
    from walp import chat, usability
    rng = random.Random(씨앗 or int(시계()))
    key = ""
    if llm is None:
        key = _열쇠()
        if not key:
            raise SystemExit("GEMINI_API_KEY 가 없다 — 환경 변수나 .env 에 넣어라(키 없이 배선만 보려면 --fake).")
        llm = lambda p: gemini(p, key)      # noqa: E731
    간격 = max(60.0 / max(1, rpm), 86400.0 / max(1, rpd))
    끝 = 시계() + 시간 * 3600
    기록 = _상태자리() / "walp_partner.jsonl"
    호출들: list = []
    통계 = {"턴": 0, "호출": 0, "429": 0, "오류": 0, "고침": 0, "맞음": 0, "잰것": 0}
    시간별: dict = {}
    p = 0
    역사: list = []
    남은턴 = rng.randint(12, 25)
    연속오류 = 0
    t_시작 = 시계()
    while 시계() < 끝 and (not 턴상한 or 통계["턴"] < 턴상한):
        if 남은턴 <= 0:                               # 새 세션: 다른 페르소나, 대화 새로
            p = (p + 1) % 페르소나수
            역사, 남은턴 = [], rng.randint(12, 25)
        who = f"gemini-{p}"
        who_h = usability.누구(who)
        권 = 권할행위(rng, 역사[-1][1] if 역사 and 역사[-1][0] == "walp" else "")
        # 하루 한도: 지난 24시간 호출 수
        지금 = 시계()
        호출들 = [t for t in 호출들 if 지금 - t < 86400]
        if len(호출들) >= rpd:
            쉬기(max(1.0, 86400 - (지금 - 호출들[0])))
            continue
        try:
            호출들.append(시계())
            통계["호출"] += 1
            d = _뽑기(llm(프롬프트(페르소나[p % len(페르소나)], 역사, 권)))
            연속오류 = 0
        except 한도초과 as e:
            통계["429"] += 1
            출력(f"[partner] 429 — {e.쉼:.0f}초 쉰다")
            쉬기(e.쉼)
            continue
        except RuntimeError as e:
            통계["오류"] += 1
            연속오류 += 1
            출력(f"[partner] 오류: {e}")
            if 연속오류 >= 8:
                출력("[partner] 오류가 여덟 번 이어져 멈춘다 — 키·모델 이름·망을 보라")
                break
            쉬기(min(300.0, 15.0 * 연속오류))
            continue
        if not d:
            통계["오류"] += 1
            쉬기(간격)
            continue
        t0 = time.time()
        답 = chat.한마디(d["say"], who=who, via="gemini", allow_write=쓰기)
        w_act, w_by = _walp_행위(who_h, t0)
        역사 += [("me", d["say"]), ("walp", _줄임(답))]
        남은턴 -= 1
        통계["턴"] += 1
        고침 = None
        # 행위를 잘못 알아들었으면 사람처럼 고친다 — 찾기인데 '모르는 말' 은 고쳐 말하기 고리에 맡긴다
        if d["act"] not in ("yes", "no") and w_act != d["act"] and not (d["act"] == "task" and w_act in (None, "task")):
            고침 = chat.한마디(f"행위 {ACT_KO[d['act']]}", who=who, via="gemini", allow_write=쓰기)
            통계["고침"] += 1
        if d["act"] not in ("yes", "no"):
            맞 = (w_act == d["act"]) or (d["act"] == "task" and w_act in (None, "task"))
            통계["잰것"] += 1
            통계["맞음"] += 맞
            h = int((시계() - t_시작) // 3600)
            시간별.setdefault(h, [0, 0])
            시간별[h][0] += 맞
            시간별[h][1] += 1
        with open(기록, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": round(time.time(), 1), "who": who, "권함": 권, "say": d["say"], "의도": d["act"],
                                "walp_act": w_act, "walp_by": w_by, "답": _줄임(답, 600), "고침": bool(고침)},
                               ensure_ascii=False) + "\n")
        if 통계["턴"] % 50 == 0:
            출력(요약(통계, 시간별))
        쉬기(간격)
    통계["시간별"] = {h: f"{m}/{n}" for h, (m, n) in sorted(시간별.items())}
    출력(요약(통계, 시간별))
    return 통계


def 요약(통계: dict, 시간별: dict) -> str:
    n = 통계["잰것"] or 1
    시간 = " · ".join(f"{h}시간째 {m}/{k}({m / k:.0%})" for h, (m, k) in sorted(시간별.items())[-6:] if k)
    return (f"[partner] 턴 {통계['턴']} · 호출 {통계['호출']} · 429 {통계['429']} · 오류 {통계['오류']} · 고침 {통계['고침']} · "
            f"WALP 가 의도를 맞힘 {통계['맞음']}/{통계['잰것']} ({통계['맞음'] / n:.0%})" + (f"\n[partner] {시간}" if 시간 else ""))


def main() -> int:
    global MODEL
    ap = argparse.ArgumentParser(description="Gemini 가 사람 역할로 WALP 와 대화한다 — WALP 는 대화로 자란다")
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--turns", type=int, default=0, help="턴 상한(0 = 시간만)")
    ap.add_argument("--rpm", type=int, default=int(os.environ.get("GEMINI_RPM", "10")))
    ap.add_argument("--rpd", type=int, default=int(os.environ.get("GEMINI_RPD", "1000")))
    ap.add_argument("--personas", type=int, default=3)
    ap.add_argument("--model", default=MODEL, help=f"Gemini 모델 이름(기본 {MODEL}, 환경 WALP_PARTNER_MODEL)")
    ap.add_argument("--write", action="store_true", help="쓰기 권한 — 고침·낱말이 페르소나 하나의 말로도 바로 들어간다(빠르지만 LLM 실수도 바로 들어간다)")
    ap.add_argument("--fake", action="store_true", help="키 없이 정해 둔 말로 배선만 본다")
    a = ap.parse_args()
    MODEL = a.model
    llm = 가짜() if a.fake else None
    돌리기(a.hours, a.turns, a.rpm, a.rpd, max(1, min(a.personas, len(페르소나))), a.write, llm=llm,
         쉬기=(lambda s: None) if a.fake else time.sleep)
    return 0


if __name__ == "__main__":
    sys.exit(main())
