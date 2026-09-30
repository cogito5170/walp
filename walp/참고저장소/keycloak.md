# Keycloak 인증 엔진 분석 — 그리고 WALP 에 옮길 수 있는 것 / 없는 것

- 대상: `keycloak` shallow clone, HEAD `634ecb13` (2026-09-29). 경로는 모두 그 클론 루트 기준(`K:` 생략).
- WALP 경로는 `/home/user/SE/walp` 기준으로 `walp/...` 로 적는다.
- 읽은 것만 적었다. 못 읽은 자리는 "확인 못 함" 으로 표시했다.
- 이 문서는 코드를 읽고 쓴 것이다. Keycloak 을 빌드하거나 테스트를 돌리지는 않았다. 여기 나오는 수는 전부 소스에 적힌 상수이고, 잰 값이 아니다.

---

## 1. 저장소 배치 (짧게)

- 루트 `pom.xml:311-336` 이 기본 모듈 목록이다: `common core crypto server-spi server-spi-private saml-core federation services themes model util rest integration authz js test-framework tests quarkus scim ssf authzen`. `testsuite`·`adapters`·`docs`·`distribution`·`operator` 는 프로파일 안에 있다(`pom.xml:1646`, `:1657`, `:1668`, `:1718`, `:1765`).
- 층은 이렇게 나뉜다.
  - `server-spi/`: 공개 모델 인터페이스. `AuthenticationExecutionModel`, `AuthenticationFlowModel`, `AuthenticationSessionModel`, `RealmModel` 이 여기 있다.
  - `server-spi-private/`: 내부 SPI. `Authenticator`, `FlowStatus`, `BruteForceProtector`, 비밀번호 해시, 기본 흐름 정의(`DefaultAuthenticationFlows`)가 여기 있다.
  - `services/`: 구현. `AuthenticationProcessor`, `DefaultAuthenticationFlow`, 각 authenticator, `AuthenticationManager`, 토큰이 여기 있다.
  - `model/{jpa,infinispan,storage,...}`: 저장소 구현이다.
  - `quarkus/`: 배포 런타임이다.
  - `js/apps/{admin-ui,account-ui,...}`: 관리·계정 콘솔이다. React 18 을 쓴다(`js/apps/admin-ui/package.json:106`). admin-ui 에만 `.tsx` 파일이 553개 있다.
- 언어는 Java 가 중심이다(`js/` 밖 `.java` 8532개). 빌드는 Maven(`mvnw`)이고, 프런트는 pnpm 워크스페이스(`js/pnpm-workspace.yaml`)다.

## 2. 인증 흐름 엔진 — "상태 기계" 부분

### 2.1 자료 모델

- `AuthenticationFlowModel` 필드는 `alias`, `providerId`, `topLevel`, `builtIn` 이다(`server-spi/src/main/java/org/keycloak/models/AuthenticationFlowModel.java:29-34`).
  - `providerId` 는 흐름의 종류를 고른다. `basic-flow` 는 `DefaultAuthenticationFlow`, `form-flow` 는 `FormAuthenticationFlow`, `client-flow` 는 `ClientAuthenticationFlow` 가 된다(`services/.../authentication/AuthenticationProcessor.java:942-956`).
- `AuthenticationExecutionModel` 필드는 `authenticator`, `flowId`, `authenticatorFlow`, `requirement`, `priority`, `parentFlow` 다(`server-spi/.../models/AuthenticationExecutionModel.java:42-49`).
  - 한 execution 은 **authenticator 하나이거나 하위 흐름 하나**다. 흐름은 execution 의 순서 있는 목록이고, 결과적으로 **트리**가 된다.
- `Requirement` 는 `REQUIRED(0)`, `CONDITIONAL(1)`, `ALTERNATIVE(2)`, `DISABLED(3)` 넷이다(`AuthenticationExecutionModel.java:125-129`).
- 실행 결과는 execution 별로 **인증 세션에 저장**된다. `AuthenticationSessionModel.getExecutionStatus()` 가 `Map<String, ExecutionStatus>` 를 준다(`server-spi/.../sessions/AuthenticationSessionModel.java:50`).
  - 값은 `FAILED, SUCCESS, SETUP_REQUIRED, ATTEMPTED, SKIPPED, CHALLENGED, EVALUATED_TRUE, EVALUATED_FALSE` 중 하나다(`server-spi/.../sessions/CommonClientSessionModel.java:60-68`).
  - 따라서 **상태 기계의 "상태" 는 이 맵 전체**다. 상태가 숫자 하나로 표현되지 않는다.

### 2.2 Authenticator SPI

- `Authenticator` 인터페이스(`server-spi-private/.../authentication/Authenticator.java`)의 메서드는 다음과 같다.
  - `authenticate(ctx)`(:57): 처음 호출된다.
  - `action(ctx)`(:64): 사용자가 폼을 POST 하면 호출된다.
  - `requiresUser()`(:72): 이 authenticator 가 이미 식별된 사용자를 전제하는지.
  - `configuredFor(session, realm, user)`(:82): 이 사용자가 해당 자격증명을 등록했는지.
  - `setRequiredActions`(:88)와 `areRequiredActionsEnabled`(:102): 미등록 사용자에게 등록을 강제하는 경로.
- authenticator 는 판정을 `Result` 에 **상태로 써 넣는다**. 반환값으로 돌려주지 않는다.
  - `success()` → `FlowStatus.SUCCESS`(`AuthenticationProcessor.java:413-422`)
  - `failure()` → `FAILED`(:426-427)
  - `challenge()` → `CHALLENGE`(:433-434)
  - `forceChallenge()` → `FORCE_CHALLENGE`(:440-441)
  - `failureChallenge()` → `FAILURE_CHALLENGE`(:447-449)
  - `attempted()` → `ATTEMPTED`(:472-473)
  - `resetFlow()` → `FLOW_RESET`(:706-712)
  - `fork()` → `FORK`(:717-718)
- `FlowStatus` 의 전체 목록은 `server-spi-private/.../authentication/FlowStatus.java:26-75` 에 있다.

