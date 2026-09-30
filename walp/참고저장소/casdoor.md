# Casdoor 코드 분석 — 그리고 WALP 에 옮길 수 있는 것 / 없는 것

대상: `casdoor` 얕은 복제 HEAD `9f13005` (2026-09-29). 읽기만 했다. 경로는 저장소 뿌리 기준.
WALP 경로는 `/home/user/SE/walp/` 기준으로 `walp/...` 로 적는다.
읽지 않은 것은 "확인 못 함" 으로 적었다. 줄 번호는 이 HEAD 에서 `sed -n`/`grep -n` 으로 확인한 것이다.

---

## 1. 배치(레이아웃)

- 언어·버전: `go 1.25.0`, `toolchain go1.25.8` (`go.mod:3-5`).
- 웹 프레임워크: Beego v2 (`go.mod:24` `github.com/beego/beego/v2 v2.3.8`). 필터 체인은 `main.go:103-114`
  의 `web.InsertFilter` 열두 줄 — 그중 권한 검사는 `routers.ApiFilter` (`main.go:110`).
- 저장소: xorm (`go.mod:81-83` `xorm-io/xorm v1.1.6`), Casbin 정책은 `casdoor/xorm-adapter/v3`
  (`go.mod:34`). 기본 설정은 MySQL `driverName = mysql`, `dataSourceName = root:123456@tcp(localhost:3306)/`
  (`conf/app.conf:5-6`).
- 최상위 디렉터리(실측 `ls`): `controllers/` (HTTP 핸들러), `object/` (도메인·DB 로직, 가장 큼),
  `routers/` (라우트와 필터), `authz/` (API 권한용 Casbin enforcer, `authz/authz.go` 287줄), `idp/` (외부
  IdP 30개 파일), `cred/` (비밀번호 해시), `captcha/` (aliyun·default·geetest·hcaptcha·recaptcha·turnstile),
  `ldap/` (LDAP **서버**), `radius/`, `scim/`, `mcpself/` (Casdoor 자체 MCP 서버), `mcp/`, `faceId/`,
  `idv/`, `sync/`, `sync_v2/`, `web/` (현 프론트), `web-old/` (이전 프론트).
- 프론트: `web/package.json:59` `"react": "19.3.0"`, `:88` `"vite": "^5.4.11"`. `web-old/` 는
  `craco.config.js` 가 있는 CRA 계열(파일 목록만 봄, 내용 확인 못 함).
- 세션: Beego 세션, 이름 `casdoor_session_id`, 기본 공급자 `file`(`./tmp`), Redis 설정 시 redis
  (`main.go:24-30`). 쿠키 수명 기본 30일 `3600 * 24 * 30` (`main.go:32-37`).

## 2. 로그인 흐름 — `controllers/auth.go` `Login()` (`controllers/auth.go:692`)

요청 본문을 `form.AuthForm` 으로 풀고(`:695-700`) 아래 갈래로 나뉜다.

| 갈래 | 조건 | 위치 |
|---|---|---|
| Magic link | `SigninMethod == "Magic link"` 이고 MFA 세션이 없을 때 | `:706`, `:709-715` |
| Face ID | `SigninMethod == "Face ID"` | `:716-752` (`CheckFaceIdWithLimit` `:748`) |
| 인증 코드(이메일/SMS) | `Username != ""` 이고 `Password == ""` | `:753-850` |
| 비밀번호 / LDAP | 비밀번호가 있을 때 | `:851-921` |
| 외부 IdP | `Username == ""` 이고 `Provider != ""` | `:958-` (`GetIdProvider` `:1022`, `GetToken` `:1046`, `GetUserInfo` `:1057`) |
| MFA 2단계 | `getMfaUserSession() != ""` | `:1356-` (passcode `:1391`, recovery `:1432`) |

- 코드 로그인: 이메일/SMS 가 애플리케이션에서 켜져 있는지 `IsCodeSigninViaEmailEnabled` /
  `IsCodeSigninViaSmsEnabled` 로 먼저 본다(`:785-792`). 코드 검사는 `object.CheckSigninCode` (`:812`),
  성공 뒤 `DisableVerificationCode` 로 1회용화(`:819`).
- 비밀번호 갈래의 주석이 중요하다: "signinMethod 가 무엇이라 주장하든 비밀번호가 있으면 비밀번호로
  인증되므로, `IsPasswordEnabled` 검사를 `signinMethod == "Password"` 로만 한정하면 우회된다"
  (`:850-853`). 그래서 LDAP 가 아니면 무조건 `IsPasswordEnabled` 를 본다(`:854-862`).
- 비밀번호 난독화(전송 중): 조직의 `PasswordObfuscatorType` 이 `Plain`/빈값이면 그대로, `DES`/`AES`
  면 복호(`controllers/auth.go:893`, `util/obfuscator.go:53-57`). 이것은 해시가 아니라 전송 난독화다.
