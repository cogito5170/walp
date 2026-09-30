"""WALP 센서 늘리기 — 디스코드가 주는데 봇이 안 받던 신호 둘: **반응 이모지 · 메시지 고침**.

사용자(2026-09-30): "센서부터 늘려라, 반응 이모지랑 메시지 수정 받게." 선행조사
`paper/선행조사/WALP_진화대화.md` §2 — 센서–센서 자기지도의 '싼 먼 센서' 를 늘리는 첫 걸음.

**센서만이다 — 행동을 안 바꾼다.** 고친 `!walp` 글을 다시 돌리지 않고, 반응에 답하지 않는다.
무슨 일이 났는지를 사용성 원장(`usability.적기`)에 한 줄 적을 뿐이다. 판정(좋다/나쁘다)도 하지
않는다 — 이모지의 극성은 손으로 적은 작은 표(`_극성`)로 **따로 적어 둘 뿐**, 원래 이모지를 같이 남긴다.

어느 메시지가 WALP 의 것인지 알아야 한다. 봇이 `!walp` 요청에 답할 때 `이어두기` 로
(요청 id · 답 id들 · 요청한 사람 · 요청 글의 지문)을 **저장소 밖**(`usability.상태자리()`)에 적어 둔다.
반응은 **답 메시지**에 달린 것만, 고침은 **요청 메시지**를 고친 것만 센다 — 다른 메시지는 버린다
(남의 대화까지 원장에 쓸어 담지 않는다).

원장 줄:
    kind=reaction         who · emoji · 극성(+1/-1/0) · 본인(요청한 사람이 달았나) · 늦음_s(답 뒤 몇 초)
    kind=reaction_remove  같은 칸 — 마음을 바꿨다
    kind=edit             who · text(새 글, ≤400자) · 본인 · 늦음_s(요청 뒤 몇 초) · walp(새 글도 `!walp` 인가)
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from walp import usability

MAX_LINKS = 500                  # 오래된 것부터 버린다 — 표가 끝없이 자라지 않게
MAX_AGE_S = 7 * 24 * 3600        # 일주일 지난 답에 단 반응은 안 센다

# 손으로 적은 극성 표(W0). 모르는 이모지는 0 — **추측하지 않는다.** 원래 이모지는 늘 같이 적는다.
_극성 = {
    "👍": 1, "✅": 1, "❤️": 1, "❤": 1, "🙏": 1, "😀": 1, "😄": 1, "😊": 1, "🎉": 1, "👌": 1, "💯": 1, "🙆": 1,
    "👎": -1, "❌": -1, "😕": -1, "😞": -1, "😡": -1, "😠": -1, "🙅": -1, "💢": -1, "😩": -1,
}


def 극성(emoji: str) -> int:
    e = (emoji or "").strip()
    if e in _극성:
        return _극성[e]
    return _극성.get(e.replace("️", ""), 0)   # 변이 선택자(FE0F) 유무를 같게 본다


def _표경로() -> Path:
    return usability.상태자리() / "walp_msglinks.json"


def _읽기() -> dict:
    try:
        d = json.loads(_표경로().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _쓰기(d: dict) -> None:
    p = _표경로()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def _지문(text: str) -> str:
    return hashlib.sha256((text or "").strip().encode()).hexdigest()[:16]


def 이어두기(요청id, 답ids, 요청한사람: str, 요청글: str, now: "float | None" = None) -> None:
    """봇이 `!walp` 요청에 답한 직후 부른다. 답이 여러 쪽이면 쪽마다 같은 요청에 잇는다."""
    now = time.time() if now is None else now
    d = _읽기()
    req = d.setdefault("req", {})
    rep = d.setdefault("rep", {})
    req[str(요청id)] = {"ts": now, "who": usability.누구(요청한사람), "fp": _지문(요청글)}
    for a in 답ids:
        rep[str(a)] = str(요청id)
    # 오래된 것 · 넘치는 것 버리기(요청 기준, 그 요청의 답도 같이)
    살림 = sorted(req.items(), key=lambda kv: kv[1].get("ts", 0))
    살림 = [kv for kv in 살림 if now - kv[1].get("ts", 0) <= MAX_AGE_S][-MAX_LINKS:]
    d["req"] = dict(살림)
    d["rep"] = {a: r for a, r in rep.items() if r in d["req"]}
    _쓰기(d)


def 반응(답id, 단사람: str, emoji: str, 뺐나: bool = False, via: str = "discord",
       now: "float | None" = None) -> "dict | None":
    """WALP 답에 달린 반응만 적는다. 적었으면 그 줄을, 아니면 None."""
    now = time.time() if now is None else now
    d = _읽기()
    r = d.get("rep", {}).get(str(답id))
    q = d.get("req", {}).get(r) if r else None
    if not q or now - q.get("ts", 0) > MAX_AGE_S:
        return None
    who = usability.누구(단사람)
    줄 = {"who": who, "via": via, "kind": "reaction_remove" if 뺐나 else "reaction", "emoji": emoji,
         "극성": 극성(emoji), "본인": who == q.get("who"), "늦음_s": round(now - q.get("ts", now), 1)}
    usability.적기(줄)
    return 줄


def 고침(요청id, 고친사람: str, 새글: str, via: str = "discord", now: "float | None" = None) -> "dict | None":
    """WALP 요청 메시지를 고친 것만 적는다. 글이 안 바뀐 수정(링크 미리보기 붙기 등)은 버린다."""
    now = time.time() if now is None else now
    d = _읽기()
    q = d.get("req", {}).get(str(요청id))
    if not q or now - q.get("ts", 0) > MAX_AGE_S:
        return None
    fp = _지문(새글)
    if fp == q.get("fp"):
        return None
    q["fp"] = fp                     # 같은 고침이 두 번 와도 한 번만 센다
    _쓰기(d)
    who = usability.누구(고친사람)
    t = (새글 or "").strip()
    줄 = {"who": who, "via": via, "kind": "edit", "text": t[:400], "본인": who == q.get("who"),
         "늦음_s": round(now - q.get("ts", now), 1), "walp": t == "!walp" or t.startswith("!walp ")}
    usability.적기(줄)
    return 줄
