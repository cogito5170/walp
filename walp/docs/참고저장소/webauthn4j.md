# WebAuthn4J 분석 (코드 근거) + WALP 에 옮길 수 있는 것 / 없는 것

- 대상: `webauthn4j` 얕은 복제, HEAD `fc3797b` (2026-09-02 17:00 +0900). 이하 경로는 복제 루트 기준.
- WALP 쪽 경로는 `/home/user/SE/walp` 기준.
- 읽지 않은 것은 "확인 못 함" 으로 적었다. 테스트·빌드는 **돌리지 않았다**(읽기만).

---

## 1. 구성

### 1.1 모듈
`settings.gradle.kts:36-47` 가 포함하는 것:

| 모듈 | 역할 (근거) |
|---|---|
| `webauthn4j-core` | 파싱·검증 본체. `WebAuthnManager` / `WebAuthnRegistrationManager` / `WebAuthnAuthenticationManager` (`webauthn4j-core/src/main/java/com/webauthn4j/`) |
| `webauthn4j-core-async` | core 의 비동기판. `api(project(":webauthn4j-core"))` (`webauthn4j-core-async/build.gradle.kts:25`). 내부는 확인 못 함 |
| `webauthn4j-metadata` | FIDO MDS3 BLOB 가져오기·신뢰앵커 저장소 (`webauthn4j-metadata/src/main/java/com/webauthn4j/metadata/`) |
| `webauthn4j-metadata-async` | metadata 비동기판 (`webauthn4j-metadata-async/build.gradle.kts:24-25`). 내부 확인 못 함 |
| `webauthn4j-appattest` | Apple App Attest / DeviceCheck. `DeviceCheckAttestationManager`, `DeviceCheckAssertionManager` (`webauthn4j-appattest/src/main/java/com/webauthn4j/appattest/`), 형식 `"apple-appattest"` (`.../appattest/data/attestation/statement/AppleAppAttestAttestationStatement.java:37`) |
| `webauthn4j-test` | 가짜 인증기·클라이언트 에뮬레이터 (7절) |
| `webauthn4j-util` | 디렉터리는 있다. 내용은 확인 못 함 (`WebAuthnException` 은 오히려 core 의 `com/webauthn4j/util/exception/WebAuthnException.java:21` 에 있다) |
| `integration-tests:*` | `spring-security-passkeys`, `webauthn4j-spring-security`, `quarkus-security-webauthn`, `fido-mds-integration`, `fido-integration-bdd` (`settings.gradle.kts:43-47`) |

**Spring Security 모듈은 이 저장소에 없다.** README 가 Spring Security(`spring-security-web`, Spring 쪽이 유지)와 별도 저장소 `webauthn4j-spring-security` 를 가리킬 뿐이다(`README.md:40-44`). 여기 있는 것은 그것들을 끌어다 돌리는 통합 시험뿐이다.

### 1.2 빌드 · 자바 판
- Gradle Kotlin DSL. 보조 플러그인 네 개를 included build 로 둔다(`settings.gradle.kts:22-25`).
- 소스/타깃 **Java 17** (`buildSrc/src/main/kotlin/webauthn4j.java-conventions.gradle.kts:25-26`).
- 빌드용 JDK 는 `toolchainJdkVersion` 속성으로 고정한다(`settings.gradle.kts:32-34`, `toolchain-pinning-plugin/.../ToolchainPinningPlugin.kt:44`).
- 판: `webAuthn4JVersion=0.31.11`, `isSnapshot=true` (`gradle.properties:16-17`).
- JSON/CBOR 는 **Jackson 3** (`tools.jackson.*`) — `ObjectConverter` 가 `JsonMapper` 와 `CBORMapper` 를 한 쌍으로 든다(`webauthn4j-core/src/main/java/com/webauthn4j/converter/util/ObjectConverter.java:26-52`).
- core 에는 BouncyCastle / BC-FIPS 를 최우선 공급자로 끼운 추가 시험 묶음이 있다(`webauthn4j-core/build.gradle.kts` 의 `testWithBouncyCastle`, `testWithBouncyCastleFIPS` — 해당 파일 28행·55행 부근).

---

## 2. 등록 흐름 (Registration)

### 2.1 입구
- `WebAuthnManager` 는 등록·인증 매니저 둘을 들고 위임한다(`WebAuthnManager.java:65-66`). 등록 입구: `verifyRegistrationResponseJSON(String|InputStream, RegistrationParameters)` (`:238`, `:251`), `verify(RegistrationRequest, …)` (`:265`), `verify(RegistrationData, …)` (`:286`).
- `WebAuthnRegistrationManager` 생성자는 형식별 `AttestationStatementVerifier` 목록, `CertPathTrustworthinessVerifier`, `SelfAttestationTrustworthinessVerifier`, `CustomRegistrationVerifier` 목록을 받아 `RegistrationDataVerifier` 를 만든다(`WebAuthnRegistrationManager.java:88-104`).
- `createNonStrictWebAuthnRegistrationManager()` 는 모든 형식에 **Null 검증기**를 끼운다(`:195-207`). Null 검증기는 형식이 맞는지만 보고 `AttestationType.NONE` 을 돌려준다(`verifier/attestation/statement/packed/NullPackedAttestationStatementVerifier.java:31-38`). 즉 "non-strict" = 증명(attestation) 서명·체인을 **전혀 안 본다**. 이름대로이지만 쓰는 쪽이 알아야 한다.