- 실제 비밀번호 검사 진입점: `object.CheckUserPassword` (`controllers/auth.go:920` →
  `object/check.go:393`). 여기서 삭제/금지/`guest-user` 태그를 거르고(`object/check.go:407-418`),
  `user.Ldap != ""` 면 `CheckLdapUserPassword` (`:432`), 아니면 `CheckPassword` (`:441`) 후
  `checkPasswordExpired` (`:446`).
- WebAuthn 은 `Login()` 안이 아니라 별도 핸들러다: `WebAuthnSigninBegin` / `WebAuthnSigninFinish`
  (`controllers/webauthn.go:123`, `:174`), 성공 시 같은 `HandleLoggedIn` 으로 합류(`:241`).
- 애플리케이션별 로그인 방식 설정: `Application.SigninMethods []*SigninMethod` (`object/application.go:139`),
  `HasSigninMethod(name)` (`:201`). 예: 기기 로그인은 `HasSigninMethod("Device login")` 로 막는다
  (`controllers/auth.go:1760`).
- 로그인 후 애플리케이션 단위 검사 `CheckApplicationSignin` (`object/check.go:642`): 금지·삭제,
  IP 화이트리스트(`CheckEntryIp` `:651`), 앱/조직의 `DisableSignin`, `CheckLoginPermission` (`:669`),
  태그 제한(`:677`).

### 2-1. 비밀번호 해시 옵션 — `cred/`

인터페이스는 두 메서드 `GetHashedPassword` / `IsPasswordCorrect` (`cred/manager.go:17-20`).
`GetCredManager` 가 문자열로 고른다(`cred/manager.go:22-41`):

| `passwordType` | 구현 | 비고 |
|---|---|---|
| `plain` | `cred/plain.go:24-30` | **평문 저장**. `GetHashedPassword` 가 비밀번호를 그대로 돌려준다 |
| `salt` | SHA-256 두 번 + 솔트 (`cred/sha256-salt.go:40-46`) | 빠른 해시. 무차별 대입에 약하다 |
| `sha512-salt` | `cred/sha512-salt.go` (내용 확인 못 함) | 이름으로 보아 빠른 해시 |
| `md5-salt` | MD5(MD5(pw)+salt) (`cred/md5-user-salt.go:40-46`) | **MD5**. 솔트가 비면 MD5 한 번 (`:41-43`) |
| `bcrypt` | `bcrypt.DefaultCost` (`cred/bcrypt.go:13`) | |
| `pbkdf2-salt` | PBKDF2-SHA256 **27500회** (`cred/pbkdf2-salt.go:34`) | Keycloak 호환 주석(`:32`) |
| `argon2id` | `argon2id.DefaultParams` (`cred/argon2id.go:27`) | `alexedwards/argon2id` (`go.mod:12`) |
| `pbkdf2-django` | PBKDF2-SHA256 260000회 (`cred/pbkdf2_django.go:36`) | Django 호환 |

- 기본값: 내장 조직 `built-in` 은 `PasswordType: "bcrypt"` (`object/init.go:135`). 사용자별
  `PasswordType` 이 비면 조직 것을 쓴다(`object/check.go:285-288`).
- 조직 해시 방식이 바뀌면 로그인 성공 시 새 방식으로 재해시한다(`object/check.go:304-313`,
  `isOutdated`). 이전 해시(MD5 등)를 가져온 사용자를 옮기는 경로다.
- 비교가 상수 시간이 아니다: `plain` `hashedPwd == plainPwd` (`cred/plain.go:29`),
  `pbkdf2-salt` `hashedPwd == cm.GetHashedPassword(...)` (`cred/pbkdf2-salt.go:39`),
  sha256 `==` (`cred/sha256-salt.go:51,56`). 코드 전체에서 `ConstantTimeCompare` 를 쓰는 곳은 비테스트
  파일 3개뿐(`object/verification_ip.go`, `pp/gc.go`, `idp/wechat.go` — grep 결과).
- `IsPasswordCorrect` 는 조직 솔트와 사용자 솔트로 **두 번** 시도한다(`object/check.go:300`).

## 3. 무차별 대입 / 위험 통제

- 기본값: `DefaultFailedSigninLimit = 5`, `DefaultFailedSigninFrozenTime = 15`(분)
  (`object/check.go:33-34`). 애플리케이션의 `FailedSigninLimit` / `FailedSigninFrozenTime` 이 0 이면
  기본값을 쓴다(`object/check_util.go:78-86`).
- 카운터는 사용자 행에 있다: `SigninWrongTimes`, `LastSigninWrongTime` (`object/user.go:245-246`).
  실패 시 `recordSigninErrorInfo` 가 올리고, 한도에 처음 닿을 때만 시각을 찍는다
  (`object/check_util.go:103-108`). 잠금 판정은 `checkSigninErrorTimes` (`object/check.go:232-256`) —
  남은 분이 양수면 `SigninReasonAccountFrozen`, 지나면 카운터를 0 으로 되돌린다(`:249`).