### 2.3 진입점

1. `AuthenticationProcessor.authenticate()`(:958)가 `authenticateOnly()`(:1109)를 부른다.
   - 여기서 클라이언트 세션 코드를 검증하고(:1111, `checkClientSession` :1093-1106), `validateUser` 로 비활성·서비스계정 사용자를 거른다(:1229-1236).
   - 그다음 최상위 흐름의 `processFlow()` 를 호출한다(:1122-1123).
2. 결과는 세 갈래다.
   - 응답(challenge)이 오면 그대로 브라우저로 돌려준다(:1123). **요청이 여기서 끝나고, 상태는 인증 세션에 남는다.**
   - `authenticatedUser == null` 이면 `UNKNOWN_USER` 로 실패한다(:1124-1131).
   - `!isSuccessful()` 이면 모아 둔 예외 목록으로 실패한다(:1132-1134).
3. 사용자가 폼을 POST 하면 `authenticationAction(execution)`(:1056)이 받는다.
   - POST 된 execution 이 인증 세션의 `CURRENT_AUTHENTICATION_EXECUTION` 과 다르면 "page expired" 로 처리한다(:1059-1063). 새로고침이나 재전송을 막는 자리다.
   - 같으면 `processAction` 으로 넘긴다(:1081-1082, 실패 시 예외 목록 :1087-1088).
4. 흐름이 성공하면 `authenticationComplete()`(:1238)로 간다. `nextRequiredAction()` 이 있으면 required action 으로 보내고(:1244-1247), 없으면 `finishedRequiredActions` 로 세션을 만든다(:1255).

### 2.4 `DefaultAuthenticationFlow.processFlow()` — 핵심 제어 흐름

파일은 `services/src/main/java/org/keycloak/authentication/DefaultAuthenticationFlow.java` 다.

1. **분류** — `fillListsOfExecutions`(:343-362)
   - 조건 authenticator 는 목록에서 뺀다(:345).
   - `REQUIRED` 와 `CONDITIONAL` 은 `requiredList` 에, `ALTERNATIVE` 는 `alternativeList` 에 넣는다(:347-350).
   - `DISABLED` 는 어느 목록에도 들어가지 않는다. 그래서 `isProcessed` 에서 곧바로 "처리됨" 이 된다(:73).
   - **같은 층에 REQUIRED 와 ALTERNATIVE 가 섞여 있으면 ALTERNATIVE 를 경고만 남기고 버린다**(:354-361). 이 설계의 가장 중요한 비대칭이다.
2. **required 순회**(:286-306)
   - CONDITIONAL 하위 흐름이 아직 처리되지 않았고 조건이 거짓이면, 목록에서 **제거**한다. 없는 것으로 취급한다는 뜻이다(:292-295).
   - 나머지는 `processSingleFlowExecutionModel(required, true)` 로 돌린다(:296).
   - 성공 여부는 `requiredElementsSuccessful &= isSuccessful || isSetupRequired` 로 AND 누적한다(:297).
   - 응답(challenge)이 나오면 **즉시 반환**한다(:298-300). 실패했는데 응답이 없으면 순회를 멈춘다(:303-305).
3. **alternative 순회**(:309-330). required 목록이 비었을 때만 돈다.
   - 이미 성공한 것이 하나라도 있으면 흐름 성공으로 끝낸다(:311-312).
   - 아니면 순서대로 시도한다.
     - 응답이 나오면 반환한다(:318-321).
     - 성공이면 흐름 성공이다(:322-324).
     - `AuthenticationFlowException` 이 나면 **삼키고** `ATTEMPTED` 로 표시한 뒤 다음 후보로 넘어간다(:325-329). 주석은 "사용자에게 대안이 있으니 계속 간다" 고 적고 있다.
4. required 가 전부 성공이면 `onFlowExecutionsSuccessful()` 이 불린다(:332-333). 최상위 흐름이면 콜백을 실행하고 `successful = true` 를 둔다(:625-633).
5. 둘 다 아니면 `null` 을 반환한다(:336). 이때 흐름은 "성공 아님" 상태이고, 상위에서 `FAILED` 로 기록된다(:434-435).

#### 단일 execution — `processSingleFlowExecutionModel`(:418-496)

- 이미 처리된 execution 이면 건너뛴다(:421-424). "처리됨" 은 `SUCCESS`, `SKIPPED`, `ATTEMPTED`, `SETUP_REQUIRED`, `DISABLED` 를 뜻한다(:72-79).
- **하위 흐름이면 재귀**한다: `createFlowExecution(...).processFlow()`(:426-428).
  - 응답이 있으면 `CHALLENGED` 로 표시하고 반환한다(:439-440).
  - 없으면 성공 여부에 따라 `SUCCESS` 또는 `FAILED` 로 표시한다(:429-436).
- 선택 목록을 만든다. 사용자의 자격증명 우선순위나 "try another way" 선택에 따라 **실제로 돌릴 execution 을 바꿀 수 있다**(:451-464, `resolveSelectedExecution` :528-535).
- 사용자 전제를 검사한다(:468-491).
  - `requiresUser()` 인데 사용자가 없으면 `UNKNOWN_USER` 예외를 던진다(:469-471).
  - 사용자가 자격증명을 등록하지 않았으면(`!configuredFor`) 두 경우로 갈린다.
    - REQUIRED 이고 사용자 설정이 허용되면 `SETUP_REQUIRED` 로 표시하고 required action 을 건다(:473-478). **"통과로 간주하되, 로그인 뒤에 등록을 강제한다"** 는 뜻이다.
    - 그 밖이면 `CREDENTIAL_SETUP_REQUIRED` 예외를 던진다(:480).
- 마지막으로 `authenticator.authenticate(context)` 를 부르고(:493), 그 결과를 `processResult(context, false)` 로 넘긴다(:495).

#### 판정 표 — `processResult`(:537-579)