### 2.2 파싱
- JSON 입구: `PublicKeyCredential<AuthenticatorAttestationResponse, …>` 로 Jackson 역직렬화(`WebAuthnRegistrationManager.java:218-221`) → `toRegistrationData` 가 `attestationObject` 바이트를 CBOR 로, `clientDataJSON` 을 `CollectedClientData` 로 변환(`:235-249`).
- `RegistrationRequest` 입구: 같은 변환 + transports·클라이언트 확장 변환(`:258-283`).
- **원본 바이트를 버리지 않는다** — `RegistrationData` 에 `attestationObjectBytes`, `clientDataBytes` 를 같이 싣는다(`:241-248`). 서명 검증은 재직렬화가 아니라 원본 바이트 위에서 한다(인증 쪽 `AssertionSignatureVerifier.java:63-67` 가 원본 `authenticatorDataBytes` + `clientDataHash` 를 이어 붙인다).

### 2.3 `RegistrationDataVerifier.verify` — 순서대로
파일: `webauthn4j-core/src/main/java/com/webauthn4j/verifier/RegistrationDataVerifier.java`. 코드 주석이 `//spec| StepN` 으로 W3C WebAuthn L3 §7.1 단계를 달아 둔다. 아래 "§7.1" 열은 **그 주석**을 옮긴 것이다(스펙 원문과 대조하지는 않았다).

| # | 검사 | §7.1 (주석) | 위치 | 실패 예외 |
|---|---|---|---|---|
| 0 | 빈 값·Bean 제약 | Step5 앞 | `:125-126` `BeanAssertUtil.validate` | `ConstraintViolationException` 계열 |
| 0' | 등록인데 `attestedCredentialData` 없음 | — | `:141-143` | `ConstraintViolationException` |
| 1 | `C.type == webauthn.create` | Step7 | `:161-163` | `InconsistentClientDataTypeException` |
| 2 | challenge 일치 | Step8 | `:167` → `internal/ChallengeVerifier.java:44-70` | 서버 challenge 없음 `MissingChallengeException`(`:50-52`), 불일치 `BadChallengeException`(`:65-69`) |
| 3 | origin | Step9 | `:171` → `OriginVerifierImpl.java:59-72` (`ServerProperty.getOriginPredicate().test`) | `BadOriginException` |
| 4 | tokenBinding (L2 호환용) | L2 Step10 | `:178` | `TokenBindingException` |
| 5 | crossOrigin / topOrigin | Step10-11 | `:187` `TopOriginVerifier` | `CrossOriginException`, `BadTopOriginException` (내부 확인 못 함) |
| 6 | rpIdHash == SHA-256(rpId) | Step14 | `:199` → `internal/RpIdHashVerifier.java:44-60` | `BadRpIdException` |
| 7 | UP / UV 플래그 | Step15-16 | `:209` → `internal/UPUVFlagsVerifier.java:45-56` | `UserNotPresentException` / `UserNotVerifiedException` |
| 8 | BE=0 인데 BS=1 이면 거부 | Step17 | `:213` → `internal/BEBSFlagsVerifier.java:18-22` | `IllegalBackupStateException` |
| 9 | 공개키 `alg` ∈ `pubKeyCredParams` | Step20 | `:229-231` | `NotAllowedAlgorithmException` 로 추정 — `COSEAlgorithmIdentifierVerifier` 내부 확인 못 함 |
| 10 | 증명문 형식 선택·검증·신뢰도 | Step21-23 | `:235` → `AttestationVerifier.java:69-134` (4절) | 형식별 |
| 11 | credentialId ≤ 1023 바이트 | Step24 | `:239`, 상한 `:61`, 조정 `:296-298` | `CredentialIdTooLongException` (이름으로 추정) |
| 12 | 클라이언트/인증기 확장 | Step27 | `:270-272` | `UnexpectedExtensionException` (내부 확인 못 함) |
| 13 | 사용자 정의 검증기 | Step28 뒤 | `:279-281` | 구현이 던지는 것 |

세부:
- challenge 비교는 `MessageDigest.isEqual` (상수 시간) (`ChallengeVerifier.java:65`). rpIdHash 는 `Arrays.equals` — 주석이 "rpIdHash 는 공격자도 아는 값이라 타이밍 공격 대비가 필요 없다" 고 이유를 적는다(`RpIdHashVerifier.java:53-55`).
- UP 는 **설정값**이다: `isUserPresenceRequired && !isFlagUP()` 일 때만 던진다(`UPUVFlagsVerifier.java:47`). 주석이 conditional mediation 일 때 호출자가 `userPresenceRequired=false` 로 넘기라고 한다(`RegistrationDataVerifier.java:202-205`). 인자를 안 주는 생성자의 기본은 `true` (`data/CoreRegistrationParameters.java:65`).
- 라이브러리 범위 밖이라고 **명시**한 단계: Step18-19(BE/BS 를 정책에 쓰기, `:215-223`), Step25(credentialId 중복 등록 검사, `:241-243`), Step26(자격 레코드 만들기·저장, `:245-258`).

