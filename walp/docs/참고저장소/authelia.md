# Authelia 분석 — 그리고 WALP 도구 호출 정책에 옮길 수 있는 것 / 없는 것

대상: `authelia` shallow clone, HEAD f98f3fb (2026-09-29), 읽기만 했다. 경로는 저장소 루트 기준.
WALP 쪽 경로는 `/home/user/SE/walp` 기준(`walp/...`), SE 루트 파일은 `/home/user/SE/...`.
읽지 않은 것은 "확인 못 함" 으로 적었다.

---

## 1. 배치

- 모듈 `github.com/authelia/authelia/v4`, `go 1.27.0`, `toolchain go1.27.1` (`go.mod:5`, `go.mod:7`, `go.mod:9`).
- `internal/` 아래 패키지(ls 로 확인): `authentication authorization clock commands configuration duo expression
  handlers logging metrics middlewares mocks model notification ntp oidc random regulation server service
  session storage suites templates totp utils webauthn`.
  - `handlers/` — HTTP 핸들러. authz 계열(`handler_authz*.go`), 1FA(`handler_firstfactor_password.go`,
    `handler_firstfactor_passkey.go`), 2FA(`handler_sign_totp.go`, `handler_sign_webauthn.go`, `handler_sign_duo.go`),
    비밀번호 재설정(`handler_reset_password.go`), 세션 승격(`handler_session_elevation.go`), OAuth2/OIDC(`handler_oauth2_*.go`).
  - `authorization/` — 접근 규칙 엔진(§3).
  - `authentication/` — 사용자 백엔드: `file_user_provider.go`, `ldap_user_provider.go` 등. 인증 수준 `Level`(§4).
  - `regulation/` — 무차별 대입 억제(§6). `regulator.go` 214줄이 본체.
  - `session/` — `UserSession` 구조체(`session/types.go:24`), 수준 계산(`session/user_session.go:29`).
  - `storage/` — SQL 제공자(`sql_provider_backend_mysql.go` / `postgres` / `sqlite`), 마이그레이션 디렉터리.
  - `notification/` — `smtp_notifier.go`, `file_notifier.go`. 템플릿은 `templates/embed/notification/`.
  - `oidc/` — 비테스트 파일 19개. 내용은 이 보고서 범위 밖이라 **확인 못 함**.
  - `middlewares/` — `Require1FA`, `RequireElevated`(`middlewares/require_auth.go:16`, `:28`), 레이트리미터, 신원 검증.
  - `configuration/` — koanf 기반 로딩(`koanf_provider_filtered_file.go` 등), 스키마는 `configuration/schema/`.
- `web/` — React 19.3.0(`web/package.json:22`), Vite 8.3.1(`web/package.json:87`). 구성 요소 세부는 **확인 못 함**.
- 라우팅 한 자리: `internal/server/handlers.go`.

## 2. Forward-auth 모델 — 프록시가 묻고 Authelia 가 허용/리다이렉트/거부

**경로 등록.** `pathAuthz = "/api/authz"`, `pathAuthzLegacy = "/api/verify"` (`internal/server/const.go:30-31`).
설정의 `server.endpoints.authz` 맵을 돌며 엔드포인트마다 `Authz` 를 지어 붙인다
(`internal/server/handlers.go:238`). 이름이 `legacy` 면 `/api/verify` 에 `ANY` 로(`:248`), 구현이 legacy/ext-authz 면
`ANY`, 그 밖(forward-auth, auth-request)은 `GET`/`HEAD` 로만 붙는다. 엔드포인트 이름 상수는
`forward-auth`, `auth-request`, `ext-authz`, `legacy` (`internal/configuration/schema/const.go:183-186`).

**구현 선택.** `WithEndpointConfig` 가 `Implementation` 으로 네 가지 중 하나를 고르고, 인증 전략
(쿠키 세션 · `Authorization` 헤더 · `Proxy-Authorization` 등)을 붙인다
(`internal/handlers/handler_authz_builder.go:85-117`).

**대상 복원.** 프록시는 원래 요청을 헤더로 넘긴다. forward-auth(Traefik·Caddy 계열)는
`X-Forwarded-Method` 가 비면 오류, 그 다음 `X-Forwarded-Proto/Host/URI` 로 `authorization.Object` 를 만든다
(`internal/handlers/handler_authz_impl_forwardauth.go:13-28`). auth-request(nginx `auth_request`)는
`handler_authz_impl_authrequest.go:16` 에서 따로 복원.

