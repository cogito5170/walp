"""LLM 앞단 실험 — 사전등록 `walp/eval/PREREG_LLM앞단.md`(수정 1 포함) 그대로. 봉인 요청 흐름 v1 을 **한 번** 연다.

    python3 -m walp.fronttest --기본만                         # 기본 체계만 길러 저장(40–60분, 이어 돌기 가능)
    python3 -m walp.fronttest --out walp/eval/results/front_v1.json

LLM 은 신탁(그 줄의 정답)이다 — 실제 Claude 를 부르지 않는다. 그래서 이 수는 **배움의 배선 상한**이다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import time
from pathlib import Path

from walp import behavior as B
from walp import deliberate as DL
from walp import dialog as D
from walp import evotest as E

HERE = Path(__file__).resolve().parent
STREAM = HERE / "eval" / "stream_v1.tsv"
STREAM_SHA = "737cb519c764fb1f3afc4afd12fe245999f9169e8e0cbee65fe97befe32bda45"
학습파일 = list(D.TRAINS) + [HERE / "eval" / f"dialog_act_test_v{k}.tsv" for k in (5, 6, 7)]
CKPT = Path(os.environ.get("WALP_CKPT_DIR") or "/tmp/claude-0/ckpt_front")
열린 = {"knowledge", "out_of_scope"}


def 학습자료() -> list:
    return [(t, a) for p in 학습파일 for a, t in D.모음(p)]


def 기본체계(seed: int = 1) -> dict:
    p = CKPT / f"base_seed{seed}.json"
    if p.exists():
        return B.from_json(json.loads(p.read_text(encoding="utf-8")))
    체 = B.기르기(학습자료(), seed=seed)
    CKPT.mkdir(parents=True, exist_ok=True)
    p.with_suffix(".tmp").write_text(B.dumps(체), encoding="utf-8")
    os.replace(p.with_suffix(".tmp"), p)
    return 체


def 흐름() -> list:
    if hashlib.sha256(STREAM.read_bytes()).hexdigest() != STREAM_SHA:
        raise SystemExit("봉인 흐름 sha 가 다르다 — 돌리지 않는다")
    out = []
    for line in open(STREAM, encoding="utf-8"):
        if not line.strip() or line.startswith("#"):
            continue
        act, g, text, ans = line.rstrip("\n").split("\t")
        out.append({"act": act, "group": g, "text": text, "answer": ans})
    return out


def _자기맞음(r: dict, 줄: dict, 묶음표: dict) -> bool:
    """스스로 답한 것이 맞나(수정 1): 답 캐시면 묶음이 같을 때, 그 밖은 행위가 같을 때."""
    o = r["행한것"] or {}
    if r["승자"] == "답캐시":
        return 묶음표.get(o.get("배운")) == 줄["group"]
    if r["승자"] == "숙고":
        return 줄["act"] == "task"
    return o.get("행위") == 줄["act"]


def 돌리기(체: dict, 줄들: list, 조건: str, seed: int = 1) -> dict:
    """조건 하나. 요청마다 (부름 0/1, 스스로 틀림 0/1) 과 토큰 어림을 돌려준다."""
    신탁 = DL.신탁숙고({z["text"]: (z["act"], z["answer"]) for z in 줄들})
    부름, 틀림 = [], []
    원장: list = []
    묶음표: dict = {}            # 캐시 id → 그 답의 묶음(신탁이 알려 준 것)
    정확: dict = {}             # EXACT: 알맹이 → 묶음
    라벨: list = []
    n = len(줄들)
    재진화 = {n // 3, 2 * n // 3}
    for i, 줄 in enumerate(줄들):
        if 조건 == "LEARN" and i in 재진화 and 라벨:
            체 = dict(체)
            센 = 체["센서"]
            체["반응"] = B._L0기르기([(센.읽기(t), a) for t, a in 학습자료() + 라벨], seed)
        if 조건 == "ALL":
            신탁.묻기(줄["text"])
            부름.append(1)
            틀림.append(0)
            continue
        if 조건 == "EXACT" and D._알맹이(줄["text"]) in 정확:
            부름.append(0)
            틀림.append(int(정확[D._알맹이(줄["text"])] != 줄["group"]))
            continue
        캐시 = B.캐시읽기(원장) if 조건 == "LEARN" else None
        r = B.버스짓기(체, 캐시=캐시).돌기(줄["text"])
        if B.모름(r):
            d = 신탁.묻기(줄["text"])
            부름.append(1)
            틀림.append(0)
            if 조건 == "EXACT" and d["행위"] in 열린:
                정확[D._알맹이(줄["text"])] = 줄["group"]
            if 조건 == "LEARN":
                기록: list = []
                B.숙고결과(d, 줄["text"], 기록)
                for z in 기록:
                    원장.append({"ts": i, **z})
                    if z["kind"] == "llm_answer":
                        묶음표[z["id"]] = 줄["group"]
                    if z["kind"] == "act_fix":
                        라벨.append((z["text"], z["act"]))
            continue
        부름.append(0)
        틀림.append(int(not _자기맞음(r, 줄, 묶음표)))
    s = 신탁.통계
    return {"조건": 조건, "부름": 부름, "틀림": 틀림, "호출": sum(부름), "스스로틀림": sum(틀림),
            "입력토큰_어림": s["입력토큰"], "출력토큰_어림": s["출력토큰"]}


def _부트위(d: list, n회: int = 10000) -> float:
    rng = random.Random(0)
    ms = sorted(sum(d[rng.randrange(len(d))] for _ in d) / len(d) for _ in range(n회))
    return ms[int(0.95 * n회) - 1]


def 분석(res: dict, n: int) -> dict:
    L, N = res["LEARN"], res["NOLEARN"]
    w = sum(1 for a, b in zip(L["부름"], N["부름"]) if a < b)      # LEARN 은 안 부르고 NOLEARN 은 부름
    l = sum(1 for a, b in zip(L["부름"], N["부름"]) if a > b)
    H1 = {"이김(덜 부름)": w, "짐": l, "p": E._p_단측(w, l)}
    H1["섬"] = H1["p"] < 0.05 and w > l
    diff = [a - b for a, b in zip(L["틀림"], N["틀림"])]
    H2 = {"LEARN틀림률": sum(L["틀림"]) / n, "NOLEARN틀림률": sum(N["틀림"]) / n, "차_평균": sum(diff) / n,
          "단측95%위한계": _부트위(diff), "여유": 0.02}
    H2["섬"] = H2["단측95%위한계"] <= 0.02
    대EXACT = {"LEARN호출": L["호출"], "EXACT호출": res["EXACT"]["호출"],
               "LEARN만_안부름": sum(1 for a, b in zip(L["부름"], res["EXACT"]["부름"]) if a < b),
               "EXACT만_안부름": sum(1 for a, b in zip(L["부름"], res["EXACT"]["부름"]) if a > b)}
    return {"H1": H1, "H2": H2, "대EXACT": 대EXACT, "결정_배움켬": H1["섬"] and H2["섬"]}


def 보이기(r: dict) -> str:
    n = r["n"]
    줄 = [f"봉인 요청 흐름 v1 · {n}요청 · {r['초']}초 (LLM = 신탁 — 배선 상한, 실사용 수 아님)"]
    for k in ("ALL", "EXACT", "NOLEARN", "LEARN"):
        x = r["조건"][k]
        줄.append(f"  {k:8s} LLM 호출 {x['호출']:3d} ({x['호출'] / n:.1%}) · 스스로 틀림 {x['스스로틀림']:3d} ({x['스스로틀림'] / n:.1%})"
                 f" · 토큰 어림 {x['입력토큰_어림'] + x['출력토큰_어림']:,}")
    a = r["분석"]
    줄.append(f"  H1 LEARN 이 덜 부름: {a['H1']['이김(덜 부름)']} / 더 부름 {a['H1']['짐']} p={a['H1']['p']:.3g} → {'섬' if a['H1']['섬'] else '안 섬'}")
    h = a["H2"]
    줄.append(f"  H2 스스로 틀림 LEARN {h['LEARN틀림률']:.1%} vs NOLEARN {h['NOLEARN틀림률']:.1%} · 차 위 한계 {h['단측95%위한계']:+.3f} ≤ 0.02 ? → "
             f"{'섬' if h['섬'] else '안 섬'}")
    e = a["대EXACT"]
    줄.append(f"  대조 정확일치 캐시: LEARN 만 안 부름 {e['LEARN만_안부름']} · EXACT 만 안 부름 {e['EXACT만_안부름']}")
    줄.append(f"**결정: 배움 켬 = {'예' if a['결정_배움켬'] else '아니오'}**")
    return "\n".join(줄)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--기본만", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    체 = 기본체계()
    if a.기본만:
        print("기본 체계 저장:", CKPT, round(time.time() - t0), "초")
        return 0
    줄들 = 흐름()
    print("봉인 흐름 v1 sha", STREAM_SHA, flush=True)
    res = {}
    for k in ("ALL", "EXACT", "NOLEARN", "LEARN"):
        p = CKPT / f"cond_{k}.json"
        if p.exists():
            res[k] = json.loads(p.read_text(encoding="utf-8"))
            continue
        res[k] = 돌리기(체, 줄들, k)
        p.write_text(json.dumps(res[k], ensure_ascii=False), encoding="utf-8")
        print(f"  {k} 끝 ({round(time.time() - t0)}초)", flush=True)
    r = {"n": len(줄들), "조건": res, "분석": 분석(res, len(줄들)), "초": round(time.time() - t0)}
    if a.out:
        Path(a.out).write_text(json.dumps({**r, "조건": {k: {kk: vv for kk, vv in v.items() if kk not in ("부름", "틀림")}
                                                        for k, v in res.items()}}, ensure_ascii=False, indent=1),
                               encoding="utf-8")
    print(보이기(r), flush=True)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