---

## 3. 인증 흐름 (Authentication)

파일: `webauthn4j-core/src/main/java/com/webauthn4j/verifier/AuthenticationDataVerifier.java` (주석은 L3 §7.2 를 단다, `:75-80`).

| # | 검사 | §7.2 (주석) | 위치 |
|---|---|---|---|
| 1 | credential.id ∈ allowCredentials (목록이 주어졌을 때만) | Step5 | `:115-117` → `internal/CredentialIdVerifier.java:13-19`, `NotAllowedCredentialIdException` |
| — | 사용자 식별·레코드 찾기 | Step6 | **범위 밖** (`:119-128`) |
| 2 | 인증인데 `attestedCredentialData` 가 있으면 거부 | — | `:150-152` |
| 3 | `C.type == webauthn.get` | Step10 | `:164-166` |
| 4 | challenge | Step11 | `:170` (등록과 같은 `ChallengeVerifier`) |
| 5 | origin | Step12 | `:174` |
| 6 | crossOrigin / topOrigin | Step13-14 | `:183` |
| 7 | tokenBinding (L2 호환) | L2 Step14 | `:190` |
| 8 | rpIdHash | Step15 | `:194` |
| 9 | UP / UV | Step16-17 | `:204` |
| 10 | BE=0 ∧ BS=1 거부 | Step18 | `:208` |
| 11 | 저장된 BE 와 현재 BE 일치 | Step19 | `:218` → `internal/BEFlagVerifier.java:22-41`, `BadBackupEligibleFlagException` |
| 12 | **서명 검증** | Step20-21 | `:224` → `internal/AssertionSignatureVerifier.java:52-85` |
| 13 | **signCount** | Step22 | `:228-249` |
| 14 | 확장 | Step23 | `:261-263` |
| 15 | 레코드 갱신 | Step24 | `:274` → `updateRecord` `:288-300` |
| 16 | 사용자 정의 검증기 | — | `:276-278` |

### 3.1 서명 검증
- 서명 대상 = `authenticatorData 원본 바이트 ‖ SHA-256(clientDataJSON)` (`AssertionSignatureVerifier.java:63-67`).
- 공개키는 **호출자가 넘긴 레코드**의 `getAttestedCredentialData().getCOSEKey()` (`AuthenticationDataVerifier.java:224`). 알고리즘은 COSE 키의 `alg` 에서 정한다(`AssertionSignatureVerifier.java:73`).
- 검증 중 예외(`IllegalArgumentException`, `SignatureException`, `InvalidKeyException`, **모든 `RuntimeException`**)는 삼키고 `false` → `BadSignatureException` (`:78-84`, `:58-60`). 원인은 debug 로그로만 남는다. "왜 틀렸나" 가 호출자에게 안 올라간다는 대가가 있다.

### 3.2 signCount (카운터 역행)
- 둘 중 하나라도 0 이 아닐 때만 본다(`:230`). 제시값 > 저장값이면 통과(`:233-237`), **그 밖(같거나 작으면)** `maliciousCounterValueHandler.maliciousCounterValueDetected(...)` (`:246-248`).
- 기본 처리기 `DefaultCoreMaliciousCounterValueHandler` 는 **항상 `MaliciousCounterValueException` 을 던진다** (`DefaultCoreMaliciousCounterValueHandler.java:33-38`; 필드 기본값 `AuthenticationDataVerifier.java:64`).
- **바꿀 수 있다**: `setMaliciousCounterValueHandler(...)` (`:310-313`). 인터페이스 주석이 "예외·경고 기록·기타 완화" 를 구현 선택으로 둔다(`CoreMaliciousCounterValueHandler.java:28-30`). 스펙 주석도 "실패시킬지는 RP 마음" 이라 옮겨 둔다(`AuthenticationDataVerifier.java:239-245`).
- 시험: 저장 카운터를 100 으로 올려 두고 인증하면 `MaliciousCounterValueException` (`webauthn4j-core/src/test/java/integration/scenario/webauthn/FIDOU2FAuthenticatorAuthenticationTest.java:511,528-530`).

### 3.3 레코드 갱신 — 라이브러리는 **메모리 객체만** 바꾼다
- `updateRecord` 가 `setCounter(signCount)`, `setBackedUp(BS)`, `uvInitialized` 가 거짓/없음이면 UV 로 채움(`:288-300`). 저장은 호출자 몫이다.
- **관찰(스펙 주석과 어긋남)**: 코드 스스로 옮긴 스펙 문장이 "추가 보안 검사가 있으면 상태 갱신은 그 뒤로 미뤄야(SHOULD)" 라고 하는데(`:272-273`), 실제로는 `updateRecord` (`:274`) 가 사용자 정의 검증기(`:276-278`) **보다 먼저** 돈다. 사용자 검증기가 던져도 넘겨준 레코드 객체의 카운터는 이미 올라가 있다. 호출자가 예외 뒤에 그 객체를 저장하지 않으면 무해하고, 캐시에 공유된 객체를 넘겼다면 영향이 있다. 의도인지는 확인 못 함.

