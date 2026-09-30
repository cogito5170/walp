// walp/core.hpp — 구체 모듈. 전부 고정 크기, 힙 없음, 플랫폼 호출 없음(R1).
#pragma once
#include "interfaces.hpp"

#include <cstddef>

namespace walp {

// ---------------------------------------------------------------- 격자 유틸
constexpr std::uint16_t kInf = 0xFFFF;
inline int cell(int x, int y, int w) { return y * w + x; }
void action_delta(std::uint16_t a, int& dx, int& dy);
std::uint16_t move_toward(int fx, int fy, int tx, int ty);  // 이웃 칸으로 가는 행동

// 너비우선 거리. passable 규칙: 벽·위험·장애물·피할 구역은 막힘, 모르는 칸은 allow_unknown 에 따라.
struct DistMap {
    std::uint16_t d[kMaxCells];
};
void bfs(const BeliefState& b, int sx, int sy, std::uint32_t avoid_mask, bool allow_unknown, DistMap& out);
// out 거리지도에서 (tx,ty) 로 가는 첫걸음(목표 쪽에서 거꾸로 내려간다). 없으면 kActNone.
std::uint16_t first_step_to(const BeliefState& b, const DistMap& from_target, int sx, int sy);

// ---------------------------------------------------------------- A. 명령 해석 + 지식
// 개념 사전 항목. 코어는 파일을 안 읽는다 — 호스트가 CSV 를 이 고정 배열로 올린다.
enum KbCat : std::uint8_t { KB_COLOR = 1, KB_BRAND, KB_BRANDCARD, KB_OBJECT, KB_ZONE, KB_MOD };
enum KbRel : std::uint8_t { REL_CANON = 1, REL_SYN, REL_NEAR, REL_HYPO, REL_HYPER, REL_MOD };
constexpr int kMaxKB = 640;
struct KBEntry {
    char word[28];
    std::uint8_t cat, rel, conf, mask;   // mask: 개념 비트(1<<값). 상위어는 비트가 여럿
};
struct KnowledgeBase {
    std::uint16_t n;
    KBEntry e[kMaxKB];
};
enum class KbAdd : std::uint8_t { Added, Duplicate, Conflict, Full, Invalid };
// 새 지식 후보를 넣는다. 문법어와 겹치거나 같은 낱말이 다른 뜻이면 Conflict — 넣지 않는다.
KbAdd kb_add(KnowledgeBase& kb, const char* word, std::uint8_t cat, std::uint8_t rel, std::uint8_t conf,
             std::uint8_t mask);
const KBEntry* kb_find(const KnowledgeBase& kb, const char* word);

struct ParserOptions {
    bool use_syn = true;    // 동의어(ablation: 끄면 기준어만)
    bool use_rel = true;    // 관계 추론 — 유사어·하위어·상위어·수식어(ablation)
    std::uint8_t min_conf = 75;
    bool use_constr = true; // v0.4 구절 틀: 조건·기한·회피 구절 안의 모르는 연결어는 넘어간다(ablation)
};

struct ParseResult {
    Status status;
    Interp interp;
    std::uint8_t conf;
    std::uint8_t n_cand;
    GoalSpec cand[4];        // Ambiguous 면 가능한 해석들(되물을 때 보인다)
    std::uint8_t relations_used;
    const char* reason;
    char token[48];          // 모르는 낱말
    std::uint8_t tolerated;  // 구절 틀 안이라 넘어간 모르는 말의 수(0 이면 v0.3 과 같은 판정)
};

class CommandParser : public ICommandParser {
public:
    explicit CommandParser(const KnowledgeBase* kb = nullptr, ParserOptions o = ParserOptions{}) : kb_(kb), opt_(o) {}
    Status parse(const char* input, GoalSpec& output) override;
    ParseResult parse_detailed(const char* input);
    const char* last_reason() const { return last_.reason ? last_.reason : ""; }
    const char* last_token() const { return last_.token; }
    const ParseResult& last() const { return last_; }
private:
    const KnowledgeBase* kb_;
    ParserOptions opt_;
    ParseResult last_{};
};
// GoalSpec 을 사람이 읽는 한 줄로(평가 비교용 정규형과 같다)
void goal_to_canon(const GoalSpec& g, char* buf, int n);

// ---------------------------------------------------------------- B. 목표 관리
class GoalManager {
public:
    // 임무 명세로 받을 수 있나. 안 되면 임무 시작 금지.
    Status validate(const GoalSpec& g, const MapHint& map, std::uint16_t& reason) const;
    static constexpr Tick kDefaultDeadline = 300;
    static constexpr Tick kMaxDeadline = 5000;
};

// ---------------------------------------------------------------- C. 상태 추정
class StateEstimator : public IStateEstimator {
public:
    explicit StateEstimator(std::uint16_t max_age = 2) : max_age_(max_age) {}
    void init(BeliefState& s, const MapHint& m) const;
    void set_now(BeliefState& s, Tick now) const { s.now = now; }
    Status update(const Observation& o, BeliefState& s) override;
private:
    std::uint16_t max_age_;
};

// ---------------------------------------------------------------- C. 사례 검색
class CaseRetriever : public IRetriever {
public:
    explicit CaseRetriever(const CaseBase* cb) : cb_(cb) {}
    Status retrieve(const GoalSpec& goal, const BeliefState& state, RetrievedCases& out) override;
private:
    const CaseBase* cb_;
};
void uniform_prior(RetrievedCases& out);

// ---------------------------------------------------------------- C. 계획
enum class ExploreMode : std::uint8_t { Fsm, Search };

class Deliberator : public IPlanner {
public:
    Deliberator(ExploreMode mode, const PolicyParams* params) : mode_(mode), p_(params) {}
    Status plan(const GoalSpec& goal, const BeliefState& state, const RetrievedCases& cases,
                Plan& out) override;
    void reset() {
        fsm_idx_ = 0; fsm_ready_ = false; returning_ = false; ignore_blockers_ = false;
        blocked_hold_run_ = 0; blocked_steps_ = 0;
    }
    std::uint16_t blocked_steps() const { return blocked_steps_; }
    // 고장 주입용: 계획이 제시간에 못 끝나는 상황
    void force_timeout(bool on) { force_timeout_ = on; }
    // 고장 주입: 계획기 결함(위험·장애물을 지나갈 수 있다고 보고, 낡은 관측에도 움직인다).
    // 안전 감독기가 '결정 버그의 마지막 방어선' 인지 재려고 둔다. 실제 배포 경로에서는 끈다.
    void inject_defect(bool on) { defect_ = on; }
    // 한 가설을 목표로 판정할 수 있나(확정/후보/아님)
    enum class Match : std::uint8_t { No, Candidate, Confirmed };
    Match match(const GoalSpec& g, const Hypothesis& h) const;
private:
    Status explore(const GoalSpec& g, const BeliefState& s, const RetrievedCases& c, Plan& out);
    Status explore_fsm(const GoalSpec& g, const BeliefState& s, const RetrievedCases& c, Plan& out);
    Status explore_search(const GoalSpec& g, const BeliefState& s, const RetrievedCases& c, Plan& out);
    void build_fsm_order(const GoalSpec& g, const BeliefState& s, const RetrievedCases& c);
    std::int32_t cell_weight(const BeliefState& s, int ci, const RetrievedCases& c) const;
    std::int32_t view_gain(const BeliefState& s, int wx, int wy, const RetrievedCases& c,
                           std::uint32_t avoid, std::uint16_t* stamp, std::uint16_t mark) const;

