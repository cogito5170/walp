"""손 없는 행동 기반 실험 — 사전등록 `walp/eval/PREREG_손없는행동.md` 그대로. 봉인 v7 을 **한 번** 연다.

    python3 -m walp.nohandtest --smoke
    python3 -m walp.nohandtest --out walp/eval/results/nohand_v7.json
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
from walp import behaviortest as BT
from walp import dialog as D
from walp import evotest as E

HERE = Path(__file__).resolve().parent
TRAIN_FILES = BT.TRAIN_FILES + [HERE / "eval" / "dialog_act_test_v6.tsv"]
TEST_V7 = HERE / "eval" / "dialog_act_test_v7.tsv"
V7_SHA = "c60ec27694823e84addcf6dfa503a358ed680c35ae84dd8b03e984dfe06d6033"
SEEDS = [1, 2, 3, 4, 5]
CS = BT.CS


def _작업(arg) -> dict:
    """씨앗 하나. WALP_CKPT_DIR 이 서 있으면 끝난 씨앗의 결과를 거기 두고 다시 돌 때 건너뛴다 —
    첫 봉인 실행이 컨테이너 회수로 결과 없이 죽었다(2026-09-30, 약 3시간째). 분석은 바뀌지 않는다."""
    seed, 학습, 시험글, 다수, 성장만들기 = arg
    import os
    ck = os.environ.get("WALP_CKPT_DIR")
    ckp = Path(ck) / f"seed{seed}.json" if ck else None
    if ckp and ckp.exists():
        d = json.loads(ckp.read_text(encoding="utf-8"))
        d["H물음"] = {float(k): v for k, v in d["H물음"].items()}
        d["N물음"] = {float(k): v for k, v in d["N물음"].items()}
        return d
    out = _작업_본(seed, 학습, 시험글, 다수, 성장만들기)
    if ckp:
        ckp.parent.mkdir(parents=True, exist_ok=True)
        tmp = ckp.with_suffix(".tmp")
        tmp.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, ckp)
    return out


def _반쪽(seed, 학습, 시험글, 다수, 성장만들기, 손: bool) -> dict:
    """한 씨앗의 한 판(손 있음 H / 손 없음 N). 두 판은 서로 독립이라 따로 돌려도 같은 수가 나온다
    (재시작이 잦아 씨앗 5 를 둘로 나눠 병렬로 돌렸다 — 2026-09-30)."""
    만들기 = (lambda s, sd: 성장만들기(s, sd, 손)) if 성장만들기 else None
    체 = B.기르기(학습, seed=seed, 손=손, 성장만들기=만들기)
    L1 = {c: (체["되묻기"] if c == B.C else B.L1기르기(체["에피소드"], c, seed)) for c in CS}
    k = "H" if 손 else "N"
    out = {k: [], f"{k}물음": {c: [] for c in CS}}
    if 손:
        out["A"] = []
    for t in 시험글:
        st = 체["센서"].읽기(t)
        o = 체["반응"].행(st, {})
        if 손:
            out["A"].append(D._모드들(체["센서"].성장, t, 다수)["채움"] or "task")
        out[k].append(o["행위"])
        for c in CS:
            out[f"{k}물음"][c].append(L1[c].행(st, {"반응": o}) is not None)
    return out


def _작업_본(seed, 학습, 시험글, 다수, 성장만들기) -> dict:
    t0 = time.time()
    import os
    ck = os.environ.get("WALP_CKPT_DIR")
    out = {"seed": seed}
    for 손 in (True, False):
        hp = Path(ck) / f"seed{seed}_{'H' if 손 else 'N'}.json" if ck else None
        if hp and hp.exists():
            d = json.loads(hp.read_text(encoding="utf-8"))
            for kk in ("H물음", "N물음"):
                if kk in d:
                    d[kk] = {float(c): v for c, v in d[kk].items()}
        else:
            d = _반쪽(seed, 학습, 시험글, 다수, 성장만들기, 손)
            if hp:
                hp.parent.mkdir(parents=True, exist_ok=True)
                hp.with_suffix(".tmp").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
                os.replace(hp.with_suffix(".tmp"), hp)
        out.update(d)
    out["sec"] = round(time.time() - t0, 1)
    return out


def 반쪽만(seed: int, 손: bool) -> None:
    """`python3 -m walp.nohandtest --반쪽 5 H` — 한 씨앗의 한 판만 계산해 WALP_CKPT_DIR 에 둔다(병렬로 나눠 돌리기)."""
    import os
    학습 = [(t, act) for p in TRAIN_FILES for act, t in D.모음(p)]
    if hashlib.sha256(TEST_V7.read_bytes()).hexdigest() != V7_SHA:
        raise SystemExit("봉인 v7 sha 가 다르다")
    시험 = D.모음(TEST_V7)
    시험알 = {D._알맹이(t) for _, t in 시험}                     # 실험() 과 같은 준비
    학습 = [(t, a) for t, a in 학습 if D._알맹이(t) not in 시험알]
    시험글 = [t for _, t in 시험]
    빈 = [a for t, a in 학습 if D.손규칙(t, D._파스(t)) is None]
    다수 = max(set(빈), key=빈.count) if 빈 else "task"
    ck = Path(os.environ["WALP_CKPT_DIR"])
    hp = ck / f"seed{seed}_{'H' if 손 else 'N'}.json"
    d = _반쪽(seed, 학습, 시험글, 다수, None, 손)
    ck.mkdir(parents=True, exist_ok=True)
    hp.with_suffix(".tmp").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    os.replace(hp.with_suffix(".tmp"), hp)


def _순열(u1, u2, n회=20000):
    d = [x - y for x, y in zip(u1, u2)]
    obs = sum(d)
    rng = random.Random(0)
    ge = sum(1 for _ in range(n회) if sum(x if rng.random() < 0.5 else -x for x in d) >= obs - 1e-12)
    return {"평균차": obs / len(d), "p": (ge + 1) / (n회 + 1)}


def _부호(u1, u2):
    w = sum(1 for x, y in zip(u1, u2) if x > y)
    l = sum(1 for x, y in zip(u1, u2) if x < y)
    return [w, l, E._p_단측(w, l)]


def _부트위(u1, u2, n회=10000):
    """(u1 − u2) 평균의 짝 부트스트랩 단측 95% 위 한계."""
    d = [x - y for x, y in zip(u1, u2)]
    rng = random.Random(0)
    ms = sorted(sum(d[rng.randrange(len(d))] for _ in d) / len(d) for _ in range(n회))
    return ms[int(0.95 * n회) - 1]


def 실험(학습: list, 시험: list, procs: int = 4, seeds=SEEDS, 이름: str = "봉인 v7", 성장만들기=None) -> dict:
    t0 = time.time()
    시험알 = {D._알맹이(t) for _, t in 시험}
    뺌 = [t for t, _ in 학습 if D._알맹이(t) in 시험알]
    학습 = [(t, a) for t, a in 학습 if D._알맹이(t) not in 시험알]
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
    맞 = {k: [BT._다수([r[k] for r in res], i) == gold[i] for i in range(n)] for k in ("A", "H", "N")}

    def 물음(key, c):
        return [sum(r[key][c][i] for r in res) * 2 > len(res) for i in range(n)]

    def u(k, c=None, 묻기=None):
        return [(1 - c) if (묻기 and 묻기[i]) else B.효용(맞[k][i]) for i in range(n)]
    쓸기, uu = {}, {}
    for c in CS:
        hq, nq = 물음("H물음", c), 물음("N물음", c)
        uu[c] = {"A": u("A"), "H": u("H", c, hq), "N": u("N", c, nq), "N_C": u("N")}
        쓸기[c] = {k: sum(v) / n for k, v in uu[c].items()} | {"E": 1 - c, "H물음률": sum(hq) / n, "N물음률": sum(nq) / n,
                                                           "N물은곳_L0오답률": sum(not m for q, m in zip(nq, 맞["N"]) if q) / max(1, sum(nq)),
                                                           "N안물은곳_L0오답률": sum(not m for q, m in zip(nq, 맞["N"]) if not q) / max(1, n - sum(nq))}
    U = uu[B.C]
    주 = {"G1 N>N_C": _순열(U["N"], U["N_C"]), "G3 N>A": _순열(U["N"], U["A"])}
    holm = E._holm({k: v["p"] for k, v in 주.items()})
    섬 = {k: holm[k]["섬"] and 주[k]["평균차"] > 0 for k in 주}
    위 = _부트위(U["H"], U["N"])
    G2 = {"uH−uN 평균": sum(x - y for x, y in zip(U["H"], U["N"])) / n, "단측95%위한계": 위, "여유": 0.05, "섬": 위 <= 0.05}
    보조 = {"G1 N>N_C": _부호(U["N"], U["N_C"]), "G3 N>A": _부호(U["N"], U["A"]), "N vs H": _부호(U["N"], U["H"])}
    배포 = 섬["G1 N>N_C"] and G2["섬"]
    return {"시험": 이름, "n_test": n, "학습에서_뺌": len(뺌), "n_train": len(학습),
            "답정확도": {k: sum(v) / n for k, v in 맞.items()}, "쓸기(c)": 쓸기, "주_순열": 주, "Holm": holm, "섬": 섬,
            "G2_비열등": G2, "보조_부호(이김,짐,p)": 보조, "결정_손없는판_배포": 배포,
            "씨앗별_초": [r["sec"] for r in res], "초": round(time.time() - t0, 1)}


def 보이기(r: dict) -> str:
    s = r["쓸기(c)"][B.C]
    a = r["답정확도"]
    줄 = [f"{r['시험']} {r['n_test']}문장 · 학습 {r['n_train']}(겹쳐 뺀 {r['학습에서_뺌']}) · {r['초']}초",
          f"답 정확도(L0/사슬): A 예전사슬 {a['A']:.1%} · H 손있는 L0 {a['H']:.1%} · N 손없는 L0 {a['N']:.1%}",
          f"효용(c={B.C}): A {s['A']:.3f} · H {s['H']:.3f} · N {s['N']:.3f} · N_C {s['N_C']:.3f} · E {s['E']:.2f} "
          f"(물음률 H {s['H물음률']:.0%} · N {s['N물음률']:.0%})",
          f"  대조: N 이 물은 곳의 L0 오답률 {s['N물은곳_L0오답률']:.1%} vs 안 물은 곳 {s['N안물은곳_L0오답률']:.1%}"]
    for k, v in r["주_순열"].items():
        줄.append(f"  {k}: 평균 효용 차 {v['평균차']:+.3f} p={v['p']:.4g} (Holm {r['Holm'][k]['문턱']:.4g}) → {'섬' if r['섬'][k] else '안 섬'}")
    g = r["G2_비열등"]
    줄.append(f"  G2 H−N 평균 {g['uH−uN 평균']:+.3f}, 단측 95% 위 한계 {g['단측95%위한계']:+.3f} ≤ 0.05 ? → {'섬' if g['섬'] else '안 섬'}")
    for k, v in r["보조_부호(이김,짐,p)"].items():
        줄.append(f"  (보조 부호) {k}: {v[0]}승 {v[1]}패 p={v[2]:.4g}")
    줄.append("c 쓸기: " + " | ".join(f"c={c}: N {v['N']:.3f} H {v['H']:.3f} N_C {v['N_C']:.3f} E {v['E']:.2f}"
                                     for c, v in r["쓸기(c)"].items()))
    줄.append(f"**결정: 손 없는 판 배포 = {'예' if r['결정_손없는판_배포'] else '아니오'}**")
    return "\n".join(줄)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--procs", type=int, default=4)
    ap.add_argument("--반쪽", nargs=2, metavar=("씨앗", "H|N"))
    a = ap.parse_args()
    if a.반쪽:
        반쪽만(int(a.반쪽[0]), a.반쪽[1] == "H")
        return 0
    학습 = [(t, act) for p in TRAIN_FILES for act, t in D.모음(p)]
    if a.smoke:
        random.Random(0).shuffle(학습)
        r = 실험(학습[100:500], [(act, t) for t, act in 학습[:100]], procs=a.procs, seeds=[1, 2, 3],
               이름="smoke(학습 자료 일부 — 봉인 아님)")
    else:
        sha = hashlib.sha256(TEST_V7.read_bytes()).hexdigest()
        if sha != V7_SHA:
            print("봉인 v7 sha 가 다르다 — 돌리지 않는다", sha)
            return 2
        print("봉인 v7 sha", sha, flush=True)
        r = 실험(학습, D.모음(TEST_V7), procs=a.procs)
    if a.out:
        Path(a.out).write_text(json.dumps(r, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(보이기(r), flush=True)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