### 3.4 패스키 BE/BS
- 등록·인증 모두 "BE 없이 BS" 를 거부(`BEBSFlagsVerifier.java:19-21`).
- 인증에서 레코드 BE 가 `null` 이면 검사 안 함, 참이면 현재 BE 필수, 거짓이면 현재 BE 금지(`BEFlagVerifier.java:27-39`). 레코드가 `CoreCredentialRecord` 가 아니면(옛 `Authenticator`) 통째로 건너뛴다(`:23`).
- BS 를 정책(예: "백업된 패스키면 추가 인증") 으로 쓰는 것은 사용자 정의 검증기로 하라고 주석이 말한다(`AuthenticationDataVerifier.java:217`).

---

## 4. 증명(Attestation)

### 4.1 형식
core 가 아는 형식 식별자(`FORMAT` 상수): `fido-u2f`, `tpm`, `apple`, `packed`, `android-key`, `android-safetynet`, `none` (`data/attestation/statement/*AttestationStatement.java` 각 35행 전후), appattest 모듈에 `apple-appattest`. 검증기는 `verifier/attestation/statement/{u2f,tpm,apple,packed,androidkey,androidsafetynet,none}/` 에 형식마다 실검증기 + `Null*` 판이 짝으로 있다. `compound` 형식 클래스는 이 트리에서 **찾지 못했다**(grep `FORMAT =` 결과 기준).

### 4.2 `AttestationVerifier.verify` 순서 (`verifier/AttestationVerifier.java`)
1. 등록된 검증기 목록을 차례로 돌며 `supports()` 첫 번째 것의 `verify()` 로 `AttestationType` 을 얻는다(`:146-151`). 아무도 지원 안 하면 `BadAttestationStatementException` (`:153`). → **`none` 을 받아줄지는 `NoneAttestationStatementVerifier` 를 목록에 넣었느냐로 정해진다**(주석 `:94-95`).
2. `fido-u2f` 면 AAGUID 가 0 이어야 한다(`:136-145`, `BadAaguidException`).
3. 유형별 신뢰 평가(`:98-132`): `SELF` → `SelfAttestationTrustworthinessVerifier`; `BASIC`/`ATT_CA` → `CertPathTrustworthinessVerifier.verify(aaguid, stmt, timestamp)`; `NONE` → 아무것도 안 함.

`packed` 예시(`packed/PackedAttestationStatementVerifier.java`): x5c 가 있으면 서명 검증 → 인증서 AAGUID 와 authData AAGUID 대조 → `BASIC` (`:93-116`); 없으면 self — 증명문 `alg` 와 자격 키 알고리즘 일치, 자격 키로 서명 검증 → `SELF` (`:134-148`).

### 4.3 신뢰앵커
- `DefaultCertPathTrustworthinessVerifier(TrustAnchorRepository)` (`verifier/attestation/trustworthiness/certpath/DefaultCertPathTrustworthinessVerifier.java:48`). U2F 면 인증서의 subjectKeyIdentifier 로, 아니면 AAGUID 로 앵커를 찾는다(`:63-78`). 없으면 `TrustAnchorNotFoundException`. 이후 PKIX 검증, 검증 시각은 등록 객체의 `timestamp`(`Instant.now()` 기본, `CoreRegistrationObject.java:71`).
- **기본값: 폐기(revocation) 검사 꺼짐**, full chain 금지 꺼짐, policy qualifier 거부 꺼짐 (`DefaultCertPathTrustworthinessVerifier.java:44-46`; `CertPathTrustworthinessVerifierBase.java:35-37`).
- 앵커 저장소 구현: core 의 `KeyStoreTrustAnchorRepository` (`anchor/`), metadata 의 `MetadataBLOBBasedTrustAnchorRepository`, `MetadataStatementsBasedTrustAnchorRepository`, `AggregatingTrustAnchorRepository` (`webauthn4j-metadata/.../metadata/anchor/`).

### 4.4 MDS3 (metadata 모듈)
- `FidoMDS3MetadataBLOBProvider`: 기본 끝점 `https://mds.fidoalliance.org/` (`FidoMDS3MetadataBLOBProvider.java:39`). 받아서 파싱 → BLOB JWS 서명 검사(`:84-86`) → 헤더 x5c 체인을 주어진 앵커로 검사(`:87`, `:91-100`). 이쪽은 **폐기 검사 기본 켜짐** (`:45`).
- 캐시: `nextUpdate` 가 오늘 이하이고 마지막 갱신이 오늘 이전이면 다시 받는다(`CachingMetadataBLOBProvider.java:39-40`).
- 상태 보고 필터: `ATTESTATION_KEY_COMPROMISE`, `USER_VERIFICATION_BYPASS`, `USER_KEY_*_COMPROMISE`, `REVOKED`, 알 수 없는 상태 → 제외; `NOT_FIDO_CERTIFIED`, `SELF_ASSERTION_SUBMITTED` 는 설정으로 허용(기본 거부) (`metadata/util/internal/MetadataBLOBUtil.java:13-58`; 기본값 `MetadataBLOBBasedMetadataStatementRepository.java:36-37`).
- **관찰(확인 필요)**: 이 상태 필터는 AAGUID 로 찾는 `find(AAGUID)` 에만 걸려 있고(`MetadataBLOBBasedMetadataStatementRepository.java:48`), U2F 용 `find(byte[] attestationCertificateKeyIdentifier)` 에는 **없다**(`:55-62`). 그런데 U2F 증명은 바로 그 경로로 앵커를 찾는다(`DefaultCertPathTrustworthinessVerifier.java:66`, → `MetadataBLOBBasedTrustAnchorRepository.java:45-46`). 코드만 보면 MDS 가 `REVOKED` 로 표시한 U2F 인증기도 앵커가 나온다. 의도인지, 다른 층에서 막는지는 확인 못 함. 시험으로 재지 않았다.