- **캡차가 켜지면 잠금 검사를 건너뛴다**: `if !enableCaptcha { checkSigninErrorTimes(...) }`
  (`object/check.go:269-274`). 즉 캡차가 잠금을 대신한다. 설계 선택이지만, 캡차 공급자가 약하면(예:
  `captcha/default.go` 의 자체 이미지 캡차 — 내용 확인 못 함) 잠금이 사라진다는 뜻이다.
- 캡차 규칙: 애플리케이션의 Captcha 공급자 항목 `Rule` 이 `Internet-Only`(외부 IP 일 때),
  `Dynamic`(실패 횟수가 한도 이상일 때), `Always` (`object/check.go:964-1002`).
- 잠금은 **사용자 단위**다. IP 단위 로그인 속도 제한은 찾지 못했다. 찾은 IP 속도 제한은
  `/api/send-email` 용 `sendEmailLimiters` (`controllers/service_ip_limit.go:25-39`) 뿐이다(전수 검색은
  안 했다 — `object/verification_ip.go` 는 파일 이름만 봄, 확인 못 함).
- MFA 코드 실패도 따로 센다: `VerifyMfaWithLimit` (`object/mfa.go:76-92`, 키 `mfaErrorKey = "mfa"` `:74`).
  Face ID 도 `CheckFaceIdWithLimit` (`controllers/auth.go:748`).
- IP 화이트리스트: 사용자·애플리케이션·조직 세 층 `IpWhitelist` (`object/check_ip.go:26-57`,
  `object/user.go:253`).
- 사용자 열거: "사용자가 없다" (`object/check.go:407-409`)와 "비밀번호가 틀렸다, 남은 기회 N"
  (`object/check_util.go:116-121`)이 **다른 메시지**다. 존재 여부가 응답으로 드러난다.

## 4. MFA 와 WebAuthn

- MFA 종류 상수: `email`, `sms`, `app`(TOTP), `radius`, `push` (`object/mfa.go:41-46`).
- TOTP 라이브러리: `github.com/pquerna/otp` (`go.mod:65`, `object/mfa_totp.go:21-22`), 생성
  `totp.Generate` (`:42`), 검증 `totp.ValidateCustom` (`:62`, `:101`).
- 강제: 조직(또는 사용자)의 `MfaItems` 중 `Rule == "Required"` 인 항목이 미설정이면
  `IsNeedPromptMfa` 가 참(`object/organization.go:742-766`) → 로그인 응답이 `RequiredMfa`
  (`controllers/auth.go:523-533`). 이미 켜진 사용자는 `NextMfa` 로 2단계를 요구(`:563-583`).
  1단계에서 쓴 수단(예: SMS 코드 로그인)은 2단계 후보에서 뺀다(`:569-571`).
- 기억(remember): `organization.MfaRememberInHours * 3600` 초 쿠키(`controllers/auth.go:1415-1425`).
  쿠키 값은 `base64(userId|deadline).HMAC` (`object/mfa_remember.go:44-52`), HMAC 키는
  **`cert-built-in` 의 개인키**에서 파생(`object/mfa_remember.go:29-40`). 쿠키는 `HttpOnly`, https
  에서만 `Secure`, `Lax` (`controllers/auth.go:519`).
- 복구 코드: `User.RecoveryCodes []string` 가 `mediumtext` 로 사용자 행에 저장
  (`object/user.go:217`). `MfaRecover` 는 `code == recoveryCode` 평문 비교 후 사용한 것을 지운다
  (`object/mfa.go:94-117`). 해시되지 않은 채 저장되는 것으로 읽힌다(저장 전에 해시하는 코드는 찾지 못함).
  `TotpSecret` 도 `varchar(100)` 평문 칸(`object/user.go:218`).
- WebAuthn: `github.com/go-webauthn/webauthn v0.10.2` (`go.mod:48`). 설정 `webauthn.New`
  (`object/user_webauthn.go:38`), 자격은 사용자 행 `WebauthnCredentials ... blob` (`object/user.go:215`).
  WebAuthn 로그인에도 MFA 검사 훅이 있다 `checkWebAuthnSigninMfa` (`controllers/webauthn.go:253`).

## 5. 프로토콜

- **OAuth2 / OIDC**: 토큰 엔드포인트 분기 `object/token_oauth.go:85-101` —
  `authorization_code`, `password`(ROPC), `client_credentials`, `token`/`id_token`(implicit),
  JWT bearer, device_code, token-exchange(RFC 8693), `refresh_token`. 애플리케이션이 허용한
  `GrantTypes` 밖이면 `UnsupportedGrantType` (`:75-77`).
  디스커버리 `/.well-known/openid-configuration` (`routers/router.go:399`), 광고 값: implicit·password
  포함 grant 목록(`object/wellknown_oidc_discovery.go:168`), 서명 `RS256 RS512 ES256 ES384 ES512`
  (`:170`), PKCE `S256` 만(`:172`). JWKS `/.well-known/jwks` (`routers/router.go:403`), 인트로스펙션
  `/api/login/oauth/introspect` (`routers/router.go:374`). DPoP 파일 `object/token_dpop.go` 존재
  (내용 확인 못 함).
