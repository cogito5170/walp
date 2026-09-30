"""성장망 — 진화(XCS)가 은닉 단위를 **제안**하고, 경량 학습기가 **남은 오차를 줄이는 것만** 붙여 은닉층이 자란다.

사용자(2026-09-30): "경량 학습->은닉층 성장" · "성장망으로 지어". 사전등록: `walp/eval/PREREG_성장망.md`.
선행: RAN(Platt 1991 — 새롭고 오차가 크면 단위를 더함) · Cascade-Correlation(Fahlman & Lebiere 1990 — 후보 가운데 남은 오차와
가장 상관 높은 것을 붙임) · X-NFCS(Bull & O'Hara 2002 — 규칙=RBF). 새로움 주장은 약하다.

    출력층   소프트맥스 회귀(행동 12) · 입력 = [특징 비트 + 자란 은닉 단위 (+ ESN 상태)]
    은닉 단위 RBF, 중심 = XCS 규칙 조건.  d = 조건이 신경 쓰는 비트 중 어긋난 수,  a = 1(d=0) · e⁻¹(d=1) · 0(d≥2, 잘라서 희소)
    자라기   후보마다 Σ_c |Σ_i (a_i − ā)(r_ic − r̄_c)| (r = 출력의 남은 오차) 가 가장 큰 것을 붙이고 몇 epoch 더 배운다
    멈추기   학습 안에서 떼어 둔 검증 10% 의 손실이 두 번 연속 안 줄면 멈추고, 가장 좋았던 때로 되돌린다(상한 64)
    ESN      글자를 차례로 읽는 저수지(무작위 고정, 누설 · 스펙트럼 반지름) — 상태의 평균을 출력층에 곁들인다(출력만 배움)

표준 라이브러리만 · CPU · 사전학습 0. 조건은 사람이 읽는다 → 붙은 단위 하나하나를 `설명()` 으로 볼 수 있다.
"""
from __future__ import annotations

import math
import random

E1 = math.exp(-1.0)


def 비트수(b: str) -> int:
    return int(b[::-1], 2) if b else 0          # 비트 i → 2^i


def 조건수(cond: str) -> "tuple[int, int]":
    care = val = 0
    for i, c in enumerate(cond):
        if c != "#":
            care |= 1 << i
            if c == "1":
                val |= 1 << i
    return care, val


def rbf(x: int, care: int, val: int) -> float:
    d = ((x ^ val) & care).bit_count()
    return 1.0 if d == 0 else (E1 if d == 1 else 0.0)


# ---------------------------------------------------------------- ESN — 글자 저수지(출력만 배운다)
class ESN:
    def __init__(self, seed: int, n: int = 100, leak: float = 0.3, rho: float = 0.9, density: float = 0.1):
        r = random.Random(seed * 7919 + 17)
        self.n, self.leak, self.seed = n, leak, seed
        self.W = [[(j, r.uniform(-1, 1)) for j in range(n) if r.random() < density] for _ in range(n)]
        # 스펙트럼 반지름을 거듭제곱법으로 어림하고 rho 로 맞춘다
        v = [r.uniform(-1, 1) for _ in range(n)]
        lam = 1.0
        for _ in range(60):
            w = [sum(val * v[j] for j, val in row) for row in self.W]
            lam = math.sqrt(sum(t * t for t in w)) or 1.0
            v = [t / lam for t in w]
        s = rho / lam
        self.W = [[(j, val * s) for j, val in row] for row in self.W]
        self._입력: dict = {}

    def _in(self, ch: str) -> list:
        v = self._입력.get(ch)
        if v is None:
            r = random.Random(self.seed * 1_000_003 + ord(ch))
            v = [(r.randrange(self.n), r.choice((-0.5, 0.5))) for _ in range(10)]
            self._입력[ch] = v
        return v

    def 상태(self, text: str) -> list:
        x = [0.0] * self.n
        합 = [0.0] * self.n
        t = (text or "").lower()[:120]
        for ch in t:
            u = [sum(val * x[j] for j, val in row) for row in self.W]
            for j, val in self._in(ch):
                u[j] += val
            x = [(1 - self.leak) * a + self.leak * math.tanh(b) for a, b in zip(x, u)]
            for k in range(self.n):
                합[k] += x[k]
        m = max(1, len(t))
        return [v / m for v in 합]


