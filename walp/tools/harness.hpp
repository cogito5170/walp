// 평가 하니스 — 코어와 시뮬레이터를 엮어 한 에피소드를 돌리고 잰다. 힙을 써도 되는 쪽.
#pragma once
#include <cstdint>
#include <string>
#include <vector>

#include "walp/core.hpp"
#include "../sim/world.hpp"

namespace walpeval {

using namespace walp;
using walpsim::ScenarioConfig;

enum LearnMode : std::uint8_t { L_NONE = 0, L_VERIFIED = 1, L_UNVERIFIED = 2 };

struct Arm {
    std::string name;
    ExploreMode mode = ExploreMode::Fsm;
    bool retrieval = false;
    LearnMode learn = L_NONE;
    bool uncertainty = true;
    bool safety = true;
    bool planner_defect = false;   // 고장 주입(안전층 효과 재기)
};

struct EpisodeResult {
    std::uint8_t outcome = O_NONE;   // 평가기 판정(정답 대조 후)
    int steps = 0, observes = 0, denials = 0, blocked_events = 0;
    std::int32_t energy_used = 0;
    walpsim::Violations viol{};
    double max_step_us = 0;
    std::vector<double> step_us;
    int max_nodes = 0;
    std::uint64_t heap_allocs_in_step = 0;
    EpisodeRecord rec{};
    std::uint8_t target_zone = 0;
    bool ended_home = false;        // 끝났을 때 집에 있었나(집 밖 안전 정지를 따로 세려고)
    std::uint16_t n_cases = 0;
    std::vector<DecisionRecord> decisions;
    // 추적성
    int trace_total = 0, trace_ok = 0;
    // 재현성(기록용)
    std::vector<Observation> obs;
    std::vector<ExecutionResult> res;
    std::vector<Tick> ticks;
    MapHint map{};
};

struct RunOpts {
    bool keep_decisions = false;
    bool record = false;
    bool time_steps = false;
    std::uint32_t policy_version = 1;
    // 고장 주입 훅(시험용)
    bool force_plan_timeout = false;
};

GoalSpec goal_for(const ScenarioConfig& sc, std::uint32_t extra_constraints = 0);
EpisodeResult run_episode(const ScenarioConfig& sc, const GoalSpec& goal, const Arm& arm, const PolicyParams& p,
                          const CaseBase* cb, const RunOpts& o);
// 기록을 되돌려 같은 결정이 나는가
bool replay_matches(const EpisodeResult& recorded, const GoalSpec& goal, const Arm& arm, const PolicyParams& p,
                    const CaseBase* cb, std::uint32_t policy_version, int* first_diff);

// 사례 수집: B0 로 수집용 시드를 돌려 성공 사례를 쌓는다
void build_case_base(const std::vector<ScenarioConfig>& scen, CaseBase& cb);

struct SuiteScore {
    int success = 0, false_declare = 0, violations = 0;
    std::vector<std::uint8_t> solved;
};
SuiteScore score_suite(const std::vector<ScenarioConfig>& suite, const Arm& arm, const PolicyParams& p,
                       const CaseBase* cb);

// ---------------------------------------------------------------- 검증기(학습기와 분리)
// 학습기는 후보만 낸다. 실행 정책을 바꾸는 권한은 여기 있다. 정적 검사(불변식·규칙 타입·잠긴 규칙)
// → 옛 환경 회귀 모음 → 새 환경 목표 모음. 셋을 다 통과해야 승인.
struct ValidationResult {
    bool approved = false;
    std::string reason;
    int old_before = 0, old_after = 0, regressions = 0;
    int new_before = 0, new_after = 0;
    int fd_before = 0, fd_after = 0, violations = 0;
    int episodes_run = 0;
};
class PolicyValidator {
public:
    PolicyValidator(std::vector<ScenarioConfig> old_suite, std::vector<ScenarioConfig> new_suite, Arm arm, int max_regress = 1)
        : old_(std::move(old_suite)), new_(std::move(new_suite)), arm_(arm), max_regress_(max_regress) {}
    ValidationResult validate(const RuleCandidate& cand, const PolicyParams& current, const CaseBase* cb);
    long episodes_total = 0;
private:
    std::vector<ScenarioConfig> old_, new_;
    Arm arm_;
    int max_regress_;
};

struct LearnLog {
    int failures = 0, proposals = 0, approved = 0, rejected_static = 0, rejected_regression = 0, applied_unverified = 0;
    int cases_added = 0;
    long validation_episodes = 0;
    double validation_seconds = 0;
    std::vector<std::string> events;
};
// 흐름: 훈련 시나리오를 차례로 돌린다. store_experience 면 성공 사례를 사례 저장소에 쌓고(규칙은 그대로),
// arm.learn 이 VERIFIED 면 실패마다 후보 → 검증기, UNVERIFIED 면 첫 후보를 그대로 적용한다.
PolicyStore learn_stream(const std::vector<ScenarioConfig>& train, PolicyValidator* validator, const Arm& arm,
                         CaseBase* cb, bool store_experience, LearnLog& log);
void append_case(CaseBase& cb, const ScenarioConfig& sc, const EpisodeResult& r, std::uint32_t version);
// ---------------------------------------------------------------- SE 원리 자기개선 고리(v0.3)
// v0.2 learn_stream 과 무엇이 다른가(각각 SE 에이전트의 원리 하나씩):
//   반례 사냥      훈련 흐름을 그저 받지 않고, 같은 분포에서 **현재 정책을 깨는 판**을 찾아 나선다(falsegreen 사냥).
//   반사실 진단    실패 분류(추측) 대신 한 걸음 이웃 전부를 **그 반례에 다시 돌려** 무엇이 고치는지 잰다(델타 디버깅).
//   RED→GREEN     후보는 그 반례를 실제로 뒤집어야(빨강→초록) 시험장에 들어간다. 못 뒤집으면 시험도 안 한다.
//   두 번째 증인  반례 하나로는 일화다: 쌓아 둔 미해결 반례 중 **다른 것 하나 이상**도 고치거나, 새 환경 모음에서
//                 올라야 승격한다(self_challenge 의 '독립 대조').
//   래칫          고친 반례는 회귀 모음에 영구히 들어간다. 이후 어떤 승격도 그것을 다시 깨면 안 된다(capability ratchet).
//   예산          모든 에피소드(사냥·진단·증명·회귀)를 센다. v0.2 와 같은 예산에서 비교한다.
struct SeLog {
    long episodes = 0;
    int hunted = 0, counterexamples = 0, unfixable = 0, fixable = 0, promoted = 0;
    int rejected_ratchet = 0, rejected_old = 0, rejected_witness = 0, cases_added = 0, ratchet = 0;
    int exp_cap = 1 << 30;   // 경험(사례) 저장은 사냥 첫 exp_cap 판까지만 — v0.2 와 같은 경험량으로 규칙 효과만 비교
    std::vector<std::string> events;
};
PolicyStore se_improve(const std::vector<ScenarioConfig>& hunt, const std::vector<ScenarioConfig>& old_suite,
                       const std::vector<ScenarioConfig>& new_suite, const Arm& arm, CaseBase* cb, bool store_experience,
                       long budget, SeLog& log, int max_regress = 1);

// v0.3b — 위 고리의 전제(한 반례를 고친 이웃 = 원인)가 이 시뮬에서 틀렸다: 이웃 하나가 결과의 25~30% 를 그냥 다시
// 굴린다(walp_cli diagnose). 그래서 (1) 진단을 **여러 반례에 걸쳐 쌓아** 후보 순위만 정하고, (2) 승격은 새 짝지은 표본에서
// 순차 부호 검정(McNemar, 40판 조각, 한 번 볼 때 단측 p<0.005)으로 증명하고, (3) 래칫은 판 단위가 아니라 **율**로 둔다(옛 환경 표본에서 유의하게
// 나빠지면 탈락). 예산은 똑같이 모든 에피소드를 센다.
struct Se2Opts {
    int min_votes = 5; int paired_new = 400; int paired_old = 80; double alpha = 0.005; int max_tests = 0;
    // v3(대기 8 에서 멈춘 뒤): 수치 걸음 배수(1=끔, 4 → ×1·×2·×4) · 패턴 이동 · 양쪽 진단(고침과 깸으로 순이득 추정)
    // (잡음 바닥 대비 투표 문턱을 먼저 넣었다가 뺐다: 이웃 대부분이 0 표라 중앙값이 0 이 되어 아무것도 안 걸렀다)
    int max_scale = 1; bool pattern_move = false; bool two_sided = false; double min_net = 0.01;
    int min_diag = 8;            // 양쪽 진단: 반례·성공 각각 이만큼 쌓인 뒤에 고른다
    bool futility = false;       // 무익 정지: 두 조각 넘게 봐서 p>0.3 이면 끊는다
};
PolicyStore se_improve2(const std::vector<ScenarioConfig>& hunt, const std::vector<ScenarioConfig>& new_pool,
                        const std::vector<ScenarioConfig>& old_pool, const Arm& arm, CaseBase* cb, bool store_experience,
                        long budget, SeLog& log, const Se2Opts& opt);
double sign_test_p(int wins, int losses);   // 단측: P(X >= wins | n=wins+losses, 1/2)

// 검증기 변이 검사: 해로운 줄 **아는** 정책(변이)을 검증기에 넣어 몇 개를 죽이나. 진짜 해로운지는 큰 평가 모음으로 따로 잰다.
struct MutantRow {
    std::string name;
    int true_delta = 0, true_fd_delta = 0, true_viol = 0;   // 큰 모음에서 기본 대비
    bool harmful = false;
    std::string v02, se;                                    // 각 관문의 판정 사유
};


// 정책 저장소를 파일로: 임시 파일에 쓰고 fsync 한 뒤 rename 한다(중간에 죽어도 옛 판이 남는다).
// crash_after_temp 는 시험용 — 임시 파일까지만 쓰고 '죽는다'.
bool save_store_atomic(const PolicyStore& st, const std::string& path, bool crash_after_temp = false,
                       std::size_t truncate_temp_to = 0);
bool load_store(PolicyStore& st, const std::string& path);

// 할당 계수(코어 스텝 안의 힙 사용을 잰다)
extern bool g_count_allocs;
extern std::uint64_t g_alloc_count;

const char* outcome_name(std::uint8_t o);

}  // namespace walpeval

namespace walpeval {
// 개념 사전 CSV → KnowledgeBase. 잘못된 줄은 errors 에 적고 건너뛴다(조용히 삼키지 않는다).
// learned 는 사용자 확인으로 쌓인 지식: kb_add 의 충돌 검사를 거쳐서만 들어간다.
bool load_kb(const std::string& path, walp::KnowledgeBase& kb, std::vector<std::string>& errors, bool learned = false);
bool parse_kb_line(const std::string& line, std::string& word, std::uint8_t& cat, std::uint8_t& rel, std::uint8_t& conf,
                   std::uint8_t& mask, std::string& err);
std::string default_kb_path();
std::string default_learned_path();
}  // namespace walpeval