- **RFC 9728 보호 자원 메타데이터**: `/.well-known/oauth-protected-resource`
  (`controllers/wellknown_oauth_prm.go:21-45`, `routers/router.go:408-409`).
  **동적 클라이언트 등록(DCR)**: `controllers/oauth_dcr.go:51` `DynamicClientRegister`, 그리고 API 정책에
  `p, app-dcr, ...` 줄(`authz/authz.go:42-45`). 이 둘은 MCP 원격 인증 흐름이 쓰는 부품이다(10절).
- **SAML**: `russellhaering/gosaml2 v0.11.0`, `goxmldsig v1.6.0` (`go.mod:72-73`); IdP·SP·SLO 파일
  `object/saml_idp.go`, `saml_sp.go`, `saml_slo.go` (파일 목록만, 내용 확인 못 함).
- **CAS**: `/cas/:organization/:application/serviceValidate`, `proxyValidate`, `p3/...`, `samlValidate`
  (`routers/router.go:411-418`).
- **LDAP 서버**: `casdoor/ldapserver` 사용(`ldap/server.go:26`), `0.0.0.0:<port>` 로 LDAP/LDAPS 대기
  (`ldap/server.go:47`, `:69`), Bind·Search 핸들러(`:108`, `:164`).
- **외부 IdP (`idp/`)**: 인터페이스는 세 메서드
  ```go
  type IdProvider interface {
      SetHttpClient(client *http.Client)
      GetToken(code string) (*oauth2.Token, error)
      GetUserInfo(token *oauth2.Token) (*UserInfo, error)
  }
  ```
  (`idp/provider.go:63-67`). 공통 결과형 `UserInfo` (`:25-39`, `EmailVerified` 는 "공급자가 보증할 때만
  참" 주석 `:31-32`). 팩토리 `GetIdProvider` 는 손으로 쓴 `switch` — `case` 26개(`idp/provider.go:69-151`,
  grep 계수), `default` 에서 `isGothSupport` 면 `markbates/goth` 로 위임(`:143-145`, `goth.go` 안
  `case` 58개), `Custom*` 이면 `NewCustomIdProvider` (`:146-148`), 아니면 오류(`:149`). 즉 직접 구현
  ~26종 + goth 경유 ~58종 + 사용자 정의. (goth 목록 58은 `idp/goth.go:95-420` 의 `case "` 계수이며,
  중복·별칭 여부는 확인 못 함.)

## 6. 권한 — Casbin

두 층이 있다.

**(가) API 라우트 권한** — 모든 요청이 `routers.ApiFilter` (`routers/authz_filter.go:512`) 를 지난다.
1. 주체: 세션/토큰에서 사용자명, 없거나 교차 출처 쿠키면 `anonymous/anonymous` (`:194-206`).
2. 객체: 요청에서 `owner/name` 을 뽑는다(`getObjects` `:365`).
3. `authz.IsAllowed` (`authz/authz.go:158`): `/api/mcp` 의 `initialize`·`ping`·`tools/list` 는 무조건
   허용(`:159-165`) → `app` 주체는 자기 조직의 관리자로 취급(`:173-187`) → 전역 관리자(`Owner ==
   "built-in"`, `object/user.go:1619-1625`)는 전부 허용(`authz/authz.go:199-201`) → 조직 관리자는 자기
   조직 객체 허용(`:203-205`) → Casbin `Enforcer.Enforce(subOwner, subName, method, urlPath, objOwner,
   objName)` (`:211`) → 거부면 DB 의 `Permission`(ResourceType `API`)으로 한 번 더(`:216-219`,
   `object/check.go:504-`).
4. 모델: 6-튜플 요청 `r = subOwner, subName, method, urlPath, objOwner, objName`, `keyMatch2` 경로
   매칭, `!anonymous` 와일드카드, 그리고 "주체 == 객체면 허용" 절(`object/init.go:438-457`).
5. 정책: **코드 안 문자열**이다(`authz/authz.go:41-` `p, *, *, POST, /api/login, *, *` 등). 기동마다
   `ClearPolicy()` 후 문자열을 싣고 DB 에 `SavePolicy()` (`authz/authz.go:36-40`, `:140-155`).
   `if true {` 로 조건을 무력화한 흔적이 있다(`:38-39`). 첫 줄 `p, built-in, *, *, *, *, *` (`:41`)는
   built-in 조직 사용자에게 전부를 연다 — 위의 `IsGlobalAdmin` 과 같은 뜻.

**(나) 사용자 앱을 위한 권한 서비스** — `Permission` 마다 enforcer 를 만든다
(`object/permission_enforcer.go:30` `getPermissionEnforcer`, 모델 `:96`, 어댑터 `:74`), 역할·그룹을
재귀로 펼친다(`:195`, `:266`). 외부에서 `/api/enforce`, `/api/batch-enforce` 로 부른다
(`controllers/casbin_api.go:36`, `:184`).