| authenticator 가 쓴 `FlowStatus` | 저장되는 `ExecutionStatus` | 엔진이 하는 일 |
|---|---|---|
| `SUCCESS` | `SUCCESS` | 완료 목록을 갱신하고 `null` 반환(다음 execution 으로)(:541-545) |
| `FAILED` | `FAILED` | `logFailure` 로 brute force 에 기록한다. challenge 가 있으면 그것을 보내고, 없으면 예외를 던진다(:546-553) |
| `CHALLENGE` / `FORCE_CHALLENGE` | `CHALLENGED` | 화면을 보내고 **요청을 멈춘다**(:558-561) |
| `FAILURE_CHALLENGE` | `CHALLENGED` | 실패를 기록한 뒤 화면을 다시 보낸다. 비밀번호 오입력이 이 경우다(:562-566) |
| `ATTEMPTED` | `ATTEMPTED` | 통과 취급, `null` 반환(:567-570) |
| `FORK` | — | `ForkFlowException` 을 던진다(:554-557) |
| `FLOW_RESET` | — | `resetFlow()` 후 처음부터 다시 돈다(:571-573) |

- `sendChallenge` 는 `CURRENT_AUTHENTICATION_EXECUTION` 에 이 execution 의 id 를 적는다(:581-584). 다음 POST 는 이 id 로만 받아들여진다. 2.3 의 page expired 검사가 이 값을 본다.
- `resetFlow` 는 인증 사용자, 실행 상태, 노트, required action 을 전부 지운다(`AuthenticationProcessor.java:998-1013`).

#### POST 이후 — `processAction`(:86-179)과 부모 흐름 재평가

- 특수 POST 부터 처리한다.
  - "switchOrganization" 이면 흐름을 리셋한다(:102-115).
  - "tryAnotherWay" 이면 authenticator 선택 화면을 보낸다(:118-123).
  - 다른 execution 으로 전환을 요청하면, 허용 목록에 있는지 먼저 확인한다(:126-146). 목록에 없으면 `INTERNAL_ERROR` 다(:135-137).
- 보통 경우에는 `authenticator.action(result)` 를 부르고 `processResult(result, true)` 로 넘긴다(:174-175). 응답이 없으면 `continueAuthenticationAfterSuccessfulAction` 으로 간다(:176-177).
- `checkAndValidateParentFlow`(:221-246)는 **아래에서 위로** 부모 흐름의 성공을 다시 계산한다.
  - 방금 끝난 execution 이 REQUIRED 나 CONDITIONAL 이면, 형제 required 가 모두 성공해야 부모가 성공이다.
  - ALTERNATIVE 이면, 형제 중 하나가 성공하고 required 형제가 없어야 부모가 성공이다(:232-233).
  - 부모가 성공이면 `SUCCESS` 를 찍고 한 층 위로 올라간다(:235-238).
- 그다음 첫 미완 조상부터 흐름을 **다시 돈다**(:191-209). 앞에서 본 `isProcessed` 캐시 덕분에 이미 끝난 단계는 건너뛰므로, 재평가가 멱등이다.

### 2.5 CONDITIONAL 의 정확한 뜻

- `ConditionalAuthenticator` 는 `matchCondition(ctx)` 하나만 추가한다. `authenticate` 는 호출되지 않는다(`services/.../conditional/ConditionalAuthenticator.java:26-40`).
- `isConditionalSubflowDisabled`(:370-384)는 CONDITIONAL **하위 흐름** 안의 활성 조건 authenticator 를 모은다.
  - **하나도 없으면 비활성**이다. 즉 CONDITIONAL 흐름에 조건이 없으면 그 흐름은 실행되지 않는다. `BrowserFlowTest.testFlowDisabledWhenConditionalAuthenticatorIsMissing` 가 이 경우를 시험한다(아래 8절).
  - 조건이 여럿이면 **하나라도 거짓이면 비활성**이다(`anyMatch(conditionalNotMatched)`, :380-381). 따라서 조건들은 AND 로 묶인다.
  - 각 조건의 평가 결과는 `EVALUATED_TRUE` 또는 `EVALUATED_FALSE` 로 저장된다(:406-408). 주석은 "조건의 조건이 흐름 도중에 바뀔 수 있어 매번 다시 평가한다" 고 적고 있다(:404-405).
- 이미 처리된 CONDITIONAL 은 조건을 다시 보지 않는다(`!isProcessed(required)`, :292).
- 내장 조건 authenticator 는 다음과 같다: `ConditionalClientScope`, `ConditionalCredential`, `ConditionalLoa`, `ConditionalRole`, `ConditionalSubFlowExecuted`, `ConditionalUserAttributeValue`, `ConditionalUserConfigured`(디렉터리 `services/.../authenticators/conditional/`).
- 예로 `ConditionalUserConfiguredAuthenticator.matchCondition` 을 보면, 같은 흐름 안의 **형제 authenticator 들이 이 사용자에게 설정돼 있는지**를 `configuredFor` 로 판정한다(`.../conditional/ConditionalUserConfiguredAuthenticator.java:19-65`). "OTP 를 등록한 사람에게만 2FA" 가 이렇게 구현된다.

### 2.6 기본 브라우저 흐름 (실제 트리)

정의는 `server-spi-private/.../models/utils/DefaultAuthenticationFlows.java` 의 `browserFlow`(:296~)에 있다.

```
browser (top)
├─ auth-cookie                         ALTERNATIVE (:308-310)
├─ auth-spnego                         DISABLED   (:315-321)
└─ forms (sub-flow)                    ALTERNATIVE (:336)
   ├─ auth-username-password-form      REQUIRED   (:346-347)
   └─ Browser - Conditional 2FA        CONDITIONAL (:355-361)
      ├─ conditional-user-configured   REQUIRED(조건) (:372-373)
      ├─ conditional-credential        REQUIRED(조건, PASSKEYS 기능일 때만) (:378-390)
      ├─ auth-otp-form                 ALTERNATIVE (:397-401)
      ├─ webauthn-authenticator        DISABLED   (:410-411)
      └─ auth-recovery-authn-code-form DISABLED   (:421-422)
```