**판정.** `(*Authz).Handler` (`internal/handlers/handler_authz.go:157-256`):
1. 대상 복원 실패 → 400 (`:165-171`). https/wss 가 아니면 400 (`:173-179`). 세션 쿠키 도메인 설정이 없으면 400 (`:181-187`).
2. `authz.authn(...)` 으로 요청자의 인증 상태를 얻는다 (`:202`).
3. `Authorizer.GetRequiredLevel(Subject{Username, Groups, ClientID, IP}, object)` 로 필요한 수준을 구한다 (`:207-215`).
4. `isAuthzResult(authn.Level, required, ruleHasSubject)` 의 세 결과로 갈린다 (`:239-255`):
   `Forbidden` → 403, `Unauthorized` → 전략별 처리(대개 포털로 리다이렉트), `Authorized` → 200 + `Remote-User` 등 헤더
   (`headerRemoteUser/Groups/Name/Email`, `internal/handlers/const.go:33-36`).

`isAuthzResult` 본체 (`internal/handlers/handler_authz_util.go:93-108`):

```go
case required == authorization.Bypass:                         -> Authorized
case required == authorization.Denied && (level != NotAuthenticated || !ruleHasSubject):
                                                                -> Forbidden
case required == OneFactor && level >= OneFactor,
     required == TwoFactor && level >= TwoFactor:               -> Authorized
default:                                                        -> Unauthorized
```

주목할 점: **익명 사용자가 subject 달린 deny 규칙에 걸리면 403 이 아니라 401(로그인 유도)** 이다 (`:97-100` 주석).
익명은 누구인지 모르므로 "그 규칙이 정말 너에게 해당하는지" 를 판정할 수 없기 때문이다.

**리다이렉트 코드.** XHR 이거나 `text/html` 을 안 받으면 401, 아니면 GET/HEAD/OPTIONS 는 302, 그 밖은 303
(`handler_authz_util.go:118-129`). auth-request 구현은 무조건 401 (`handler_authz_impl_authrequest.go:38-40`) —
nginx 가 401 을 받아 `error_page` 로 로그인 페이지를 띄우는 방식이라서다.

## 3. 접근 규칙 엔진 (`internal/authorization`)

**정책 수준** (`internal/authorization/const.go:8-22`): `Bypass=0, OneFactor, TwoFactor, Denied` (iota 순). 설정 문자열은
`bypass / one_factor / two_factor / deny` (`const.go:36-41`).

**규칙 구조** (`access_control_rule.go:44-55`): `Position, Domains, Resources, Query, Methods, Networks, Subjects, Policy`,
그리고 `HasSubjects`. `domain_regex` 는 별도 필드가 아니라 `ruleAddDomainRegex` 로 `Domains` 에 합쳐진다
(`access_control_rule.go:36-37`). 규칙은 설정 순서대로 1부터 `Position` 을 받는다 (`:13-16`).

**첫 일치(first-match)** (`authorizer.go:55-72`):

```go
for _, rule := range p.rules {
    if rule.IsMatch(subject, object) {
        return rule.HasSubjects, rule.Policy        // 첫 일치에서 끝
    }
}
return false, p.defaultPolicy                       // 아무것도 안 맞으면 default_policy
```

**한 규칙 안의 조건은 AND, 순서 고정** (`access_control_rule.go:58-84`):
domains → resources → query → methods → networks → subjects. 각 조건 목록 안은 OR 이고
(`MatchesDomains` `:87-99`, `MatchesResources` `:102-114`, `MatchesQuery` `:117-129`),
**빈 목록은 "모두 일치"** 다 (`:88`, `:103`, `:118`, `:133`; 네트워크는 `types.go:20-21`).

**subjects 의 두 겹 논리.** 바깥 목록은 OR (`MatchesSubjectExact`, `access_control_rule.go:155-169`), 안쪽 목록은 AND
(`AccessControlSubjects.IsMatch`, `access_control_subjects.go:26-28`). 즉 `[[group:admins, user:john]]` 은
"admins 이면서 john". 그리고 **익명 subject 는 subject 조건을 통과한다** (`MatchesSubjects`, `:146-152`).
그래서 익명은 subject 달린 규칙에 걸리고, 그 결과 `HasSubjects=true` 가 §2 의 "401 로 로그인 유도" 로 이어진다.
`IsAnonymous` 는 username·groups·client_id 가 모두 비었을 때 (`types.go:68-70`).

**default_policy 와 MFA 여부.** `NewAuthorizer` 는 default 가 `TwoFactor` 이거나 규칙 하나라도 `TwoFactor` 면 `mfa=true`
(`authorizer.go:23-47`). 이 값이 2FA UI 노출 여부를 정한다(`IsSecondFactorEnabled`, `:50-52`).

**진단.** `GetRuleMatchResults` 는 규칙마다 7가지 조건의 일치 여부와 "앞에서 이미 걸려 건너뛰었나(`Skipped`)" 를
돌려준다 (`authorizer.go:75-98`). "왜 이 규칙이 안 걸렸나" 를 설명하는 도구다 — CLI 연결 자리는 **확인 못 함**.

