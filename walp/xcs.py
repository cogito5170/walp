"""XCS — 정확도 기반 학습 분류자 시스템(Wilson 1995; 알고리즘은 Butz & Wilson 의 서술을 따른 꼴). 표준 라이브러리만.

WALP 에서의 쓰임(`walp/dialog.py`): 입력 = 대화 행위 특징 비트열, 행동 = 대화 행위 번호. 집단은 **원장 재생**으로 자란다.
선행조사: `paper/선행조사/WALP_진화대화.md`. 학습된 가중치 0(사전학습 없음) · GPU 0 — 규칙은 {0,1,#} 조건과 행동, 수 몇 개다.

보상 두 가지:
    라벨이 있는 표본(파서가 받아들인 말 · 사람의 `행위` 고침 · 학습 모음)  → 어느 행동이든 보상을 안다(1000/0) — 탐색이 자유롭다
    좋음/나쁨만 있는 표본(다음 턴의 감사·불만)                             → **고른 행동**의 보상만 안다(밴딧)

결정적이다: 같은 씨앗 · 같은 표본 순서면 같은 집단이 나온다(재현성 — 승격 판단을 다시 돌려 볼 수 있어야 한다).
"""
from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass, field


@dataclass
class Params:
    N: int = 800            # 집단 크기 상한(마이크로 분류자 수)
    beta: float = 0.2
    alpha: float = 0.1
    eps0: float = 10.0
    nu: float = 5.0
    theta_ga: int = 25
    chi: float = 0.8
    mu: float = 0.04
    theta_del: int = 20
    delta: float = 0.1
    theta_sub: int = 20
    p_hash: float = 0.66    # 덮기에서 비트를 # 로 둘 확률 — 특징이 드물게 1 이라 넓게 시작한다
    p_hash_one: float = -1  # 0 이상이면 입력이 1 인 비트에만 이 확률을 쓴다(드문 특징 — 1 을 붙잡게)
    p_i: float = 10.0
    eps_i: float = 0.0
    f_i: float = 0.01
    reward: float = 1000.0


@dataclass
class Cl:
    cond: str
    act: int
    p: float
    eps: float
    F: float
    exp: int = 0
    ts: int = 0
    as_: float = 1.0
    num: int = 1

    def matches(self, x: str) -> bool:
        return all(c == "#" or c == b for c, b in zip(self.cond, x))

    def more_general(self, o: "Cl") -> bool:
        if self.cond.count("#") <= o.cond.count("#"):
            return False
        return all(c == "#" or c == d for c, d in zip(self.cond, o.cond))


