"""층 순서 × ESN 실험 — 사전등록 `walp/eval/PREREG_층순서_ESN_숙고기.md`(A) 그대로. 봉인 v5 를 **한 번** 연다.

    python3 -m walp.ordertest --out walp/eval/results/order_esn_v5.json
    python3 -m walp.ordertest --smoke
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import time
from multiprocessing import Pool
from pathlib import Path

from walp import dialog as D
from walp import evotest as E
from walp import grownet as G
from walp import growtest as GT

HERE = Path(__file__).resolve().parent
TRAIN_FILES = GT.TRAIN_FILES + [HERE / "eval" / "dialog_act_test_v4.tsv"]
TEST_V5 = HERE / "eval" / "dialog_act_test_v5.tsv"
SEEDS = [1, 2, 3, 4, 5]
N_ACT = len(D.ACTS)


def _작업(arg) -> dict:
    seed, 학습, 시험글, 어휘 = arg
    t0 = time.time()
    n_bits = len(D.입력("x", {}, 어휘))
    x = D.훈련(학습, [], seed=seed, ga=True, 어휘=어휘)
    후보 = G.후보뽑기(x, 300)
    out = {"seed": seed, "preds": {}, "단위": {}}
    m = G.성장망(n_bits, N_ACT, 후보, seed).fit(GT._자료(학습, 어휘, None))
    out["preds"]["GROW"] = GT._예측(m, 시험글, 어휘, None)
    out["단위"]["GROW"] = len(m.단위)
    esn = G.ESN(seed)
    m2 = G.성장망(n_bits, N_ACT, 후보, seed, esn_n=esn.n).fit(GT._자료(학습, 어휘, esn))
    out["preds"]["GROW+ESN"] = GT._예측(m2, 시험글, 어휘, esn)
    out["단위"]["GROW+ESN"] = len(m2.단위)
    out["sec"] = round(time.time() - t0, 1)
    return out


def 실험(학습: list, 시험: list, procs: int = 4, seeds=SEEDS, 이름: str = "봉인 v5") -> dict:
    t0 = time.time()
    학습글 = {D._알맹이(t) for t, _ in 학습}
    뺌 = [t for a, t in 시험 if D._알맹이(t) in 학습글]
    시험 = [(a, t) for a, t in 시험 if D._알맹이(t) not in 학습글]
    gold = [a for a, _ in 시험]
    시험글 = [t for _, t in 시험]
    어휘 = D.어휘기르기(학습)
    파 = [D._파스(t) for t in 시험글]
    p_ok = [r.get("status") == "ok" for r in 파]
    손 = [D.손규칙(t, r) for t, r in zip(시험글, 파)]
    빈 = [a for t, a in 학습 if D.손규칙(t, D._파스(t)) is None]
    다수 = max(set(빈), key=빈.count) if 빈 else "task"
    with Pool(procs) as pool:
        res = pool.map(_작업, [(s, 학습, 시험글, 어휘) for s in seeds], chunksize=1)

    def 손먼저(p):
        return [("task" if ok else None) or h or (a if c >= 0.5 else None) for ok, h, (a, c) in zip(p_ok, 손, p)]

    def 학습기먼저(p):
        return [("task" if ok else None) or (a if c >= 0.5 else None) or h for ok, h, (a, c) in zip(p_ok, 손, p)]

    def 만(p):
        return [a for a, _ in p]

    def 다수결(L):
        return [g if sum(1 for x in L if x[i] == g) * 2 > len(L) else None for i, g in enumerate(gold)]

    cond = {"손만": 손, "손+다수": [h or 다수 for h in 손]}
    씨앗별: dict = {}
    for k in ("GROW", "GROW+ESN"):
        ps = [r["preds"][k] for r in res]
        cond[f"손먼저({k})"] = 다수결([손먼저(p) for p in ps])
        cond[f"학습기먼저({k})"] = 다수결([학습기먼저(p) for p in ps])
        cond[f"학습기만({k})"] = 다수결([만(p) for p in ps])
        씨앗별[k] = {"손먼저": [E._acc(손먼저(p), gold) for p in ps], "학습기먼저": [E._acc(학습기먼저(p), gold) for p in ps],
                   "학습기만": [E._acc(만(p), gold) for p in ps], "단위": [r["단위"][k] for r in res]}
    정확도 = {k: E._acc(v, gold) for k, v in cond.items()}
    검정 = {"O1 학습기먼저>손먼저": E._짝(cond["학습기먼저(GROW)"], cond["손먼저(GROW)"], gold),
            "O2 학습기먼저>손+다수": E._짝(cond["학습기먼저(GROW)"], cond["손+다수"], gold),
            "E1 ESN 곁들임(학습기만)": E._짝(cond["학습기만(GROW+ESN)"], cond["학습기만(GROW)"], gold),
            "E2 ESN 곁들임(학습기먼저)": E._짝(cond["학습기먼저(GROW+ESN)"], cond["학습기먼저(GROW)"], gold)}
    holm = E._holm({k: v[2] for k, v in 검정.items()})
    섬 = {k: holm[k]["섬"] and 검정[k][0] > 검정[k][1] for k in 검정}
    순서 = "학습기먼저" if 섬["O1 학습기먼저>손먼저"] and 섬["O2 학습기먼저>손+다수"] else "손먼저"
    esn = 섬["E1 ESN 곁들임(학습기만)"] and 섬["E2 ESN 곁들임(학습기먼저)"]
    return {"시험": 이름, "n_test": len(gold), "누수_뺌": len(뺌), "n_train": len(학습), "다수행위": 다수, "정확도": 정확도,
            "검정(이김,짐,p)": {k: list(v) for k, v in 검정.items()}, "Holm": holm, "섬": 섬,
            "결정_층순서": 순서, "결정_ESN": esn, "씨앗별": 씨앗별, "초": round(time.time() - t0, 1),
            "씨앗별_초": [r["sec"] for r in res]}


def 보이기(r: dict) -> str:
    줄 = [f"{r['시험']} {r['n_test']}문장(누수 뺌 {r['누수_뺌']}) · 학습 {r['n_train']} · {r['초']}초",
          "정확도: " + " · ".join(f"{k} {v:.1%}" for k, v in r["정확도"].items())]
    for k, v in r["검정(이김,짐,p)"].items():
        줄.append(f"  {k}: {v[0]}승 {v[1]}패 p={v[2]:.4g} (Holm 문턱 {r['Holm'][k]['문턱']:.4g}) → {'섬' if r['섬'][k] else '안 섬'}")
    for k, v in r["씨앗별"].items():
        줄.append(f"  {k} 씨앗별: {v}")
    줄.append(f"**결정: 층 순서 = {r['결정_층순서']} · ESN 곁들임 = {'예' if r['결정_ESN'] else '아니오'}**")
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
        r = 실험(학습[80:300], [(act, t) for t, act in 학습[:80]], procs=a.procs, seeds=[1, 2, 3],
               이름="smoke(학습 자료 일부 — 봉인 아님)")
    else:
        print("봉인 v5 sha", hashlib.sha256(TEST_V5.read_bytes()).hexdigest(), flush=True)
        r = 실험(학습, D.모음(TEST_V5), procs=a.procs)
    if a.out:
        Path(a.out).write_text(json.dumps(r, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(보이기(r), flush=True)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