---

## 5. 확장점과 예외 체계

### 5.1 확장점
- `CustomRegistrationVerifier.verify(RegistrationObject)` / `CustomAuthenticationVerifier.verify(AuthenticationObject)` — 단일 메서드 인터페이스(`verifier/CustomRegistrationVerifier.java:8-10`, `CustomAuthenticationVerifier.java:8-10`). 내장 검사가 **다 끝난 뒤** 목록 순서로 돈다(등록 `RegistrationDataVerifier.java:279-281`, 인증 `AuthenticationDataVerifier.java:276-278`). 앞 검사를 바꾸거나 건너뛸 수는 없다 — 덧붙이기만 된다.
- `AttestationStatementVerifier` (`verify` + `supports`, `attestation/statement/AttestationStatementVerifier.java:27-33`) — 형식 추가·교체.
- `CertPathTrustworthinessVerifier`, `SelfAttestationTrustworthinessVerifier`, `TrustAnchorRepository` — 신뢰 정책.
- `OriginVerifier` 교체(`setOriginVerifier`, 등록 `:288-290`, 인증 `:319-321`); `ServerProperty` 의 `OriginPredicate` (`server/OriginPredicate.java` 등).
- `CoreMaliciousCounterValueHandler` (3.2).
- `setMaxCredentialIdLength` (`RegistrationDataVerifier.java:296-298`).

### 5.2 예외
- 뿌리: `WebAuthnException extends RuntimeException` (`util/exception/WebAuthnException.java:21`) — **전부 unchecked**.
- 두 갈래: 파싱 실패 `DataConversionException extends WebAuthnException` (`converter/exception/DataConversionException.java:22`), 검증 실패 `abstract VerificationException extends WebAuthnException` (`verifier/exception/VerificationException.java:25`).
- 검증 실패는 **검사마다 전용 하위 클래스** (`verifier/exception/` 에 28개): `BadChallengeException`, `MissingChallengeException`, `BadOriginException`, `BadTopOriginException`, `CrossOriginException`, `BadRpIdException`, `UserNotPresentException`, `UserNotVerifiedException`, `IllegalBackupStateException`, `BadBackupEligibleFlagException`, `BadSignatureException`, `MaliciousCounterValueException`, `NotAllowedCredentialIdException`, `NotAllowedAlgorithmException`, `BadAlgorithmException`, `BadAaguidException`, `BadAttestationStatementException`, `CertificateException`, `TrustAnchorNotFoundException`, `SelfAttestationProhibitedException`, `KeyDescriptionValidationException`, `PublicKeyMismatchException`, `CredentialIdTooLongException`, `InconsistentClientDataTypeException`, `TokenBindingException`, `UnexpectedExtensionException`, `ConstraintViolationException` 등.
- 일부는 대조에 쓴 값을 실어 나른다: `BadChallengeException(msg, expected, actual)` (`ChallengeVerifier.java:68`), `BadRpIdException(msg, expected, actual)` (`RpIdHashVerifier.java:58`), `MaliciousCounterValueException(msg, stored, presented)` (`DefaultCoreMaliciousCounterValueHandler.java:37`).
- 설계 요지: **첫 실패에서 멈춘다(fail-fast)** — 여러 사유를 모아 돌려주지 않는다. 형(type)이 곧 사유 코드다.

---

## 6. 라이브러리가 하지 **않는** 것 — 앱이 대야 하는 것

- **challenge 발급·저장·1회 소비**: 없다. 서버 challenge 는 호출자가 `ServerProperty` 에 넣어 온다(`server/CoreServerProperty.java:35-43,63-64`). 없으면 `MissingChallengeException` (`ChallengeVerifier.java:50-52`) — 비교만 한다. **같은 challenge 로 두 번 검증해도 라이브러리는 모른다** — 재사용 방지(세션에서 지우기 등)는 앱 몫이다. `DefaultChallenge()` 는 `UUID.randomUUID()` 16바이트를 쓴다(`data/client/challenge/DefaultChallenge.java:47-52`) — 편의 생성기일 뿐 저장소가 아니다.
- **자격 레코드 저장·조회**: 없다. `ChallengeRepository`, `CredentialRecordRepository`, `HttpSession` 같은 것을 main 소스 전체에서 grep 해도 안 나온다. 앱은 `CredentialRecord` (`credential/CredentialRecord.java:10`, `CoreCredentialRecord` + `Authenticator` 확장) 구현을 만들어 `AuthenticationParameters` 로 넘긴다(`data/AuthenticationParameters.java:40`). 인터페이스가 요구하는 것: `getAttestedCredentialData()`, `getCounter()/setCounter()` (`authenticator/CoreAuthenticator.java:40,56,63`), `isUvInitialized/setUvInitialized`, `isBackupEligible/setBackupEligible`, `isBackedUp/setBackedUp` (`credential/CoreCredentialRecord.java:17-55`), `getClientData()` (`CredentialRecord.java:16`). 기본 구현 `CredentialRecordImpl` 이 있다.
- **사용자 식별(§7.2 Step6)**, **credentialId 중복 등록 검사(§7.1 Step25)**, **레코드 생성·저장(Step26/28)**, **갱신된 카운터 저장**: 전부 범위 밖이라고 주석이 명시(2.3, 3절).
- 세션·HTTP·사용자 저장소: 없다. 그 층은 Spring Security / webauthn4j-spring-security / Quarkus 가 한다(`README.md:40-44`, 통합시험 모듈 이름).

