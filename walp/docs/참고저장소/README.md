# 인증 오픈소스 네 곳 — 코드 분석과 WALP 에 옮길 수 있는가 (2026-09-29)

요청: "Instagram 로그인 보안 → 참고 저장소 넷(Keycloak·WebAuthn4J·Authelia·Casdoor) → WALP 에 적용" 제안을
저장소마다 코드로 확인하라. 네 저장소를 이날 HEAD 로 shallow clone 해서 **읽기만** 했다(빌드·시험 안 돌림).
문서마다 주장에 `path:line` 이 붙어 있고, 못 읽은 자리는 "확인 못 함" 으로 적었다.

| 저장소 | HEAD | 문서 | 붙여 온 제안 | 판정 |
|---|---|---|---|---|
| Keycloak | `634ecb13` | [keycloak.md](keycloak.md) | 인증 상태 머신 | **약한 맞춤** — Keycloak 흐름은 REQUIRED/ALTERNATIVE AND·OR 트리 + 재개 가능한 세션 상태. WALP `plan` 은 처음 참인 조건이 이기는 평평한 우선순위표 |
| WebAuthn4J | `fc3797b` | [webauthn4j.md](webauthn4j.md) | 공개키 인증 검증 | **버린다** — WALP 에 사용자·키·신뢰 경계 너머 상대가 없다. 가져올 것은 signCount 식 **단조 검사** 하나 |
| Authelia | `f98f3fb` | [authelia.md](authelia.md) | 접근 정책 엔진 | **가장 맞다** — first-match 규칙표 + `default_policy: deny` + 수준 비교 + 차단을 WALP 도구 호출 정책(`se_router`/`mcp_server`)에 |
| Casdoor | `9f13005` | [casdoor.md](casdoor.md) | OAuth/OIDC 연동 | **지금은 풀 문제가 없다** — WALP MCP 는 stdio(`walp/mcp_server.py:1,110`), 디스코드는 디스코드가 인증 |

원 제안의 사실 확인: Keycloak 의 WebAuthn 검증은 실제로 **webauthn4j 0.30.3** 을 쓴다(`keycloak/pom.xml:249`) —
그러니 "Keycloak 과 WebAuthn4J 를 서로 다른 사례로 비교" 는 같은 코드를 두 번 보는 셈이다. Keycloak 의
brute-force 보호는 realm 기본값이 **꺼짐**(`RealmManager.java:273`, `// default settings off for now`).
"새 기기 로그인 알림" 은 Keycloak·Authelia 어느 쪽에서도 찾지 못했다(찾은 범위는 각 문서에).

## 분석 중 드러난 WALP 쪽 틈 — 코드를 직접 열어 확인한 것

재지 않았다. 코드를 읽어 확인만 했다.

1. **[고침] 관측 순서 역전을 받아들인다.** `StateEstimator::update` 는 `o.tick + max_age_ < s.now`(`walp/src/state.cpp:88`,
   `max_age=2`)만 본다. `last_fresh_tick` 은 쓰기만 하고(`:108`) 비교하지 않는다 — 2틱 창 안에서 더 오래된 관측이
   오면 상태가 과거로 돌아간다. 시뮬은 지연을 늘 3틱으로만 주입하므로(`walp/sim/world.cpp:226-230`) 기존 시험이
   이 경우를 밟은 적이 없다. WebAuthn signCount 검사와 같은 모양(`o.tick < last_fresh_tick` 이면 거부)으로 막힌다.
2. **[고침] 분류 안 된 도구가 `compute` 로 통과한다.** `WRITE_MARKERS`(`walp/se_tools.py:30`)는 정의만 있고 쓰는 곳이 없다.
   이름표가 없으면 `network` 아니면 `compute`(`:190`) — 쓰기 도구가 표에서 빠지면 허용된다. Authelia 식
   `default_policy: deny` 가 여기 맞는 답이다. 고친 것: 이름표가 없으면 몸통 코드의 표지로 shell > write > network 순으로
   가른다(`SHELL_MARKERS` 추가). `report_pdf`→write, `ruh2_make`·`recon_make`(배경 Popen)→shell 로 기본 거부.
   표지가 없는 도구는 여전히 compute 다 — 전면 기본 거부는 도구 29개를 막아서 하지 않았다. 시험: `tests/test_walp_se.py` [1].
3. **MCP `walp_teach` 에 권한 검사가 없다.** 디스코드는 관리 채널로 막는데(`walp/discord_cmd.py:59`),
   MCP 는 `front.teach` 로 바로 간다(`walp/mcp_server.py:61`). stdio 라 지금은 로컬 호출자뿐 — HTTP 로 여는 순간 틈이 된다.
4. **`allow_write` 가 자식에 안 간다.** `se_router.execute` 는 `{"tool","args"}` 만 넘기고(`walp/se_router.py:347`)
   `se_exec.py:98` 은 `req.get("allow_write", False)` 를 읽는다. 닫힌 쪽으로 틀려 보안 문제는 아니다.
5. **정책 롤백이 불변식을 다시 안 본다.** `PolicyStore::rollback`(`walp/src/learner.cpp:227-232`)은 `status==1` 만 본다.
   적재(`:273-276`)는 현재 버전만 재검사한다.
6. 사용성 원장 호출자 해시는 솔트 없는 sha256 앞 12자(`walp/usability.py:50`) — 익명이 아니라 가명이다.
   `walp/MCP.md:98` 은 도구 7개라 적었지만 `mcp_server.py` 에는 9개.

## 저장소 쪽에서 본 것 (각 문서에 근거)

- WebAuthn4J: `updateRecord`(signCount 갱신)가 사용자 정의 검증기보다 먼저 돈다
  (`AuthenticationDataVerifier.java:274-278`) — 바로 위에 인용한 스펙 문장("추가 검사 뒤로 미뤄야")과 반대 순서.
  MDS 상태 필터가 AAGUID 조회에만 걸린다는 관찰은 시험으로 확인하지 않았다.
- Casdoor: `plain`·`md5-salt` 해시를 여전히 고를 수 있고(`cred/manager.go:21-28`), 캡차가 켜지면 잠금 검사를
  건너뛴다(`object/check.go:268-273`). API 정책은 기동마다 코드 문자열로 덮어쓴다(`authz/authz.go:39`, `if true {`).
