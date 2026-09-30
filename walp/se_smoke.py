"""SE 도구 실행 점검 — 도구마다 한 번, **LLM 이 막힌 채로** 최소 인자로 불러 본다(`!walp 도구점검`).

무엇을 재나: 도구가 LLM 없이 돌아 쓸 만한 답을 내는가 / 환경(numpy·yosys 등)이 없어 못 도는가 /
LLM 을 부르려다 막혔는가(시도 수). 저장소를 더럽히지 않게 HEAD 를 임시 워크트리로 꺼내 그 안에서 돈다
(도구가 제 원장·산출물을 쓰기 때문이다 — 실측: research 한 번에 추적 원장 둘과 기억 노트 하나가 생겼다).

    python3 walp/se_smoke.py [--out 결과.json]
"""
from __future__ import annotations

import json
import re
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
V = "module cnt(input clk, output reg [2:0] q);\ninitial q=0;\nalways @(posedge clk) q <= q + 1;\nendmodule"
TB = ("module tb; reg clk=0; wire [2:0] q; cnt u(.clk(clk),.q(q)); always #1 clk=~clk; initial begin #20 "
      "if (q == 2) $display(\"PASS\"); else $display(\"FAIL q=%d\", q); $finish; end endmodule")
# 시험 입력도 도구의 규약을 지켜야 도구가 제 일을 한다 — 첫 판은 PASS/FAIL 없는 TB · assert 없는 설계 · .param 없는
# 넷리스트 · 필수 절 없는 보고서를 줘서, 도구가 **옳게 거절한 것**을 '환경 없음' 이나 '돌았다' 로 셌다
VF = ("module cnt(input clk, output reg [2:0] q);\ninitial q=0;\nalways @(posedge clk) q <= q + 1;\n"
      "always @(*) assert(q <= 3'd7);\nendmodule")
NET = ("* rc lowpass -- spice.py 의 rc_lowpass 를 .param 으로 뺀 것\n.param R1=1000\nV1 in 0 DC 0 AC 1\n"
       "R1 in out {R1}\nC1 out 0 1n\n.control\nac dec 50 10 100Meg\nmeas ac f3db WHEN vdb(out)=-3\n.endc\n")
MD = ("# 시험 보고\n\n## 요약\n- 실행 점검용 문서다(수치 없음 · 가정)\n\n## 선행연구\n- 없음\n\n## 원리\n- 없음\n\n## 설계\n- 없음\n\n"
      "## 결과\n- 없음\n\n## 검증\n- 없음\n\n## 증명한 것과 못 한 것\n- 아무것도 증명하지 않았다\n\n"
      "## 한계\n- 시험 입력이다\n\n## 참고문헌\n1. 이 문서 자체 [기억]")
ARGS = {"read_file": {"path": "README.md", "lines": 5}, "run_rtl": {"design": V, "testbench": TB, "seconds": 30},
        "lint_rtl": {"design": V}, "synth_rtl": {"design": V}, "prove_rtl": {"design": VF, "seconds": 60},
        "place_rtl": {"design": V, "target_mhz": 48, "seconds": 60}, "ip_signoff": {"design": V, "testbench": TB, "seconds": 60},
        "run_spice": {"netlist": "rc_lowpass"}, "monte_carlo": {"netlist": NET, "spread": "R1 100", "runs": 5, "checks": "f3db 100000 200000"},
        "concept": {"name": "CTLE"}, "textbook": {"question": "DFE error propagation"}, "spice_example": {"name": ""},
        "serdes_link": {"bits": 20000}, "quant_sweep": {"bits": 20000, "widths": "4,8"}, "adc_sweep": {"bits": 20000, "widths": "5,6", "full_scales": "2.5"},
        "loss_sweep": {"bits": 20000, "losses": "20,25", "widths": "8"}, "nn_equalizer": {"bits": 20000, "epochs": 2},
        "eq_area": {"seconds": 60}, "draw_circuit": {"example": "rc_lowpass", "check": False},
        "read_image": {"path": "temp_ocr/file1-01.png"}, "read_pdf": {"path": "paper/JCN2002.pdf", "쪽": "1"},
        "search_memory": {"query": "WALP"}, "orchestrator_status": {}, "ruh2_battery": {}, "recon_rover": {},
        "simulate_inspection": {}, "simulate_formation": {}, "report_pdf": {"markdown_text": MD},
        "codify_paper": {"arxiv_id": "2412.18579"}, "research": {"goal": "low power DFE"}}


