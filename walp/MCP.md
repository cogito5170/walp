# MCP 층 — 두 안 중 무엇을 골랐나

사용자(2026-09-29)가 준 두 안: ①은 Context & Evidence Interpreter · Policy Executive · Policy Graph ·
Episodic Memory · Experience Update · Safety Supervisor · MCP Tool Router(탐색→호출→검증→반환),
②는 Language-to-Goal Compiler · Autonomous Policy Engine · Execution Supervisor(권한·인자·예산) ·
MCP Client/Tool Registry(발견·스키마·JSON-RPC) · MCP Servers.

## 결정: ②를 뼈대로, ①에서 셋을 가져온다

```
사람 / 다른 에이전트
   │  MCP (stdio JSON-RPC)                         ← 바깥쪽: walp/mcp_server.py  [지었다]
   ▼
Language-to-Goal Compiler      파서 + 개념 사전 → GoalSpec + 해석 상태      (②)  [src/parser.cpp]
   ▼
Policy Engine                                                               (②, 안을 ①처럼 가른다)
   ├─ Goal Manager                                                          [GoalManager]
   ├─ Context & Evidence Interpreter   관측·도구 결과 → 믿음(불확실성)        (①)  [StateEstimator]
   ├─ Episodic Memory                  사례                                (①)  [CaseRetriever]
   ├─ Policy Graph                     조건 → 행동(타입·잠긴 규칙)           (①)  [rule_action]
   ├─ Planner                          FSM / 제한 탐색                           [Deliberator]
   ├─ Experience Update                RuleLearner(제안) ≠ PolicyValidator(승인) (①) [learner.cpp · harness]
   └─ LoopAct                          고리 자체가 최상위 행동                    [walp/loop.py]
   ▼
Execution Supervisor  ── 두 개의 문 ──                                          (② + ①)
   ├─ 도구 호출 문: 등록·인자 스키마·권한(금지 경로)·예산·중복             [loop.Supervisor]
   └─ 불변 안전 문: 위험·미지·낡은 관측·피할 구역·귀환 에너지             [SafetySupervisor]
   ▼
Tool Registry(설정 시점에 얼림, 해시를 정책 버전 옆에)                         (②)
   ▼
MCP Servers / 시뮬레이터 / 하드웨어                        ← 안쪽: [설계만 — 아래]
```

**왜 ②가 사용자 친화적인가.** 사람이 처음 닿는 층이 컴파일러이고, 거기서 나오는 것이 '이렇게 알아들었다'
한 줄과 해석 상태(KNOWN · NOVEL_COMPOSITE · AMBIGUOUS · UNKNOWN · CONFLICT)다. 실행 전에 보여 줄 한 점이
있다. ①은 목표가 곧장 맥락 해석기로 들어가 그 점이 없다.

**왜 ②가 추상화하기 좋은가.** 모든 바깥 능력이 레지스트리의 스키마 하나로 준다. 코어의 `IPlatform` 경계가
그대로 MCP 클라이언트 자리다 — 코어는 상대가 시뮬레이터인지 로버인지 모른다.

**①에서 가져온 셋.** ⑴ 증거 해석기: 도구가 돌려준 것을 사실로 두지 않고 관측으로(수준 상한 · 불확실성).
⑵ Policy Graph · Episodic Memory · Experience Update 를 한 상자에 두지 않고 갈랐다 — 그래야 '규칙 갱신이
이득을 냈나, 경험 저장이 냈나' 를 따로 잰다(RESULTS §4 의 '경험만' 대조군). ⑶ 불변 제약 감독을 권한 검사와
**다른 문**으로 — 스키마상 합법인 호출도 물리 불변식을 따로 통과해야 한다.

**①에서 안 가져온 것.** 결정마다 도구를 '탐색' 하는 라우터. 실행 중에 목록이 바뀌면 같은 입력에 다른
호출열이 나와 재현이 깨진다. 발견은 설정 때 한 번, 레지스트리 해시를 남긴다(`loop.registry_hash`).

## 지은 것 / 안 지은 것

| | 상태 |
|---|---|
| 바깥쪽 MCP 서버(도구 9: interpret · run · loop · teach(기본 닫힘) · rate · sus · usability_report · se_tool · se_catalog) | 지었다 · `tests/test_walp.py` 가 JSON-RPC 로 돌린다 |
| LoopAct(최상위 행동 · 감독기 · 증거 상한 · 멈춤 규칙) | 지었다 · `tests/test_walp_loop.py` 22개 |
| 안쪽 MCP 클라이언트(C++ 코어 → JSON-RPC → 환경 서버) | **안 지었다.** 지금 환경은 같은 프로세스의 시뮬레이터(`IPlatform` 구현). 지을 때의 계약: `observe`/`execute`/`map_hint` 를 MCP 도구 셋으로, 결과는 증거 해석기를 거친다, 호출마다 Execution Supervisor 두 문 |
| 레지스트리 해시를 정책 버전에 묶기 | 고리 안에서는 한다(보고서에 찍힘). 정책 저장소(`PolicyStore`)에는 아직 안 묶었다 |

## 신원과 권한 (2026-09-29 보탬)

- MCP 서버는 호출자를 **모른다**: `WALP_MCP_USER` 는 서버를 띄운 사람이 정하는 값이지 인증이 아니다. 그래서 사전을 바꾸는
  `walp_teach` 는 **기본으로 닫혀 있고** `WALP_MCP_ALLOW_TEACH=1` 로 띄운 경우만 받는다. 부작용 도구(셸·쓰기·네트워크)는
  MCP 에서 확인 표를 만들 수는 있어도 **확인할 길이 없다**(확인은 디스코드 관리 채널에서만).
- 지금은 stdio 다(로컬 사용자만). **HTTP 로 열려면 먼저 인증을 붙여야 한다** — 이 문서의 가정이 거기서 깨진다.
- `walp_loop` 의 `k` 는 1~5 로 자른다.