# ---------------------------------------------------------------- 성장망
class 성장망:
    def __init__(self, n_bits: int, n_act: int, 후보: list, seed: int, 최대: int = 64, esn_n: int = 0,
                 lr: float = 0.05, 처음: int = 30, 걸음마다: int = 4):
        self.n_bits, self.n_act, self.seed = n_bits, n_act, seed
        self.후보 = 후보                      # [(조건문자열, care, val)]
        self.최대, self.esn_n, self.lr, self.처음, self.걸음마다 = 최대, esn_n, lr, 처음, 걸음마다
        self.단위: list = []                   # 붙은 후보의 번호
        self.W: list = []                      # 특징 번호 → [행동별 가중치]
        self.b = [0.0] * n_act

    # 특징: (번호, 값) 목록 — 비트 [0, n_bits) · ESN [n_bits, n_bits+esn_n) · 단위 [그 뒤)
    def _특징(self, x: int, e: "list | None", 단위=None) -> list:
        f = [(i, 1.0) for i in range(self.n_bits) if (x >> i) & 1]
        if self.esn_n and e is not None:
            f += [(self.n_bits + k, v) for k, v in enumerate(e)]
        base = self.n_bits + self.esn_n
        for k, u in enumerate(self.단위 if 단위 is None else 단위):
            _, care, val = self.후보[u]
            a = rbf(x, care, val)
            if a:
                f.append((base + k, a))
        return f

    def _확률(self, f: list) -> list:
        o = list(self.b)
        for i, v in f:
            w = self.W[i]
            for c in range(self.n_act):
                o[c] += v * w[c]
        m = max(o)
        e = [math.exp(t - m) for t in o]
        s = sum(e)
        return [t / s for t in e]

    def _늘리기(self):
        need = self.n_bits + self.esn_n + len(self.단위)
        while len(self.W) < need:
            self.W.append([0.0] * self.n_act)

    def _학습(self, data: list, epochs: int, rng: random.Random):
        self._늘리기()
        idx = list(range(len(data)))
        for _ in range(epochs):
            rng.shuffle(idx)
            for n in idx:
                x, e, y = data[n]
                f = self._특징(x, e)
                p = self._확률(f)
                p[y] -= 1.0
                for i, v in f:
                    w = self.W[i]
                    for c in range(self.n_act):
                        w[c] -= self.lr * v * p[c]
                for c in range(self.n_act):
                    self.b[c] -= self.lr * p[c]

    def _손실(self, data: list) -> float:
        return -sum(math.log(max(1e-12, self._확률(self._특징(x, e))[y])) for x, e, y in data) / max(1, len(data))

    def fit(self, data: list) -> "성장망":
        """data = [(x비트수, esn상태|None, 행동번호)]."""
        rng = random.Random(self.seed * 31 + 5)
        d = list(data)
        rng.shuffle(d)
        nv = max(1, len(d) // 10)
        val, tr = d[:nv], d[nv:]
        self._학습(tr, self.처음, rng)
        # 후보마다 켜지는 표본(희소)을 한 번만 센다
        켜짐 = []
        for _, care, v in self.후보:
            켜짐.append([(i, a) for i, (x, _, _) in enumerate(tr) if (a := rbf(x, care, v))])
        최선 = (self._손실(val), [], [list(w) for w in self.W], list(self.b))
        나빠짐 = 0
        쓴 = set()
        self.기록 = []
        while len(self.단위) < self.최대:
            # 남은 오차 r_ic = p_ic − y_ic (행동별 평균을 뺀다)
            R = []
            for x, e, y in tr:
                p = self._확률(self._특징(x, e))
                p[y] -= 1.0
                R.append(p)
            평균 = [sum(r[c] for r in R) / len(R) for c in range(self.n_act)]
            best, bs = None, 0.0
            for k, on in enumerate(켜짐):
                if k in 쓴 or len(on) < 2:
                    continue
                # Σ_i (a_i − ā)(r_ic − r̄_c) = Σ_i a_i (r_ic − r̄_c)  (r 을 평균으로 빼 두었으므로 ā 항은 0) — 켜진 표본만 돈다
                s = 0.0
                for c in range(self.n_act):
                    s += abs(sum(a * (R[i][c] - 평균[c]) for i, a in on))
                if s > bs:
                    best, bs = k, s
            if best is None:
                break
            쓴.add(best)
            self.단위.append(best)
            self._학습(tr, self.걸음마다, rng)
            l = self._손실(val)
            self.기록.append((self.후보[best][0], round(bs, 3), round(l, 4)))
            if l < 최선[0] - 1e-4:
                최선 = (l, list(self.단위), [list(w) for w in self.W], list(self.b))
                나빠짐 = 0
            else:
                나빠짐 += 1
                if 나빠짐 >= 2:
                    break
        _, self.단위, self.W, self.b = 최선[0], 최선[1], 최선[2], 최선[3]
        return self

    def proba(self, x: int, e: "list | None" = None) -> list:
        return self._확률(self._특징(x, e))

    def 설명(self, 이름들: list) -> list:
        """붙은 은닉 단위를 사람이 읽는 조건으로."""
        out = []
        for u in self.단위:
            cond = self.후보[u][0]
            켬 = [이름들[i] for i, c in enumerate(cond) if c == "1" and i < len(이름들)]
            끔 = [이름들[i] for i, c in enumerate(cond) if c == "0" and i < len(이름들)]
            out.append(("+" + ",".join(켬) if 켬 else "") + (" −" + ",".join(끔[:6]) if 끔 else ""))
        return out


def 후보뽑기(xcs, 상한: int = 300) -> list:
    """XCS 집단에서 조건을 뽑는다(중복 제거, 적합도×수 순, 전부 # 인 것은 뺌)."""
    seen, out = set(), []
    for c in sorted(xcs.pop, key=lambda c: -(c.F * c.num)):
        if c.cond in seen or "1" not in c.cond and "0" not in c.cond:
            continue
        seen.add(c.cond)
        care, val = 조건수(c.cond)
        out.append((c.cond, care, val))
        if len(out) >= 상한:
            break
    return out
