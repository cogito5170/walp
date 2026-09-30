// walp/types.hpp — 공통 자료형 (명세 §3.1 을 따르고, 자리를 채운다)
//
// 코어는 힙을 쓰지 않는다. 모든 배열은 아래 상한으로 고정된다. 상한을 바꾸면 RAM 예산이
// 바뀌므로 `walp_cli mem` 이 sizeof 를 다시 찍는다.
#pragma once
#include <cstdint>

namespace walp {

using Tick = std::uint64_t;
using ObjectId = std::uint32_t;
using EvidenceId = std::uint32_t;
using RuleId = std::uint32_t;
using CaseId = std::uint32_t;

constexpr int kMaxW = 32;
constexpr int kMaxH = 32;
constexpr int kMaxCells = kMaxW * kMaxH;
constexpr int kViewR = 2;                          // 체비셰프 반경
constexpr int kViewN = (2 * kViewR + 1) * (2 * kViewR + 1);
constexpr int kMaxDet = 8;
constexpr int kMaxHyp = 16;
constexpr int kMaxPlanActions = 8;
constexpr int kMaxCases = 256;
constexpr int kMaxRetrieved = 32;
constexpr int kNumZones = 4;

// 에너지 단위(배터리 정수). 행동 비용은 플랫폼이 아니라 계약이다 — 시뮬과 코어가 같은 값을 쓴다.
constexpr std::int32_t kMoveEnergy = 10;
constexpr std::int32_t kObserveEnergy = 3;
constexpr std::int32_t kHoldEnergy = 1;

enum class Status : std::uint8_t {
    Ok,
    InvalidInput,
    Unsupported,
    Ambiguous,      // 명세에 없던 값: '재질문 요청'을 거부와 구분한다(R3)
    Conflict,       // v0.2: 기존 지식·다른 조건과 충돌
    NoEvidence,
    NoPlan,
    Timeout,
    Fault
};

enum class Decision : std::uint8_t { Act, Observe, Replan, Hold, Abort };

// v0.2 해석 상태. UNKNOWN 을 억지로 KNOWN 으로 바꾸지 않는 것이 요점이다.
//   Known          기준어·동의어만으로 풀렸다
//   NovelComposite 아는 낱말이지만 관계 추론(유사어·하위어·상위어 좁히기·수식어)을 거쳐 풀렸다
//   Ambiguous      해석이 여럿이거나 대상이 없다 — 되묻는다
//   Unknown        사전에 없는 낱말이 있다 — 거부한다(추측하지 않는다)
//   Conflict       조건끼리 또는 기존 지식과 어긋난다
//   OutOfScope     다른 동사·질문·부정 — 이 시스템이 하는 일이 아니다
enum class Interp : std::uint8_t { Known, NovelComposite, Ambiguous, Unknown, Conflict, OutOfScope };

enum ObjType : std::uint8_t { kObjNone = 0, kCard, kKey, kCup, kBox, kNumObj };
enum Color : std::uint8_t { kColorAny = 0, kRed, kBlue, kGreen, kBlack, kNumColor };
enum Brand : std::uint8_t { kBrandAny = 0, kVisa, kMaster, kNumBrand };
enum Zone : std::uint8_t { kFloor = 0, kDesk, kShelf, kCounter };
enum CellKind : std::uint8_t { kUnknown = 0, kFree, kWall, kHazard, kBlocker };

enum ActionType : std::uint16_t {
    kActNone = 0, kMoveN, kMoveE, kMoveS, kMoveW, kObserve, kHold, kDeclare, kAbort
};

enum GoalType : std::uint16_t { kGoalNone = 0, kGoalFindObject = 1 };

// constraints 비트: 하위 4비트 = 피할 구역(1<<zone), 조건 비트는 기본값을 뒤집는 쪽만 둔다.
constexpr std::uint32_t kAvoidMask = 0x0F;
constexpr std::uint32_t kCondUncertainSkip = 1u << 8;   // 기본: 불확실하면 다시 관측
constexpr std::uint32_t kCondBlockedHold = 1u << 9;     // 기본: 막히면 재계획
constexpr std::uint32_t kCondUncertainStated = 1u << 10; // 사용자가 명시했음(기록용)
constexpr std::uint32_t kCondBlockedStated = 1u << 11;

// required_attributes 포장: 하위 바이트 = Color, 다음 바이트 = Brand (0 = 무관)
inline std::uint32_t pack_attrs(Color c, Brand b) { return std::uint32_t(c) | (std::uint32_t(b) << 8); }
inline Color attr_color(std::uint32_t a) { return Color(a & 0xFF); }
inline Brand attr_brand(std::uint32_t a) { return Brand((a >> 8) & 0xFF); }

struct GoalSpec {
    std::uint16_t goal_type;
    std::uint16_t target_type;
    std::uint32_t required_attributes;
    std::uint32_t constraints;
    Tick deadline;     // 상대 틱(임무 시작부터). 0 = 기본값 사용
};

struct EvidenceRef {
    EvidenceId id;
    Tick timestamp;
    std::uint16_t source_type;
    std::uint16_t quality_code;
};

struct ActionRequest {
    std::uint16_t action_type;
    std::int32_t arg0;
    std::int32_t arg1;
    Tick timeout;
};

// 규칙 ID — 결정마다 어느 규칙이 불렀는지 남긴다(R2).
enum Rule : RuleId {
    R_NONE = 0,
    R_DECLARE_CONFIRMED = 1,
    R_OBSERVE_UNCERTAIN = 2,
    R_APPROACH_CANDIDATE = 3,
    R_EXPLORE_FSM = 4,
    R_EXPLORE_SEARCH = 5,
    R_RETURN_BATTERY = 6,
    R_HOLD_STALE = 7,
    R_HOLD_DENIED = 8,
    R_ABORT_FAULT = 9,
    R_ABORT_DEADLINE = 10,
    R_REPLAN_BLOCKED = 11,
    R_HOLD_PLAN_TIMEOUT = 12,
    R_ABORT_DENY_LOOP = 13,
    R_OBSERVE_STALE = 14,
    R_ABORT_NO_PLAN = 15,
    R_HOLD_BLOCKED = 16,
    R_ABORT_HOME_NO_TARGET = 17,
    R_DECLARE_FIRST_SIGHT = 18,   // 불확실성 보류를 끈 ablation 에서만
    R_SAFETY_RETURN = 19,         // 안전층이 에너지 하한에서 제어를 넘겨받았다(귀환 한 걸음 / 대기 / 집에서 종료)
    R_COUNT
};

// 거부·보류 사유 코드
enum Reason : std::uint16_t {
    RS_NONE = 0,
    RS_CELL_UNKNOWN = 1,
    RS_CELL_BLOCKED = 2,
    RS_HAZARD = 3,
    RS_STALE_OBS = 4,
    RS_BATTERY_RESERVE = 5,
    RS_ACTUATOR_FAULT = 6,
    RS_AVOID_ZONE = 7,
    RS_NO_EVIDENCE = 8,
    RS_OUT_OF_BOUNDS = 9,
    RS_PLAN_TIMEOUT = 10,
    RS_SENSOR_INVALID = 11,
    RS_DEADLINE = 12,
    RS_NO_PLAN = 13,
    RS_UNKNOWN_ACTION = 14,
    RS_COUNT
};

struct DecisionRecord {
    Tick timestamp;
    Decision decision;
    RuleId rule_id;
    EvidenceId evidence_id;
    std::uint16_t reason_code;
    // 명세보다 넓힌 칸 — 기록만으로 결정을 재구성하려면 필요했다
    std::uint16_t action_type;
    std::int32_t arg0, arg1;
    std::uint32_t policy_version;
    std::uint32_t obs_id;          // 이 결정이 본 관측
    std::uint16_t n_cases;         // 검색된 사례 수
    CaseId first_case;             // 대표 사례 ID(없으면 0)
    std::uint16_t plan_nodes;
    bool authorized;
};

}  // namespace walp