**(다) MCP 도구 권한** — `mcpself/` 의 자체 MCP 서버는 Casbin 이 아니라 **scope → 도구 목록 표**로 한다.
`BuiltinScopes` (`mcpself/permission.go:22-`: `application:read` → `get_applications`,
`get_application` 등), 편의 scope `read`/`write`/`admin` 펼침(`:110-114`), `GetToolsForScopes`
(`:119-145`). `tools/list` 는 scope 로 걸러 보여 주고(`mcpself/base.go:294-322`), `tools/call` 은
`checkToolPermission` 으로 막는다(`mcpself/base.go:416-439`). 주의: 토큰 없이 **대화형 세션이면 전체
허용**(`:419-422`), scope 없는 요청은 `tools/list` 에서 **전체 목록을 본다**(`:298-305`, "discovery").
주석은 레지스트리가 `Application.Scopes` 일 수도 있다고 하지만(`permission.go:117-118`) 확인한 호출처
세 곳은 모두 `BuiltinScopes` 를 넘긴다(`base.go:307`, `:430`, `:444`).
또 `/api/server/:owner/:name` 은 **MCP 프록시**다(`controllers/mcp_server.go:39-135`): 요청 JSON 의
중복·대소문자 변형 키를 거부(`:48-51`, `:137-`), `tools/call` 이면 서버별 도구 허용목록 `IsAllowed`
로 거르고(`:80-102`), 호출자의 쿠키·Authorization 을 떼고 서버 토큰으로 바꿔 전달(`:117-128`).

## 7. 세션·토큰·로그아웃

- 세션: Beego 세션(1절) + DB `Session` 행(`object/session.go`). 요청마다 마지막 활동 시각을 비동기로
  갱신(`routers/authz_filter.go:522-532`). 동시 세션 한도 `EnforceBrowserSessionLimit` /
  `EnforceApplicationSessionLimit` (`object/session.go:384`, `:436`).
- 로그아웃 `Logout` (`controllers/account.go:392`): OIDC RP-initiated(`id_token_hint`,
  `post_logout_redirect_uri` `:394-395`), 백채널 로그아웃 `SendBackchannelLogout` (`:439`,
  `object/token_logout.go:91`), SAML SLO `SendSamlLogout` (`:440`), 세션 지우기(`:442-448`), 외부 Custom
  OAuth 공급자에게도 전파(`:451`). 세션 삭제 시 그 세션 id 로 발급된 토큰을 만료
  `ExpireTokensBySessionIds` (`object/session.go:354-356`). SSO 로그아웃 `SsoLogout` (`account.go:568`).
- 토큰 폐기: 관리용 `/api/delete-token` (`routers/router.go:255`), 동의 철회 `/api/revoke-consent`
  (`:398`). RFC 7009 형식의 `revoke` 엔드포인트는 `routers/router.go` 에서 grep 으로 찾지 못했다
  (디스커버리 문서에 `revocation_endpoint` 가 있는지도 grep 결과 없음).

## 8. 보안 태세 관찰(읽은 것만)

1. **내장 관리자 `built-in/admin` / 비밀번호 `"123"`** (`object/init.go:160-199`, `:175`). DB 가 비어
   처음 기동할 때만 만든다(`object/init.go:29-36`). 저장 시에는 조직 방식(bcrypt)으로 해시된다
   (`object/user.go:1081-1083`). 조직 `DefaultPassword` 가 있으면 `"123"` 을 그것으로 바꾼다
   (`:1077-1079`). 기동 시 이 비밀번호를 바꾸라는 경고는 찾지 못했다. e2e 시험도 `"123"` 을 쓴다
   (`web/cypress/support/commands.js:10`).
2. **내장 LDAP 설정에도 `Password: "123"`** (`object/init.go:332`) — 호스트는 `example.com` 자리표시자.
3. **공개된 JWT 개인키**: `object/token_jwt_key.key` 가 저장소에 있다. 새 설치는 새 키쌍을 만들고
   (`object/init.go:282-284` 주석), 기존 DB 의 인증서가 그 공개 키와 같으면 기동 시 경고를 찍는다
   (`object/init.go:303-313`). 경고만 하고 멈추지 않는다. MFA 기억 HMAC 키도 이 인증서에서 파생되므로
   (`object/mfa_remember.go:30-38`) 같은 영향 범위다.
4. **약한 해시 옵션이 선택지로 남아 있다**: `plain`(평문), `md5-salt`, `salt`(SHA-256), `sha512-salt`
   (`cred/manager.go:23-30`). 기본은 bcrypt 다(`object/init.go:135`). 이전 시스템 이관용이라는
   것이 합리적 해석이지만 UI 에서 `plain` 을 고를 수 있는지는 확인 못 함.
5. **조직 마스터 비밀번호**: 조직의 `MasterPassword` 가 있으면 어떤 사용자로든 로그인된다; 평문
   `password == organization.MasterPassword` 비교도 함께 한다(`object/check.go:293-297`). 설계상
   기능이지만 저장 형태가 평문일 수 있다는 뜻이다.
