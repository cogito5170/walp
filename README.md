# WALP — Weight-free Adaptive Language Policy (v0.4)

학습된 신경망 가중치 **없이** 제한된 자연어 명령을 목표로 바꾸고, 사례를 찾고, 계획하고, 안전 감독을
거쳐 한 행동씩 실행하고, 실패에서 규칙 후보를 만들어 **검증을 통과한 것만** 정책으로 올리는 C++17 코어.
과업은 하나다: 격자 세계(방 넷)에서 물체(카드·열쇠·컵·상자)를 색·브랜드로 찾기.

**결과부터: [`RESULTS.md`](RESULTS.md).** 선행연구: `paper/선행조사/WALP.md`(새 이론이 아니다 —
Soar/Rosie · CBR · BDI · CoALA 계보의 공학적 조합). SE 와의 비교: [`SE_비교.md`](SE_비교.md).
MCP 층 결정: [`MCP.md`](MCP.md).

## 짓고 돌리기

```bash
make -C walp BUILD=/tmp/wb all test          # 코어 + 시뮬 + CLI, 코어 시험 68개(고장 주입·재시작·재현성·힙 0·이웃 불변식·배율 이웃)
/tmp/wb/walp_cli parse "Locate the azure drinking cup"
/tmp/wb/walp_cli run "파란 비자카드 찾아줘, 선반은 피해서" --seed 3
/tmp/wb/walp_cli eval --n 200 --streams 5 --train 60 --out /tmp/eval.json   # 전체 평가 ~3분
/tmp/wb/walp_cli selfimprove --eval0 40000 --out /tmp/si.json               # v0.3 자기개선 고리 vs v0.2 ~5분
/tmp/wb/walp_cli diagnose                                                  # 이웃 하나가 결과를 얼마나 다시 굴리나
python3 tests/test_walp.py && python3 tests/test_walp_loop.py && python3 tests/test_walp_se.py && python3 tests/test_walp_search.py
```

사람이 쓰는 길(저자 없이 사용성을 재는 길): 디스코드 `!walp <명령>` · `!walp 루프 <명령>` · `!walp 결과` ·
SE 도구를 LLM 없이 `!walp 도구 <요청>` · `!walp 도구목록` · `!walp 도구점검 [결과]` · 부작용 도구는 `!walp 확인 <표>` ·
바깥 지식 물음은 `!walp 찾아보기 <물음>`(받은 문장 그대로 + 출처) · `!walp 찾아보기점검 [결과]`,
또는 MCP 클라이언트에 `python3 walp/mcp_server.py` 를 등록.
대화로 자라기(최소항, `walp/evolve.py`): 모르는 말로 거부된 뒤 **고쳐 말해 받아들여지면**, 그 낱말 자리에 아는 개념을 하나씩
넣어 해석이 꼭 같아지는 것이 하나뿐일 때만 "'주홍' 을 빨강 뜻으로 쓰셨나요?" 를 묻고, `네` 면 사전에 넣어 처음 말을 다시 돌린다.
쓰기 권한 없는 사람은 서로 다른 둘의 `네` 가 모여야 넣는다. 가중치 0 · GPU 0 · LLM 0. 시험: `tests/test_walp_evolve.py`.
대화 행위(`walp/dialog.py`): 찾기 말고도 인사 · 작별 · 감사 · 자기질문 · 능력질문 · 도움 · 바깥지식 · 불만 · 범위밖 · 네 · 아니에
**손으로 쓴 틀**로 답한다(지어내지 않는다). 층: 파서 → 손 규칙(W0, 모음보다 먼저 커밋) → 승격된 XCS(`walp/xcs.py`)가 빈자리만.
`!walp 행위 <이름>` 으로 잘못 알아들은 것을 고치면 라벨로 남고, `!walp 진화`(관리)가 원장을 재생해 XCS 를 새로 기르고
**봉인 모음 관문**(손만 · 손+다수 대조를 둘 다 쌍대로 이겨야 승격)을 돌린다. 봉인 v2(183)에서: 예전(찾기만) 7.1% → 손 규칙 61.7% →
손+XCS 65.0% — 손만은 6/0(p=0.016)로 이겼지만 손+다수는 3/2 로 못 이겨 **승격하지 않았다**(`eval/results/dialog_xcs_gate_v2.json`).
대화로 자라기(2026-09-30): 사람이 `행위 <이름>` 으로 고친 말은 **사례 기억**으로 손 규칙보다 앞서 곧바로 쓰이고(권한 또는 서로 다른 둘),
고침·감사·불만이 20개 쌓이면 **배경에서 XCS 진화**가 저절로 돌며, 승격은 봉인 모음(되풀이해 볼수록 엄격한 α 소비) + 사람이 고친 말의
1/3 을 떼어 둔 **사용자 관문**을 지나야 한다. 승격 모드는 '채움'(손 규칙 빈자리만) 또는 '덮기'(아주 확신하면 손 규칙도 덮음).
**Gemini 대화 상대**: `python3 -m walp.partner --hours 24`(또는 `bash walp/run_partner.sh 24`) — LLM 이 사람 역할로 말하고, WALP 가
행위를 잘못 알아들으면 구동기가 고친다. 내 PC 에서 돌리는 법은 `walp/PC_실행.md`. 시험: `tests/test_walp_grow.py` · `tests/test_walp_partner.py`.
숙고기(`walp/strips.py`, STRIPS): 여러 걸음 요청(차례 `&&` · 우발 `||` · 공유 피할 곳)을 계획해 걸음마다 시뮬 — 봉인 계획 모음 92.9%.
대화 행위 학습기: 성장망 + ESN, **학습기 먼저** 고르고 손 규칙은 뒤(봉인 v5 사전등록 실험, `eval/PREREG_층순서_ESN_숙고기.md`).
터미널에서는 `python3 -m walp.chat`(저장소 뿌리에서, g++·make 필요) — 한 줄에 한 말, `!walp` 없이, `끝` 으로 나간다.

