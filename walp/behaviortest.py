"""행동 기반(포섭) 실험 — 사전등록 `walp/eval/PREREG_행동기반.md` 그대로. 봉인 v6 을 **한 번** 연다.

    python3 -m walp.behaviortest --smoke                     # 학습 자료 일부로 배선만(봉인 아님)
    python3 -m walp.behaviortest --out walp/eval/results/behavior_v6.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import time
from multiprocessing import Pool
from pathlib import Path

from walp import behavior as B
from walp import dialog as D
from walp import evotest as E

HERE = Path(__file__).resolve().parent
TRAIN_FILES = list(D.TRAINS) + [HERE / "eval" / "dialog_act_test_v5.tsv"]
TEST_V6 = HERE / "eval" / "dialog_act_test_v6.tsv"
V6_SHA = "5c3c47b4f7e8a43f798c0363afbd4a5e6e088481be1fe466fd69b5cbc4a7ebb6"
SEEDS = [1, 2, 3, 4, 5]
CS = [0.1, 0.2, 0.3, 0.5, 0.7, 0.9]


def _작업(arg) -> dict:
    seed, 학습, 시험글, 다수, 성장만들기 = arg
    t0 = time.time()
    체 = B.기르기(학습, seed=seed, c=B.C, 성장만들기=성장만들기)
    센, l0 = 체["센서"], 체["반응"]
    L1 = {c: (체["되묻기"] if c == B.C else B.L1기르기(체["에피소드"], c, seed)) for c in CS}
    θ = {c: B.문턱고르기(체["oof"], c) for c in CS}
    out = {"seed": seed, "A": [], "C": [], "B물음": {c: [] for c in CS}, "D물음": {c: [] for c in CS}, "성장확신": [],
           "θ": θ, "oof_L0정확도": sum(o["맞음"] for o in 체["oof"]) / len(체["oof"]),
           "oof_물음률": {c: sum(L1[c].x.predict(b)[0] == 1 for b, _ in 체["에피소드"]) / len(체["에피소드"]) for c in CS}}
    for t in 시험글:
        상태 = 센.읽기(t)
        o0 = l0.행(상태, {})
        out["A"].append(D._모드들(센.성장, t, 다수)["채움"] or "task")
        out["C"].append(o0["행위"])
        out["성장확신"].append(상태["성장확신"])
        for c in CS:
            out["B물음"][c].append(L1[c].행(상태, {"반응": o0}) is not None)
            out["D물음"][c].append(상태["성장확신"] < θ[c])
    out["sec"] = round(time.time() - t0, 1)
    return out


def _다수(L: list, i: int):
    from collections import Counter
    v, n = Counter(x[i] for x in L).most_common(1)[0]
    return v if n * 2 > len(L) else None


def 실험(학습: list, 시험: list, procs: int = 4, seeds=SEEDS, 이름: str = "봉인 v6", 성장만들기=None) -> dict:
    t0 = time.time()
    시험알 = {D._알맹이(t) for _, t in 시험}
    뺌 = [t for t, _ in 학습 if D._알맹이(t) in 시험알]
    학습 = [(t, a) for t, a in 학습 if D._알맹이(t) not in 시험알]      # 사전등록: 봉인과 알맹이가 같은 **학습** 문장을 뺀다
    gold = [a for a, _ in 시험]
    시험글 = [t for _, t in 시험]
    빈 = [a for t, a in 학습 if D.손규칙(t, D._파스(t)) is None]
    다수 = max(set(빈), key=빈.count) if 빈 else "task"
    인자 = [(s, 학습, 시험글, 다수, 성장만들기) for s in seeds]
    if procs > 1:
        with Pool(procs) as pool:
            res = pool.map(_작업, 인자, chunksize=1)
    else:
        res = [_작업(a) for a in 인자]
    n = len(gold)
    A = [_다수([r["A"] for r in res], i) for i in range(n)]
    Cc = [_다수([r["C"] for r in res], i) for i in range(n)]
    Am = [a == g for a, g in zip(A, gold)]
    Cm = [a == g for a, g in zip(Cc, gold)]

    def 물음(key, c):
        return [sum(r[key][c][i] for r in res) * 2 > len(res) for i in range(n)]

    def u답(m):
        return [B.효용(x) for x in m]

    쓸기 = {}
    for c in CS:
        bq, dq = 물음("B물음", c), 물음("D물음", c)
        uB = [(1 - c) if q else B.효용(m) for q, m in zip(bq, Cm)]
        uD = [(1 - c) if q else B.효용(m) for q, m in zip(dq, Cm)]
        쓸기[c] = {"B": sum(uB) / n, "C": sum(u답(Cm)) / n, "A": sum(u답(Am)) / n, "D": sum(uD) / n, "E": 1 - c,
                  "B물음률": sum(bq) / n, "D물음률": sum(dq) / n,
                  # 독립 대조: L1 이 묻는 문장에서 L0 이 실제로 더 틀리나(묻지 않은 문장 대비)
                  "물은곳_L0오답률": (sum(not m for q, m in zip(bq, Cm) if q) / max(1, sum(bq))),
                  "안물은곳_L0오답률": (sum(not m for q, m in zip(bq, Cm) if not q) / max(1, n - sum(bq))),
                  "_uB": uB, "_uD": uD}
    c0 = B.C

    def 짝(u1, u2):
        w = sum(1 for x, y in zip(u1, u2) if x > y)
        l = sum(1 for x, y in zip(u1, u2) if x < y)
        return w, l, E._p_단측(w, l)
    uB, uD = 쓸기[c0]["_uB"], 쓸기[c0]["_uD"]
    검정 = {"H1 B>C": 짝(uB, u답(Cm)), "H2 B>D": 짝(uB, uD), "H3 B>A": 짝(uB, u답(Am))}

    def 순열(u1, u2, n회=20000):          # 수정 1(b): 보조 — 효용 차 평균의 짝 부호뒤집기(단측)
        d = [x - y for x, y in zip(u1, u2)]
        obs = sum(d)
        rng = random.Random(0)
        ge = sum(1 for _ in range(n회) if sum(x if rng.random() < 0.5 else -x for x in d) >= obs - 1e-12)
        return {"평균차": obs / len(d), "p": (ge + 1) / (n회 + 1)}
    보조 = {"H1 B>C": 순열(uB, u답(Cm)), "H2 B>D": 순열(uB, uD), "H3 B>A": 순열(uB, u답(Am))}
    holm = E._holm({k: v[2] for k, v in 검정.items()})
    섬 = {k: holm[k]["섬"] and 검정[k][0] > 검정[k][1] for k in 검정}
    wA = sum(1 for a, c in zip(Am, Cm) if a and not c)
    lA = sum(1 for a, c in zip(Am, Cm) if c and not a)
    N1 = {"A맞고C틀림": wA, "C맞고A틀림": lA, "p(A>C)": E._p_단측(wA, lA)}
    N1["기각"] = N1["p(A>C)"] < 0.05 and wA > lA
    BleE = 쓸기[c0]["B"] <= 쓸기[c0]["E"]
    배포 = 섬["H1 B>C"] and 섬["H3 B>A"] and not N1["기각"] and not BleE
    for c in CS:
        쓸기[c] = {k: v for k, v in 쓸기[c].items() if not k.startswith("_")}
    return {"시험": 이름, "n_test": n, "학습에서_뺌": len(뺌), "n_train": len(학습), "다수행위": 다수,
            "정확도": {"A 기존": sum(Am) / n, "C 선끊음(L0)": sum(Cm) / n}, "쓸기(c)": 쓸기,
            "검정(이김,짐,p)": {k: list(v) for k, v in 검정.items()}, "Holm": holm, "섬": 섬, "N1": N1, "보조_순열": 보조,
            "B가_늘묻기보다_못함": BleE, "결정_배포": 배포,
            "씨앗별": [{k: r[k] for k in ("seed", "θ", "oof_L0정확도", "oof_물음률", "sec")} for r in res],
            "초": round(time.time() - t0, 1)}


def 보이기(r: dict) -> str:
    c0 = B.C
    s = r["쓸기(c)"]
    줄 = [f"{r['시험']} {r['n_test']}문장 · 학습 {r['n_train']}(봉인과 겹쳐 뺀 {r['학습에서_뺌']}) · {r['초']}초",
          "답 정확도: " + " · ".join(f"{k} {v:.1%}" for k, v in r["정확도"].items()),
          f"효용(c={c0}) 문장당: A {s[c0]['A']:.3f} · B {s[c0]['B']:.3f} · C {s[c0]['C']:.3f} · D {s[c0]['D']:.3f} · E {s[c0]['E']:.3f}"
          f"  (B 물음률 {s[c0]['B물음률']:.1%}, D {s[c0]['D물음률']:.1%})",
          f"  대조: B 가 물은 곳의 L0 오답률 {s[c0]['물은곳_L0오답률']:.1%} vs 안 물은 곳 {s[c0]['안물은곳_L0오답률']:.1%}"]
    for k, v in r["검정(이김,짐,p)"].items():
        줄.append(f"  {k}: {v[0]}승 {v[1]}패 p={v[2]:.4g} (Holm 문턱 {r['Holm'][k]['문턱']:.4g}) → {'섬' if r['섬'][k] else '안 섬'}")
    for k, v in r["보조_순열"].items():
        줄.append(f"  (보조) {k}: 평균 효용 차 {v['평균차']:+.3f} p={v['p']:.4g}")
    n1 = r["N1"]
    줄.append(f"  N1 A>C: {n1['A맞고C틀림']}/{n1['C맞고A틀림']} p={n1['p(A>C)']:.4g} → {'기각(L0 이 못하다)' if n1['기각'] else '퇴행 못 찾음'}")
    줄.append("c 쓸기: " + " | ".join(f"c={c}: B {v['B']:.3f} C {v['C']:.3f} D {v['D']:.3f} E {v['E']:.2f} 물음 {v['B물음률']:.0%}"
                                     for c, v in ((float(k), v) for k, v in s.items())))
    줄.append(f"**결정: 배포 = {'예' if r['결정_배포'] else '아니오'}**" + (" (B ≤ 늘묻기)" if r["B가_늘묻기보다_못함"] else ""))
    return "\n".join(줄)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--procs", type=int, default=4)
    a = ap.parse_args()
    학습 = [(t, act) for p in TRAIN_FILES for act, t in D.모음(p)]
    if a.smoke:
        random.Random(0).shuffle(학습)
        r = 실험(학습[100:500], [(act, t) for t, act in 학습[:100]], procs=a.procs, seeds=[1, 2, 3],
               이름="smoke(학습 자료 일부 — 봉인 아님)")
    else:
        sha = hashlib.sha256(TEST_V6.read_bytes()).hexdigest()
        if sha != V6_SHA:
            print("봉인 v6 sha 가 다르다 — 돌리지 않는다", sha)
            return 2
        print("봉인 v6 sha", sha, flush=True)
        r = 실험(학습, D.모음(TEST_V6), procs=a.procs)
    if a.out:
        Path(a.out).write_text(json.dumps(r, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(보이기(r), flush=True)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