- 쿠키가 성공하면 top 흐름의 ALTERNATIVE 가 채워져 곧바로 끝난다. 이것이 SSO 다.
- 쿠키가 없으면 `forms` 로 간다. 그 안의 2FA 는 조건이 참일 때만 존재한다.

## 3. 비밀번호

- `PasswordCredentialProvider.createCredential` 은 비밀번호를 만들 때 두 단계를 거친다(`services/.../credential/PasswordCredentialProvider.java:87-105`).
  - 먼저 `PasswordPolicyManagerProvider.validate` 로 정책을 검사한다. 위반이면 `ModelException` 을 던진다(:90-91).
  - 그다음 `getHashProvider(policy)` 가 고른 해시로 인코딩한다(:93-98).
- 해시 고르기(:170-181)는 두 경우다.
  - realm 정책에 `hashAlgorithm` 이 있으면 그 provider 를 쓴다.
  - 없으면 SPI 기본 provider 를 쓴다. SPI 기본은 "`order()` 가 가장 큰 factory" 다(`services/.../DefaultKeycloakSessionFactory.java:270-272`).
- factory 의 `order` 값은 다음과 같다.
  - `argon2` = 300(`crypto/default/.../Argon2PasswordHashProviderFactory.java:156-158`). 단 FIPS 모드에서는 비지원이다(:151-153).
  - `pbkdf2-sha512` = 200(`server-spi-private/.../credential/hash/Pbkdf2Sha512PasswordHashProviderFactory.java:32-34`).
  - `pbkdf2-sha256` = 100(`Pbkdf2Sha256...java:32-34`).
- → 소스만 보면 **FIPS 가 아닌 배포의 기본은 Argon2, FIPS 배포의 기본은 PBKDF2-SHA512** 라고 추론된다. Quarkus 설정이 이 기본을 덮어쓰는지는 **확인 못 함**.
- 각 해시의 기본 파라미터는 다음과 같다.
  - Argon2: `id`, v1.3, 해시 32바이트, memory 7168(KiB 로 추정), iterations 5, parallelism 1(`crypto/default/.../Argon2Parameters.java:9-14`). 구현은 BouncyCastle 이다(:19-28).
  - PBKDF2: SHA1 1,300,000회(`Pbkdf2PasswordHashProviderFactory.java:37-42`), SHA256 600,000회(`Pbkdf2Sha256...:14-19`), SHA512 210,000회(`Pbkdf2Sha512...:14-19`).
- 검증(`isValid` :209-247)은 **저장된 자격증명에 적힌 algorithm** 으로 provider 를 찾아 `verify` 한다(:224-231).
  - 성공하면 `rehashPasswordIfRequired`(:277~)가 현재 정책과 맞는지 `policyCheck`(:286)로 본다.
  - 맞지 않으면 **별도 트랜잭션에서 새 정책으로 재해시**한다(:287-296). 알고리즘을 바꿔도 사용자가 로그인할 때마다 조용히 이행되는 구조다.
- 비밀번호 정책 provider 는 `server-spi-private/.../policy/` 에 있다: length, maxLength, digits, lower/upperCase, specialChars, notUsername, notEmail, notContainsUsername, regexPattern, denylist, history, age, forceExpiredPasswordChange, hashAlgorithm, hashIterations, maxAuthAge, recoveryCodesWarningThreshold. 정책 키 상수는 `server-spi/.../models/PasswordPolicy.java:38-54` 에 있다.

## 4. MFA

- **OTP 폼**: `OTPFormAuthenticator.validateOTP`(`services/.../browser/OTPFormAuthenticator.java:74-121`).
  - 사용자가 brute force 로 막혔으면 `SESSION_INVALID` 노트를 남기고 끝낸다(:96-105).
  - 입력이 없으면 `challenge` 를 보낸다(:107-111).
  - 틀리면 `failureChallenge(INVALID_CREDENTIALS)` 다(:112-118). 이 경로가 엔진의 `logFailure` 를 거쳐 brute force 카운트를 올린다.
  - 맞으면 `success(OTPCredentialModel.TYPE)` 다(:120).
- **검증**: `OTPCredentialProvider.isValid`(`services/.../credential/OTPCredentialProvider.java:83-126`).
  - HOTP 는 `HmacOTP.validateHOTP` 로 look-ahead 창 안에서 찾고, 찾으면 카운터를 갱신해 저장한다(:103-111).
  - TOTP 는 `TimeBasedOTP.validateTOTP` 로 검증한다. ±lookAround 구간을 본다(`server-spi-private/.../models/utils/TimeBasedOTP.java:83-86`).
  - `isCodeReusable` 가 false(기본, `OTPPolicy.java:76`)면 `singleUseObjects().putIfAbsent(credId.code, lifespan)` 로 **같은 코드를 다시 쓰지 못하게 막는다**(:112-122).
- **OTP 기본 정책**: TOTP, HmacSHA1, 6자리, look-ahead 1, 주기 30초(`server-spi/.../models/OTPPolicy.java:75`, 생성자 :61-63).
- **WebAuthn**: 검증 라이브러리는 **webauthn4j** 다.
  - 버전은 `pom.xml:249` 의 `0.30.3.RELEASE` 이고, 의존성은 `pom.xml:1232-1239` 와 `services/pom.xml:265-266` 에 걸려 있다.
  - `WebAuthnCredentialProvider.isValid`(`services/.../credential/WebAuthnCredentialProvider.java:208~`)는 `WebAuthnAuthenticationManager.parse`(:229)와 `.verify`(:237)를 부른다. 서명 카운터는 갱신한다(:256).
- **passwordless**: `WebAuthnPasswordlessAuthenticator` 는 `WebAuthnAuthenticator` 에서 정책(`getWebAuthnPolicyPasswordless`, :55-56)과 자격증명 타입(:60)만 바꾼 하위 클래스다.
  - 사용자 식별은 assertion 의 `userHandle` 을 base64url 로 디코드해서 한다(`WebAuthnAuthenticator.java:199-215`).
  - 식별이 되면 `context.setUser(user)` 후 `success` 다(:278-282). 그래서 username 단계 없이 **이 authenticator 하나로 사용자 식별과 인증을 같이** 끝낼 수 있다.
