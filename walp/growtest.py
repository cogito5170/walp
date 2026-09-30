"""성장망 실험 — 사전등록 `walp/eval/PREREG_성장망.md` 그대로. 봉인 v4 를 **한 번** 연다.

    python3 -m walp.growtest --out walp/eval/results/grownet_v4.json      # 수십 분(CPU 4개)
    python3 -m walp.growtest --smoke                                     # 작게, 봉인 모음 대신 학습 모음 일부로
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

HERE = Path(__file__).resolve().parent
TRAIN_FILES = E.TRAIN_FILES + [HERE / "eval" / "dialog_act_test_v3.tsv"]
TEST_V4 = HERE / "eval" / "dialog_act_test_v4.tsv"
SEEDS = [1, 2, 3, 4, 5]
FRACS = [0.25, 0.5, 1.0]
N_ACT = len(D.ACTS)


def _자료(tr: list, 어휘: list, esn: "G.ESN | None"):
    return [(G.비트수(D.입력(t, D._파스(t), 어휘)), esn.상태(t) if esn else None, D.ACTS.index(a)) for t, a in tr]


def _예측(m, 시험글: list, 어휘: list, esn) -> list:
    out = []
    for t in 시험글:
        p = m.proba(G.비트수(D.입력(t, D._파스(t), 어휘)), esn.상태(t) if esn else None)
        k = max(range(N_ACT), key=lambda i: p[i])
        out.append((D.ACTS[k], p[k]))
    return out


def _작업(arg) -> dict:
    kind, seed, frac, 학습, 시험글, 어휘 = arg
    t0 = time.time()
    tr = list(학습)
    if frac < 1.0:
        random.Random(seed * 101 + int(frac * 100)).shuffle(tr)
        tr = tr[: max(12, int(len(tr) * frac))]
        어휘 = D.어휘기르기(tr)
    n_bits = len(D.입력("x", {}, 어휘))
    out = {"kind": kind, "seed": seed, "frac": frac, "n_train": len(tr), "preds": {}}
    if kind in ("GA", "X0"):
        x = D.훈련(tr, [], seed=seed, ga=(kind == "GA"), 어휘=어휘)
        후보 = G.후보뽑기(x, 300)
        out["후보"] = len(후보)
        data = _자료(tr, 어휘, None)
        m = G.성장망(n_bits, N_ACT, 후보, seed).fit(data)
        out["preds"][f"GROW-{kind}"] = _예측(m, 시험글, 어휘, None)
        out["단위"] = {f"GROW-{kind}": len(m.단위)}
        out["단위_보기"] = m.설명(D.FEATURES + 어휘)[:12]
        if kind == "GA" and frac == 1.0:
            esn = G.ESN(seed)
            m2 = G.성장망(n_bits, N_ACT, 후보, seed, esn_n=esn.n).fit(_자료(tr, 어휘, esn))
            out["preds"]["GROW-GA+ESN"] = _예측(m2, 시험글, 어휘, esn)
            out["단위"]["GROW-GA+ESN"] = len(m2.단위)
    elif kind == "LR":
        m = G.성장망(n_bits, N_ACT, [], seed, 최대=0).fit(_자료(tr, 어휘, None))
        out["preds"]["LR"] = _예측(m, 시험글, 어휘, None)
    elif kind == "ESN":
        esn = G.ESN(seed)
        m = G.성장망(0, N_ACT, [], seed, 최대=0, esn_n=esn.n).fit([(0, esn.상태(t), D.ACTS.index(a)) for t, a in tr])
        out["preds"]["ESN"] = [(D.ACTS[k], p[k]) for p in (m.proba(0, esn.상태(t)) for t in 시험글)
                               for k in [max(range(N_ACT), key=lambda i: p[i])]]
    elif kind == "MLP":
        bits = [(D.입력(t, D._파스(t), 어휘), D.ACTS.index(a)) for t, a in tr]
        m = E.MLP(len(bits[0][0]), seed).fit(bits, epochs=60, lr=0.05, seed=seed)
        preds = []
        for t in 시험글:
            p = m.proba(D.입력(t, D._파스(t), 어휘))
            k = max(range(N_ACT), key=lambda i: p[i])
            preds.append((D.ACTS[k], p[k]))
        out["preds"]["MLP"] = preds
    elif kind == "NB":
        bits = [(D.입력(t, D._파스(t), 어휘), D.ACTS.index(a)) for t, a in tr]
        m = E.NB(bits, len(bits[0][0]))
        preds = []
        for t in 시험글:
            p = m.proba(D.입력(t, D._파스(t), 어휘))
            k = max(range(N_ACT), key=lambda i: p[i])
            preds.append((D.ACTS[k], p[k]))
        out["preds"]["NB"] = preds
    out["sec"] = round(time.time() - t0, 1)
    return out


def 실험(학습: list, 시험: list, procs: int = 4, seeds=SEEDS, fracs=FRACS, 이름: str = "봉인 v4") -> dict:
    t0 = time.time()
    학습글 = {D._알맹이(t) for t, _ in 학습}
    뺌 = [t for a, t in 시험 if D._알맹이(t) in 학습글]
    시험 = [(a, t) for a, t in 시험 if D._알맹이(t) not in 학습글]
    gold = [a for a, _ in 시험]
    시험글 = [t for _, t in 시험]
    어휘 = D.어휘기르기(학습)
    손 = [D.손규칙(t, D._파스(t)) for t in 시험글]
    빈 = [a for t, a in 학습 if D.손규칙(t, D._파스(t)) is None]
    다수 = max(set(빈), key=빈.count) if 빈 else "task"
    jobs = [("NB", 0, 1.0, 학습, 시험글, 어휘)]
    for f in fracs:
        for s in seeds:
            jobs.append(("GA", s, f, 학습, 시험글, 어휘))
            if f == 1.0:
                jobs += [("X0", s, f, 학습, 시험글, 어휘), ("LR", s, f, 학습, 시험글, 어휘),
                         ("ESN", s, f, 학습, 시험글, 어휘)]
            jobs.append(("MLP", s, f, 학습, 시험글, 어휘))
    jobs.sort(key=lambda j: (-j[2], j[0] != "GA"))
    with Pool(procs) as pool:
        res = pool.map(_작업, jobs, chunksize=1)
    preds: dict = {}
    단위: dict = {}
    보기: dict = {}
    for r in res:
        for name, p in r["preds"].items():
            preds[(name, r["seed"], r["frac"])] = p
        for name, n in r.get("단위", {}).items():
            단위.setdefault((name, r["frac"]), []).append(n)
        if r["kind"] == "GA" and r["frac"] == 1.0 and r["seed"] == seeds[0]:
            보기 = {"GROW-GA_단위_보기": r.get("단위_보기"), "후보": r.get("후보")}

    def 만(p):
        return [a for a, _ in p]

    def 채움(p):
        return [h or (a if c >= 0.5 else None) for h, (a, c) in zip(손, p)]

    def 다수결(L):
        return [g if sum(1 for x in L if x[i] == g) * 2 > len(L) else None for i, g in enumerate(gold)]

    cond = {"손만": 손, "손+다수": [h or 다수 for h in 손], "NB만": 만(preds[("NB", 0, 1.0)]),
            "손+NB": 채움(preds[("NB", 0, 1.0)])}
    씨앗별: dict = {}
    for name in ("GROW-GA", "GROW-X0", "LR", "MLP", "ESN", "GROW-GA+ESN"):
        ps = [preds[(name, s, 1.0)] for s in seeds]
        cond[f"{name}만"] = 다수결([만(p) for p in ps])
        cond[f"손+{name}"] = 다수결([채움(p) for p in ps])
        씨앗별[name] = [E._acc(만(p), gold) for p in ps]
    정확도 = {k: E._acc(v, gold) for k, v in cond.items()}
    검정 = {"G1a 손+GROW>손만": E._짝(cond["손+GROW-GA"], cond["손만"], gold),
            "G1b 손+GROW>손+다수": E._짝(cond["손+GROW-GA"], cond["손+다수"], gold),
            "G2 GROW-GA>GROW-X0": E._짝(cond["GROW-GA만"], cond["GROW-X0만"], gold),
            "G3 GROW>LR": E._짝(cond["GROW-GA만"], cond["LR만"], gold),
            "G4 GROW>NB": E._짝(cond["GROW-GA만"], cond["NB만"], gold)}
    holm = E._holm({k: v[2] for k, v in 검정.items()})
    섬 = {k: holm[k]["섬"] and 검정[k][0] > 검정[k][1] for k in 검정}
    넷이상 = sum(1 for a, b in zip(씨앗별["GROW-GA"], 씨앗별["GROW-X0"]) if a > b)
    섬["G2 GROW-GA>GROW-X0"] = 섬["G2 GROW-GA>GROW-X0"] and 넷이상 >= 4
    w, l, _ = 검정["G4 GROW>NB"]
    NB검 = {"손+NB>손만": E._짝(cond["손+NB"], cond["손만"], gold), "손+NB>손+다수": E._짝(cond["손+NB"], cond["손+다수"], gold)}
    if 섬["G1a 손+GROW>손만"] and 섬["G1b 손+GROW>손+다수"] and w >= l:
        결정 = "성장망을 채움 자리에 붙인다"
    elif all(v[0] > v[1] and v[2] < 0.05 for v in NB검.values()):
        결정 = "성장망은 조건 미달 → NB 를 채움 자리에 붙인다"
    else:
        결정 = "아무것도 붙이지 않는다"
    곡선 = {"GROW-GA": {f: [E._acc(만(preds[("GROW-GA", s, f)]), gold) for s in seeds] for f in fracs},
            "MLP": {f: [E._acc(만(preds[("MLP", s, f)]), gold) for s in seeds] for f in fracs},
            "GROW-GA_단위수": {f: 단위.get(("GROW-GA", f)) for f in fracs}}
    return {"시험": 이름, "n_test": len(gold), "누수_뺌": len(뺌), "n_train": len(학습), "다수행위": 다수, "정확도": 정확도,
            "검정(이김,짐,p)": {k: list(v) for k, v in 검정.items()}, "Holm": holm, "섬": 섬, "G2_씨앗_넷이상": 넷이상,
            "NB_검정": {k: list(v) for k, v in NB검.items()}, "결정": 결정, "씨앗별(만)": 씨앗별,
            "자란_단위수(100%)": {k: 단위.get((k, 1.0)) for k in ("GROW-GA", "GROW-X0", "GROW-GA+ESN")},
            "학습곡선": 곡선, **보기, "초": round(time.time() - t0, 1)}


def 보이기(r: dict) -> str:
    줄 = [f"{r.get('시험', '봉인 v4')} {r['n_test']}문장(누수 뺌 {r['누수_뺌']}) · 학습 {r['n_train']} · {r['초']}초",
          "정확도: " + " · ".join(f"{k} {v:.1%}" for k, v in r["정확도"].items())]
    for k, v in r["검정(이김,짐,p)"].items():
        줄.append(f"  {k}: {v[0]}승 {v[1]}패 p={v[2]:.4g} (Holm 문턱 {r['Holm'][k]['문턱']:.4g}) → {'섬' if r['섬'][k] else '안 섬'}")
    줄.append(f"  G2 씨앗 다섯 중 GA>X0: {r['G2_씨앗_넷이상']}")
    for k, v in r["NB_검정"].items():
        줄.append(f"  {k}: {v[0]}승 {v[1]}패 p={v[2]:.4g}")
    줄.append(f"  자란 은닉 단위(씨앗별): {r['자란_단위수(100%)']}")
    for k, v in r["씨앗별(만)"].items():
        줄.append(f"  {k} 씨앗별 {v}")
    c = r["학습곡선"]
    for k in ("GROW-GA", "MLP"):
        줄.append(f"  곡선 {k}: " + " · ".join(f"{int(f * 100)}% {sum(x) / len(x):.1%}" for f, x in c[k].items()))
    줄.append(f"  곡선 GROW-GA 단위 수: {c['GROW-GA_단위수']}")
    if r.get("GROW-GA_단위_보기"):
        줄.append(f"  붙은 단위 예(씨앗1): {r['GROW-GA_단위_보기'][:8]}")
    줄.append(f"**결정: {r['결정']}**")
    return "\n".join(줄)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--procs", type=int, default=4)
    a = ap.parse_args()
    학습 = [(t, act) for p in TRAIN_FILES for act, t in D.모음(p)]
    if a.smoke:
        rng = random.Random(0)
        rng.shuffle(학습)
        시험 = [(act, t) for t, act in 학습[:80]]
        r = 실험(학습[80:300], 시험, procs=a.procs, seeds=[1, 2, 3], fracs=[0.5, 1.0], 이름="smoke(학습 자료 일부 — 봉인 아님)")
    else:
        print("봉인 v4 sha", hashlib.sha256(TEST_V4.read_bytes()).hexdigest(), flush=True)
        r = 실험(학습, D.모음(TEST_V4), procs=a.procs)
    if a.out:
        Path(a.out).write_text(json.dumps(r, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(보이기(r), flush=True)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