## 4. 인증 수준과 세션

- `authentication.Level`: `NotAuthenticated=iota, OneFactor, TwoFactor` (`internal/authentication/types.go:503-513`).
- 세션은 수준을 **저장하지 않고 AMR(Authentication Method References)에서 계산**한다
  (`session/user_session.go:29-42`): username 이 비면 NotAuthenticated, 소지(possession) + 지식(knowledge) 요소가 둘 다
  있으면 TwoFactor, passkey 가 사용자 검증(UV)까지 했고 설정이 허용하면 TwoFactor, 하나면 OneFactor.
- `UserSession` 필드 (`session/types.go:24-52`): `Username, Groups, Emails, KeepMeLoggedIn, LastActivity,
  FirstFactorAuthnTimestamp, SecondFactorAuthnTimestamp, AuthenticationMethodRefs, WebAuthn, TOTP,
  PasswordResetUsername, RefreshTTL, Elevations`.
- 수준 설정자: `SetOneFactorPassword`, `SetOneFactorPasskey`, `SetTwoFactorTOTP/Duo/WebAuthn/Password`
  (`session/user_session.go:45-106`).
- **비활동(inactivity)**: 익명이거나 `KeepMeLoggedIn`(remember-me)이거나 설정이 0 이면 검사 안 함, 아니면
  `LastActivity + Inactivity < now` 면 무효 (`handlers/handler_authz_authn.go:567-576`, 호출은 `:542`).
- 절대 만료(expiration)·remember_me 기간의 쿠키 수명 처리 자리는 **확인 못 함**(session provider 설정 쪽으로 보이나 안 읽었다).
- **승격(elevation)**: `Elevations{User *Elevation}`, `Elevation{ID, RemoteIP, Expires}` (`session/types.go:78-87`).
  `RequireElevated` 미들웨어는 1FA 확인 → (설정에 따라) 2FA 면 승격 생략(`SkipSecondFactor`, `require_auth.go:67`) 또는
  2FA 요구(`RequireSecondFactor`, `:73`) → 승격 없음이면 403 `Elevation:true` (`:97-98`) → 만료(`:113`)나
  **IP 불일치**(`:119-126`)면 승격을 지우고 403. 비밀번호 변경·TOTP/WebAuthn 등록·삭제가 이 문 뒤에 있다
  (`internal/server/handlers.go:283`(change-password) 부근, `:314-345`).

## 5. 2차 요소와 비밀번호 백엔드

- **TOTP**: 라이브러리 `github.com/authelia/otp v1.0.4` (`go.mod:16`, pquerna/otp 계열 포크로 보이나 출처는 확인 못 함).
  생성·검증은 `internal/totp/totp.go:78-89`, `:113`. 엔드포인트 `/api/secondfactor/totp` 는 전용 레이트리미터
  (`internal/server/handlers.go:313`).
- **WebAuthn/Passkey**: `github.com/go-webauthn/webauthn v0.18.2` (`go.mod:27`). 엔드포인트 `:323-345`.
  passkey 는 1FA 로도 쓰인다(`handler_firstfactor_passkey.go`).
- **Duo**: `github.com/duosecurity/duo_api_golang v0.3.0` (`go.mod:17`). `/auth/v2/preauth` 호출(`internal/duo/duo.go:64-68`)과
  Push 응답 처리(`duo.go:46-58`). 엔드포인트 `internal/server/handlers.go:368-371`.
- **file 백엔드**: `CheckUserPassword` 는 비활성 사용자를 `ErrUserNotFound` 로 돌리고(존재 여부를 숨긴다)
  `details.Password.MatchAdvanced` 로 비교 (`internal/authentication/file_user_provider.go:92-104`).
  해시는 `github.com/go-crypt/crypt v0.14.15` (`go.mod:22`), argon2 기본 변형 `argon2id`, iterations 기본 3
  (`internal/configuration/schema/authentication.go:103-105`).
- **LDAP**: `github.com/go-ldap/ldap/v3 v3.4.14` (`go.mod:23`). `CheckUserPassword` 는 사용자 DN 으로 bind 하는 방식
  (`ldap_user_provider.go:88-98` 에서 클라이언트 획득까지 읽음, bind 줄 자체는 **확인 못 함**).
- `internal/suites/PAM` 이 있으나 그 설정은 `authentication_backend.file` 을 쓴다(`internal/suites/PAM/configuration.yml:43-45`).
  PAM 백엔드 코드는 `internal/authentication` 에서 찾지 못했다 — PAM *모듈* 쪽 스위트로 추정, **확인 못 함**.