---

## 7. 시험

- 파일 수(`*Test.java`, `src/test` 기준): core 275, metadata 37, core-async 14, appattest 11, metadata-async 7, test 3, integration-tests 4.
- core 시험은 단위(`src/test/java/com/webauthn4j/...`)와 시나리오(`src/test/java/integration/scenario/webauthn/` — `AttestationVariationTest`, `FIDOU2FAuthenticatorAuthenticationTest`, `CustomAuthenticationValidationTest`, `CrossOriginRegistrationVerificationTest` 등 12개) 두 층.
- **가짜 인증기 (`webauthn4j-test` 모듈, main 소스로 배포)**:
  - `EmulatorUtil` 이 형식별 싱글턴을 준다: `PACKED_AUTHENTICATOR`, `ANDROID_KEY_AUTHENTICATOR`, `ANDROID_SAFETY_NET_AUTHENTICATOR`, `TPM_AUTHENTICATOR`, `FIDO_U2F_AUTHENTICATOR`, `NONE_ATTESTATION_AUTHENTICATOR` (`webauthn4j-test/src/main/java/com/webauthn4j/test/EmulatorUtil.java:28-33`).
  - `WebAuthnModelAuthenticator` 가 CTAP2 authenticatorMakeCredential/GetAssertion 을 흉내 낸다. 카운터는 전역 하나이고 `countUpEnabled` 로 증가를 끌 수 있다(`.../authenticator/webauthn/WebAuthnModelAuthenticator.java:77-78,300,413,461-471`; 카운터 모드 선택은 `// TODO: counter mode` 로 남아 있다 `:300`).
  - `ClientPlatform` 이 브라우저 몫(`create()`/`get()`, `CollectedClientData` 생성, origin/topOrigin)을 맡는다(`.../test/client/ClientPlatform.java:49-206`).
  - 증명 인증서는 `AttestationCertificateBuilder`, `CACertificatePath`, `TestAttestationUtil` 로 즉석 발급(파일 목록 기준, 내부 확인 못 함).
- 시나리오 시험 한 판의 모양: 에뮬레이터로 등록 → `TestDataUtil.createAuthenticator(attestationObject)` 로 레코드 만들기 → 값을 비틀기(예: `setCounter(100)`) → `assertThrows(해당 예외)` (`FIDOU2FAuthenticatorAuthenticationTest.java:505-530`).
- 외부 적합성: README 가 FIDO2 Test Tools 필수 항목 전부 + Android Key 선택 항목 통과를 주장한다(`README.md:13-15`). 이 주장은 확인 못 함(돌리지 않았다).

---

## 8. WALP 에 옮길 수 있는 것 / 없는 것

붙여 넣은 제안: "WebAuthn4J → WALP 공개키 인증 검증: 챌린지, 공개키, 서명 검증".

### 8.0 먼저 판정: 제안 그대로는 **옮길 자리가 없다**
WebAuthn 은 "**원격의, 믿을 수 없는** 당사자가 **미리 등록된 개인키**를 지금 쥐고 있음을 증명" 하는 규약이다. 성립하려면 셋이 있어야 한다: (a) 사용자·자격 레코드, (b) 신뢰 경계 너머의 상대, (c) 비밀 키.

WALP 에는 셋 다 없다.
- (a) 사용자·키 개념 없음. 코어의 입력은 `Observation` / `MapHint` / 명령 문자열뿐이다(`walp/include/walp/interfaces.hpp:14-33,201-249`).
- (b) 관측을 주는 `IPlatform` 은 같은 프로세스 안의 C++ 객체다(`interfaces.hpp:242-249`); 시뮬 `World` 가 그 자리다(`walp/sim/world.cpp:215-234`). 관측 위조자를 가정한 위협 모형은 README 에 없다(`walp/README.md:1-45`). 코어는 "파일·OS 를 안 부른다(R1)" (`README.md:44`).
- (c) 서명할 키 없음. 코어는 `-fno-exceptions -fno-rtti` 이고 암호 라이브러리를 쓰지 않는다(`README.md:44`).

그래서 "WALP 에 공개키 인증을 넣자" 는 **풀 문제가 없는 곳에 해법을 넣는 것**이다. 아래는 그 대신 WebAuthn4J 에서 **모양**을 빌릴 만한 곳이 실제로 있는지를 코드로 본 것이다.

