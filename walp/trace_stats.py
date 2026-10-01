"""에이전트 추적(Claude Code 세션 JSONL)에서 토큰이 어디로 가나를 잰다 -- 실행 정책층 V&V 의 V0(오프라인 재생, LLM 0).

    python3 -m walp.trace_stats ~/.claude/projects/<프로젝트>/<세션>.jsonl [--json]

**집계만 낸다** -- 대화 글 · 명령 · 파일 내용은 내보내지 않는다(기록을 git 에 남기지 않는다는 규칙). 내는 것:
턴 수 · 토큰(새 입력 / 캐시 만들기 / 캐시 읽기 / 출력) · 턴당 맥락 크기 · 걸음 종류별 토큰 · 반복 연쇄 · 무엇이 턴을 불렀나
(사람 · 훅 · 시스템) · 그리고 같은 파일의 cost-state 와의 **독립 대조**(다른 길로 센 합과 얼마나 다른가).

걸음 종류는 그 턴의 첫 도구로 정한다. 한 턴의 토큰은 거의 다 '그때까지의 맥락' 이므로(캐시 읽기), 종류별 토큰은 사실상
**그 종류가 부른 턴 수 × 그때의 맥락** 이다 -- '그 걸음이 비쌌다' 가 아니라 '그 걸음 때문에 턴이 하나 더 돌았다' 로 읽는다.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

ROUTINE = ("git", "PR·머지", "기다리기", "검사 돌리기")


def _tok(u: dict) -> int:
    return sum((u.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens"))


def kind(name: str, inp: dict) -> str:
    """도구 호출 하나의 걸음 종류. 레포에 매이지 않는 범주만 쓴다."""
    if name == "Bash":
        c = str(inp.get("command", ""))
        if re.search(r"\bgit\b", c) and not re.search(r"unittest|pytest", c):
            return "git"
        if re.search(r"unittest|pytest|npm (run )?test|cargo test|go test|make test|node --check|bash -n|ctest", c):
            return "검사 돌리기"
        if re.search(r"kill -0|\bsleep\b|wait\b|seq 1 \d+", c):
            return "기다리기"
        if re.search(r"<<\s*'?EOF|sed -i|\bcat >|\btee\b|\bmv\b|\bcp\b|\brm\b", c):
            return "고치기"
        if re.search(r"^\s*(cat|sed -n|head|tail|grep|rg|ls|wc|find|tree|diff)\b|\| *(head|tail|grep)", c):
            return "읽기"
        return "셸 기타"
    if name in ("Read", "Grep", "Glob", "NotebookRead", "WebFetch", "WebSearch"):
        return "읽기"
    if name in ("Write", "Edit", "NotebookEdit", "MultiEdit"):
        return "고치기"
    if re.search(r"pull_request|merge|create_branch|push_files", name):
        return "PR·머지"
    if name in ("Agent", "Task"):
        return "하위 에이전트"
    return "기타 도구"


def load(path: "str | Path", sidechain: bool = False) -> dict:
    """sidechain: 하위 에이전트 추적(모든 줄이 isSidechain)을 읽을 때."""
    turns, events, cost = {}, [], None
    for line in Path(path).open(encoding="utf-8"):
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("type") == "cost-state":
            cost = d
        m = d.get("message")
        if not isinstance(m, dict) or (d.get("isSidechain") and not sidechain):
            continue
        if d.get("type") == "assistant" and m.get("usage"):
            tools = [(c.get("name", ""), c.get("input") or {}) for c in (m.get("content") or [])
                     if isinstance(c, dict) and c.get("type") == "tool_use"]
            if m.get("id") not in turns:
                events.append(("turn", m.get("id")))
            prev = turns.get(m.get("id"))
            turns[m.get("id")] = {"u": m["usage"], "tools": (prev["tools"] if prev else []) + tools}
        elif d.get("type") == "user":
            c = m.get("content")
            txt = c if isinstance(c, str) else " ".join(x.get("text", "") for x in c if isinstance(x, dict)
                                                         and x.get("type") == "text") if isinstance(c, list) else ""
            if txt.strip():
                events.append(("hook" if "Stop hook feedback" in txt else "sys" if txt.lstrip().startswith("<") else "user", None))
    return {"turns": turns, "events": events, "cost": cost}


def stats(tr: dict) -> dict:
    turns = tr["turns"]
    order = [i for k, i in tr["events"] if k == "turn"]
    T = [turns[i] for i in order]
    toks = [_tok(t["u"]) for t in T]
    total = sum(toks)
    kinds = [kind(*t["tools"][0]) if t["tools"] else "답 쓰기" for t in T]
    out = {"turns": len(T), "tokens": total,
           "split": {k: sum(t["u"].get(k) or 0 for t in T) for k in
                     ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")}}
    ctx = sorted((t["u"].get("cache_read_input_tokens") or 0) + (t["u"].get("cache_creation_input_tokens") or 0)
                 + (t["u"].get("input_tokens") or 0) for t in T)
    out["context_per_turn"] = {"mean": sum(ctx) / max(1, len(ctx)), "median": ctx[len(ctx) // 2] if ctx else 0,
                               "p90": ctx[int(.9 * len(ctx))] if ctx else 0}
    by = collections.defaultdict(lambda: [0, 0])
    for k, t in zip(kinds, toks):
        by[k][0] += 1
        by[k][1] += t
    out["by_kind"] = {k: {"turns": n, "tokens": v, "share": v / total if total else 0}
                      for k, (n, v) in sorted(by.items(), key=lambda x: -x[1][1])}
    # 같은 루틴이 연달아 -- 한 번(매크로 · 이벤트)이면 될 것을 여러 턴으로
    extra_turns = extra_tok = 0
    i = 0
    while i < len(kinds):
        j = i
        while j + 1 < len(kinds) and kinds[j + 1] == kinds[i]:
            j += 1
        if j > i and kinds[i] in ROUTINE:
            extra_turns += j - i
            extra_tok += sum(toks[i + 1:j + 1])
        i = j + 1
    out["routine"] = {"turns": sum(k in ROUTINE for k in kinds),
                      "share": sum(t for k, t in zip(kinds, toks) if k in ROUTINE) / total if total else 0,
                      "chain_extra_turns": extra_turns, "chain_extra_share": extra_tok / total if total else 0}
    # 무엇이 턴을 불렀나
    cur, why = None, collections.defaultdict(lambda: [0, 0, 0])
    tid = {i: tk for i, tk in zip(order, toks)}
    for k, i in tr["events"]:
        if k != "turn":
            cur = k
            why[k][0] += 1
        elif cur:
            why[cur][1] += 1
            why[cur][2] += tid[i]
    out["triggered_by"] = {k: {"messages": a, "turns": b, "tokens": c, "share": c / total if total else 0}
                           for k, (a, b, c) in why.items()}
    seq = collections.Counter(tuple(kinds[i:i + 3]) for i in range(len(kinds) - 2))
    out["top_3step"] = [[" → ".join(k), v] for k, v in seq.most_common(8)]
    c = tr["cost"]
    if c and c.get("modelUsage"):
        mu = c["modelUsage"]
        other = sum((m.get("inputTokens") or 0) + (m.get("outputTokens") or 0) + (m.get("cacheReadInputTokens") or 0)
                    + (m.get("cacheCreationInputTokens") or 0) for m in mu.values())
        out["cross_check"] = {"cost_state_tokens": other, "trace_tokens": total,
                              "rel_diff": (other - total) / other if other else None, "cost_usd": c.get("totalCostUSD")}
    return out


def show(s: dict) -> str:
    L = [f"모형 턴 {s['turns']:,} · 토큰 {s['tokens'] / 1e6:.1f}M "
         f"(캐시 읽기 {s['split']['cache_read_input_tokens'] / max(1, s['tokens']):.1%})",
         f"턴당 맥락 평균 {s['context_per_turn']['mean'] / 1e3:.0f}k · 중앙 {s['context_per_turn']['median'] / 1e3:.0f}k "
         f"· 90% {s['context_per_turn']['p90'] / 1e3:.0f}k  -- 턴 하나를 없애면 대략 이만큼"]
    for k, v in s["by_kind"].items():
        L.append(f"  {k:10s} 턴 {v['turns']:5d}  {v['share']:6.1%}")
    r = s["routine"]
    L.append(f"루틴({' · '.join(ROUTINE)}) {r['share']:.1%} · 같은 루틴 연쇄의 덧턴 {r['chain_extra_turns']} ({r['chain_extra_share']:.1%})")
    for k, v in s["triggered_by"].items():
        L.append(f"  {k:5s} 메시지 {v['messages']:4d} -> 턴 {v['turns']:5d}  {v['share']:6.1%}")
    if "cross_check" in s:
        x = s["cross_check"]
        L.append(f"독립 대조: cost-state {x['cost_state_tokens'] / 1e6:.1f}M vs 추적 {x['trace_tokens'] / 1e6:.1f}M "
                 f"(차 {x['rel_diff']:.1%}) · ${x['cost_usd']:.2f}")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="walp.trace_stats")
    ap.add_argument("transcript")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--sidechain", action="store_true", help="하위 에이전트 추적")
    a = ap.parse_args(argv)
    s = stats(load(a.transcript, a.sidechain))
    print(json.dumps(s, ensure_ascii=False, indent=1) if a.json else show(s))
    return 0


if __name__ == "__main__":
    sys.exit(main())
