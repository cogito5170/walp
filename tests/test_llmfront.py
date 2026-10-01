"""walp-front -- 잡담은 WALP 가 답하고(토큰 0), 나머지는 LLM CLI 로 넘기는가. CLI 는 진짜 하위 프로세스로 부른다(가짜 실행 파일).

    python3 -m unittest tests.test_llmfront      (저장소 뿌리에서)
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from walp import llmfront as F  # noqa: E402

FAKE = r'''#!/usr/bin/env python3
import json, os, sys
open(os.environ["FAKE_LOG"], "a").write(os.path.basename(sys.argv[0]) + " " + json.dumps(sys.argv[1:], ensure_ascii=False) + "\n")
name = os.path.basename(sys.argv[0])
if name == "gemini" and os.environ.get("FAKE_AUTH"):
    print(json.dumps({"error": {"type": "Error", "message": "Please set an Auth method", "code": 41}}), file=sys.stderr); sys.exit(41)
if name == "gemini":
    print(json.dumps({"response": "가짜 제미나이", "stats": {"models": {"g": {"tokens": {"prompt": 900, "cached": 100, "candidates": 30, "thoughts": 0, "total": 930}}}}}))
else:
    print(json.dumps({"result": "가짜 클로드", "is_error": False, "total_cost_usd": 0.01,
                      "usage": {"input_tokens": 3, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 7500, "output_tokens": 40}}))
'''


class Front(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.st = F.SmallTalk()
        cls.bin = Path(tempfile.mkdtemp())
        for n in ("claude", "gemini"):
            (cls.bin / n).write_text(FAKE)
            (cls.bin / n).chmod(0o755)
        cls.log = cls.bin / "log"
        cls.old = {k: os.environ.get(k) for k in ("PATH", "FAKE_LOG", "FAKE_AUTH")}
        os.environ["PATH"] = f"{cls.bin}{os.pathsep}{os.environ['PATH']}"
        os.environ["FAKE_LOG"] = str(cls.log)

    @classmethod
    def tearDownClass(cls):
        for k, v in cls.old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

    def setUp(self):
        os.environ.pop("FAKE_AUTH", None)
        self.log.write_text("")

    def calls(self):
        return [x for x in self.log.read_text().splitlines() if x]

    def test_잡담은_WALP_가_토큰_0(self):
        for q, act in (("안녕하세요", "greet"), ("고마워", "thanks")):
            r = F.ask(q, "claude", st=self.st)
            self.assertEqual((r["by"], r["act"], r["tokens"]["total"]), ("walp", act, 0), q)
        self.assertEqual(self.calls(), [])                           # LLM 을 안 불렀다

    def test_잡담이_아니면_LLM_으로(self):
        r = F.ask("다음 주 회의 잡아줘", "claude", st=self.st)
        self.assertEqual((r["by"], r["answer"], r["tokens"]["total"]), ("claude", "가짜 클로드", 7543))
        self.assertEqual(self.calls(), ['claude ["-p", "다음 주 회의 잡아줘", "--output-format", "json"]'])

    def test_gemini_토큰과_로그인_없으면_다음으로(self):
        r = F.ask("다음 주 회의 잡아줘", "gemini", st=self.st)
        self.assertEqual((r["by"], r["tokens"]["total"], r["tokens"]["cache_read"]), ("gemini", 930, 100))
        self.assertIsNone(r["cost_usd"])                             # Gemini CLI 는 비용을 안 낸다 -- 0 이 아니다
        os.environ["FAKE_AUTH"] = "1"
        r = F.ask("다음 주 회의 잡아줘", "gemini,claude", st=self.st)
        self.assertEqual((r["by"], r["route"]), ("claude", "walp 모름 -> gemini auth -> claude"))

    def test_C_코어를_빌드하지_않는다(self):
        # 숙고 · 기억 층을 끈다 -- make/g++ 가 없는 맥에서도 돈다
        names = {type(s).__name__ for s in self.st.bus.행동들}
        self.assertIn("반응", names); self.assertNotIn("숙고", names); self.assertNotIn("기억", names)
        r = subprocess.run([sys.executable, "-m", "walp.llmfront", "route", "안녕"], cwd=ROOT, capture_output=True, text=True,
                           env={"PATH": "/nonexistent", "HOME": tempfile.mkdtemp(), "PYTHONPATH": str(ROOT)}, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["act"], "greet")

    def test_묶인_체계(self):
        self.assertEqual(self.st.model, F.MODEL)
        self.assertTrue(F.MODEL.is_file())

    def test_답을_바꿀_수_있다(self):
        r = F.ask("안녕", "off", replies={"greet": "어서 오세요"}, st=self.st)
        self.assertEqual(r["answer"], "어서 오세요")


if __name__ == "__main__":
    unittest.main()