- **복구 코드**: `RecoveryAuthnCodesFormAuthenticator` 가 있다(`services/.../browser/RecoveryAuthnCodesFormAuthenticator.java`). 이 authenticator 도 brute force 검사를 한다(:62).
  - `RecoveryAuthnCodesCredentialProvider.isValid` 는 **"다음 코드" 하나와만** 비교하고, 맞으면 그 코드를 제거한다(`services/.../credential/RecoveryAuthnCodesCredentialProvider.java:104-115`).
  - 코드는 12개를 만들고, SHA-512 로 해시해 저장하며, 상수 시간으로 비교한다(`server-spi/.../models/utils/RecoveryAuthnCodesUtils.java:27`, `:31-46`).
  - 기본 브라우저 흐름에서는 `DISABLED` 로 들어 있다(2.6).

## 5. Brute force 보호와 위험 신호

### 5.1 구현과 흐름

- 인터페이스는 `server-spi-private/.../managers/BruteForceProtector.java` 이고, 기본 구현은 `services/.../managers/DefaultBruteForceProtector.java` 다.
- factory 가 동시성 방식을 고른다(`DefaultBruteForceProtectorFactory.java:43-56`).
  - 요청마다 `DefaultLockingBruteForceProtector`(사용자 락 맵) 를 쓰거나,
  - 설정에 따라 `DefaultBruteForceProtector` 또는 `DefaultBlockingBruteForceProtector` 를 쓴다.
- 기록 경로는 이렇다.
  - 실패: 엔진의 `processResult` 에서 `FAILED` 나 `FAILURE_CHALLENGE` 가 나오면 `processor.logFailure` 가 불린다(`DefaultAuthenticationFlow.java:548`, `:564`). 이것이 `failedLogin` 을 부른다(`AuthenticationProcessor.java:769-778`).
  - 성공: `AuthenticationManager.logSuccess` 가 `successfulLogin` 을 부른다(`services/.../managers/AuthenticationManager.java:1767-1778`). 호출 위치는 :1003 과 ROPC grant(`ResourceOwnerPasswordCredentialsGrantType.java:181`)다.
- 모든 로그인 결과를 세지는 않는다. 카테고리가 password, OTP, recovery code 인 것만 센다(`ALLOWED_AUTHENTICATION_CATEGORIES`, `DefaultBruteForceProtector.java:68-72`, 필터 :229-231, :243-245).
- 처리는 **"bruteforce" executor 에서 비동기로, 별도 트랜잭션에서** 한다(:251-267).

### 5.2 잠금 계산 — `failure()`(:83-165)

1. 마지막 실패로부터 `maxDeltaTimeSeconds` 가 지났으면 실패 수를 초기화한다(:98-103). 단 "영구잠금 + 임시잠금 0회" 모드에서는 초기화하지 않는다.
2. 대기 시간을 계산한다(:110-116).
   - `MULTIPLE` 전략: `waitIncrement × (실패수 / failureFactor)`
   - 그 밖(`LINEAR`): `waitIncrement × (1 + 실패수 − failureFactor)`
3. 대기가 0 이하인데 직전 실패와의 간격이 `quickLoginCheckMilliSeconds` 보다 짧으면, `minimumQuickLoginWaitSeconds` 를 강제한다(:121-128).
4. 대기는 `maxFailureWaitSeconds` 로 상한을 둔다(:131). 그다음 `failedLoginNotBefore` 를 설정하고 `USER_DISABLED_BY_TEMPORARY_LOCKOUT` 이벤트를 낸다(:137-142).
5. OTP 실패는 별도로 센다. `maxSecondaryAuthFailures` 를 넘으면 **영구잠금**이다(:146-155).
6. 영구잠금 조건은 `임시잠금 횟수 > maxTemporaryLockouts`, 또는 `maxTemporaryLockouts==0 && 실패수 ≥ failureFactor` 다(:161-164).
   - 영구잠금은 `user.setEnabled(false)` 와 `DISABLED_REASON` 속성으로 구현한다(:167-181).

### 5.3 기본값과 검사 위치

- realm 생성 시 기본값(`services/.../managers/RealmManager.java:273-283`)은 다음과 같다.
  - **보호 꺼짐**(`setBruteForceProtected(false)`, 주석 "todo set it on").
  - 영구잠금 false, 임시잠금 0회, 전략 `MULTIPLE`.
  - 최대 대기 900초, quick-login 최소 대기 60초, 증가분 60초, quick-login 판정 1000ms, 초기화 창 12시간, failureFactor 30, OTP 한도 0(꺼짐).
- 검사 위치: 각 authenticator 가 `AuthenticatorUtils.getDisabledByBruteForceEventError` 를 부른다(`services/.../util/AuthenticatorUtils.java:53-64`). 이 함수가 `isPermanentlyLockedOut`(`DefaultBruteForceProtector.java:288-298`)와 `isTemporarilyDisabled`(`:271-285`, `now < failedLoginNotBefore`)를 확인한다.
  - username/password 폼에서는 `AbstractUsernameFormAuthenticator.isDisabledByBruteForce`(:254-265)가 **`forceChallenge`** 로 같은 화면을 다시 보낸다. 실패로 처리하지 않는다.
  - 그래서 잠긴 동안의 시도는 실패 수를 다시 올리지 않는다(`FORCE_CHALLENGE` 는 `logFailure` 를 거치지 않는다, `DefaultAuthenticationFlow.java:558-561`).

### 5.4 "새 기기" 알림이 있나 — 정직하게

