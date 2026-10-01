"""trace_stats -- 손으로 지은 작은 추적으로 셈이 맞는지(독립 대조: 손계산), 그리고 글을 밖으로 안 내는지."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from walp import trace_stats as S  # noqa: E402


def turn(i, ctx, tool=None, inp=None, out=10):
    content = [{"type": "tool_use", "id": f"t{i}", "name": tool, "input": inp or {}}] if tool else [{"type": "text", "text": "비밀 답"}]
    return {"type": "assistant", "message": {"id": f"m{i}", "role": "assistant", "content": content,
                                             "usage": {"input_tokens": 0, "cache_read_input_tokens": ctx,
                                                       "cache_creation_input_tokens": 0, "output_tokens": out}}}


def user(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


class Stats(unittest.TestCase):
    def setUp(self):
        rows = [user("비밀 요청"),
                turn(1, 100, "Bash", {"command": "git status"}),
                turn(2, 200, "Bash", {"command": "git push origin x"}),          # git 연쇄 -> 덧턴 1
                turn(3, 300, "Bash", {"command": "python3 -m unittest discover"}),
                turn(4, 400),
                user("Stop hook feedback: There are 3 unpushed commit(s)"),
                turn(5, 500),
                {"type": "cost-state", "modelUsage": {"m": {"inputTokens": 0, "outputTokens": 50,
                                                            "cacheReadInputTokens": 1500, "cacheCreationInputTokens": 0}},
                 "totalCostUSD": 1.0}]
        self.p = Path(tempfile.mkdtemp()) / "s.jsonl"
        self.p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows))
        self.s = S.stats(S.load(self.p))

    def test_손계산과_같다(self):
        s = self.s
        self.assertEqual((s["turns"], s["tokens"]), (5, 1550))
        self.assertEqual(s["by_kind"]["git"]["turns"], 2)
        self.assertEqual(s["by_kind"]["git"]["tokens"], 110 + 210)
        self.assertEqual(s["routine"]["chain_extra_turns"], 1)
        self.assertAlmostEqual(s["routine"]["chain_extra_share"], 210 / 1550)
        self.assertEqual(s["triggered_by"]["hook"], {"messages": 1, "turns": 1, "tokens": 510, "share": 510 / 1550})
        self.assertEqual(s["triggered_by"]["user"]["turns"], 4)
        self.assertEqual(s["cross_check"]["rel_diff"], 0.0)

    def test_글을_내보내지_않는다(self):
        out = json.dumps(self.s, ensure_ascii=False) + S.show(self.s)
        for secret in ("비밀", "git push origin x", "unittest discover"):
            self.assertNotIn(secret, out)

    def test_스트림으로_나뉜_같은_턴은_한_번(self):
        rows = [user("x"), turn(1, 100, "Read"), turn(1, 100, "Read")]
        p = Path(tempfile.mkdtemp()) / "s.jsonl"
        p.write_text("\n".join(json.dumps(r) for r in rows))
        self.assertEqual(S.stats(S.load(p))["turns"], 1)

    def test_하위_에이전트_추적은_따로(self):
        rows = [dict(r, isSidechain=True) for r in (user("x"), turn(1, 100, "Read"), turn(2, 200))]
        p = Path(tempfile.mkdtemp()) / "a.jsonl"
        p.write_text("\n".join(json.dumps(r) for r in rows))
        self.assertEqual(S.stats(S.load(p))["turns"], 0)                    # 주 세션 셈에 섞이지 않는다
        self.assertEqual(S.stats(S.load(p, sidechain=True))["turns"], 2)


if __name__ == "__main__":
    unittest.main()
