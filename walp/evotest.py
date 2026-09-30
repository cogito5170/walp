"""진화 효과 입증 실험 — 사전등록 `walp/eval/PREREG_진화효과.md` 그대로. 봉인 v3 를 **한 번** 연다.

    python3 -m walp.evotest --out walp/eval/results/evolution_effect_v3.json      # 약 20분(CPU 4개)
    python3 -m walp.evotest --smoke                                              # 시험용: 작게, 봉인 모음 대신 학습 모음 일부로

조건: 손만 · 손+다수 · NB(베르누이 나이브 베이즈) · XG(XCS+GA) · X0(XCS, GA 끔) · NN(MLP 125→32→12, 사전학습 0).
씨앗 1..5, 학습 곡선 25/50/100%. 판정은 사전등록의 H1 · H2a · H2b · H3 (단측 부호검정, Holm α=0.05).
표준 라이브러리만(GPU 0). 학습된 가중치를 밖에서 가져오지 않는다 — NN 은 씨앗으로 무작위 초기화해 우리 자료로 처음부터 배운다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import time
from multiprocessing import Pool
from pathlib import Path

from walp import dialog as D

HERE = Path(__file__).resolve().parent
TRAIN_FILES = [HERE / "eval" / "dialog_act_train.tsv", HERE / "eval" / "dialog_act_test.tsv",
               HERE / "eval" / "dialog_act_test_v2.tsv"]
TEST_V3 = HERE / "eval" / "dialog_act_test_v3.tsv"
SEEDS = [1, 2, 3, 4, 5]
FRACS = [0.25, 0.5, 1.0]
N_ACT = len(D.ACTS)


# ---------------------------------------------------------------- 학습기 둘(진화 없는 것) — NB · NN
class NB:
    """베르누이 나이브 베이즈 — 셈의 닫힌꼴(W1). 라플라스 1."""

    def __init__(self, data: list, n_bits: int):
        self.prior = [1.0] * N_ACT
        self.one = [[1.0] * n_bits for _ in range(N_ACT)]
        cnt = [2.0] * N_ACT
        for b, a in data:
            self.prior[a] += 1
            cnt[a] += 1
            for i, c in enumerate(b):
                if c == "1":
                    self.one[a][i] += 1
        tot = sum(self.prior)
        self.lp = [math.log(p / tot) for p in self.prior]
        self.l1 = [[math.log(self.one[a][i] / cnt[a]) for i in range(n_bits)] for a in range(N_ACT)]
        self.l0 = [[math.log(1 - self.one[a][i] / cnt[a]) for i in range(n_bits)] for a in range(N_ACT)]

    def proba(self, b: str) -> list:
        s = [self.lp[a] + sum(self.l1[a][i] if c == "1" else self.l0[a][i] for i, c in enumerate(b)) for a in range(N_ACT)]
        m = max(s)
        e = [math.exp(v - m) for v in s]
        z = sum(e)
        return [v / z for v in e]


class MLP:
    """125→H(tanh)→12(softmax). 입력이 드물게 1 이라 켜진 비트만 곱한다(빠르다). 씨앗으로 무작위 초기화 — 사전학습 없음."""

    def __init__(self, n_in: int, seed: int, H: int = 32):
        r = random.Random(seed)
        s1, s2 = 1 / math.sqrt(max(1, n_in)), 1 / math.sqrt(H)
        self.W1 = [[r.uniform(-s1, s1) for _ in range(H)] for _ in range(n_in)]
        self.b1 = [0.0] * H
        self.W2 = [[r.uniform(-s2, s2) for _ in range(N_ACT)] for _ in range(H)]
        self.b2 = [0.0] * N_ACT
        self.H = H

    def _fwd(self, on: list):
        z = list(self.b1)
        for i in on:
            w = self.W1[i]
            for j in range(self.H):
                z[j] += w[j]
        h = [math.tanh(v) for v in z]
        o = list(self.b2)
        for j in range(self.H):
            hj, w = h[j], self.W2[j]
            for k in range(N_ACT):
                o[k] += hj * w[k]
        m = max(o)
        e = [math.exp(v - m) for v in o]
        s = sum(e)
        return h, [v / s for v in e]

    def fit(self, data: list, epochs: int, lr: float, seed: int):
        r = random.Random(seed + 1000)
        xs = [([i for i, c in enumerate(b) if c == "1"], a) for b, a in data]
        for _ in range(epochs):
            r.shuffle(xs)
            for on, a in xs:
                h, p = self._fwd(on)
                g2 = [p[k] - (1.0 if k == a else 0.0) for k in range(N_ACT)]            # 교차엔트로피 기울기
                gh = [sum(self.W2[j][k] * g2[k] for k in range(N_ACT)) * (1 - h[j] * h[j]) for j in range(self.H)]
                for j in range(self.H):
                    hj, w = h[j], self.W2[j]
                    for k in range(N_ACT):
                        w[k] -= lr * hj * g2[k]
                for k in range(N_ACT):
                    self.b2[k] -= lr * g2[k]
                for i in on:
                    w = self.W1[i]
                    for j in range(self.H):
                        w[j] -= lr * gh[j]
                for j in range(self.H):
                    self.b1[j] -= lr * gh[j]
        return self

    def proba(self, b: str) -> list:
        return self._fwd([i for i, c in enumerate(b) if c == "1"])[1]


# ---------------------------------------------------------------- 한 작업(일꾼에서 돈다)
def _작업(arg) -> dict:
    kind, seed, frac, 학습, 시험글, 어휘 = arg
    t0 = time.time()
    rng = random.Random(seed * 101 + int(frac * 100))
    tr = list(학습)
    if frac < 1.0:
        rng.shuffle(tr)
        tr = tr[: max(12, int(len(tr) * frac))]
        어휘 = D.어휘기르기(tr)
    out = {"kind": kind, "seed": seed, "frac": frac, "n_train": len(tr)}
    if kind in ("XG", "X0"):
        x = D.훈련(tr, [], seed=seed, ga=(kind == "XG"), 어휘=어휘)
        preds = []
        for t in 시험글:
            a, pa = x.predict(D.입력(t, D._파스(t), 어휘))
            if a < 0:
                preds.append((None, 0.0))
            else:
                preds.append((D.ACTS[a], pa[a] / 1000.0))
        out["rules"] = len(x.pop)
    else:
        bits = [(D.입력(t, D._파스(t), 어휘), D.ACTS.index(a)) for t, a in tr]
        if kind == "NB":
            m = NB(bits, len(bits[0][0]))
        else:
            m = MLP(len(bits[0][0]), seed).fit(bits, epochs=60, lr=0.05, seed=seed)
        preds = []
        for t in 시험글:
            p = m.proba(D.입력(t, D._파스(t), 어휘))
            k = max(range(N_ACT), key=lambda i: p[i])
            preds.append((D.ACTS[k], p[k]))
    out["preds"] = preds
    out["sec"] = round(time.time() - t0, 1)
    return out


# ---------------------------------------------------------------- 셈
def _p_단측(w: int, l: int) -> float:
    n = w + l
    return 1.0 if n == 0 else sum(math.comb(n, k) for k in range(w, n + 1)) / 2 ** n


def _짝(a: list, b: list, gold: list) -> tuple:
    w = sum(1 for x, y, g in zip(a, b, gold) if x == g and y != g)
    l = sum(1 for x, y, g in zip(a, b, gold) if x != g and y == g)
    return w, l, _p_단측(w, l)


def _acc(pred: list, gold: list) -> float:
    return round(sum(p == g for p, g in zip(pred, gold)) / len(gold), 4)


def _holm(ps: dict, alpha: float = 0.05) -> dict:
    order = sorted(ps, key=lambda k: ps[k])
    out, 막힘 = {}, False
    for i, k in enumerate(order):
        thr = alpha / (len(order) - i)
        ok = (not 막힘) and ps[k] < thr
        막힘 = 막힘 or not ok
        out[k] = {"p": round(ps[k], 6), "문턱": round(thr, 6), "섬": ok}
    return out


def 실험(학습: list, 시험: list, procs: int = 4, seeds=SEEDS, fracs=FRACS) -> dict:
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
        for k in ("XG", "X0", "NN"):
            for s in seeds:
                jobs.append((k, s, f, 학습, 시험글, 어휘))
    jobs.sort(key=lambda j: (-j[2], j[0] != "XG"))           # 무거운 것부터
    with Pool(procs) as pool:
        res = pool.map(_작업, jobs, chunksize=1)
    by = {(r["kind"], r["seed"], r["frac"]): r for r in res}

    def 만(r):
        return [a for a, _ in r["preds"]]

    def 채움(r, conf):
        return [h or (a if a and c >= conf else None) for h, (a, c) in zip(손, r["preds"])]

    def 다수결(listss):                      # 씨앗별 예측 목록들 → 문장마다 셋 이상 맞으면 정답, 아니면 None(틀림)
        out = []
        for i, g in enumerate(gold):
            n = sum(1 for L in listss if L[i] == g)
            out.append(g if n * 2 > len(listss) else None)
        return out

    cond: dict = {"손만": 손, "손+다수": [h or 다수 for h in 손]}
    nb = by[("NB", 0, 1.0)]
    cond["NB만"] = 만(nb)
    cond["손+NB"] = 채움(nb, 0.5)
    씨앗별: dict = {}
    for k, conf in (("XG", 0.5), ("X0", 0.5), ("NN", 0.5)):
        rs = [by[(k, s, 1.0)] for s in seeds]
        cond[f"{k}만"] = 다수결([만(r) for r in rs])
        cond[f"손+{k}"] = 다수결([채움(r, conf) for r in rs])
        씨앗별[k] = {"만": [_acc(만(r), gold) for r in rs], "채움": [_acc(채움(r, conf), gold) for r in rs],
                  "규칙수": [r.get("rules") for r in rs], "초": [r["sec"] for r in rs]}
    정확도 = {k: _acc(v, gold) for k, v in cond.items()}
    검정 = {"H1 XG만>X0만": _짝(cond["XG만"], cond["X0만"], gold),
            "H2a 손+XG>손만": _짝(cond["손+XG"], cond["손만"], gold),
            "H2b 손+XG>손+다수": _짝(cond["손+XG"], cond["손+다수"], gold),
            "H3 XG만>NB만": _짝(cond["XG만"], cond["NB만"], gold)}
    holm = _holm({k: v[2] for k, v in 검정.items()})
    넷이상 = sum(1 for a, b in zip(씨앗별["XG"]["만"], 씨앗별["X0"]["만"]) if a > b)
    H1 = holm["H1 XG만>X0만"]["섬"] and 넷이상 >= 4 and 검정["H1 XG만>X0만"][0] > 검정["H1 XG만>X0만"][1]
    H2 = all(holm[k]["섬"] and 검정[k][0] > 검정[k][1] for k in ("H2a 손+XG>손만", "H2b 손+XG>손+다수"))
    NN검 = {"손+NN>손만": _짝(cond["손+NN"], cond["손만"], gold), "손+NN>손+다수": _짝(cond["손+NN"], cond["손+다수"], gold)}
    NN_붙임 = (not H1) and all(v[0] > v[1] and v[2] < 0.05 for v in NN검.values())
    결정 = ("진화 효과 입증 — XCS 유지" if H1 and H2 else
            "GA 가 덮기만보다 낫지만 WALP 에 쓸 만큼은 아니다 — 붙이지 않음" if H1 else
            "진화 효과 입증 못 함 → 신경망을 채움 자리에 붙인다" if NN_붙임 else
            "진화 효과 입증 못 함 · 신경망도 손 규칙을 못 이김 → 아무것도 붙이지 않음")
    곡선 = {k: {f: [_acc(만(by[(k, s, f)]), gold) for s in seeds] for f in fracs} for k in ("XG", "X0", "NN")}
    return {"n_test": len(gold), "누수_뺌": len(뺌), "n_train": len(학습), "다수행위": 다수, "정확도": 정확도,
            "검정(이김,짐,p)": {k: list(v) for k, v in 검정.items()}, "Holm": holm, "H1_씨앗_넷이상": 넷이상,
            "H1": H1, "H2": H2, "신경망_검정": {k: list(v) for k, v in NN검.items()}, "결정": 결정,
            "씨앗별": 씨앗별, "학습곡선(만, 씨앗별)": 곡선, "초": round(time.time() - t0, 1)}


def 보이기(r: dict) -> str:
    a = r["정확도"]
    줄 = [f"봉인 v3 {r['n_test']}문장(누수 뺌 {r['누수_뺌']}) · 학습 {r['n_train']} · {r['초']}초",
          "정확도: " + " · ".join(f"{k} {v:.1%}" for k, v in a.items())]
    for k, v in r["검정(이김,짐,p)"].items():
        h = r["Holm"][k]
        줄.append(f"  {k}: {v[0]}승 {v[1]}패 p={v[2]:.4g} (Holm 문턱 {h['문턱']:.4g}) → {'섬' if h['섬'] else '안 섬'}")
    줄.append(f"  H1 씨앗 다섯 중 XG>X0: {r['H1_씨앗_넷이상']}")
    for k, v in r["신경망_검정"].items():
        줄.append(f"  {k}: {v[0]}승 {v[1]}패 p={v[2]:.4g}")
    for k, v in r["씨앗별"].items():
        줄.append(f"  {k} 씨앗별 만 {v['만']} · 채움 {v['채움']}")
    for k, v in r["학습곡선(만, 씨앗별)"].items():
        줄.append(f"  곡선 {k}: " + " · ".join(f"{int(f * 100)}% {sum(x) / len(x):.1%}" for f, x in v.items()))
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
        시험 = [(act, t) for t, act in 학습[:60]]
        r = 실험(학습[60:200], 시험, procs=a.procs, seeds=[1, 2, 3], fracs=[0.5, 1.0])
    else:
        print("봉인 v3 sha", hashlib.sha256(TEST_V3.read_bytes()).hexdigest(), flush=True)
        r = 실험(학습, D.모음(TEST_V3), procs=a.procs)
    if a.out:
        Path(a.out).write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    print(보이기(r), flush=True)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
