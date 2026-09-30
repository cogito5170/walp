"""행위 늘리기 실험 — 사전등록 `walp/eval/PREREG_행위늘리기.md` 그대로. 봉인 가르침 대본 v1 을 **한 번** 연다.

    python3 -m walp.teachtest --out walp/eval/results/teach_v1.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from walp import behavior as B
from walp import dialog as D

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "eval" / "teach_script_v1.tsv"
SHA = "c55f22f6531980410ec4cd119c7dd171e725711cf2a44e24c9bc8adcb1e54ba2"
θS = [0.3, 0.4, 0.5, 0.6, 0.7]


def 읽기(path=SCRIPT) -> "tuple[list, list]":
    새, 대조 = [], []
    for line in open(path, encoding="utf-8"):
        if not line.strip() or line.startswith("#"):
            continue
        f = line.rstrip("\n").split("\t")
        if f[0] == "NEW" and len(f) == 8:
            새.append({"id": f[1], "원래": f[2], "설명": f[3], "답": f[4], "다른말": f[5:8]})
        elif f[0] == "CTRL" and len(f) == 3:
            대조.append((f[1], f[2]))
    return 새, 대조


def _자카드(a: str, b: str) -> float:
    g, h = D._조각(a), D._조각(b)
    return len(g & h) / len(g | h) if (g | h) else 0.0


def 재기(새: list, 대조: list, θ: float) -> dict:
    def 배움들(예문수):
        return B.배움([B.배운뜻(n["id"], [n["원래"]] + n["다른말"][:예문수 - 1], n["답"], n["설명"], θ) for n in 새])
    b1, b2 = 배움들(1), 배움들(2)
    맞1 = 틀1 = 0
    for n in 새:
        for p in n["다른말"]:
            f, _ = b1.고르기(p)
            맞1 += bool(f and f.id == n["id"])
            틀1 += bool(f and f.id != n["id"])
    맞2 = sum(bool((f := b2.고르기(p)[0]) and f.id == n["id"]) for n in 새 for p in n["다른말"][1:])
    대조켜짐 = sum(bool(b1.고르기(t)[0]) for _, t in 대조)
    기억 = sum(_자카드(p, n["원래"]) >= D.SIM for n in 새 for p in n["다른말"])
    return {"θ": θ, "R1": 맞1 / (3 * len(새)), "R2": 맞2 / (2 * len(새)),
            "F": (대조켜짐 + 틀1) / (len(대조) + 3 * len(새)), "대조켜짐": 대조켜짐, "엉뚱한뜻": 틀1,
            "기억층_재현율(자카드≥0.75)": 기억 / (3 * len(새))}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    a = ap.parse_args()
    sha = hashlib.sha256(SCRIPT.read_bytes()).hexdigest()
    if sha != SHA:
        print("봉인 대본 sha 가 다르다 — 돌리지 않는다", sha)
        return 2
    새, 대조 = 읽기()
    쓸기 = [재기(새, 대조, θ) for θ in θS]
    주 = next(r for r in 쓸기 if r["θ"] == B.TEACH_θ)
    결정 = "켠다(θ=0.5)" if 주["F"] <= 0.05 else "끈다 — θ=0.75(원래 말과 거의 같을 때만)"
    r = {"대본": "봉인 가르침 대본 v1", "sha": sha, "새뜻": len(새), "대조": len(대조), "주(θ=0.5)": 주, "쓸기": 쓸기, "결정": 결정}
    if a.out:
        Path(a.out).write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"봉인 가르침 대본 v1 — 새 뜻 {len(새)} · 대조 {len(대조)}")
    for x in 쓸기:
        print(f"  θ={x['θ']}: R1 {x['R1']:.1%} · R2 {x['R2']:.1%} · F {x['F']:.1%} (대조 켜짐 {x['대조켜짐']} · 엉뚱한 뜻 {x['엉뚱한뜻']})"
              f" · 기억층 {x['기억층_재현율(자카드≥0.75)']:.1%}")
    print(f"**결정: {결정}**")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