- 1FA 앞에 타이밍 공격 지연기: `NewTimingAttackDelay(10, time.Second)` (`internal/server/handlers.go:265`).

## 6. Regulation(무차별 대입) 과 알림

**설정** (`internal/configuration/schema/regulation.go:12-25`): `modes`(기본 `[user]`, `ip` 가능), `max_retries`(기본 3),
`find_time`(2분), `ban_time`(5분).

**기록.** 모든 시도(성공·실패·차단 중)를 `AuthenticationAttempt{Time, Successful, Banned, Username, Type, RemoteIP,
RequestURI, RequestMethod}` 로 저장 (`regulation/regulator.go:41-55`, 호출은 `handlers/response.go:492`).

**판정 — 차단은 '읽을 때 세는 것' 이 아니라 '넘는 순간 행을 쓰는 것'** 이다(새 구조):
- 실패이고, 아직 차단 중이 아니고, 모드가 켜져 있고, **`authType == "1FA"` 일 때만** 차단을 검토한다
  (`regulator.go:57-61`). 즉 TOTP·Duo·WebAuthn 실패는 기록되지만 이 경로로 차단을 만들지 않는다(그쪽은 §2 의
  엔드포인트 레이트리미터가 맡는다, `internal/server/handlers.go:313`, `:370`).
- `since = now - find_time` 이후 기록을 최대 `max_retries` 개 읽고 (`:63`, `:83`, `:121`), `expires()` 가 최근부터 훑다가
  **성공을 만나면 멈춘다** (`:186-214`, 특히 `:192-193`). 실패가 `max_retries` 이상이면 만료 = 첫 실패 시각 + `ban_time`.
- 넘으면 `BannedIP` / `BannedUser` 행을 `Source:"regulation"`, `Reason:"Exceeding Maximum Retries"` 로 저장
  (`:95-106`, `:133-144`).
- **검사**는 IP 차단 먼저, 없으면 사용자 차단 (`BanCheck`, `:166-184`). 1FA 핸들러는 비밀번호 확인 **전에** 이것을 부르고
  (`handlers/handler_firstfactor_password.go:54` → `:70`), 저장소 오류면 **닫힌 쪽으로**(401) 실패한다 (`:63-67`).
- 차단은 사람이 관리할 수 있다: `authelia storage bans` 하위에 user/ip 명령 (`internal/commands/storage.go:287`, `:306`, `:326`).
  즉 regulation 은 "차단 행의 한 출처" 일 뿐이고, 수동 차단이 같은 표에 들어간다.

**알림.**
- 템플릿은 셋뿐: `Event`, `IdentityVerificationJWT`, `IdentityVerificationOTC` (`internal/templates/embed/notification/`).
- `Event` 메일: 비밀번호 변경(`handler_change_password.go:146`, `handler_reset_password.go:236`), 2FA 추가·삭제
  (`ctxLogEvent`, `handlers/util.go:45`; 호출 `handler_register_totp.go:261,358`, `handler_register_webauthn.go:314`,
  `handler_webauthn_credentials.go:276`).
- **"새 로그인" 알림은 찾지 못했다.** `grep -rin "new login\|NewLogin\|login notification"` 가 비테스트 Go 에서 0건.
  로그인 성공 경로에서 `Notifier.Send` 를 부르는 자리도 위 목록에 없다. 없다고 단정하지는 않는다 — 웹 쪽·설정 쪽은 안 봤다.

## 7. 비밀번호 재설정 · 신원 확인 · 승격용 일회 코드

- **재설정 (JWT 링크)**: `/api/reset-password/identity/start` → `/finish` → `POST /api/reset-password`, 시작·끝 둘 다
  전용 레이트리미터 (`internal/server/handlers.go:271-279`). 시작/끝은 공용 미들웨어
  `IdentityVerificationStart/Finish` 로 짓는다 (`handlers/handler_reset_password.go:270`, `:302-303`;
  구현 `middlewares/identity_verification.go`, 세부는 안 읽음). 토큰은 JWT 로 서명·발급자·알고리즘·시각을 엄격히 검사하고
  (`handler_reset_password.go:50-58`), 저장소의 `IdentityVerification` 행과 대조해 **폐기(revoke) 가능** (`:114`, `:128`).
