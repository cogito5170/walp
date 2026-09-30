"""LoopAct — '고리를 도는 것' 자체를 최상위 행동으로. LLM 없음, 표준 라이브러리만.

사용자(2026-09-29): "4번의 대화 30개 남짓의 도구. loop가 최상위 Act가 되기 위해 loop를 도는
concept를 구현해야한다." — 연구형 서브에이전트가 바깥 네 바퀴 · 도구 서른 번 남짓을 돌고,
WebFetch 가 막히자 모든 인용을 [조각] 으로 두고 못 본 것을 적어 내놓은 그 모양이다.

LLM 에이전트에서는 '한 바퀴 더 돌까' 가 모델의 재량이다. 여기서는 그것을 **정책의 행동**으로
꺼낸다. 최상위 결정은 셋 중 하나다:

    ANSWER  기억(지난 고리의 증거)만으로 모든 칸이 요구 수준을 채운다 → 도구를 안 부른다
    LOOP    채워야 할 칸이 있다 → 예산 안에서 고리를 돈다
    REFUSE  목표가 허용되지 않는 행동을 요구한다(예: 금지 경로 쓰기) → 한 번도 안 부른다

고리 한 바퀴(= 대화 한 턴):
    1. 규칙이 **서로 독립인 호출들을 한 묶음**으로 고른다(칸마다 수준을 가장 싸게 올리는 도구)
    2. 감독기가 호출마다 검사: 등록된 도구인가 · 인자 스키마 · 권한(금지 경로) · 예산 · 중복
    3. 부른다 → 증거 해석기가 결과를 원장에 적는다. **수준은 도구가 낼 수 있는 상한으로 자른다**
       (검색 도구가 '전문' 이라고 해도 조각이다 — 올리려면 실제로 읽는 도구가 성공해야 한다)
    4. 멈춤 판정: 다 채움(done) · 도구 예산(budget_tools) · 바퀴 예산(budget_turns) ·
       두 바퀴 잇달아 아무 칸도 안 올랐고 새 단서도 없음(stalled) · 더 부를 것이 없음(exhausted)

보고서는 칸마다 수준 꼬리표와 출처, 그리고 **못 채운 것**을 적는다. 올리지 않는다.

도구 목록은 고리 시작 때 얼린다(해시를 남긴다). 고리 도중에 도구를 새로 찾지 않는다 —
실행 중에 목록이 바뀌면 같은 목표에 다른 호출열이 나와 재현이 깨진다(MCP 층 선택 때 1번안의
'도구 탐색' 을 안 가져온 이유).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Callable

# 증거 수준 — 이 저장소의 논문 원장(G022)과 같은 꼬리표를 쓴다
LEVELS = {0: "없음", 1: "조각", 2: "목록", 3: "초록", 4: "전문"}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    args: tuple               # 필수 인자 이름들(값은 전부 문자열)
    needs: str                # 인자를 어디서 만드나: "query"(칸의 질의) | "url"(앞선 증거의 URL)
    yields: int               # 이 도구가 낼 수 있는 증거 수준 상한
    cost: int = 1
    writes: bool = False      # 부작용(쓰기)이 있나 — 금지 경로 검사를 더 엄하게


@dataclass
class Slot:
    id: str
    queries: list             # 검색에 쓸 질의 변형들(한 번씩만 쓴다)
    need: int                 # 요구 수준


@dataclass
class Evidence:
    slot: str
    claim: str
    url: str
    level: int
    tool: str
    turn: int


@dataclass
class Budget:
    turns: int = 4
    tools: int = 30
    per_turn: int = 8


@dataclass
class LoopResult:
    act: str                      # ANSWER | LOOP | REFUSE
    stop: str                     # done | budget_tools | budget_turns | stalled | exhausted | memory | refused
    turns: int = 0
    calls: int = 0
    registry_hash: str = ""
    slots: dict = field(default_factory=dict)       # 칸 → (수준, [출처])
    unverified: list = field(default_factory=list)   # 요구 수준을 못 채운 칸과 까닭
    trace: list = field(default_factory=list)        # (바퀴, 도구, 인자, 규칙, 결과)
    denied: list = field(default_factory=list)       # 감독기가 막은 호출


class Supervisor:
    """호출 하나하나를 실행 전에 막는 자리. 계획(호출 고르기)과 분리돼 있다."""

    def __init__(self, registry: dict, budget: Budget, forbidden: tuple = ()):
        self.registry, self.budget, self.forbidden = registry, budget, forbidden
        self.seen: set = set()

    def check(self, tool: str, args: dict, used: int, in_turn: int) -> "tuple[bool, str]":
        spec = self.registry.get(tool)
        if spec is None:
            return False, "unregistered_tool"
        if set(args) != set(spec.args) or not all(isinstance(v, str) and v for v in args.values()):
            return False, "schema"
        for v in args.values():
            for bad in self.forbidden:
                if bad and bad in v:
                    return False, f"forbidden:{bad}"
        if used >= self.budget.tools:
            return False, "budget_tools"
        if in_turn >= self.budget.per_turn:
            return False, "budget_per_turn"
        key = (tool, json.dumps(args, sort_keys=True))
        if key in self.seen:
            return False, "duplicate"
        self.seen.add(key)
        return True, "ok"


def registry_hash(specs: list) -> str:
    blob = json.dumps(sorted((s.name, s.args, s.needs, s.yields, s.cost, s.writes) for s in specs), ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


class LoopAct:
    def __init__(self, specs: list, impl: "dict[str, Callable[[dict], dict]]", budget: Budget = Budget(),
                 forbidden: tuple = (), memory: "list[Evidence] | None" = None):
        self.specs = list(specs)
        self.registry = {s.name: s for s in self.specs}
        self.impl = impl
        self.budget = budget
        self.forbidden = forbidden
        self.memory = list(memory or [])
        self.hash = registry_hash(self.specs)
        self.stall_turns = 2

    # ------------------------------------------------------------ 최상위 결정
    def decide(self, slots: list, required_writes: list = ()) -> str:
        for w in required_writes:
            if any(bad in w for bad in self.forbidden):
                return "REFUSE"
        lv = self._levels(slots, self.memory)
        return "ANSWER" if all(lv[s.id] >= s.need for s in slots) else "LOOP"

    def run(self, slots: list, required_writes: list = ()) -> LoopResult:
        act = self.decide(slots, required_writes)
        R = LoopResult(act=act, stop="", registry_hash=self.hash)
        ledger: list = list(self.memory)
        if act == "REFUSE":
            R.stop = "refused"
            R.unverified = [(s.id, "목표가 금지된 쓰기를 요구한다 — 한 번도 부르지 않았다") for s in slots]
            return R
        if act == "ANSWER":
            R.stop = "memory"
            return self._report(R, slots, ledger)

        sup = Supervisor(self.registry, self.budget, self.forbidden)
        used_q: set = set()
        failed_urls: set = set()
        fetched_urls: set = set()
        idle = 0
        for turn in range(1, self.budget.turns + 1):
            before = self._levels(slots, ledger)
            urls_before = {e.url for e in ledger}
            batch = self._select(slots, ledger, before, used_q, failed_urls, fetched_urls)
            if not batch:
                R.stop = "exhausted"
                break
            ran = 0
            for tool, args, rule, sid in batch:
                ok, why = sup.check(tool, args, R.calls, ran)
                if not ok:
                    R.denied.append((turn, tool, args, why))
                    if why == "budget_tools":
                        break
                    continue
                ran += 1
                R.calls += 1
                try:
                    out = self.impl[tool](dict(args))
                except Exception as e:  # noqa: BLE001 — 도구 실패는 증거가 아니라 기록이다
                    out = {"ok": False, "error": f"{type(e).__name__}: {e}"}
                status = "ok" if out.get("ok") else out.get("error", "fail")
                R.trace.append((turn, tool, args, rule, status))
                spec = self.registry[tool]
                if spec.needs == "url":
                    (fetched_urls if out.get("ok") else failed_urls).add(args["url"])
                for it in out.get("items", []) if out.get("ok") else []:
                    # 증거 해석: 도구 상한으로 자른다 — 여기서 수준이 '부풀지' 않는다
                    lvl = min(int(it.get("level", spec.yields)), spec.yields)
                    ledger.append(Evidence(sid, str(it.get("claim", ""))[:300], str(it.get("url", "")), lvl, tool, turn))
            R.turns = turn
            after = self._levels(slots, ledger)
            if all(after[s.id] >= s.need for s in slots):
                R.stop = "done"
                break
            if R.calls >= self.budget.tools:
                R.stop = "budget_tools"
                break
            gained = any(after[k] > before[k] for k in after)
            new_leads = {e.url for e in ledger} - urls_before
            # 한 바퀴의 헛걸음(죽은 링크 하나)으로는 안 멈춘다 — 다음 단서가 남아 있을 수 있다.
            # 두 바퀴 잇달아 아무 칸도 안 오르고 새 단서도 없으면 멈춘다(첫 판은 한 바퀴로 멈춰서,
            # 404 하나 뒤의 멀쩡한 URL 을 못 읽고 끝났다 — tests/test_walp_loop.py 가 잡았다).
            idle = 0 if (gained or new_leads) else idle + 1
            if idle >= self.stall_turns:
                R.stop = "stalled"
                break
        else:
            R.stop = "budget_turns"
        if not R.stop:
            R.stop = "budget_turns"
        self.memory = ledger     # 다음 목표의 ANSWER 판단에 쓴다(일화 기억)
        return self._report(R, slots, ledger)

    # ------------------------------------------------------------ 규칙: 다음 묶음 고르기
    def _select(self, slots, ledger, levels, used_q, failed_urls, fetched_urls):
        """칸마다 하나: 현재 수준을 올릴 수 있는 도구 중 (올림폭/비용) 최대. 규칙 ID 를 같이 낸다.
        R1 질의형 도구 — 안 쓴 질의 변형이 있을 때. R2 URL 형 도구 — 그 칸의 증거에 아직 안 읽은 URL 이 있을 때."""
        batch = []
        for s in slots:
            cur = levels[s.id]
            if cur >= s.need:
                continue
            best = None
            for spec in self.specs:
                if spec.yields <= cur:
                    continue
                if spec.needs == "query":
                    q = next((q for q in s.queries if (spec.name, q) not in used_q), None)
                    if q is None:
                        continue
                    cand = (spec, {spec.args[0]: q}, "R1_query")
                elif spec.needs == "url":
                    urls = [e.url for e in ledger if e.slot == s.id and e.url and e.url not in failed_urls
                            and e.url not in fetched_urls]
                    if not urls:
                        continue
                    cand = (spec, {spec.args[0]: urls[0]}, "R2_read_url")
                else:
                    continue
                score = (min(spec.yields, s.need) - cur) / spec.cost
                if best is None or score > best[0]:
                    best = (score, cand)
            if best:
                spec, args, rule = best[1]
                if spec.needs == "query":
                    used_q.add((spec.name, args[spec.args[0]]))
                batch.append((spec.name, args, rule, s.id))
        return batch

    @staticmethod
    def _levels(slots, ledger):
        lv = {s.id: 0 for s in slots}
        for e in ledger:
            if e.slot in lv and e.level > lv[e.slot]:
                lv[e.slot] = e.level
        return lv

    def _report(self, R: LoopResult, slots, ledger) -> LoopResult:
        for s in slots:
            evs = [e for e in ledger if e.slot == s.id]
            top = max((e.level for e in evs), default=0)
            R.slots[s.id] = (top, sorted({e.url for e in evs if e.level == top and e.url}))
            if top < s.need:
                why = "근거 없음" if top == 0 else f"{LEVELS[top]} 까지만 확인(요구 {LEVELS.get(s.need, s.need)})"
                R.unverified.append((s.id, why))
        return R


def render(R: LoopResult) -> str:
    """보고서(한국어). 꼬리표는 올리지 않는다."""
    lines = [f"최상위 행동: {R.act} · 멈춤: {R.stop} · 바퀴 {R.turns} · 호출 {R.calls} · 도구목록 {R.registry_hash}"]
    for sid, (lv, urls) in R.slots.items():
        lines.append(f"- {sid}: [{LEVELS.get(lv, lv)}] " + (" ".join(urls[:3]) if urls else "(출처 없음)"))
    if R.unverified:
        lines.append("못 채운 것: " + "; ".join(f"{a} — {b}" for a, b in R.unverified))
    if R.denied:
        lines.append("감독기가 막은 호출: " + "; ".join(f"{t}:{why}" for _, t, _, why in R.denied[:6]))
    return "\n".join(lines)


# ============================================================== WALP 도메인 바인딩
# 명령 한 줄의 '끝에서 끝' 점검: 해석 → (모호하면 후보마다) → 시뮬 k 판. 고르지 않는다 — 후보별
# 결과를 나란히 보인다. 수준: 1 해석됨 · 2 시뮬 1판 · 3 시뮬 k 판 모두 끝.
def walp_campaign(text: str, k: int = 3, budget: Budget = Budget(turns=4, tools=30, per_turn=8)) -> str:
    try:
        from . import front
    except ImportError:  # pragma: no cover
        import front  # type: ignore

    canon_of: dict = {}

    def interp(a):
        r = front.cli("parse", a["text"])
        if "error" in r:
            return {"ok": False, "error": r["error"]}
        cands = r.get("candidates") or []
        if r.get("status") not in ("ok", "ambiguous") or not cands:
            return {"ok": True, "items": [], "reason": r.get("reason")}
        for c in cands:
            canon_of[c] = c
        return {"ok": True, "items": [{"claim": c, "url": "goal:" + c, "level": 1} for c in cands]}

    def simulate(a):
        canon = a["url"].removeprefix("goal:").split("#")[0]
        seed = a["url"].split("#")[1] if "#" in a["url"] else "1"
        r = front.cli("run", _canon_text(canon), "--seed", seed, timeout=120)
        if "error" in r or not r.get("parsed"):
            return {"ok": False, "error": r.get("error", r.get("reason", "parse"))}
        return {"ok": True, "items": [{"claim": f"{canon} seed {seed}: {r['outcome']} steps {r['steps']} viol {r['violations']}",
                                       "url": f"sim:{canon}#{seed}", "level": 2}]}

    specs = [ToolSpec("walp_interpret", ("text",), "query", 1), ToolSpec("walp_simulate", ("url",), "url", 2)]
    # 1단계: 해석 칸 하나
    L = LoopAct(specs, {"walp_interpret": interp, "walp_simulate": simulate}, budget)
    r1 = L.run([Slot("해석", [text], 1)])
    cands = sorted(canon_of)
    if not cands:
        return render(r1) + "\n해석이 안 돼 시뮬레이션을 하지 않았다."
    # 2단계: 후보마다 k 판 — 칸 = (후보, 시드). 같은 도구목록·같은 예산 안에서 잇는다.
    slots, mem = [], []
    for c in cands:
        for s in range(1, k + 1):
            sid = f"{c}#{s}"
            slots.append(Slot(sid, [], 2))
            mem.append(Evidence(sid, c, f"goal:{c}#{s}", 1, "walp_interpret", 0))
    remaining = Budget(turns=max(0, budget.turns - r1.turns), tools=max(0, budget.tools - r1.calls), per_turn=budget.per_turn)
    L2 = LoopAct(specs, {"walp_interpret": interp, "walp_simulate": simulate}, remaining, memory=mem)
    r2 = L2.run(slots)
    out = [render(r1), "", render(r2)]
    if len(cands) > 1:
        out.append("\n해석이 여럿이라 **고르지 않았다** — 후보마다 결과를 나란히 두었다. 사람이 고른다.")
    return "\n".join(out)


def _canon_text(canon: str) -> str:
    """'OK type=card color=blue brand=visa avoid=shelf+desk deadline=200' → 파서가 기준어로 읽는 영어 한 줄."""
    f = dict(kv.split("=", 1) for kv in canon.split()[1:] if "=" in kv)
    words = ["find", "the"] + [f[k] for k in ("color", "brand") if k in f] + [f.get("type", "")]
    if "avoid" in f:
        words += ["avoid"] + f["avoid"].split("+")
    if "deadline" in f:
        words += ["within", f["deadline"], "steps"]
    if "uncertain" in f:
        words += ["if", "unsure", "skip" if f["uncertain"] == "skip" else "observe"]
    if "blocked" in f:
        words += ["if", "blocked", "stop" if f["blocked"] == "hold" else "replan"]
    return " ".join(w for w in words if w)