- `services`·`server-spi*` 에서 `new.?device|unknown.?device|newDevice` 로 grep 했다. 로그인 위험 판정이나 새 기기 알림 로직은 **찾지 못했다**. 결과는 OAuth Device Grant 와 테스트뿐이었다. 이 grep 범위 밖(예: `ssf/`, 테마, 확장)은 **확인 못 함**.
- 있는 것은 다음 두 가지다.
  - 기기 정보를 **기록**하는 기능. `UserSessionManager.createUserSession` 이 `DeviceActivityManager.attachDevice` 로 UA 파싱 결과를 사용자 세션 노트 `KC_DEVICE_NOTE` 에 넣는다(`services/.../managers/UserSessionManager.java:185-197`, `server-spi-private/.../device/DeviceActivityManager.java:35`, `:64-69`). 이것을 판정에 쓰는 코드는 찾지 못했다.
  - 이벤트 이메일. `EmailEventListenerProvider` 는 설정한 이벤트 타입만 메일로 보낸다(`services/.../events/email/EmailEventListenerProvider.java:66`). 지원 목록은 `LOGIN_ERROR, UPDATE_PASSWORD, REMOVE_TOTP, UPDATE_TOTP, UPDATE_CREDENTIAL, REMOVE_CREDENTIAL` 이다(`...EmailEventListenerProviderFactory.java:46`). **"로그인 성공, 새 기기"** 알림은 없다.

## 6. 세션·토큰·로그아웃

- 세션은 두 종류다.
  - **인증 세션** `AuthenticationSessionModel` 은 흐름 도중의 상태를 담는다: 실행 상태 맵, 인증 사용자, required action, 노트(`AuthenticationSessionModel.java:50`, `:68`, `:80`).
  - **사용자 세션** `UserSessionModel` 은 로그인이 끝난 뒤 생긴다.
  - `AuthenticationProcessor.attachSession`(:1139-1150)이 인증 세션을 사용자 세션과 클라이언트 세션으로 옮긴다. 주석은 "required action 과 동의가 끝난 뒤에 붙는다" 고 적고 있다(:1241).
- 토큰 발급은 `TokenManager.createClientAccessToken`(`services/.../protocol/oidc/TokenManager.java:493`)과 `responseBuilder`(:1206)가 한다. 검증은 `validateToken`(:178)이다.
- 토큰 폐기: `TokenRevocationEndpoint.revoke()`(`services/.../oidc/endpoints/TokenRevocationEndpoint.java:90`)가 access token 을 `session.revokedTokens().put(id, lifespan)` 에 넣는다(:263-266).
- 로그아웃: `AuthenticationManager.backchannelLogout`(`AuthenticationManager.java:282-326`)와 `browserLogout`(:715)이 한다.
- 계정 콘솔 기기 목록: `services/.../resources/account/SessionResource.java` 에 있다.
  - `@GET` devices(:92~)가 세션마다 `DeviceRepresentation` 을 만든다(:109).
  - `@DELETE` 는 전체 로그아웃(:140-151) 또는 id 하나 로그아웃(:168-181)이다. 둘 다 `backchannelLogout` 을 쓴다.

## 7. 계정 복구와 required action

- 기본 `reset credentials` 흐름(`DefaultAuthenticationFlows.java:160-226`)의 구성은 다음과 같다.
  - REQUIRED `reset-credentials-choose-user`(:173-174)
  - REQUIRED `reset-credential-email`(:182-183)
  - REQUIRED `reset-password`(:191-192)
  - CONDITIONAL 하위 흐름 "Reset - Conditional OTP"(:200-210). 그 안에 조건 `conditional-user-configured`(:214-215)와 REQUIRED `reset-otp`(:222-223)가 있다.
- `ResetCredentialEmail.authenticate`(`services/.../resetcred/ResetCredentialEmail.java:78~`)의 동작은 다음과 같다.
  - 이메일이 없는 사용자에게도 계속 같은 challenge 를 보낸다. 주석은 "사용자명 추측을 막으려고" 라고 적고 있다(:103-104).
  - `ResetCredentialsActionToken` 에 만료를 넣어 만들고(:113-118), `sendPasswordReset` 으로 링크를 메일로 보낸다(:125).
- `ResetPassword.authenticate` 는 비밀번호를 여기서 바꾸지 않는다. `UPDATE_PASSWORD` required action 을 인증 세션에 **걸기만** 한다(`.../resetcred/ResetPassword.java:34-38`).
  - 즉 복구 흐름은 "누구인지 확인하는 흐름" 과 "로그인 뒤에 해야 할 일(required action)" 로 **나뉘어** 있다.
- required action 엔진(`AuthenticationManager.java`)은 다음 순서로 돈다.
  1. `nextRequiredAction`(:1149)이 트리거를 평가한다(`evaluateRequiredActionTriggers` :1487-1536). 트리거 평가 중에 challenge·success 를 부르면 `RuntimeException` 이다(:1512-1532).
  2. 첫 적용 가능 action 을 고른다(`getFirstApplicableRequiredAction` :1412). 없으면 동의 화면이 필요한지 본다(:1163-1172).
  3. `executeAction`(:1344)이 `requiredActionChallenge` 를 부른다(:1379). 결과는 `FAILURE`, `CHALLENGE`, `IGNORE`, `SUCCESS` 로 갈린다(:1381-1400).
- 내장 required action: `UpdatePassword`, `UpdateTotp`, `VerifyEmail`, `UpdateProfile`, `TermsAndConditions`, `WebAuthnRegister`, `WebAuthnPasswordlessRegister`, `RecoveryAuthnCodesAction`, `DeleteAccount`, `DeleteCredentialAction`, `UpdateEmail`, `VerifyUserProfile`, `UpdateUserLocaleAction`(`services/.../authentication/requiredactions/`).

## 8. 테스트 — 흐름 엔진은 어디서 시험되나