6. 상수 시간이 아닌 비교: 클라이언트 시크릿 `application.ClientSecret != clientSecret`
   (`routers/base.go:145`), 해시 비교들(2-1절), 복구 코드(`object/mfa.go:102`). 네트워크 너머
   타이밍 공격의 실효성은 별개 문제이고 여기서 재지 않았다.
7. 복구 코드·TOTP 비밀 평문 칸(4절).
8. 로그인 응답으로 사용자 존재 여부 노출(3절).
9. OIDC 디스커버리가 implicit 과 password grant 를 광고(`object/wellknown_oidc_discovery.go:168`).
   실제 허용은 애플리케이션 `GrantTypes` 가 정한다(`object/token_oauth.go:75`).
10. 좋은 쪽: 비밀번호 로그인 우회를 막는 주석과 검사(`controllers/auth.go:850-862`), MCP 프록시의 중복
    키 거부와 자격 전달 차단(`controllers/mcp_server.go:48-51`, `:117-128`), 외부 IdP 가 이메일 검증을
    보증할 때만 `EmailVerified` (`idp/provider.go:31-32`).

## 9. 시험

- Go `*_test.go` 33개(웹 제외, `find` 계수). 분포: `object/` 14, `util/` 3, 그 외 2개 이하씩
  (`sync_v2`, `i18n`, `faceId`, `certificate`, `xlsx`, `sync`, `rule`, `radius`, `ldap`, `deployment`,
  `cred`, `conf`).
- 그중 14개 파일에 `skipCi` 표지가 있다(grep). 이름으로 보아 DB·외부 자원이 필요한 것들이다
  (예: `object/transaction_test.go`, `radius/server_test.go`). 표지의 정확한 의미(빌드 태그인지)는
  열어 보지 않았다 — 확인 못 함.
- **`controllers/`, `routers/`, `authz/`, `idp/` 에는 Go 시험 파일이 하나도 없다**(분포에 없음).
  `cred/` 는 `sha256-salt_test.go` 하나뿐. 즉 로그인 분기·잠금·API 권한 필터는 단위 시험이 없다.
- MFA: `object/mfa_totp_test.go`, `object/mfa_radius_test.go`. 권한: `object/permission_enforcer_test.go`.
- 프론트 e2e: Cypress 19개(`web/cypress/e2e/*.cy.js`, `login.cy.js` 포함).

---

## 10. WALP 에 옮길 수 있는 것 / 없는 것

붙여 온 제안: "Casdoor → WALP 외부 인증 서비스 연결: OAuth/OIDC 등 프로토콜 연동".

### 10-1. WALP 가 지금 무엇인가 (읽은 것)

- 과업: 격자 세계에서 색·브랜드로 물체 찾기, C++17, 신경망 가중치 없음(`walp/README.md:3-5`).
- MCP 서버 **전송은 stdio** 다: 문서 첫 줄 "WALP MCP 서버(stdio, JSON-RPC 2.0)" (`walp/mcp_server.py:1`),
  본체는 `for line in sys.stdin:` (`walp/mcp_server.py:110`). HTTP 소켓을 여는 코드가 없다.
  `MCP.md` 도 "MCP (stdio JSON-RPC)" (`walp/MCP.md:57`).
- 호출자 신원: MCP 쪽은 **환경 변수 상수 하나** `WHO = os.environ.get("WALP_MCP_USER", "mcp")`
  (`walp/mcp_server.py:25`) — 모든 MCP 호출이 같은 사람이다. 디스코드 쪽은 봇 컨텍스트의
  `current_author` (`walp/discord_cmd.py:26-31`).
- 사용성 원장의 호출자 해시: `sha256("walp:" + id)[:12]` (`walp/usability.py:48-50`), 솔트 없음.
- 도구 라우터: 낱말 사전 점수 → `REJECT`/`ASK`/`DENY`/`TOOL` (`walp/se_router.py:279-320`),
  종류 거부 `DENY_KINDS = {"shell", "write", "network"}` (`walp/se_router.py:32`, `:318-319`),
  실행은 LLM 키를 뺀 자식 프로세스(`walp/se_router.py:327-361`).
- 호출 문(Supervisor): 등록 여부·스키마·금지 문자열·예산·중복 (`walp/loop.py:86-110`).
- 쓰기 권한: 디스코드 `가르치기`(사전 변경)는 `allow_write` 가 거짓이면 막힌다
  (`walp/discord_cmd.py:58-60`, 도구점검 시작도 `:51-53`).

### 10-2. OAuth/OIDC 가 필요한가 — **지금은 아니다**