- **승격용 일회 코드(OTC)**: `UserSessionElevationPOST` 가 `model.NewOneTimeCode(username, Characters, CodeLifespan)` 을 만들어
  저장하고 (`handler_session_elevation.go:171`, `:182`) `IdentityVerificationOTC` 메일로 보낸다 (`:217`).
  `PUT` 은 공백 제거·대문자화·길이 상한 20 (`:274-276`), **요청 IP 와 의도(intent)로** 코드를 찾고 (`:285`),
  `subtle.ConstantTimeCompare` 로 비교 (`:339`), 한 번 쓰면 `ConsumeOneTimeCode` (`:359`), 승격 만료는
  `ElevationLifespan` (`:371`). 코드 폐기 `DELETE` 경로도 있다 (`:415`, `:455`). POST/PUT 은 레이트리미터와 1초 인위 지연
  (`internal/server/handlers.go:290-298` 부근의 `middlewareElevatePOST/PUT`).

## 8. 시험

- `_test.go` 파일 362개(저장소 전체, `find` 로 셈). regulation 만 해도 블랙박스 1275줄 (`regulation/regulator_blackbox_test.go`).
- e2e: `internal/suites/` 에 스위트 디렉터리(`TwoFactor`, `Traefik`, `Caddy`, `HAProxy`, `Envoy`, `LDAP`, `ActiveDirectory`,
  `DuoPush`, `NetworkACL`, `BypassAll`, `OneFactorOnly`, `ShortTimeouts`, `MultiCookieDomain`, `Postgres`, `MySQL`,
  `MariaDB`, `OIDC`, `OIDCConformance`, `HighAvailability`, `PAM` ...). 각 스위트는 docker compose 파일 묶음을 겹쳐 올린다 —
  예: TwoFactor 는 공통 `compose.yml` + 스위트 compose + authelia 백/프론트 + nginx + smtp (`suite_two_factor.go:20-27`),
  준비 대기 `waitUntilAutheliaIsReady` (`:35`).
- 브라우저 구동은 `github.com/go-rod/rod v0.116.2` (`go.mod:24`). 시나리오는 `scenario_*_test.go`
  (`scenario_regulation_test.go`, `scenario_inactivity_test.go`, `scenario_bypass_policy_test.go` ...).
- CI 에서 어떤 스위트가 도는지는 **확인 못 함**(`.github`/buildkite 안 읽음).

---

## 9. WALP 에 옮길 수 있는 것 / 없는 것

붙여 넣은 제안: "Authelia → WALP 접근 정책 엔진: 요청별 접근 허용·거부". 이것을 WALP 의 **격자 세계 에이전트**에
대는 것은 맞지 않는다(요청자도 자원도 없다). 대볼 만한 자리는 **도구 호출 정책** — MCP 서버(`walp/mcp_server.py`),
SE 도구 라우터(`walp/se_router.py`), 실행기(`walp/se_exec.py`), LoopAct 감독기(`walp/loop.py`) — 하나뿐이다.
거기에 한해 본다.

### 9.1 WALP 가 이미 가진 것 (지금의 "정책")

| 문 | 무엇을 보나 | 어디 |
|---|---|---|
| 라우터 거부 | 도구 `kind` 가 `shell/write/network` 면 `DENY` | `DENY_KINDS` `walp/se_router.py:31`, 판정 `:318-319` |
| 실행 직전 재검사 | 미등록 도구 거부, `DENY_KINDS` 는 `allow_write` 없으면 거부 | `walp/se_router.py:337-342` |
| LLM 차단 실행 | 자식 프로세스, LLM 키 환경변수 제거, `nollm/` 대역 | `walp/se_router.py:324-334`, `walp/se_exec.py:77-86` |
| "추측 안 함" | 점수 미달 `REJECT`, 1·2등 근접 `ASK`, 필수 인자 없음 `ASK` | `walp/se_router.py:305-317` |
| 다절 전부-아니면-무 | 절 하나라도 `TOOL` 이 아니면 아무것도 안 부름 | `walp/front.py:240-242` |
| LoopAct 감독기 | 미등록 · 스키마 · 금지 문자열 · 총 예산 · 턴 예산 · 중복 | `walp/loop.py:93-111`, 예산 `Budget(turns=4, tools=30, per_turn=8)` `:67-70` |
| 쓰기 목표 거부 | `required_writes` 에 금지 문자열이 있으면 `REFUSE`, 한 번도 안 부름 | `walp/loop.py:132-146` |
| 레지스트리 해시 | 도구 목록을 해시로 얼림 | `walp/loop.py:114-116`, `walp/MCP.md:46-47` |
| MCP 도구 이름 확인 | 목록 밖 이름은 `-32602` | `walp/mcp_server.py:95-96` |
| 디스코드 쓰기 권한 | 관리 채널이고 (화이트리스트가 비었거나 그 안의 사용자) | `/home/user/SE/discord_bot_server.py:1303-1304`; WALP 가 `teach`·`도구점검 시작` 에 적용 `walp/discord_cmd.py:52-60` |
| 코어의 규칙 타입 | 조건 `RuleCond` 마다 허용 행동 비트마스크 `kRuleAllowed`, 안전 쪽 `kRuleLocked` | `walp/include/walp/interfaces.hpp:100-125` |