### 8.1 옮길 만한 것 ① — signCount 의 "단조 증가" 검사 ↔ WALP 관측 신선도 (가장 구체적)
- WebAuthn4J: 절대 시간 창이 아니라 **직전에 받아들인 값보다 커야** 한다(`AuthenticationDataVerifier.java:228-248`). 역행은 처리기로 보낸다.
- WALP: `StateEstimator::update` 의 거부 조건은 `!o.valid || o.tick + max_age_ < s.now` 하나다(`walp/src/state.cpp:88`). `max_age` 기본 2 (`walp/include/walp/core.hpp:87`, 실사용 `tools/harness.cpp:85,160`, `tests/core_test.cpp:51`). 받아들이면 `last_obs_id` 와 `last_fresh_tick` 을 **덮어쓰기만** 하고(`state.cpp:95,108`) 비교는 안 한다. `last_obs_id` 를 읽는 곳은 기록용 `executive.cpp:55` 한 군데뿐이다.
- 뜻: **창 안에서 순서가 뒤집힌 관측**(tick 이 `last_fresh_tick` 보다 작지만 `now-2` 이상)은 거부되지 않고 자세·배터리를 **과거로 되돌린다**(`state.cpp:106-108`). signCount 로 치면 "counter ≤ stored" 를 통과시키는 꼴이다.
- 그런데 **지금 시뮬은 이 경우를 안 만든다**: 지연 고장은 항상 `history_[size-4]`, 주석상 "3틱 전 것" 을 준다(`walp/sim/world.cpp:226-230`). 3 > `max_age`=2 이므로 늘 창 밖이라 거부된다. 즉 현 시험 67개(README 14행)가 초록이어도 이 틈은 **재지 않은 것**이다 — 틈이 무해하다는 증거가 아니다. (실제로 해가 되는지는 1~2틱 지연을 주입해 재 봐야 안다. 나는 재지 않았다.)
- 옮길 모양: `update` 에 `o.tick < s.last_fresh_tick` (또는 `o.obs_id <= s.last_obs_id`) 이면 `stale` 로 다루는 한 줄 + `sim/world.cpp` 에 1~2틱 지연 고장 하나. 단 주의: 시뮬의 지연 관측은 `obs_id` 를 **새로 매긴다**(`world.cpp:229` "번호는 새로 받지만 내용과 시각은 3틱 전 것"). 그러므로 `obs_id` 단조성으로는 이 고장을 **못 잡는다** — 비교 기준은 `tick` 이어야 한다. signCount 를 그대로 베끼면(번호 비교) 틀린다.
- WebAuthn4J 에서 하나 더 빌릴 점: 역행 처리를 **정책 객체로 분리**했다(`setMaliciousCounterValueHandler`, `:310-313`). WALP 는 이미 조건→행동을 데이터로 두고 `RC_STALE` 을 잠갔다(`interfaces.hpp:101,115-125`). 역행을 `RC_STALE` 로 흘려보내면 새 규칙 없이 기존 잠긴 경로를 탄다.

### 8.2 부분적으로 — CRC 저장 ↔ 서명
- WALP 정책 이미지: `"WALP" | 판 | n | cur | 버전들 | crc32` (`walp/src/learner.cpp:247-259`), 적재 시 CRC·판·n 검사 + **현재 버전**만 승인 상태·불변식 재검사(`learner.cpp:262-280`). 헤더 주석대로 목적은 **손상 감지**다(`core.hpp:264-265`); 시험도 "쓰다 만 임시 파일", "CRC 깨진 파일" 을 본다(`tests/core_test.cpp:267-284`).
- CRC32 는 공개 알고리즘(`learner.cpp:238-245`)이라 파일을 쓸 수 있는 누구나 다시 계산한다. **위조 방지가 아니다.** 서명(또는 HMAC)이면 위조 방지가 된다.
- 그런데 필요한가? 정책 파일을 쓰는 것은 호스트의 `save_store_atomic` (`tools/harness.cpp:655-668`)이고, 부르는 곳은 시험뿐이다(grep: `tests/core_test.cpp:267-284`). 파일을 바꿀 수 있는 공격자는 바이너리도 바꿀 수 있다. **위협 모형이 없는 한 서명은 과잉**이다. 그래서 "서명으로 바꾸자" 가 아니라 아래 두 가지가 더 값싸고 실제 틈에 맞는다.
  - (i) **검증이 모든 경로에 걸려 있지 않다**: `deserialize` 는 현재 버전만 불변식을 본다(`learner.cpp:273-276`). `rollback(id)` 는 `status==1` 만 보고 불변식은 안 본다(`learner.cpp:227-232`). CRC 가 맞는(=손상은 아니지만 다른 판 코드가 쓴, 혹은 손으로 고친) 이미지 안의 비현재 승인 버전은 `rollback` 한 번으로 **재검사 없이** 현재가 된다. WebAuthn4J 가 매 호출마다 전 단계를 다시 도는 것(`RegistrationDataVerifier.verify` 전체)과 대비된다. 적재 때 모든 `status==1` 버전에 `params_invariants_ok` 를 걸거나 `rollback` 안에서 다시 거는 것이 옮길 모양이다.
  - (ii) **다운그레이드**: 이미지에 단조 세대 번호가 없어 옛 파일을 되돌려 놓으면 그대로 적재된다(`learner.cpp:268-285` 에 이전 세대 비교 없음). signCount 의 "저장값보다 커야" 와 같은 모양이다. 다만 옛 파일도 당시엔 검증을 통과한 정책이므로 해가 되는지는 운영 방식에 달렸다 — 판단 못 함.