def classify(r: dict) -> str:
    txt = (r.get("result") or r.get("error") or "")
    if r.get("llm_attempts") or "API_KEY" in txt:   # 키가 지워져 시도 전에 멈춘 것도 LLM 의존이다(read_image)
        return "llm_blocked"
    if not r.get("ok"):
        return "env_missing" if ("ModuleNotFoundError" in txt or "FileNotFoundError" in txt) else "error"
    # '못잼 0'(못 잰 것이 0개라는 집계)은 환경 없음이 아니다 — 첫 판은 security_audit 를 그것으로 셌다
    if re.search(r"도구가 없다|가 없다\s*--\s*(`?apt|배포가)|not installed|ModuleNotFoundError|No module named|apt-get install|pip install", txt):
        return "env_missing"
    # 도구는 돌았고 입력을 **규약 위반으로 거절**했다(PASS/FAIL 없음 · assert 없음 · 필수 절 없음) — 환경 탓이 아니다
    if re.search(r"\*\*못잼\*\*|못잼\s*--|\[[^\]]*거절", txt):
        return "input_rejected"
    # 돌았지만 아무것도 안 만들었다 — 첫 판은 이것을 'ran' 으로 셌다(codify_paper '스펙 0 · 성공 0')
    if re.search(r"스펙 0 · 성공 0|\[그림 아님\]|결과 없음", txt):
        return "empty"
    return "ran"


def run_all(repo: Path) -> list:
    sys.path.insert(0, str(repo))
    from walp import se_router, se_tools
    rows = []
    for t in se_tools.bot_tools_catalog():
        name = t.name
        bg = ("배경" in t.doc or "background" in t.doc.lower() or name.endswith("_make") or
              name in ("render_space", "orchestrator_solve", "orchestrator_resume", "orchestrator_stop", "delegate"))
        if t.kind in se_router.DENY_KINDS:
            rows.append({"tool": name, "kind": t.kind, "class": "denied"}); continue
        if bg:
            rows.append({"tool": name, "kind": t.kind, "class": "skip_background"}); continue
        r = se_router.execute(name, ARGS.get(name, {}), timeout=240)
        rows.append({"tool": name, "kind": t.kind, "class": classify(r), "llm_attempts": r.get("llm_attempts", 0),
                     "llm_blocked": r.get("llm_blocked", []), "seconds": r.get("wall_s"),
                     "excerpt": (r.get("result") or r.get("error") or "")[:200]})
    return rows


def main():
    out = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else None
    tmp = tempfile.mkdtemp(prefix="walp-smoke-")
    t0 = time.time()
    subprocess.run(["git", "-C", str(REPO), "worktree", "add", "-q", "--detach", tmp, "HEAD"], check=True)
    try:
        code = ("import json,sys; sys.path.insert(0, %r); from walp import se_smoke; "
                "print(json.dumps(se_smoke.run_all(__import__('pathlib').Path(%r)), ensure_ascii=False))" % (tmp, tmp))
        p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=tmp, timeout=3600)
        rows = json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else []
    finally:
        subprocess.run(["git", "-C", str(REPO), "worktree", "remove", "--force", tmp], capture_output=True)
    summary = {"ts": time.time(), "seconds": round(time.time() - t0, 1), "rows": rows,
               "counts": {c: sum(1 for r in rows if r["class"] == c) for c in
                          ("ran", "empty", "input_rejected", "env_missing", "llm_blocked", "error", "denied", "skip_background")}}
    text = json.dumps(summary, ensure_ascii=False, indent=1)
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(text, encoding="utf-8")
    print(json.dumps(summary["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