마지막 줄은 이름이 비슷해도 **도구 정책이 아니다.** `kRuleAllowed` 는 격자 세계 전술 조건(낡은 관측·배터리 부족 등)에서
학습 후보가 고를 수 있는 행동을 제한하는 타입 검사다 (`interfaces.hpp:97-99`, `:114`). 다만 "우선순위 고정 순서의 조건
목록 + 잠긴 규칙" 이라는 **모양**은 Authelia 의 first-match 목록과 같다 — 그 점만 기억해 둔다.

### 9.2 옮길 수 있는 것

**(a) first-match 규칙 목록 + default_policy 를 "도구 호출 정책" 으로.** 지금 WALP 의 허용 판정은 여러 파일의 `if` 에
흩어져 있다(`se_router.py:318`, `:341`, `loop.py:94-111`, `discord_cmd.py:52-60`). Authelia 모양으로 옮기면:

```
rules:  (위에서부터 첫 일치)
  - tools: [cmd:*]                         policy: deny
  - tools: [walp_teach], via: [mcp]        policy: deny          # 9.3 (가) 참고
  - kinds: [write, shell, network], subjects: [admin]  policy: elevated
  - kinds: [compute, read]                 policy: allow
default_policy: deny
```

조건 축 대응: domain/resource → 도구 이름·`kind`, method → MCP 메서드(`tools/call` 뿐이라 실익 적음),
networks → 호출 경로(`via`: discord/mcp/cli — 이미 `usability` 원장에 찍힌다, `front.py:236`),
subjects → 호출자, query → 인자 조건(예: 경로 인자가 저장소 밖이면 거부). 알고리즘은 `authorizer.go:59-71` 수준이라
Python 30줄이면 된다. **이득은 판정이 한 표에 모이고, `GetRuleMatchResults`(`authorizer.go:75-98`)처럼 "몇 번 규칙이 왜
걸렸나" 를 원장에 남길 수 있다는 것**이다. WALP 는 이미 결정마다 규칙 ID 를 남기는 문화가 있다(`README.md:35`).

**(b) default_policy 를 deny 로.** 지금 WALP 의 실질 기본값은 **허용**이다. `se_tools` 는 이름표(`KIND_BY_NAME`)에 없고
몸통에 `NET_MARKERS` 가 없으면 `kind="compute"` 로 둔다 (`walp/se_tools.py:188-190`, 기본 필드값 `:48`). 즉 새로 생긴
`bot_tools` 도구는 **분류되지 않은 채 실행 허용**으로 들어온다. 게다가 쓰기 탐지용 `WRITE_MARKERS` 가 정의돼 있지만
(`se_tools.py:30`) 파일 안에서 **쓰는 곳이 없다**(grep 결과 정의 줄 하나). 파일을 쓰는 새 도구가 이름표에 빠지면 compute 가
된다. Authelia 식으로 "모르는 것은 deny, 허용은 명시" 로 바꾸는 것이 이 비교에서 나오는 가장 값싼 개선이다.

**(c) 수준(level)의 순서 비교.** `isAuthzResult` 의 `level >= required` (`handler_authz_util.go:102-104`)는 도구에도 맞는다:
`read < compute < write(승격 필요) < deny`. 지금 WALP 는 사실상 두 단계(허용 / DENY_KINDS)에 `allow_write` 불리언
하나다. 수준으로 두면 "관리 채널이면 write 까지" 가 규칙 한 줄이 된다.

**(d) 승격(elevation)의 모양 — 시간 제한 + 출처 고정.** `Elevation{ID, RemoteIP, Expires}` 와 IP 불일치 시 폐기
(`session/types.go:83-87`, `require_auth.go:113-126`). WALP 에 옮기면 "쓰기 허가는 N 분, 그 채널/그 호출자에게만".
지금 `allow_write` 는 호출마다 계산되는 불리언이라 수명 개념이 없다 — 그 자체로는 문제가 아니지만, MCP 쪽에 쓰기 도구를
열게 되면 필요해진다.