@dataclass
class XCS:
    n_actions: int
    params: Params = field(default_factory=Params)
    seed: int = 1
    pop: list = field(default_factory=list)
    t: int = 0

    def __post_init__(self):
        self.rng = random.Random(self.seed)

    # ------------------------------------------------------------ 기본 조작
    def _match(self, x: str) -> list:
        M = [c for c in self.pop if c.matches(x)]
        acts = {c.act for c in M}
        while len(acts) < self.n_actions:          # 덮기 — 없는 행동마다 한 규칙
            a = self.rng.choice([a for a in range(self.n_actions) if a not in acts])
            P = self.params
            cond = "".join("#" if self.rng.random() < (P.p_hash_one if (b == "1" and P.p_hash_one >= 0) else P.p_hash)
                           else b for b in x)
            c = Cl(cond, a, self.params.p_i, self.params.eps_i, self.params.f_i, ts=self.t)
            self._insert(c)
            self._delete()
            M = [c for c in self.pop if c.matches(x)]
            acts = {c.act for c in M}
        return M

    def _pa(self, M: list) -> list:
        num = [0.0] * self.n_actions
        den = [0.0] * self.n_actions
        for c in M:
            num[c.act] += c.p * c.F
            den[c.act] += c.F
        return [num[a] / den[a] if den[a] > 0 else None for a in range(self.n_actions)]

    def predict(self, x: str) -> "tuple[int, list]":
        """이용(exploit) — 집단을 바꾸지 않는다. 맞는 규칙이 없으면 (-1, [])."""
        M = [c for c in self.pop if c.matches(x)]
        if not M:
            return -1, []
        pa = self._pa(M)
        best = max((a for a in range(self.n_actions) if pa[a] is not None), key=lambda a: pa[a])
        return best, pa

    def _update(self, A: list, R: float) -> None:
        P = self.params
        tot = sum(c.num for c in A)
        for c in A:
            c.exp += 1
            lr = max(P.beta, 1.0 / c.exp)
            c.eps += lr * (abs(R - c.p) - c.eps)
            c.p += lr * (R - c.p)
            c.as_ += lr * (tot - c.as_)
        kap = {id(c): (1.0 if c.eps < P.eps0 else P.alpha * (c.eps / P.eps0) ** (-P.nu)) for c in A}
        ks = sum(kap[id(c)] * c.num for c in A) or 1.0
        for c in A:
            c.F += P.beta * (kap[id(c)] * c.num / ks - c.F)

    def _insert(self, c: Cl) -> None:
        for o in self.pop:
            if o.cond == c.cond and o.act == c.act:
                o.num += 1
                return
        self.pop.append(c)

    def _delete(self) -> None:
        P = self.params
        while sum(c.num for c in self.pop) > P.N:
            tot_num = sum(c.num for c in self.pop)
            avgF = sum(c.F for c in self.pop) / tot_num
            votes = []
            for c in self.pop:
                v = c.as_ * c.num
                if c.exp > P.theta_del and c.F / c.num < P.delta * avgF:
                    v *= avgF / (c.F / c.num)
                votes.append(v)
            r = self.rng.random() * sum(votes)
            for c, v in zip(self.pop, votes):
                r -= v
                if r <= 0:
                    c.num -= 1
                    if c.num <= 0:
                        self.pop.remove(c)
                    break

    def _ga(self, A: list, x: str) -> None:
        P = self.params
        tot = sum(c.num for c in A)
        if not A or self.t - sum(c.ts * c.num for c in A) / tot <= P.theta_ga:
            return
        for c in A:
            c.ts = self.t

        def pick():
            s = sum(c.F for c in A)
            r = self.rng.random() * s
            for c in A:
                r -= c.F
                if r <= 0:
                    return c
            return A[-1]
        p1, p2 = pick(), pick()
        c1 = list(p1.cond)
        c2 = list(p2.cond)
        if self.rng.random() < P.chi:
            i, j = sorted(self.rng.sample(range(len(c1) + 1), 2))
            c1[i:j], c2[i:j] = c2[i:j], c1[i:j]
        kids = []
        for cc, par in ((c1, p1), (c2, p2)):
            for k in range(len(cc)):          # 틈새 변이: # ↔ 지금 입력 비트
                if self.rng.random() < P.mu:
                    cc[k] = x[k] if cc[k] == "#" else "#"
            a = par.act
            if self.rng.random() < P.mu:
                a = self.rng.randrange(self.n_actions)
            kids.append(Cl("".join(cc), a, (p1.p + p2.p) / 2, (p1.eps + p2.eps) / 2, 0.1 * (p1.F + p2.F) / 2, ts=self.t))
        for k in kids:
            parent = next((par for par in (p1, p2) if par.act == k.act and par.exp > P.theta_sub
                           and par.eps < P.eps0 and par.more_general(k)), None)
            if parent:                        # GA 포섭 — 더 일반적이고 정확한 부모가 자식을 삼킨다
                parent.num += 1
            else:
                self._insert(k)
            self._delete()

    # ------------------------------------------------------------ 한 걸음
    def step_label(self, x: str, label: int, explore: bool = True) -> int:
        """라벨이 있는 표본. 탐색이면 무작위 행동에 보상(맞으면 1000), 아니면 가장 좋은 행동. 고른 행동을 돌려준다."""
        self.t += 1
        M = self._match(x)
        pa = self._pa(M)
        if explore:
            a = self.rng.randrange(self.n_actions)
        else:
            a = max((b for b in range(self.n_actions) if pa[b] is not None), key=lambda b: pa[b])
        A = [c for c in M if c.act == a]
        R = self.params.reward if a == label else 0.0
        self._update(A, R)
        if explore:
            self._ga(A, x)
        return a

    def step_label_full(self, x: str, label: int) -> None:
        """라벨이 있으면 **모든 행동의 보상을 안다** — 맞는 규칙들을 행동마다 한꺼번에 갱신하고(지도형, UCS 와 같은 뜻),
        GA 는 정답 행동의 집합에서 돈다. 표본 하나로 12 행동을 다 배우므로 적은 대화에서 훨씬 빨리 자란다."""
        self.t += 1
        M = self._match(x)
        for a in {c.act for c in M}:
            self._update([c for c in M if c.act == a], self.params.reward if a == label else 0.0)
        self._ga([c for c in M if c.act == label], x)

    def step_bandit(self, x: str, act: int, good: bool) -> None:
        """좋음/나쁨만 아는 표본 — 그때 고른 행동의 규칙만 갱신한다."""
        self.t += 1
        M = self._match(x)
        A = [c for c in M if c.act == act]
        self._update(A, self.params.reward if good else 0.0)
        self._ga(A, x)

    def step_reward(self, x: str, act: int, R: float) -> None:
        """수치 보상을 아는 밴딧 표본(0..reward) — 그때 고른 행동의 규칙만 갱신한다(행동 기반 WALP 의 결과 보상)."""
        self.t += 1
        M = self._match(x)
        A = [c for c in M if c.act == act]
        self._update(A, R)
        self._ga(A, x)

    def step_rewards(self, x: str, R: dict) -> None:
        """행동마다 보상을 다 아는 표본(오프라인 모사에서 반사실 효용을 안다) — 행동마다 갱신, GA 는 보상이 가장 큰 행동의 집합."""
        self.t += 1
        M = self._match(x)
        for a in {c.act for c in M}:
            if a in R:
                self._update([c for c in M if c.act == a], R[a])
        best = max(R, key=R.get)
        self._ga([c for c in M if c.act == best], x)

    # ------------------------------------------------------------ 저장
    def to_json(self) -> dict:
        return {"n_actions": self.n_actions, "params": asdict(self.params), "seed": self.seed, "t": self.t,
                "pop": [asdict(c) for c in self.pop]}

    @classmethod
    def from_json(cls, d: dict) -> "XCS":
        x = cls(d["n_actions"], Params(**d["params"]), d.get("seed", 1))
        x.t = d.get("t", 0)
        x.pop = [Cl(**c) for c in d["pop"]]
        return x

    def dumps(self) -> str:
        return json.dumps(self.to_json(), ensure_ascii=False)