- **`DefaultAuthenticationFlow` 단위 테스트는 찾지 못했다.** `services/src/test/java/org/keycloak/authentication/` 에는 `authenticators/x509/X509AuthenticatorConfigModelTest.java` 하나뿐이다. 엔진은 서버를 띄운 **통합 테스트**로만 검증된다.
- 옛 Arquillian 스위트는 `testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/` 에 있다.
  - `forms/BrowserFlowTest.java` 는 `AbstractChangeImportedUserPasswordsTest` 를 상속한다(:71). 시험 항목 예: `testUserWithoutAdditionalFactorConnection`(:146), `testUserWithOneAdditionalFactorOtpFails`(:154), `testUserWithTwoAdditionalFactors`(:180), `testFlowDisabledWhenConditionalAuthenticatorIsMissing`(:251), `testAlternativeNonInteractiveExecutorInSubflow`(:546).
  - 그 밖에 `forms/CustomFlowTest`, `DirectGrantFlowTest`, `LevelOfAssuranceFlowTest`, `ResetCredentialsAlternativeFlowsTest`, `BruteForceTest`, `ConditionalUserAttributeAuthenticatorTest`, `login/ConditionalCredentialAuthenticatorTest`, `login/ConditionalSubFlowExecutedAuthenticatorTest` 가 있다.
- 새 JUnit 기반 스위트는 `tests/base/src/test/java/org/keycloak/tests/` 에 있다: `admin/authentication/FlowTest`, `InitialFlowsTest`, `forms/AuthenticationFlowCallbackProviderTest`(:58), `oidc/AcrAuthFlowTest`, `oauth/DeviceGrantBruteForceTest`. 기반은 `test-framework/` 다.
- 위 테스트들은 이름과 선언 줄만 봤다. 본문의 단언은 **확인 못 함**.

---

## 9. WALP 에 옮길 수 있는 것 / 없는 것

붙여 넣은 제안은 "Keycloak → WALP 인증 상태 머신: 인증 단계와 조건부 분기" 였다. **결론부터: 약한 맞춤이다.** 두 시스템의 "상태 기계" 는 이름만 같고 모양이 다르다.

### 9.1 모양이 어떻게 다른가

| | Keycloak | WALP |
|---|---|---|
| 목적 | **증명의 합성** — 여러 증거가 모이면 "이 사람이다" | **행동 선택** — 매 틱마다 다음 한 걸음 |
| 구조 | execution **트리**, 노드마다 REQUIRED/ALTERNATIVE/CONDITIONAL/DISABLED(`AuthenticationExecutionModel.java:125-129`) | **평평한 우선순위 목록** 8칸(`walp/include/walp/interfaces.hpp:100-110`)을 `Deliberator::plan` 이 위에서부터 보고, 처음 맞는 조건에서 반환(`walp/src/planner.cpp:281-330`) |
| 상태 | execution 별 상태 **맵**. HTTP 요청 사이에 인증 세션에 보존되고, 재평가는 `isProcessed` 캐시로 멱등(`DefaultAuthenticationFlow.java:72-79`) | `BeliefState` 하나. 관측으로 매번 갱신(`interfaces.hpp:47-69`) |
| 멈춤 | challenge 가 나오면 **요청을 끝내고** 사람 입력을 기다린다(`:558-561`) | 한 행동을 실행하고 다음 틱으로 간다. 기다리는 것은 `kHold` 행동이다 |
| 성공 판정 | AND(required) / OR(alternative) 를 **아래에서 위로** 계산(`:221-246`) | 없다. 목표 확정은 가설 투표(`planner.cpp:295-303`) |

### 9.2 옮길 수 있는 것 (구체적으로)

1. **CONDITIONAL 의 "조건이 없으면 꺼진다" 규칙**(`DefaultAuthenticationFlow.java:380-381`).
   - WALP 의 `RC_CANDIDATE_NEAR/FAR`, `RC_BLOCKED` 는 잠기지 않은 학습 가능 규칙이다(`interfaces.hpp:125`).
   - 학습이 새 조건부 규칙을 제안할 때, **"발동 조건이 비었으면 그 규칙은 존재하지 않는 것" 을 기본값으로** 두라는 교훈으로 옮길 수 있다. 조건이 빈 규칙이 조용히 항상 켜지는 사고를 막는다.
   - 다만 WALP 의 조건 8개는 지금 **코드에 고정된 술어**이고(`planner.cpp:281-330`), 규칙 데이터(`rule_action[RC_COUNT]`, `interfaces.hpp:140`)는 "조건 → 행동" 의 행동 칸만 바꾼다. 조건을 데이터로 추가하는 길은 현재 없다. 그래서 이 교훈이 쓸모 있어지는 것은 **조건 자체를 학습 대상으로 넓힐 때부터**다.
2. **"같은 층에 REQUIRED 와 ALTERNATIVE 를 섞으면 ALTERNATIVE 를 버린다"**(`:354-361`).
   - 교훈은 규칙 조합에 **구조적 타입 검사**를 두라는 것이다.
   - WALP 에는 이미 비슷한 장치가 있다. `kRuleAllowed` 와 `kRuleLocked` 로 조건마다 허용 행동을 비트로 막고, 학습 후보를 거절한다(`walp/src/learner.cpp:59-64`). 이 점에서는 **WALP 가 더 엄격하다**. Keycloak 은 경고만 찍고 계속 돈다.
   - 가져올 것은 "잘못 조합된 설정을 조용히 받아들이지 말라" 는 원리뿐이다. WALP 는 이미 그렇게 하고 있다.
3. **SETUP_REQUIRED = "통과시키되 뒤에 할 일을 건다"**(`:473-478`)와 required action 의 분리(`ResetPassword.java:34-38`).
   - 이것은 WALP 보다 **`se_router` 의 `ASK`** 에 더 맞는다.
   - 지금 `route()` 는 필수 인자가 비면 `ASK` 로 **멈춘다**(`walp/se_router.py:314-317`).
   - Keycloak 식으로 가면, 안전한 도구는 기본값으로 실행하고 "뒤에 확인할 일" 을 기록하는 길이 생긴다. 다만 이것은 WALP 의 원칙 "추측하지 않는다"(`se_router.py:6-10`)와 **정면으로 부딪힌다**. 옮기려면 원칙 쪽에서 먼저 결정해야 한다.