**(e) regulation 의 "넘는 순간 차단 행을 쓰고, 검사는 행만 본다".** `regulator.go:57-67`, `:95-106`, `:166-184`.
WALP 에 대면: 같은 호출자가 find_time 안에 `DENY`/`REJECT` 를 max_retries 번 받으면 ban_time 동안 그 호출자의 도구 호출을
전부 거부. 지금 WALP 의 예산은 **한 LoopAct 실행 안**의 것뿐이고(`loop.py:103-110`, `Supervisor.seen` 은 인스턴스 필드
`:91`), 호출자별·시간창 제한은 **없다**(`walp/` 에서 `rate_limit|ban|cooldown` grep 0건, 테스트 제외).
`walp_se_tool` 한 번이 최대 600초 자식 프로세스를 띄운다(`front.py:245`) — 비용 쪽 레이트리밋 근거는 있다.
Authelia 에서 같이 가져올 것: **성공을 만나면 세기를 멈춘다**(`regulator.go:192-193`), **저장소 오류면 닫힌 쪽**
(`handler_firstfactor_password.go:63-67`), **수동 차단과 자동 차단이 같은 표**(`commands/storage.go:287`).

**(f) 익명 + subject 규칙 = "모른다" 로 처리.** Authelia 는 익명이 subject 달린 deny 에 걸리면 403 이 아니라 401 로
신원을 요구한다(`handler_authz_util.go:97-100`). WALP 의 "추측하지 않고 되묻는다"(`ASK`) 원리와 같은 모양이다.
호출자가 불명확하면 거부가 아니라 "누구인지 밝혀라" 로 돌려주는 것 — 다만 9.3 (나) 때문에 MCP 에서는 밝힐 수단이 없다.

### 9.3 깨지는 곳 / 조심할 곳

**(가) 같은 행동, 채널마다 다른 정책 — 이미 어긋나 있다.** 디스코드는 `teach`(사전 변경)를 관리 채널에서만 허용하는데
(`walp/discord_cmd.py:58-60`), MCP 는 `walp_teach` 를 조건 없이 `front.teach` 로 넘긴다(`walp/mcp_server.py:61-62`),
`front.teach` 자체에도 문이 없다(`walp/front.py:152-161`). 사전 충돌 검사는 있지만(`front.py:160`) 권한 검사는 아니다.
이것은 Authelia 식 **중앙 규칙표가 정확히 막는 종류의 결함**이다 — 판정을 입구마다 따로 짜면 입구 하나가 빠진다.
(MCP 서버가 stdio 라 그 프로세스를 띄운 사람만 부를 수 있다는 점에서 위험은 제한적이다. 그래도 정책이 둘인 것은 사실이다.)

**(나) subject 가 없다 — MCP 쪽 신원은 자기 신고다.** `WHO = os.environ.get("WALP_MCP_USER", "mcp")`
(`walp/mcp_server.py:25`). 인증이 없다. Authelia 의 규칙 엔진은 **앞단에서 인증된 subject 가 있다는 전제**에서만 뜻이 있다
(`handler_authz.go:202-215` 에서 authn 이 먼저). WALP 에 subjects 축을 넣어도 MCP 에서는 누구나 이름을 바꿔 적으면 된다.
디스코드 쪽만 플랫폼이 준 `author.id` 로 subject 가 성립한다(`discord_bot_server.py:1303-1304`).
→ **호출자 기준 규칙과 호출자 기준 regulation 은 디스코드에서만 실효가 있다.** MCP 에서는 `via` 기준(입구 기준)
규칙까지만 믿을 수 있다.

**(다) "빈 목록 = 모두 일치" 의 함정이 WALP 에도 이미 있다.** Authelia 는 빈 subjects/networks 를 전부 일치로 본다
(`access_control_rule.go:156-157`, `types.go:21`). SE 의 쓰기 권한도 `not ADMIN_ALLOWED_USER_IDS or ...` 라서
화이트리스트 환경변수가 비면 관리 채널의 **누구나** 쓰기 권한을 얻는다(`discord_bot_server.py:1303-1304`).
Authelia 는 이 의미를 문서화된 규칙 문법으로 두지만, 규칙표를 옮길 때 이 기본 의미를 그대로 가져오면
"설정을 빠뜨리면 열린다" 가 된다. 도구 정책에서는 **빈 subjects = 아무도 아님** 으로 뒤집는 편이 맞다.

**(라) 자원 식별이 문자열 일치로는 약하다.** Authelia 의 자원은 URL 정규식이고 대상이 정규화된 URL 이다
(`handler_authz_impl_forwardauth.go:23`). WALP 의 금지 조건은 인자 값에 대한 **부분 문자열 검사**다
(`walp/loop.py:99-102`, `:133-135`). 경로라면 `../`·심볼릭 링크·절대/상대 차이로 쉽게 빗나간다. `query` 축을 인자 조건으로
옮길 때 문자열 패턴이 아니라 **정규화한 값**(resolve 한 경로가 허용 루트 아래인가)으로 판정해야 한다. Authelia 에서 가져올 수
있는 것은 문법뿐이고 이 정규화는 WALP 가 도구마다 스스로 해야 한다.