    ExploreMode mode_;
    const PolicyParams* p_;
    bool force_timeout_ = false;
    bool defect_ = false;
    bool returning_ = false;
    // FSM 상태: 고정 순서의 경유점 목록
    bool fsm_ready_ = false;
    std::uint16_t fsm_n_ = 0, fsm_idx_ = 0;
    std::uint16_t fsm_order_[kMaxCells / 4];
    std::uint8_t fsm_done_[kMaxCells / 4];
    // 정책 그래프 BLOCKED 규칙 상태
    bool ignore_blockers_ = false;
    std::uint8_t blocked_hold_run_ = 0;
    std::uint16_t blocked_steps_ = 0;
    void bfs_i(const BeliefState& b, int sx, int sy, std::uint32_t avoid, DistMap& out) const;
    void pbfs(const BeliefState& b, int sx, int sy, std::uint32_t avoid, DistMap& out) const;
    // 작업 공간(스택이 작은 MCU 를 생각해 멤버로 둔다)
    DistMap dm_, dm2_, dm2h_;
    DistMap cand_dm_[6];
    std::uint16_t stamp_[kMaxCells];
};

// ---------------------------------------------------------------- D. 안전 감독
struct SafetyLimits {
    std::uint16_t max_obs_age = 2;      // 이보다 오래 전에 본 칸으로는 안 움직인다
    std::int32_t hard_margin = 20;      // 귀환 에너지 하드 여유 — 학습이 못 건드린다
    // 대기 여유: 집으로 가는 길이 막혀 기다려야 할 때 쓸 에너지. 보류·관측도 에너지를 쓰므로(첫 판은
    // 이동만 검사해서, 학습이 귀환 여유를 10 으로 내린 정책이 막힌 길 앞에서 보류하다 좌초했다)
    // 이동이 아닌 행동에도 '귀환 비용 + 하드 여유 + 대기 여유' 를 남기게 한다.
    std::int32_t wait_reserve = 40;
};

class SafetySupervisor : public ISafetySupervisor {
public:
    explicit SafetySupervisor(SafetyLimits lim = SafetyLimits{}, bool enabled = true)
        : lim_(lim), enabled_(enabled) {}
    bool authorize(const ActionRequest& a, const SafetyState& s, std::uint16_t& reason) override;
    // 에너지 하한에서 넘겨받은 행동: 집 쪽 한 걸음(자유·싱싱한 칸) → 없으면 보류 → 집이면 종료
    ActionRequest fallback(const SafetyState& s) override;
    bool enabled() const { return enabled_; }
    const SafetyLimits& limits() const { return lim_; }
private:
    SafetyLimits lim_;
    bool enabled_;
    DistMap home_;
    void home_dist(const BeliefState& b, std::uint32_t avoid);
};

// ---------------------------------------------------------------- 기록
struct DecisionSink {
    void (*fn)(void* ctx, const DecisionRecord& r);
    void* ctx;
};

// ---------------------------------------------------------------- 실행 루프
struct ExecutiveConfig {
    bool use_retrieval = false;
    std::uint32_t policy_version = 1;
    std::uint16_t deny_abort_run = 8;   // 연속 거부가 이만큼이면 중단
};

enum class StepResult : std::uint8_t { Continue, Declared, Aborted, Deadline };

class Executive {
public:
    Executive(IPlatform& platform, StateEstimator& est, IRetriever* retriever, Deliberator& planner,
              ISafetySupervisor& safety, ExecutiveConfig cfg, DecisionSink sink);
    Status begin(const GoalSpec& goal);          // 평면도 받고 믿음 초기화, 한 번 검색
    StepResult step();                           // 관측 → 추정 → 계획 → 허가 → 한 행동 → 기록
    const BeliefState& belief() const { return b_; }
    const RetrievedCases& cases() const { return cases_; }
    EpisodeRecord episode() const;
    std::uint16_t steps() const { return steps_; }
    std::uint16_t blocked_events() const { return planner_.blocked_steps(); }
    std::int32_t declared_x() const { return dx_; }
    std::int32_t declared_y() const { return dy_; }
private:
    IPlatform& pf_;
    StateEstimator& est_;
    IRetriever* retr_;
    Deliberator& planner_;
    ISafetySupervisor& safety_;
    ExecutiveConfig cfg_;
    DecisionSink sink_;
    GoalSpec goal_{};
    MapHint map_{};
    BeliefState b_{};
    RetrievedCases cases_{};
    Tick start_ = 0;
    std::uint16_t steps_ = 0, observes_ = 0, denials_ = 0, deny_run_ = 0, stale_steps_ = 0;
    std::uint8_t outcome_ = O_NONE;
    std::int32_t dx_ = -1, dy_ = -1;
    void emit(const Plan& p, const ActionRequest& a, bool authorized, std::uint16_t reason, Decision d,
              RuleId rule);
};

// ---------------------------------------------------------------- 정책 학습(후보 생성만)
class PolicyLearner : public IPolicyLearner {
public:
    Status propose(const EpisodeRecord& e, const PolicyParams& cur, RuleCandidate* out, int max_out,
                   int& n_out) override;
};
// 안전 불변식 — 후보가 이것을 어기면 시험도 안 돌리고 버린다(반례 검사의 정적 부분)
bool params_invariants_ok(const PolicyParams& p, const char** why);
// 한 걸음 이웃(불변식 통과 · 현재와 다른 것만). 반사실 진단용 — 실패 분류를 거치지 않는다.
constexpr int kMaxNeighbors = 32;
// scale>1: 수치 걸음을 그 배수로(규칙 바꾸기 없음), 변화 코드 + 32·log2(scale)
int policy_neighbors(const PolicyParams& cur, RuleCandidate* out, int max_out, int scale = 1);
std::uint32_t crc32(const std::uint8_t* d, std::size_t n);
PolicyParams default_params();
PolicyParams default_params_raw();

// 버전 저장소: 승인된 것만 current 가 된다. 거부된 후보도 기록은 남는다.
struct PolicyVersion {
    std::uint32_t id, parent;
    PolicyParams params;
    std::uint8_t status;   // 1=승인, 2=거부
    std::uint16_t reason, change_code;
};
class PolicyStore {
public:
    static constexpr int kMax = 64;
    PolicyStore();
    std::uint32_t current_id() const { return cur_; }
    const PolicyParams& current() const;
    std::uint32_t submit(const RuleCandidate& c, bool approved);  // 새 버전 ID
    bool rollback(std::uint32_t id);                                // 승인된 버전으로만
    int size() const { return n_; }
    const PolicyVersion& at(int i) const { return v_[i]; }
    const PolicyVersion* find(std::uint32_t id) const;
    // 영속: 고정 바이트열 + CRC32. 파일 쓰기(임시 파일 → rename)는 호스트가 한다.
    // deserialize 는 CRC·크기·버전 일관성이 하나라도 틀리면 false 이고 저장소를 **안 바꾼다**.
    static constexpr std::size_t kImageSize = 16 + sizeof(PolicyVersion) * kMax + 4;
    std::size_t serialize(std::uint8_t* buf, std::size_t cap) const;
    bool deserialize(const std::uint8_t* buf, std::size_t len);
private:
    PolicyVersion v_[kMax];
    int n_ = 0;
    std::uint32_t cur_ = 1;
};

}  // namespace walp