## 구조

| 자리 | 파일 | 한 일 |
|---|---|---|
| 공통 자료형·인터페이스 | `include/walp/types.hpp` `interfaces.hpp` `core.hpp` | 명세 §3 + 해석 상태·정책 그래프·고정 크기 |
| 명령 해석 + 개념 사전 | `src/parser.cpp` · `data/lexicon.csv` | 문법어는 코드, 내용어는 사전(동의어·유사어·하위어·상위어·수식어), 추측 안 함 · v0.4 구절 틀(조건·기한·회피 창 안의 모르는 연결어만 넘어감) |
| 상태 추정·사례 검색·안전 감독 | `src/state.cpp` | 낡은/지연 관측 거부 · 액추에이터 감시 · 사례 → 구역 사전확률 · 행동마다 허가 |
| 계획 | `src/planner.cpp` | 정책 그래프(조건→행동) · FSM / 제한 탐색(깊이 3·노드 100) · 막힘 판정 |
| 실행 루프 | `src/executive.cpp` | 관측→추정→계획→허가→한 행동→기록(규칙·증거·버전·사례 ID) |
| 규칙 학습·정책 버전 | `src/learner.cpp` | 템플릿 후보(제안만) · 불변식·규칙 타입·잠긴 안전 규칙 · CRC 저장 |
| 시뮬레이터(정답·위반 계수) | `sim/world.*` | 코어와 분리 · 고장 주입 · 기록/재생 |
| 평가·검증기 | `tools/harness.*` `tools/walp_cli.cpp` | PolicyValidator(옛 환경 회귀·새 환경 이득) · B0~B4 · ablation · 적응 · 재현성 |
| LoopAct | `loop.py` | 고리를 최상위 행동으로(ANSWER/LOOP/REFUSE) · 예산 · 증거 상한 · 멈춤 규칙 |
| SE 도구 배선(v0.3) | `se_tools.py` `se_router.py` `se_exec.py` `nollm/` `data/tools.csv` `se_smoke.py` | AST 로 도구 69개 목록 · LLM 없는 라우터(되묻기·거부) · LLM 을 막고 센 실행 · 실행 점검 |
| 자기개선 고리(v0.3) | `tools/harness.cpp`(`se_improve` `se_improve2`) · `src/learner.cpp`(`policy_neighbors`) | 반례 사냥 · 반사실 진단 · 짝 부호 검정 승격 · 검증기 변이 검사 |
| 검색 경로(v0.3b) | `search_path.py` | 가르기 · 엔진 제안 교정 · dig 로 받기 · 추출형 고르기 · 지시 문장 버림 |
| 사람 쪽 | `front.py` `discord_cmd.py` `mcp_server.py` `usability.py` | LLM 없는 앞단 · 사용성 원장(호출자 해시) |

코어(`src/`)는 `-fno-exceptions -fno-rtti -Werror` 로 빌드되고 파일·OS 를 안 부른다(R1). 사전 CSV 는 호스트가
고정 배열로 올려 넘긴다.