4. **FAILURE_CHALLENGE 와 FORCE_CHALLENGE 의 구분**(`:558-566`).
   - FAILURE_CHALLENGE 는 실패를 세고 다시 묻는다. FORCE_CHALLENGE 는 세지 않고 다시 묻는다. 잠긴 동안의 시도가 카운트를 올리지 않는 것이 후자 덕분이다(5.3).
   - WALP 의 안전층 거부 뒤 `fallback`(`walp/src/state.cpp:274-296`, 호출 `walp/src/executive.cpp:95-101`)과 학습용 `EpisodeRecord.denials`(`interfaces.hpp:183`)에 대응하는 교훈이 있다. **"안전층이 스스로 낸 보류" 를 "정책이 실패한 증거" 로 세지 말라**는 것이다.
   - WALP 가 이미 이렇게 구분하는지는 **확인 못 함**(learner 의 실패 분류를 읽지 않았다).
5. **brute force 의 "지수적이지 않은, 상한 있는 점증 대기 + 빠른 재시도 하한"**(`DefaultBruteForceProtector.java:110-131`).
   - `se_router` 에 반복 REJECT/DENY 요청이 들어올 때 속도를 제한하는 모형으로 쓸 수 있다.
   - 다만 WALP 쪽에 그런 공격 표면이 있는지는 **확인 못 함**. `se_router.py` 에는 호출자 상태가 없다(`route` 는 순수 함수다, :279-322).

### 9.3 옮길 수 없는 것 / 비유가 깨지는 곳

1. **"인증 단계 ≈ WALP 정책 그래프 조건" 은 틀린 대응이다.**
   - Keycloak 의 REQUIRED 는 "**전부** 통과해야 한다" 다. WALP 의 조건 목록은 "**처음** 참인 것 하나가 이긴다" 다(`planner.cpp:281-330` 의 연속 `return`).
   - WALP 에 대응하는 의미론은 Keycloak 의 **ALTERNATIVE 하나뿐**이다. 그것도 "먼저 성공한 것" 이 아니라 "먼저 **참인** 조건" 이다.
   - REQUIRED 의 AND 누적(`DefaultAuthenticationFlow.java:297`), 부모 재평가(`:221-246`), 트리 재귀(`:426-437`)는 WALP 에 받을 자리가 없다.
2. **WALP 의 잠긴 안전 규칙은 Keycloak 에 대응물이 없다.**
   - `kRuleLocked`(`interfaces.hpp:125`)는 "학습이 이 칸을 못 바꾼다" 는 **시간에 걸친 불변식**이다.
   - Keycloak 흐름은 관리자가 언제든 바꿀 수 있다. 내장 흐름을 보호하는 `builtIn` 플래그(`AuthenticationFlowModel.java:34`)가 있을 뿐이다. 그 강제 방식은 이번에 읽지 않았으므로 **확인 못 함**.
   - 방향이 반대라는 점이 핵심이다. WALP 는 안전을 **잠그고** 전술을 학습한다. Keycloak 은 둘 다 사람이 설정한다.
3. **WALP 안전층은 "행동마다 허가" 다. Keycloak 에는 이런 층이 없다.**
   - `SafetySupervisor::authorize` 는 모든 행동을 거친다(`walp/src/state.cpp:205-262`, 주석 `walp/src/executive.cpp:5`).
   - Keycloak 의 흐름 엔진은 authenticator 가 쓴 상태를 **그대로 믿는다**(`processResult`). 가장 가까운 것은 `validateUser`(`AuthenticationProcessor.java:1229-1236`)와 brute force 선검사(5.3)다. 이것들은 **흐름 진입 때 한 번 하는 검문**이지, 행동 단위 감독이 아니다.
4. **"사람 입력을 기다리며 요청을 끊는다" 는 WALP 루프에 맞지 않는다.**
   - Keycloak 의 중심 기법은 **재개 가능성**이다. 상태를 인증 세션에 영속하고, POST 가 오면 `CURRENT_AUTHENTICATION_EXECUTION` 검사(:1059-1063)와 `isProcessed` 캐시로 트리를 다시 걷는다.
   - WALP 코어는 한 프로세스 안 동기 루프이고, 파일·OS 를 부르지 않는다(README `walp/README.md:44-45`).
   - 이것이 쓸모 있는 곳은 코어가 아니라 **디스코드의 `!walp 루프`** 같은 여러 턴짜리 사람 대화다(`walp/README.md:23-24`). 다만 그 경로의 상태 보존 방식은 이번에 읽지 않았으므로 **확인 못 함**.
5. **Keycloak 에는 학습이 없다.** WALP 의 핵심인 "후보 → 검증 → 승격"(`walp/README.md:3-4`, `:41`)에 줄 교훈이 Keycloak 에는 없다. 그런 대응을 원한다면 Keycloak 이 아니라 선행조사에 적힌 Soar/CBR 쪽을 봐야 한다(`walp/README.md:7-8`).

### 9.4 판정

- "Keycloak → WALP 인증 상태 머신" 을 **구조 이식**으로 읽으면 **약한 맞춤이다.** 합성 논리(AND/OR 트리, 재개 가능한 부분 상태)가 WALP 의 평평한 우선순위 선택과 맞지 않는다. 옮기면 WALP 의 단순성(고정 크기, 무예외, `planner.cpp` 한 함수 안의 우선순위)만 잃는다.
- **원리 몇 개**는 옮길 만하다.
  1. 조건이 빈 조건부 규칙은 꺼진 것으로 본다.
  2. 잘못 조합된 설정은 조용히 받지 않는다. WALP 는 이미 한다.
  3. "안전층이 되묻거나 보류한 것" 과 "실패" 를 따로 센다.
  4. 되묻기 대신 "통과 + 뒤에 할 일" 이라는 제3의 길이 있다. 다만 WALP 원칙과 충돌한다.
- 이 가운데 **조사 없이 바로 짓는 것은 권하지 않는다.** 저장소 규칙(`CLAUDE.md` "짓기 전에 조사한다")을 따르면, 먼저 WALP learner 가 거부·보류를 실패로 세는지부터 확인해야 한다. 9.2-4 에서 "확인 못 함" 으로 둔 자리다.