1. **stdio MCP 에는 OAuth 가 들어갈 자리가 없다.** 호출자는 이 프로세스를 띄운 로컬 사용자이고,
   신뢰 경계는 OS 프로세스 경계다. MCP 명세(2025-06-18)의 Authorization 절은 HTTP 기반 전송에
   적용되고 stdio 구현은 자격을 환경에서 가져오라고 적었다고 알고 있다 — **이 세션에서 명세 원문을
   읽지 않았다 [출처:기억, 확인 못 함]**. 방증은 Casdoor 자신에게 있다: Casdoor 가 MCP 인증을 위해
   만든 부품(RFC 9728 보호 자원 메타데이터 `controllers/wellknown_oauth_prm.go:21-45`, DCR
   `controllers/oauth_dcr.go:51`, Bearer 추출 `mcpself/auth.go:132-146`)은 전부 **HTTP 엔드포인트**
   `/api/mcp` (`routers/router.go:422`) 를 위한 것이다. stdio 엔 붙일 헤더가 없다.
2. 디스코드 앞단은 디스코드가 이미 인증한다. WALP 가 받는 것은 봇이 넘긴 작성자 id 다
   (`walp/discord_cmd.py:29`). 여기에 OIDC 를 더하면 사용자는 디스코드 로그인 + Casdoor 로그인을 두 번
   하게 된다. 사용성을 재는 시스템(`walp/README.md:23`)에 마찰을 넣는 쪽이다.
3. Casdoor 를 붙이는 비용: Go 서버 + MySQL(`conf/app.conf:5-6`) + 내장 관리자 비밀번호 교체(8절 1) +
   공개 키 교체(8절 3). WALP 는 "표준 라이브러리만" 을 두 번 적는다(`walp/mcp_server.py:4`,
   `walp/se_router.py:1`). 인증 서버 하나를 운영 대상에 추가하는 것은 이 원칙과 정면으로 부딪친다.
4. **필요해지는 조건**(이것을 못 적으면 주장이 아니다): WALP MCP 서버를 **HTTP(Streamable HTTP)로
   네트워크에 노출**하고, **서로 다른 사람**이 붙고, 도구마다 **다른 권한**을 줘야 할 때. 그때는
   Casdoor 가 아니어도 되는 표준 부품(Authorization Server + RFC 9728 메타데이터 + Bearer 검증)이 필요하고,
   Casdoor 는 그 AS 후보 중 하나일 뿐이다. 지금 코드에는 이 셋 중 어느 것도 없다.

### 10-3. 옮길 만한 것 (작고 구체적인 것만)

1. **scope → 도구 표** (`mcpself/permission.go:22-145`). WALP 의 `DENY_KINDS` (`walp/se_router.py:32`)
   는 주체가 없는 1차원 규칙이다 — 누가 부르든 `write` 는 막힌다. 그런데 디스코드 쪽에는 이미
   "관리 채널이면 쓰기 허용" 이라는 **두 번째 축**이 `allow_write` 로 흩어져 있다
   (`walp/discord_cmd.py:51-60`). Casdoor 처럼 `호출자 부류 → 허용 도구 종류` 표 한 장으로 모으면
   (예: `mcp-local: read+sim`, `discord-public: read+sim`, `discord-admin: +teach`) 두 앞단의 규칙이 한
   곳에 적힌다. Casbin 라이브러리는 필요 없다 — Casdoor 도 MCP 도구에는 Casbin 이 아니라 딕셔너리를 썼다.
2. **목록도 권한으로 거른다** (`mcpself/base.go:307-321`). WALP `tools/list` 는 9개를 늘 다 돌려준다
   (`walp/mcp_server.py:90-91`). 호출할 수 없는 도구는 보이지 않게 하면 LLM 클라이언트가 헛호출을 덜
   한다. 단, Casdoor 는 scope 없는 요청에 **전부** 보여 준다(`base.go:298-305`) — 이 부분은 따라 하지
   말 것.
3. **중복·대소문자 변형 키 거부** (`controllers/mcp_server.go:48-51`, `:137-`). WALP 는 `json.loads`
   (`walp/mcp_server.py:115`)가 중복 키를 조용히 마지막 값으로 덮는다. 감독기(`walp/loop.py:97`)가
   `set(args)` 로 인자 이름을 보므로 이름 검사는 되지만, 같은 이름 두 번은 못 본다. `object_pairs_hook`
   한 줄로 막을 수 있다. 싸고 결정론적이다.
4. **이상 사유를 원장에 남긴다** — Casdoor 는 로그인 실패 사유를 `recordDetail` 로 따로 남긴다
   (`controllers/auth.go:924-927`, `:1410`). WALP 원장도 `reason` 칸을 이미 쓴다
   (`walp/front.py:236-239`). 새로 배울 것은 없다; 이미 같은 모양이다.

### 10-4. 옮길 수 없거나 비유가 깨지는 것

