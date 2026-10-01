"""trace_patterns -- 손으로 지은 작은 추적으로 셈이 맞는지(손계산), 그리고 경로 · 글을 밖으로 안 내는지."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from walp import trace_patterns as P  # noqa: E402


def turn(i, tools, ctx=100):
    return {"type": "assistant", "message": {"id": str(i), "usage": {"input_tokens": 1, "cache_read_input_tokens": ctx,
                                                                     "cache_creation_input_tokens": 0, "output_tokens": 1},
                                             "content": [{"type": "tool_use", "name": n, "input": x} for n, x in tools]}}


def user(text):
    return {"type": "user", "message": {"content": text}}


class Patterns(unittest.TestCase):
    def setUp(self):
        e = lambda f, a, b: ("Edit", {"file_path": f, "old_string": a, "new_string": b})
        rows = [user("비밀 요청"),
                turn(1, [("Read", {"file_path": "/x/비밀.py"})]),
                turn(2, [("Read", {"file_path": "/x/비밀.py"})]),                 # 같은 호출 반복 1
                turn(3, [("Bash", {"command": "cat /x/비밀.py | head"})]),        # 같은 파일 세 번째 읽기
                turn(4, [e("/x/비밀.py", "1", "2")]), turn(5, [e("/x/비밀.py", "2", "3")]),
                turn(6, [e("/x/b.py", "1", "2")]), turn(7, [e("/x/b.py", "1", "2")]),   # 같은 호출 반복 2
                user("Stop hook feedback: same warn"), turn(8, []),
                user("Stop hook feedback: same warn"), turn(9, [])]
        self.f = Path(tempfile.mkdtemp()) / "t.jsonl"
        self.f.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
        self.s = P.main(str(self.f))

    def test_hand_count(self):
        s = self.s
        self.assertEqual((s["turns"], s["tokens"]), (9, 9 * 102))
        self.assertEqual(s["hook_feedback"], {"distinct": 1, "total": 2, "repeats": 1, "max": 2})
        self.assertEqual(s["same_call_repeats"]["repeats"], 2)
        self.assertEqual(s["file_reads"], {"distinct": 1, "total": 3, "repeats": 2, "max": 3})
        self.assertEqual(s["file_edits"]["repeats"], 2)
        self.assertEqual((s["edit3_windows"], s["read3_windows"]), (2, 1))       # 겹치는 창: 4-5-6, 5-6-7
        self.assertEqual(s["edit_runs>=3"]["extra_turns"], 3)                    # 연쇄 하나(4턴) -> 덧턴 3
        self.assertAlmostEqual(s["edit_runs>=3"]["extra_share"], 3 / 9)

    def test_no_text_leaks(self):
        out = json.dumps(self.s, ensure_ascii=False)
        for secret in ("비밀", "/x/", "same warn"):
            self.assertNotIn(secret, out)


if __name__ == "__main__":
    unittest.main()
