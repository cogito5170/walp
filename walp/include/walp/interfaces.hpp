// walp/interfaces.hpp — 모듈 경계(명세 §3.2) + 그 사이를 오가는 고정 크기 구조체
#pragma once
#include "types.hpp"

namespace walp {

// ---------------------------------------------------------------- 관측 (플랫폼 → 코어)
struct Detection {
    std::uint8_t x, y;
    std::uint8_t type, color, brand;
    std::uint8_t conf;   // 0..100 (센서가 준 값, 코어는 투표 가중에 쓰지 않는다)
};

struct Observation {
    std::uint32_t obs_id;        // 플랫폼이 매기는 증거 번호의 뿌리
    Tick tick;                   // 이 관측이 찍힌 시각(지연 고장이면 now 보다 오래됐다)
    bool valid;                  // 누락 고장이면 false
    std::uint8_t x, y;           // 자세(위치)
    std::int32_t battery;
    std::uint8_t view_kind[kViewN];  // CellKind, 자세 중심 (2R+1)^2, 범위 밖은 kWall
    std::uint8_t view_zone[kViewN];
    std::uint8_t n_det;
    Detection det[kMaxDet];
};

// 임무 시작 때 한 번 받는 평면도: 구조 벽과 구역 표지만(장애물·위험·물체는 없다).
struct MapHint {
    std::uint8_t w, h;
    std::uint8_t env_type;       // 0=모름
    std::uint8_t home_x, home_y;
    std::uint8_t wall[kMaxCells];
    std::uint8_t zone[kMaxCells];
};

// ---------------------------------------------------------------- 믿음 상태
struct Hypothesis {
    std::uint8_t x, y;
    std::uint16_t v_type[kNumObj];
    std::uint16_t v_color[kNumColor];
    std::uint16_t v_brand[kNumBrand];
    std::uint16_t n;
    std::uint16_t observes_spent;   // 이 가설 때문에 '다시 관측'한 횟수
    bool dismissed;                 // 목표가 아니라고 판정했거나 포기했다
    EvidenceId first_ev, last_ev;
};

struct BeliefState {
    std::uint8_t w, h, env_type;
    std::uint8_t home_x, home_y;
    std::uint8_t kind[kMaxCells];
    std::uint8_t zone[kMaxCells];
    std::uint16_t seen_tick[kMaxCells]; // 0 = 본 적 없음 (관측 시각 + 1, 기한 상한 5000 이라 16비트로 족하다)
    std::uint8_t scanned[kMaxCells];// 물체를 찾아 훑어봤나
    std::uint8_t x, y;
    std::int32_t battery;
    Tick now;
    Tick last_fresh_tick;           // 마지막 '현재로 인정한' 관측 시각
    std::uint32_t last_obs_id;
    bool stale;                     // 이번 스텝의 관측을 현재 상태로 못 쓴다
    std::uint16_t stale_run;
    std::uint8_t n_hyp;
    Hypothesis hyp[kMaxHyp];
    EvidenceId next_ev;
    // 행동 결과 감시(FDIR)
    bool actuator_fault;
    std::uint8_t move_mismatch_run;
    std::int8_t expect_dx, expect_dy;
    bool expect_move;
};

// ---------------------------------------------------------------- 사례·검색
struct CaseRecord {
    CaseId id;
    std::uint16_t task_type;
    std::uint16_t environment_type;
    std::uint16_t event_type;       // 여기서는 '목표를 찾은 구역'(Zone)
    std::uint16_t action_type;      // 그 에피소드의 탐색 전략(1=FSM, 2=탐색)
    std::int32_t outcome_code;      // 1=성공
    std::uint32_t duration_ms;      // 틱 수
    std::uint32_t policy_version;
    std::uint16_t target_type;
    std::uint32_t required_attributes;
};

struct CaseBase {
    std::uint16_t n;
    CaseRecord c[kMaxCases];
};

struct RetrievedCases {
    std::uint16_t n;
    CaseId ids[kMaxRetrieved];
    std::uint16_t zone_prior_pm[kNumZones];  // 구역 사전확률, 천분율
    bool used;                               // 사전확률이 균등이 아닌가
};

// ---------------------------------------------------------------- 정책 그래프(조건 → 행동)
// v0.2: 전술 규칙을 코드가 아니라 데이터로 둔다. 우선순위는 아래 순서로 고정이고, 각 조건이
// 부를 수 있는 행동은 kRuleAllowed 로 **타입이 정해져** 있다. 잠긴 규칙(안전 쪽)은 학습이 못 바꾼다.
enum RuleCond : std::uint8_t {
    RC_STALE = 0,        // 관측이 낡았다                       [잠김]
    RC_ACT_FAULT,        // 액추에이터 고장 판정                 [잠김]
    RC_CONFIRMED,        // 목표 확정                            [잠김]
    RC_BATTERY_LOW,      // 귀환 에너지 부족                     [잠김]
    RC_CANDIDATE_NEAR,   // 불확실한 후보가 한 칸 안
    RC_CANDIDATE_FAR,    // 불확실한 후보가 멀리
    RC_BLOCKED,          // 가려던 길을 장애물이 막았다
    RC_DEFAULT,          // 그 밖 — 탐사
    RC_COUNT
};
enum RuleAct : std::uint8_t {
    RA_OBSERVE = 1, RA_HOLD, RA_ABORT, RA_DECLARE, RA_RETURN, RA_APPROACH, RA_SKIP, RA_REPLAN, RA_EXPLORE, RA_COUNT
};
// 조건별 허용 행동(비트 1<<RuleAct). 학습 후보가 이 밖의 행동을 고르면 타입 검사에서 떨어진다.
constexpr std::uint16_t kRuleAllowed[RC_COUNT] = {
    (1u << RA_OBSERVE) | (1u << RA_HOLD),                      // STALE
    (1u << RA_ABORT),                                          // ACT_FAULT
    (1u << RA_DECLARE),                                        // CONFIRMED
    (1u << RA_RETURN),                                         // BATTERY_LOW
    (1u << RA_OBSERVE) | (1u << RA_APPROACH) | (1u << RA_SKIP),// CANDIDATE_NEAR
    (1u << RA_APPROACH) | (1u << RA_SKIP),                     // CANDIDATE_FAR
    (1u << RA_REPLAN) | (1u << RA_HOLD),                       // BLOCKED
    (1u << RA_EXPLORE),                                        // DEFAULT
};
constexpr bool kRuleLocked[RC_COUNT] = {true, true, true, true, false, false, false, true};

// ---------------------------------------------------------------- 정책 파라미터(버전 관리 대상)
struct PolicyParams {
    std::uint8_t k_confirm;        // 확정에 필요한 관측 수
    std::uint8_t theta_pct;        // 속성 다수표 비율 하한
    std::uint8_t observe_max;      // 한 가설에 쓰는 재관측 상한
    std::uint8_t stale_observe_max;// 관측이 낡았을 때 재관측 시도 상한
    std::uint16_t lambda_t;        // 이동 비용 가중(×100)
    std::uint16_t lambda_g;        // 발견 확률 가중(×100)
    std::int32_t reserve_margin;   // 귀환 여유(에너지). 안전층의 하드 여유와 별개
    std::uint16_t prior_weight_pct;// 사례 사전확률을 얼마나 믿나(0..100)
    std::uint16_t max_nodes;       // 계획 탐색 노드 예산
    std::uint8_t depth;            // 계획 탐색 깊이
    std::uint8_t use_uncertainty;  // 0 이면 첫 목격에 확정(ablation)
    std::uint8_t rule_action[RC_COUNT]; // 정책 그래프: 조건마다 부를 행동
    std::uint8_t blocked_wait;     // BLOCKED→HOLD 일 때 기다리는 최대 스텝(넘으면 재계획)
    std::uint8_t blocker_ttl;      // 장애물 기억 수명(틱). 이보다 오래 못 본 장애물 칸은 계획에서 '지나갈 수
                                   // 있을지 모른다' 로 본다(0 = 영영 기억). 안전층은 인접 칸을 늘 새로 보고 막는다.
};

// ---------------------------------------------------------------- 계획
struct SafetyState;
struct Plan {
    std::uint8_t count;
    ActionRequest actions[kMaxPlanActions];
    Decision decision;
    RuleId rule_id;
    EvidenceId evidence_id;
    std::uint16_t reason_code;
    std::uint16_t nodes;
    std::int32_t cost;
    std::uint8_t wx, wy;           // 향하는 곳
    bool has_target;               // wx,wy 가 뜻이 있나(막힘 감지에 쓴다)
};

// 안전층은 계획이 아니라 믿음에서 제 상태를 만든다(계획이 안전 상태를 들고 오게 하지 않는다).
struct SafetyState {
    const BeliefState* belief;
    std::uint32_t constraints;
    Tick now;
};

struct ExecutionResult {
    Status status;
    std::int32_t energy_used;
};

// ---------------------------------------------------------------- 학습
enum Outcome : std::uint8_t {
    O_NONE = 0, O_SUCCESS, O_TIMEOUT, O_FALSE_DECLARE, O_STRANDED, O_ABORT_SAFETY,
    O_ABORT_FAULT, O_ABORT_NO_TARGET, O_COUNT
};

struct EpisodeRecord {
    std::uint8_t outcome;
    std::uint16_t steps;
    std::uint16_t observes;
    std::uint16_t denials;
    std::uint16_t stale_steps;
    std::uint16_t scanned_cells;
    std::uint16_t free_cells;
    std::int32_t energy_left;
    std::uint16_t hyps_dismissed;
    std::uint16_t blocked_steps;    // BLOCKED 조건이 참이던 스텝 수
    std::uint16_t false_declares;   // 평가기가 채운다(코어는 정답을 모른다)
    std::uint32_t policy_version;
};

struct RuleCandidate {
    PolicyParams params;
    std::uint16_t reason;        // 어떤 실패 분류에서 나왔나
    std::uint16_t change_code;   // 무엇을 바꿨나
};

// ---------------------------------------------------------------- 인터페이스
class ICommandParser {
public:
    virtual ~ICommandParser() = default;
    virtual Status parse(const char* input, GoalSpec& output) = 0;
};

class IStateEstimator {
public:
    virtual ~IStateEstimator() = default;
    virtual Status update(const Observation& observation, BeliefState& state) = 0;
};

class IRetriever {
public:
    virtual ~IRetriever() = default;
    virtual Status retrieve(const GoalSpec& goal, const BeliefState& state, RetrievedCases& output) = 0;
};

class IPlanner {
public:
    virtual ~IPlanner() = default;
    virtual Status plan(const GoalSpec& goal, const BeliefState& state, const RetrievedCases& cases,
                        Plan& output) = 0;
};

class ISafetySupervisor {
public:
    virtual ~ISafetySupervisor() = default;
    virtual bool authorize(const ActionRequest& action, const SafetyState& safety,
                           std::uint16_t& reason) = 0;
    // 거부했을 때 안전층이 스스로 내는 행동(제어권 인수). 기본은 제자리 보류.
    virtual ActionRequest fallback(const SafetyState&) { return ActionRequest{kHold, 0, 0, 1}; }
};

class IPolicyLearner {
public:
    virtual ~IPolicyLearner() = default;
    virtual Status propose(const EpisodeRecord& episode, const PolicyParams& current,
                           RuleCandidate* out, int max_out, int& n_out) = 0;
};

class IPlatform {
public:
    virtual ~IPlatform() = default;
    virtual Status map_hint(MapHint& output) = 0;
    virtual Status observe(Observation& output) = 0;
    virtual Status execute(const ActionRequest& action, ExecutionResult& result) = 0;
    virtual Tick now() const = 0;
};

}  // namespace walp