**(마) 분류가 코드 추정이다.** Authelia 의 정책은 **자원에 대한 사실**(URL)에 걸린다. WALP 의 `kind` 는 AST·문자열 표지로
**추정**한 값이다(`se_tools.py:188-190`, 이름표 `:56-59`). 규칙이 아무리 정확해도 입력인 `kind` 가 틀리면(9.2(b)의 compute 기본값,
미사용 `WRITE_MARKERS`) 규칙표는 틀린 것을 정확히 집행한다. 규칙 엔진을 들이기 전에 **분류가 먼저**다 — 모르는 도구는
`unknown` 으로 두고 default deny 에 걸리게.

**(바) regulation 을 그대로 옮기면 잘못 센다.** Authelia 는 **1FA 실패만** 차단을 만든다(`regulator.go:59`) — "비밀번호를
틀렸다" 는 적대 신호가 분명한 사건이라서다. WALP 의 `ASK`/`REJECT` 는 대부분 **사람이 말을 모호하게 했다**는 신호이지
공격이 아니다(`se_router.py:305-310`). 이것을 실패로 세면 사용성 원장이 재는 "되묻기 회복"(`README.md:42`,
`mcp_server.py:51`)을 차단이 망친다. 세어야 할 것은 `DENY`(금지 종류를 반복 요청)와 `forbidden:` 일치(`loop.py:102`)뿐이다.
그리고 (나) 때문에 MCP 에서는 호출자별로 셀 수 없다.

**(사) 수준 계산을 "저장" 하지 말 것.** Authelia 는 세션에 수준을 적지 않고 AMR 에서 매번 계산한다
(`user_session.go:29-42`). WALP 도 `allow_write` 를 매 요청 계산한다(`discord_bot_server.py:1303`) — 이미 옳은 쪽이다.
옮기면서 "승격됨" 플래그를 영구 저장하는 쪽으로 바꾸면 오히려 나빠진다.

**(아) 작은 배선 어긋남 하나.** `se_router.execute(..., allow_write=True)` 로 불러도 자식에 넘기는 JSON 은
`{"tool", "args"}` 뿐이라(`walp/se_router.py:347`), `se_exec` 은 `cmd:` 도구에 대해 언제나 `allow_write=False` 로 본다
(`walp/se_exec.py:98`). 닫힌 쪽으로 틀린 것이라 보안 문제는 아니지만, "문이 둘인데 서로 다른 값을 본다" 는 점에서 (가)와
같은 부류다. 지금 `execute` 를 `allow_write=True` 로 부르는 호출자는 찾지 못했다(`grep allow_write`, 테스트 제외).

### 9.4 판정

- **개념 이식은 타당하다 — 규모는 작다.** 실제로 옮길 것은 ① first-match 표 + `default_policy: deny`,
  ② 순서 있는 수준 비교, ③ 한 호출자 단위의 "넘으면 차단 행을 쓴다" 식 regulation. 코드로는 Authelia 에서
  `authorizer.go:55-72` + `handler_authz_util.go:93-108` + `regulator.go:186-214` 세 토막의 **모양**이다. Go 코드를 가져올
  이유는 없다(WALP 쪽은 표준 라이브러리 Python, `se_router.py:1-12`).
- **가장 큰 실익은 규칙 엔진 자체가 아니라 그것이 드러내는 두 결함이다:** MCP `walp_teach` 가 디스코드 정책을 우회하는 것
  (9.3 가), 분류 안 된 도구가 compute 로 허용되는 것(9.2 b / 9.3 마). 둘 다 규칙 엔진 없이 몇 줄로 고칠 수 있고,
  엔진을 들이면 같은 부류가 다시 안 생기게 한다.
- **옮길 수 없는 것:** 인증된 subject 를 전제한 부분 전부 — subjects 축, 호출자별 regulation, 승격의 "출처 고정" —
  은 MCP stdio 에서 신원이 자기 신고(`mcp_server.py:25`)인 한 **디스코드 입구에서만** 성립한다. 세션·쿠키·리다이렉트·
  2FA 는 WALP 에 대응물이 없고 가져올 이유도 없다.
- **과장하지 말 것:** "Authelia 가 WALP 의 접근 정책 엔진이 된다" 는 틀린 말이다. WALP 는 이미 문 여섯 개(9.1 표)가 있고,
  빠진 것은 그것들을 **한 표로 모으는 것과 기본값을 뒤집는 것**이다. 격자 세계 코어(`kRuleAllowed`)와는 관계가 없다.
- 이 보고서의 WALP 쪽 진술은 코드를 읽어서 한 것이고 **돌려서 확인하지 않았다**. 특히 9.3 (가) 의 MCP `walp_teach` 우회는
  JSON-RPC 로 실제로 불러 사전이 바뀌는지 재 보면 한 번에 확정된다.