### 8.3 이미 있는 것 — 순서 있는 검증 사슬
- WebAuthn4J 의 "고정 순서, 첫 실패에서 멈춤, 실패마다 전용 형" (2.3, 5.2) 은 WALP `SafetySupervisor::authorize` 가 **이미 그렇게 생겼다**: 행동 종류별로 고장→낡음→경계→위험→벽→미지→칸 신선도→회피 구역→귀환 에너지 순으로 보고, 첫 실패에서 `reason = RS_*` 를 채워 `false` (`walp/src/state.cpp:205-264`). 사유 코드가 예외 형의 자리다. 하네스는 "거부에는 사유가 있어야" 를 기록 검사로 강제한다(`tools/harness.cpp:55`).
- 옮길 게 없다. 굳이 하나 꼽자면 WebAuthn4J 의 `Custom*Verifier` 는 **내장 검사 뒤에 덧붙기만** 하고 앞 검사를 못 끈다(5.1). WALP 도 잠긴 규칙을 학습이 못 바꾼다(`interfaces.hpp:125`, `learner.cpp:62-63`). 같은 원칙이 이미 있다.
- 오히려 WebAuthn4J 에서 **옮기면 안 되는** 것: 3.3 의 "상태 갱신이 추가 검사보다 먼저" 순서. WALP `update` 는 거부 판정(`state.cpp:88-92`) 뒤에만 상태를 바꾼다 — 지금이 맞다.

### 8.4 챌린지/논스 — 자리 없음 (한 곳만 검토)
- 챌린지는 "상대가 **지금** 응답했다(재생 아님)" 를 원격 상대에게 증명시키는 장치다. WALP 코어의 관측은 같은 프로세스의 플랫폼이 주므로 재생 공격자가 없다(8.0 b).
- 유일하게 경계가 있는 곳은 SE 도구 실행: 부모 `se_router.execute` 가 등록 여부·쓰기 금지 종류를 검사한 뒤(`walp/se_router.py:338-342`) 자식 `se_exec.py` 를 띄운다(`:347-348`). 자식은 받은 이름으로 `getattr(bot_tools, name)` 을 **다시 검사 없이** 부른다(`walp/se_exec.py:101-104`). 자식 입력은 부모가 표준입력으로만 주므로 챌린지로 막을 재생 공격은 없다. 굳이 말하면 "검사는 부모에만 있다" 는 단일 지점 신뢰이고, 자식에서 `is_se_tool` 표시(`se_exec.py:30`)를 확인하는 한 줄이 방어 심화가 된다 — 이것은 WebAuthn 과 무관한 일반 원칙이다. 또 `execute` 는 `allow_write` 를 자식 JSON 에 싣지 않아(`se_router.py:347`) 자식의 `cmd:` 경로는 늘 쓰기 금지로 돈다(`se_exec.py:98`) — 의도인지 확인 못 함.

### 8.5 비유가 깨지는 곳 (정리)
| WebAuthn4J | WALP 의 가장 가까운 것 | 왜 안 맞나 |
|---|---|---|
| 사용자·자격 레코드 | 없음 | 인증할 주체가 없다 |
| 공개키 서명 검증 | 없음 | 비밀 키도, 신뢰 경계 너머 상대도 없다 |
| challenge | 없음 | 재생 공격자가 없다(같은 프로세스) |
| signCount 단조성 | `tick` 신선도 창(`state.cpp:88`) | **모양은 맞다**. 다만 `obs_id` 가 아니라 `tick` 으로 비교해야 한다(`world.cpp:229`) |
| 증명 신뢰앵커·MDS | 없음 | 장치 출처를 증명할 제조사가 없다 |
| 레코드 갱신은 앱이 저장 | `PolicyStore` + 호스트 저장(`harness.cpp:655-678`) | 모양 비슷. 저장 무결성은 CRC 로 충분(위협 모형 없음) |
| 검증 사슬·전용 실패형 | `authorize` + `RS_*` | 이미 있다 |

### 8.6 한 줄 결론
"공개키 인증 검증을 WALP 에" 는 **버린다** — 주체·키·경계가 없다. WebAuthn4J 에서 실제로 가져갈 것은 **signCount 식 단조 검사 하나**(관측 `tick` 역행 거부 + 그 고장을 시뮬에 주입) 이고, 덤으로 **정책 이미지 적재·롤백 경로 전부에 불변식 재검사**다. 둘 다 암호가 아니라 순서·일관성 문제다. 두 틈 모두 **코드를 읽어 본 관찰이고 재지 않았다** — 해가 되는지는 1~2틱 지연 주입과 불변식 위반 버전을 넣은 이미지로 재 봐야 안다.