1. **`IdProvider` 추상화 → WALP 도구 라우팅**: 비유가 약하다. `IdProvider` 는 "코드 → 토큰 →
   `UserInfo`" 라는 **고정된 3단계**를 공급자마다 구현하는 어댑터다(`idp/provider.go:63-67`). WALP
   라우팅의 어려운 부분은 어댑터가 아니라 **어느 도구를 고를지**(점수·여백·되묻기,
   `walp/se_router.py:299-313`)다. Casdoor 에서 "고르기" 는 요청에 `Provider` 이름이 **명시**돼 오므로
   (`controllers/auth.go:987`) 문제 자체가 없다. 배울 수 있는 것은 "결과를 공통형으로 정규화" 하나인데,
   WALP 는 이미 도구 결과를 증거로 격하하는 설계를 적어 두었다(`walp/MCP.md:86`). 게다가 Casdoor
   팩토리는 손으로 쓴 `switch` 26갈래 + 폴백 두 개(`idp/provider.go:69-151`)이고, WALP 목록은 AST 로
   자동 추출한다(`walp/README.md:40`). 이 점에서는 WALP 쪽이 덜 손이 간다.
2. **Casbin 모델 → WALP 정책 그래프**: 다른 물건이다. Casbin 은 `(주체, 객체, 행동)` 에 대한
   **허가 판정**(`authz/authz.go:211`)이고, WALP 정책 그래프는 `조건 → 행동` 의 **행동 선택**
   (`walp/MCP.md:65`)이다. WALP 에서 Casbin 과 같은 층은 Execution Supervisor 의 "도구 호출 문"
   (`walp/MCP.md:71`, `walp/loop.py:86-110`) 하나뿐이고, 그 크기(도구 9개, 주체 둘 셋)에 Casbin 의
   RBAC·도메인·keyMatch 는 과하다. 또 Casdoor 정책이 기동마다 코드 문자열에서 덮어써지는
   구조(`authz/authz.go:36-40`, `if true {`)는 "정책 버전·해시를 남긴다" 는 WALP 원칙
   (`walp/MCP.md:91-92`)과 반대 방향이다.
3. **OIDC 신원을 사용성 원장의 호출자로**: 오히려 해가 될 수 있다. 원장은 "디스코드 ID 를 그대로
   적지 않는다 — 세션을 가를 만큼만" (`walp/usability.py:49`)이 목적이다. OIDC `sub`·이메일을 받으면
   식별력이 올라가는데 원장은 그것이 필요 없다. 참고로 지금 해시는 솔트 없는 `sha256("walp:"+id)`
   를 12자로 자른 것이라(`walp/usability.py:50`) 디스코드 사용자 id 를 아는 사람은 대조할 수 있다.
   도움말은 "익명(해시)" 이라고 적는다(`walp/discord_cmd.py:23`). **가명**이 정확한 말이다. 이것은
   Casdoor 와 무관하게 고칠 수 있는 것이다(비밀 솔트 한 개).

### 10-5. 지금 코드에서 실제로 보이는 권한 틈 (Casdoor 를 읽다 보인 것)

- 디스코드에서 사전을 바꾸는 `가르치기` 는 관리 채널에서만 된다(`walp/discord_cmd.py:58-60`).
  **MCP 의 `walp_teach` 는 아무 검사 없이 `front.teach` 를 부른다** (`walp/mcp_server.py:61-62`), 이
  함수는 `walp_cli learn` 을 거쳐 learned 파일에 덧붙인다(`walp/tools/walp_cli.cpp:272-295`).
  stdio 라서 호출자가 곧 로컬 사용자이므로 지금은 **취약점이 아니라 두 앞단 규칙의 불일치**다.
  HTTP 로 여는 순간 취약점이 된다. 10-3 의 1번 표가 이 불일치를 한 곳에서 드러낸다.
- 문서 불일치: `walp/MCP.md:98` 는 도구 7개라고 적고, `walp/mcp_server.py:3` 는 아홉 개, 실제
  `TOOLS` 는 9개(`walp/mcp_server.py:27-53`).

### 10-6. 한 줄 결론

제안된 "Casdoor OAuth/OIDC 연동" 은 **지금 WALP 에 풀 문제가 없다** — MCP 는 stdio(`walp/mcp_server.py:1,110`),
사람 쪽은 디스코드가 이미 인증한다. 가져올 것은 인증이 아니라 **권한 표 한 장**(Casdoor
`mcpself/permission.go` 의 scope → 도구 모양)과 **중복 키 거부** 정도이고, 둘 다 표준 라이브러리로 된다.
OAuth 는 "HTTP 로 노출 + 여러 사람 + 도구별 권한" 세 조건이 **모두** 생길 때 다시 볼 일이다.

### 이 보고서가 못 본 것

- `object/token_*.go` 의 토큰 수명·회전·DPoP 세부, SAML 서명 검증 경로, `captcha/default.go`,
  `object/verification_ip.go`, `web/` 의 로그인 화면 코드 — 열지 않았다.
- `skipCi` 가 빌드 태그인지, 시험이 CI 에서 실제로 도는지 — 확인 못 함.
- 8절의 항목들은 코드 읽기에 근거한 관찰이다. 어느 것도 실행해 재현하지 않았다.
- MCP 명세의 stdio/HTTP 인증 적용 범위 — 원문 미확인(10-2 의 1).
