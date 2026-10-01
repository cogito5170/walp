// walp_cli — 파서·한 판 실행·전체 평가·메모리 보고.
//
//   walp_cli parse "<명령>"                 명령 → GoalSpec (JSON 한 줄)
//   walp_cli corpus <tsv>                   문장 모음으로 파서 채점
//   walp_cli run "<명령>" [--seed N] [--family office|shop|home|shift]
//                                           명령 한 줄로 시뮬 한 판(사용성 평가 경로가 부른다)
//   walp_cli eval [--n N] [--streams S] [--out f.json]
//                                           B0~B4 · ablation · 일반화 · H3 · 재현성 · 자원
//   walp_cli mem                            코어 구조체 크기(RAM 예산)
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <map>
#include <memory>
#include <sstream>
#include <string>
#include <vector>

#include "harness.hpp"

using namespace walp;
using namespace walpeval;
using walpsim::ScenarioConfig;

namespace {

const char* status_name(Status s) {
    switch (s) {
    case Status::Ok: return "ok";
    case Status::InvalidInput: return "invalid";
    case Status::Unsupported: return "unsupported";
    case Status::Ambiguous: return "ambiguous";
    case Status::Conflict: return "conflict";
    case Status::NoEvidence: return "no_evidence";
    case Status::NoPlan: return "no_plan";
    case Status::Timeout: return "timeout";
    case Status::Fault: return "fault";
    }
    return "?";
}

const char* interp_name(Interp i) {
    switch (i) {
    case Interp::Known: return "KNOWN";
    case Interp::NovelComposite: return "NOVEL_COMPOSITE";
    case Interp::Ambiguous: return "AMBIGUOUS";
    case Interp::Unknown: return "UNKNOWN";
    case Interp::Conflict: return "CONFLICT";
    case Interp::OutOfScope: return "OUT_OF_SCOPE";
    }
    return "?";
}

// 전역 개념 사전(호스트가 한 번 올린다)
KnowledgeBase* g_kb = nullptr;
std::vector<std::string> g_kb_errors;
KnowledgeBase& kb() {
    if (!g_kb) {
        g_kb = new KnowledgeBase();
        std::memset(g_kb, 0, sizeof(KnowledgeBase));
        load_kb(default_kb_path(), *g_kb, g_kb_errors, false);
        load_kb(default_learned_path(), *g_kb, g_kb_errors, true);
    }
    return *g_kb;
}

std::string jesc(const std::string& s) {
    std::string o;
    for (char c : s) {
        if (c == '"' || c == '\\') { o += '\\'; o += c; }
        else if (c == '\n') o += "\\n";
        else if (c == '\t') o += "\\t";
        else if ((unsigned char)c < 0x20) o += ' ';
        else o += c;
    }
    return o;
}

std::string arg_value(int argc, char** argv, const char* key, const char* def) {
    for (int i = 0; i + 1 < argc; ++i) if (std::strcmp(argv[i], key) == 0) return argv[i + 1];
    return def;
}

// ---------------------------------------------------------------- parse / corpus
std::string cand_list(const ParseResult& r) {
    std::string o = "[";
    for (int i = 0; i < r.n_cand; ++i) {
        char c[256];
        goal_to_canon(r.cand[i], c, sizeof c);
        o += std::string(i ? "," : "") + "\"" + c + "\"";
    }
    return o + "]";
}

int cmd_parse(const char* text) {
    CommandParser p(&kb());
    const ParseResult r = p.parse_detailed(text);
    char canon[256] = "";
    if (r.status == Status::Ok) goal_to_canon(r.cand[0], canon, sizeof canon);
    std::printf("{\"status\":\"%s\",\"interp\":\"%s\",\"reason\":\"%s\",\"token\":\"%s\",\"conf\":%d,\"relations_used\":%d,"
                "\"canon\":\"%s\",\"candidates\":%s,\"kb_entries\":%d,\"kb_errors\":%zu,\"tolerated\":%d}\n",
                status_name(r.status), interp_name(r.interp), r.reason ? r.reason : "", jesc(r.token).c_str(), r.conf,
                r.relations_used, canon, cand_list(r).c_str(), kb().n, g_kb_errors.size(), int(r.tolerated));
    return r.status == Status::Ok ? 0 : 1;
}

// 기준선: 학습 문장 꼴의 템플릿 매칭. 기준어만, 정해진 어순만.
//   EN: [please] (find|locate) [a|an|the] [COLOR] [BRAND] OBJECT
//   KO: [COLOR] [BRAND] OBJECT[를|을] (찾아|찾아줘|찾아주세요|찾아봐)
ParseResult template_parse(const std::string& text) {
    ParseResult R{};
    R.status = Status::Unsupported;
    R.interp = Interp::Unknown;
    R.reason = "no_template";
    std::string t;
    for (char c : text) {
        unsigned char u = (unsigned char)c;
        if (u >= 'A' && u <= 'Z') u = u - 'A' + 'a';
        if (u == '.' || u == ',' || u == '!' || u == '?') u = ' ';
        t += char(u);
    }
    std::vector<std::string> w;
    std::stringstream ss(t);
    std::string x;
    while (ss >> x) w.push_back(x);
    auto canon = [&](const std::string& s, std::uint8_t cat) -> int {
        const KBEntry* e = kb_find(kb(), s.c_str());
        if (!e || e->rel != REL_CANON || e->cat != cat) return -1;
        for (int i = 0; i < 8; ++i) if (e->mask & (1u << i)) return i;
        return -1;
    };
    std::size_t i = 0;
    int col = 0, br = 0, obj = -1;
    GoalSpec g{};
    auto finish = [&]() {
        if (obj < 0) return R;
        g.goal_type = kGoalFindObject;
        g.target_type = std::uint16_t(obj);
        g.required_attributes = pack_attrs(Color(col), Brand(br));
        R.cand[0] = g;
        R.n_cand = 1;
        R.status = Status::Ok;
        R.interp = Interp::Known;
        R.reason = "ok";
        return R;
    };
    if (i < w.size() && w[i] == "please") ++i;
    if (i < w.size() && (w[i] == "find" || w[i] == "locate")) {
        ++i;
        if (i < w.size() && (w[i] == "a" || w[i] == "an" || w[i] == "the")) ++i;
        if (i < w.size() && canon(w[i], KB_COLOR) > 0) col = canon(w[i++], KB_COLOR);
        if (i < w.size() && canon(w[i], KB_BRAND) > 0) br = canon(w[i++], KB_BRAND);
        if (i + 1 == w.size() && canon(w[i], KB_OBJECT) > 0) { obj = canon(w[i], KB_OBJECT); return finish(); }
        return R;
    }
    if (i < w.size() && canon(w[i], KB_COLOR) > 0) col = canon(w[i++], KB_COLOR);
    if (i < w.size() && canon(w[i], KB_BRAND) > 0) br = canon(w[i++], KB_BRAND);
    if (i + 2 == w.size()) {
        std::string o = w[i];
        for (const char* pp : {"를", "을"})
            if (o.size() > 3 && o.compare(o.size() - 3, 3, pp) == 0) { o = o.substr(0, o.size() - 3); break; }
        const std::string v = w[i + 1];
        if (canon(o, KB_OBJECT) > 0 && (v == "찾아" || v == "찾아줘" || v == "찾아주세요" || v == "찾아봐")) {
            obj = canon(o, KB_OBJECT);
            return finish();
        }
    }
    return R;
}

// 라벨의 한 칸 값
std::string field(const std::string& canon, const std::string& key) {
    const std::string k = " " + key + "=";
    const auto p = (" " + canon).find(k);
    if (p == std::string::npos) return "";
    const auto s = p + k.size() - 1;
    const auto e = canon.find(' ', s);
    return canon.substr(s, e == std::string::npos ? std::string::npos : e - s);
}

// 라벨과 해석이 같은 목표인가 — 회피 구역은 **집합**이다(v0.4: 라벨은 알파벳 순, canon 은 구역 번호 순이라
// 같은 목표가 '다르게 실행' 으로 세어졌다 — v5 채점에서 8건 중 6건이 이것이었다).
std::string sort_avoid(const std::string& c) {
    const std::string k = " avoid=";
    const auto p = (" " + c).find(k);
    if (p == std::string::npos) return c;
    const auto s = p + k.size() - 1;
    const auto e = c.find(' ', s);
    std::string v = c.substr(s, e == std::string::npos ? std::string::npos : e - s);
    std::vector<std::string> z;
    std::stringstream ss(v);
    for (std::string x; std::getline(ss, x, '+');) z.push_back(x);
    std::sort(z.begin(), z.end());
    std::string j;
    for (std::size_t i = 0; i < z.size(); ++i) j += (i ? "+" : "") + z[i];
    return c.substr(0, s) + j + (e == std::string::npos ? "" : c.substr(e));
}

bool same_goal(const std::string& label, const std::string& canon) { return sort_avoid(label) == sort_avoid(canon); }

int cmd_corpus(int argc, char** argv) {
    const char* path = argv[2];
    const std::string mode = arg_value(argc, argv, "--mode", "full");
    std::ifstream f(path);
    if (!f) { std::fprintf(stderr, "corpus 를 못 읽었다: %s\n", path); return 2; }
    ParserOptions opt;
    if (mode == "nosyn") { opt.use_syn = false; opt.use_rel = false; }
    else if (mode == "norel") opt.use_rel = false;
    else if (mode == "noconstr") opt.use_constr = false;
    else if (mode != "full" && mode != "template") { std::fprintf(stderr, "모르는 mode: %s\n", mode.c_str()); return 2; }
    std::string line;
    // 라벨: OK <목표> · REJECT · AMBIGUOUS
    int n_ok = 0, n_rej = 0, n_amb = 0;
    int ok_exact = 0, ok_wrong = 0, ok_refused = 0, ok_asked = 0;
    int rej_refused = 0, rej_executed = 0;
    int amb_asked = 0, amb_refused = 0, amb_executed = 0;
    int accepted_known = 0, accepted_known_exact = 0, accepted_novel = 0, accepted_novel_exact = 0;
    const char* keys[] = {"type", "color", "brand", "avoid", "deadline", "uncertain", "blocked"};
    int field_ok[7] = {0}, field_n = 0;
    std::map<std::string, int> interp_on_ok, reasons;
    std::vector<std::string> fails;
    while (std::getline(f, line)) {
        if (line.empty()) continue;
        const auto tab = line.find('\t');
        if (tab == std::string::npos) continue;
        const std::string text = line.substr(0, tab);
        std::string label = line.substr(tab + 1);
        while (!label.empty() && (label.back() == '\r' || label.back() == ' ')) label.pop_back();
        ParseResult r;
        if (mode == "template") r = template_parse(text);
        else { CommandParser p(&kb(), opt); r = p.parse_detailed(text.c_str()); }
        char canon[256] = "";
        if (r.status == Status::Ok) goal_to_canon(r.cand[0], canon, sizeof canon);
        const bool executed = r.status == Status::Ok;
        const bool asked = r.status == Status::Ambiguous;
        if (!executed) ++reasons[r.reason ? r.reason : "?"];
        if (label == "REJECT") {
            ++n_rej;
            if (executed) { ++rej_executed; fails.push_back("REJECT 인데 실행 | " + text + " | " + canon); }
            else ++rej_refused;
        } else if (label == "AMBIGUOUS") {
            ++n_amb;
            if (executed) { ++amb_executed; fails.push_back("AMBIGUOUS 인데 실행 | " + text + " | " + canon); }
            else if (asked) ++amb_asked;
            else ++amb_refused;
        } else {
            ++n_ok;
            ++interp_on_ok[interp_name(r.interp)];
            if (executed) {
                const bool exact = same_goal(label, canon);
                ++field_n;
                for (int k = 0; k < 7; ++k) field_ok[k] += field(label, keys[k]) == field(canon, keys[k]);
                if (r.interp == Interp::Known) { ++accepted_known; accepted_known_exact += exact; }
                else { ++accepted_novel; accepted_novel_exact += exact; }
                if (exact) ++ok_exact;
                else { ++ok_wrong; fails.push_back("다르게 실행 | " + text + " | 기대 " + label + " | 실제 " + canon); }
            } else {
                if (asked) ++ok_asked; else ++ok_refused;
                fails.push_back(std::string("OK 인데 ") + (asked ? "되물음(" : "거부(") + (r.reason ? r.reason : "") + " " +
                                r.token + ") | " + text);
            }
        }
    }
    const int n = n_ok + n_rej + n_amb;
    const int wrong_exec = ok_wrong + rej_executed + amb_executed;
    std::printf("{\"mode\":\"%s\",\"n\":%d,\"n_ok\":%d,\"n_reject\":%d,\"n_ambiguous\":%d,", mode.c_str(), n, n_ok, n_rej, n_amb);
    std::printf("\"ok_exact\":%d,\"ok_wrong_goal\":%d,\"ok_refused\":%d,\"ok_asked\":%d,", ok_exact, ok_wrong, ok_refused, ok_asked);
    std::printf("\"reject_refused\":%d,\"reject_executed\":%d,\"amb_asked\":%d,\"amb_refused\":%d,\"amb_executed\":%d,",
                rej_refused, rej_executed, amb_asked, amb_refused, amb_executed);
    std::printf("\"goal_accuracy\":%.4f,\"rejection_rate\":%.4f,\"appropriate_hold_rate\":%.4f,"
                "\"wrong_execution\":%d,\"wrong_execution_rate\":%.4f,",
                n_ok ? double(ok_exact) / n_ok : 0.0, n_rej ? double(rej_refused) / n_rej : 0.0,
                n_amb ? double(amb_asked) / n_amb : 0.0, wrong_exec, n ? double(wrong_exec) / n : 0.0);
    std::printf("\"accepted_known\":%d,\"accepted_known_exact\":%d,\"accepted_novel\":%d,\"accepted_novel_exact\":%d,",
                accepted_known, accepted_known_exact, accepted_novel, accepted_novel_exact);
    std::printf("\"field_accuracy_given_accepted\":{\"n\":%d", field_n);
    for (int k = 0; k < 7; ++k) std::printf(",\"%s\":%.4f", keys[k], field_n ? double(field_ok[k]) / field_n : 0.0);
    std::printf("},\"interp_on_ok_labels\":{");
    bool first = true;
    for (auto& kv : interp_on_ok) { std::printf("%s\"%s\":%d", first ? "" : ",", kv.first.c_str(), kv.second); first = false; }
    std::printf("},\"refusal_reasons\":{");
    first = true;
    for (auto& kv : reasons) { std::printf("%s\"%s\":%d", first ? "" : ",", jesc(kv.first).c_str(), kv.second); first = false; }
    std::printf("},\"failures\":[");
    for (std::size_t i = 0; i < fails.size(); ++i) std::printf("%s\"%s\"", i ? "," : "", jesc(fails[i]).c_str());
    std::printf("]}\n");
    return 0;
}

// 사용자 확인으로 새 지식을 넣는다: 충돌 검사를 통과한 것만 learned 파일에 붙는다.
int cmd_learn(int argc, char** argv) {
    if (argc < 5) { std::fprintf(stderr, "usage: walp_cli learn <낱말> <color|object|brand|zone|modifier> <개념|-> [rel]\n"); return 2; }
    const std::string word = argv[2], cat = argv[3], con = argv[4];
    const std::string rel = argc >= 6 ? argv[5] : (cat == "modifier" ? "mod" : "syn");
    const std::string line = word + "," + cat + "," + con + "," + rel + ",90";
    std::string w, err;
    std::uint8_t c, r, cf, m;
    if (!parse_kb_line(line, w, c, r, cf, m, err)) {
        std::printf("{\"result\":\"invalid\",\"why\":\"%s\"}\n", jesc(err).c_str());
        return 1;
    }
    KnowledgeBase& K = kb();
    const KbAdd res = kb_add(K, w.c_str(), c, r, cf, m);
    const char* rn = res == KbAdd::Added ? "added" : res == KbAdd::Duplicate ? "duplicate"
                   : res == KbAdd::Conflict ? "conflict" : res == KbAdd::Full ? "full" : "invalid";
    std::string why;
    if (res == KbAdd::Conflict) {
        const KBEntry* e = kb_find(K, w.c_str());
        why = e ? "이미 다른 뜻으로 있다" : "문법어(조사·부정·조건 등)와 겹친다";
    }
    if (res == KbAdd::Added) {
        std::ofstream out(default_learned_path(), std::ios::app);
        if (!out) { std::printf("{\"result\":\"io_error\",\"path\":\"%s\"}\n", jesc(default_learned_path()).c_str()); return 1; }
        out << line << "\n";
    }
    std::printf("{\"result\":\"%s\",\"line\":\"%s\",\"why\":\"%s\"}\n", rn, jesc(line).c_str(), jesc(why).c_str());
    return res == KbAdd::Added || res == KbAdd::Duplicate ? 0 : 1;
}

// ---------------------------------------------------------------- 시나리오
enum Cond { C_NOMINAL, C_NOISE, C_SENSOR, C_SHIFT, C_UNKNOWN_ENV, C_ACTUATOR, C_COUNT };
const char* kCondName[] = {"C1_nominal", "C2_high_noise", "C3_sensor_faults", "C4_distribution_shift",
                           "C5_unknown_env", "C6_actuator_dynamic"};

ScenarioConfig make_scen(std::uint32_t seed, Cond c) {
    ScenarioConfig s;
    s.seed = seed;
    std::uint32_t h = seed * 2246822519u + 3266489917u;
    h ^= h >> 15;
    s.target_type = std::uint8_t(1 + h % 4);
    s.color = std::uint8_t(1 + (h >> 4) % 4);
    s.brand = s.target_type == kCard ? std::uint8_t(1 + (h >> 8) % 2) : 0;
    s.family = (seed & 1) ? walpsim::F_SHOP : walpsim::F_OFFICE;
    switch (c) {
    case C_NOMINAL: break;
    case C_NOISE: s.noise = 0.25; break;
    case C_SENSOR: s.fault.dropout = 0.15; s.fault.latency = 0.10; break;
    case C_SHIFT: s.family = walpsim::F_OFFICE_SHIFT; break;
    case C_UNKNOWN_ENV: s.family = walpsim::F_HOME; break;
    case C_ACTUATOR:
        s.fault.act_fail = 0.03; s.fault.act_silent = 0.02; s.fault.dyn_blockers = 3; s.fault.dyn_hazards = 2;
        break;
    default: break;
    }
    return s;
}

ScenarioConfig make_train(std::uint32_t seed) {
    // 학습 흐름은 험한 조건: 잡음·누락·동적 장애물이 섞인다
    ScenarioConfig s = make_scen(seed, C_NOMINAL);
    s.noise = 0.20;
    s.fault.dropout = 0.05;
    s.fault.dyn_blockers = 2;
    return s;
}

// ---------------------------------------------------------------- run (한 판)
const char* rule_name(RuleId r) {
    static const char* k[] = {"none", "declare_confirmed", "observe_uncertain", "approach_candidate", "explore_fsm",
                              "explore_search", "return_battery", "hold_stale", "hold_denied", "abort_fault",
                              "abort_deadline", "replan_blocked", "hold_plan_timeout", "abort_deny_loop",
                              "observe_stale", "abort_no_plan", "hold_blocked", "abort_home_no_target",
                              "declare_first_sight"};
    return r < R_COUNT ? k[r] : "?";
}

int cmd_run(int argc, char** argv) {
    const char* text = argv[2];
    CommandParser p(&kb());
    GoalSpec g{};
    const Status s = p.parse(text, g);
    if (s != Status::Ok) {
        std::printf("{\"parsed\":false,\"status\":\"%s\",\"interp\":\"%s\",\"reason\":\"%s\",\"token\":\"%s\","
                    "\"candidates\":%s}\n", status_name(s), interp_name(p.last().interp), p.last_reason(),
                    jesc(p.last_token()).c_str(), cand_list(p.last()).c_str());
        return 1;
    }
    const std::uint32_t seed = std::uint32_t(std::stoul(arg_value(argc, argv, "--seed", "7")));
    const std::string fam = arg_value(argc, argv, "--family", "office");
    ScenarioConfig sc = make_scen(seed, C_NOMINAL);
    sc.family = fam == "shop" ? walpsim::F_SHOP : fam == "home" ? walpsim::F_HOME
              : fam == "shift" ? walpsim::F_OFFICE_SHIFT : walpsim::F_OFFICE;
    sc.target_type = std::uint8_t(g.target_type);
    if (attr_color(g.required_attributes)) sc.color = attr_color(g.required_attributes);
    sc.brand = g.target_type == kCard ? (attr_brand(g.required_attributes) ? std::uint8_t(attr_brand(g.required_attributes))
                                                                             : std::uint8_t(1 + seed % 2)) : 0;
    if (g.deadline) sc.deadline = int(g.deadline);
    else { g.deadline = Tick(sc.deadline); }
    // 사례: 수집용 시드로 쌓는다(평가 시드와 겹치지 않는다)
    std::vector<ScenarioConfig> col;
    for (std::uint32_t i = 0; i < 120; ++i) col.push_back(make_scen(1000 + i, C_NOMINAL));
    auto cb = std::make_unique<CaseBase>();
    build_case_base(col, *cb);
    Arm arm{"WALP", ExploreMode::Search, true, L_NONE, true, true};
    RunOpts o;
    o.keep_decisions = true;
    o.judge_by_goal = true;   // 말하지 않은 색·브랜드는 아무것이나 맞다(예: '파란 카드' 에 파란 비자카드)
    const EpisodeResult r = run_episode(sc, g, arm, default_params(), cb.get(), o);
    char canon[256];
    goal_to_canon(g, canon, sizeof canon);
    std::map<std::string, int> rules;
    for (const auto& d : r.decisions) ++rules[rule_name(d.rule_id)];
    std::printf("{\"parsed\":true,\"interp\":\"%s\",\"canon\":\"%s\",\"seed\":%u,\"family\":\"%s\",\"outcome\":\"%s\",\"steps\":%d,"
                "\"energy_used\":%d,\"observes\":%d,\"denials\":%d,\"violations\":%d,\"cases\":%u,"
                "\"trace_ok\":%d,\"trace_total\":%d,\"rules\":{",
                interp_name(p.last().interp), canon, seed, fam.c_str(), outcome_name(r.outcome), r.steps, r.energy_used, r.observes, r.denials,
                r.viol.total(), r.n_cases, r.trace_ok, r.trace_total);
    bool first = true;
    for (auto& kv : rules) { std::printf("%s\"%s\":%d", first ? "" : ",", kv.first.c_str(), kv.second); first = false; }
    std::printf("},\"last\":[");
    const std::size_t start = r.decisions.size() > 6 ? r.decisions.size() - 6 : 0;
    for (std::size_t i = start; i < r.decisions.size(); ++i) {
        const auto& d = r.decisions[i];
        std::printf("%s{\"t\":%llu,\"rule\":\"%s\",\"action\":%u,\"ev\":%u,\"reason\":%u,\"ok\":%s}", i > start ? "," : "",
                    (unsigned long long)d.timestamp, rule_name(d.rule_id), d.action_type, d.evidence_id, d.reason_code,
                    d.authorized ? "true" : "false");
    }
    std::printf("]}\n");
    return 0;
}

// ---------------------------------------------------------------- eval
struct Cell {
    int n = 0, success = 0, false_declare = 0, timeout = 0, abort = 0, stranded = 0;
    int hazard = 0, collision = 0, avoid = 0, viol_eps = 0;
    long steps = 0, energy = 0, observes = 0, denials = 0, blocked = 0;
    double max_us = 0;
    std::vector<double> us;
    int max_nodes = 0;
    std::uint64_t heap = 0;
    long trace_total = 0, trace_ok = 0;
    std::vector<std::uint8_t> ok;   // 짝지은 비교용
    int cases_used = 0;
};

void add(Cell& c, const EpisodeResult& r) {
    ++c.n;
    c.success += r.outcome == O_SUCCESS;
    c.false_declare += r.outcome == O_FALSE_DECLARE;
    c.timeout += r.outcome == O_TIMEOUT;
    c.abort += r.outcome == O_ABORT_FAULT || r.outcome == O_ABORT_SAFETY || r.outcome == O_ABORT_NO_TARGET;
    c.stranded += r.outcome == O_STRANDED;
    c.hazard += r.viol.hazard_entered;
    c.collision += r.viol.collisions;
    c.avoid += r.viol.avoid_entered;
    c.viol_eps += r.viol.total() > 0;
    c.steps += r.steps;
    c.energy += r.energy_used;
    c.observes += r.observes;
    c.denials += r.denials;
    c.blocked += r.blocked_events;
    c.max_us = std::max(c.max_us, r.max_step_us);
    c.us.insert(c.us.end(), r.step_us.begin(), r.step_us.end());
    c.max_nodes = std::max(c.max_nodes, r.max_nodes);
    c.heap += r.heap_allocs_in_step;
    c.trace_total += r.trace_total;
    c.trace_ok += r.trace_ok;
    c.ok.push_back(r.outcome == O_SUCCESS);
    c.cases_used += r.n_cases > 0;
}

void wilson(int k, int n, double& lo, double& hi) {
    if (n == 0) { lo = hi = 0; return; }
    const double z = 1.96, p = double(k) / n, d = 1 + z * z / n;
    const double c = (p + z * z / (2 * n)) / d, h = z * std::sqrt(p * (1 - p) / n + z * z / (4.0 * n * n)) / d;
    lo = std::max(0.0, c - h);
    hi = std::min(1.0, c + h);
}

// 짝지은 부호 검정(정확 이항, 양측)
double sign_test(int b, int c) {
    const int n = b + c;
    if (n == 0) return 1.0;
    const int k = std::min(b, c);
    double s = 0;
    for (int i = 0; i <= k; ++i) s += std::exp(std::lgamma(n + 1) - std::lgamma(i + 1) - std::lgamma(n - i + 1) - n * std::log(2.0));
    return std::min(1.0, 2 * s);
}

double pct(std::vector<double> v, double q) {
    if (v.empty()) return 0;
    std::sort(v.begin(), v.end());
    return v[std::min(v.size() - 1, std::size_t(q * double(v.size() - 1) + 0.5))];
}

void cell_json(std::FILE* f, const Cell& c, const Cell* base) {
    double lo, hi;
    wilson(c.success, c.n, lo, hi);
    int b = 0, cc = 0;
    if (base) for (std::size_t i = 0; i < c.ok.size() && i < base->ok.size(); ++i) { b += c.ok[i] && !base->ok[i]; cc += !c.ok[i] && base->ok[i]; }
    std::fprintf(f, "{\"n\":%d,\"success\":%d,\"success_rate\":%.4f,\"ci95\":[%.4f,%.4f],\"false_declare\":%d,"
                    "\"timeout\":%d,\"abort\":%d,\"stranded\":%d,\"violations\":{\"hazard\":%d,\"collision\":%d,"
                    "\"avoid\":%d,\"episodes_with_violation\":%d},\"mean_steps\":%.2f,\"mean_energy\":%.1f,"
                    "\"mean_observes\":%.2f,\"mean_denials\":%.2f,\"mean_blocked_events\":%.2f,"
                    "\"step_us_max\":%.1f,\"step_us_p99\":%.1f,\"step_us_p50\":%.1f,\"max_plan_nodes\":%d,"
                    "\"heap_allocs_in_step\":%llu,\"trace_ok\":%ld,\"trace_total\":%ld,\"episodes_with_cases\":%d,"
                    "\"vs_B0\":{\"wins\":%d,\"losses\":%d,\"sign_p\":%.4g}}",
                 c.n, c.success, c.n ? double(c.success) / c.n : 0, lo, hi, c.false_declare, c.timeout, c.abort,
                 c.stranded, c.hazard, c.collision, c.avoid, c.viol_eps, c.n ? double(c.steps) / c.n : 0,
                 c.n ? double(c.energy) / c.n : 0, c.n ? double(c.observes) / c.n : 0,
                 c.n ? double(c.denials) / c.n : 0, c.n ? double(c.blocked) / c.n : 0, c.max_us, pct(c.us, 0.99),
                 pct(c.us, 0.5), c.max_nodes, (unsigned long long)c.heap, c.trace_ok, c.trace_total, c.cases_used, b, cc,
                 sign_test(b, cc));
}

struct ArmRun {
    Arm arm;
    PolicyParams params;
    std::uint32_t version;
};

// 적응 시험의 '바뀐 환경': 문간에 사람이 서성이고 센서가 조금 더 흐리다
ScenarioConfig make_envB(std::uint32_t seed) {
    ScenarioConfig s = make_scen(seed, C_NOMINAL);
    s.fault.door_blockers = 3;
    s.fault.door_move = 0.15;
    s.noise = 0.22;
    return s;
}

void pjson(std::FILE* f, const PolicyParams& p) {
    static const char* an[] = {"-", "observe", "hold", "abort", "declare", "return", "approach", "skip", "replan", "explore"};
    std::fprintf(f, "{\"k_confirm\":%d,\"theta_pct\":%d,\"observe_max\":%d,\"stale_observe_max\":%d,\"lambda_t\":%d,"
                    "\"lambda_g\":%d,\"reserve_margin\":%d,\"prior_weight_pct\":%d,\"max_nodes\":%d,\"depth\":%d,"
                    "\"blocked_wait\":%d,\"blocker_ttl\":%d,\"rules\":{\"candidate_near\":\"%s\",\"candidate_far\":\"%s\",\"blocked\":\"%s\"}}",
                 p.k_confirm, p.theta_pct, p.observe_max, p.stale_observe_max, p.lambda_t, p.lambda_g, p.reserve_margin,
                 p.prior_weight_pct, p.max_nodes, p.depth, p.blocked_wait, p.blocker_ttl,
                 an[p.rule_action[RC_CANDIDATE_NEAR] < RA_COUNT ? p.rule_action[RC_CANDIDATE_NEAR] : 0],
                 an[p.rule_action[RC_CANDIDATE_FAR] < RA_COUNT ? p.rule_action[RC_CANDIDATE_FAR] : 0],
                 an[p.rule_action[RC_BLOCKED] < RA_COUNT ? p.rule_action[RC_BLOCKED] : 0]);
}

void loglson(std::FILE* f, const LearnLog& l) {
    std::fprintf(f, "{\"failures\":%d,\"proposals\":%d,\"approved\":%d,\"rejected_static\":%d,\"rejected_regression\":%d,"
                    "\"applied_unverified\":%d,\"cases_added\":%d,\"validation_episodes\":%ld,\"validation_seconds\":%.2f,\"events\":[",
                 l.failures, l.proposals, l.approved, l.rejected_static, l.rejected_regression, l.applied_unverified,
                 l.cases_added, l.validation_episodes, l.validation_seconds);
    for (std::size_t i = 0; i < l.events.size() && i < 30; ++i) std::fprintf(f, "%s\"%s\"", i ? "," : "", jesc(l.events[i]).c_str());
    std::fprintf(f, "]}");
}

struct Rate { int k = 0, n = 0, fd = 0, viol = 0; };
Rate eval_set(const std::vector<ScenarioConfig>& set, const Arm& arm, const PolicyParams& p, const CaseBase* cb, std::uint32_t ver) {
    Rate r;
    for (const auto& sc : set) {
        RunOpts o;
        o.policy_version = ver;
        const EpisodeResult e = run_episode(sc, goal_for(sc), arm, p, cb, o);
        ++r.n;
        r.k += e.outcome == O_SUCCESS;
        r.fd += e.outcome == O_FALSE_DECLARE;
        r.viol += e.viol.total();
    }
    return r;
}

int cmd_eval(int argc, char** argv) {
    const bool quick = argc > 2 && std::string(argv[2]) == "--quick";
    const int N = std::stoi(arg_value(argc, argv, "--n", quick ? "24" : "200"));
    const int S = std::stoi(arg_value(argc, argv, "--streams", quick ? "1" : "5"));
    const int TRAIN = std::stoi(arg_value(argc, argv, "--train", quick ? "16" : "60"));
    const std::string out = arg_value(argc, argv, "--out", "");
    const auto T0 = std::chrono::steady_clock::now();

    // 시드 대역 — 서로 겹치지 않는다(정보 누출 방지)
    const std::uint32_t COL0 = 1000, NCOL = 200, TRAIN0 = 2000, SUITE0 = 3000, SUITEB0 = 3100, SUITEH0 = 3200, NSUITE = 20,
                        EVAL0 = 5000;
    if (!(COL0 + NCOL <= TRAIN0 && TRAIN0 + 100u * std::uint32_t(S + 1) <= SUITE0 && SUITEH0 + NSUITE <= EVAL0 && TRAIN <= 100)) {
        std::fprintf(stderr, "시드 대역이 겹친다\n");
        return 2;
    }
    std::vector<ScenarioConfig> col, suiteA, suiteB, suiteH;
    for (std::uint32_t i = 0; i < NCOL; ++i) col.push_back(make_scen(COL0 + i, C_NOMINAL));
    for (std::uint32_t i = 0; i < NSUITE; ++i) {
        suiteA.push_back(make_scen(SUITE0 + i, C_NOMINAL));
        suiteB.push_back(make_envB(SUITEB0 + i));
        suiteH.push_back(make_train(SUITEH0 + i));
    }
    auto cb0 = std::make_unique<CaseBase>();
    build_case_base(col, *cb0);
    int cb_success = 0, zone_hist[kNumZones] = {0};
    for (int i = 0; i < cb0->n; ++i) if (cb0->c[i].outcome_code == 1) { ++cb_success; ++zone_hist[cb0->c[i].event_type]; }
    std::fprintf(stderr, "[eval] 사례 %d개 (성공 %d)\n", cb0->n, cb_success);

    auto train_hard = [&](int stream) {
        std::vector<ScenarioConfig> t;
        for (int i = 0; i < TRAIN; ++i) t.push_back(make_train(TRAIN0 + 100u * std::uint32_t(stream) + std::uint32_t(i)));
        return t;
    };
    auto train_B = [&](int stream) {
        std::vector<ScenarioConfig> t;
        for (int i = 0; i < TRAIN; ++i) t.push_back(make_envB(TRAIN0 + 50 + 100u * std::uint32_t(stream) + std::uint32_t(i)));
        return t;
    };

    const Arm B0{"B0_fsm", ExploreMode::Fsm, false, L_NONE, true, true};
    const Arm B1{"B1_fsm+retrieval", ExploreMode::Fsm, true, L_NONE, true, true};
    const Arm B2{"B2_fsm+search", ExploreMode::Search, false, L_NONE, true, true};
    const Arm B3{"B3_B1+verified_update", ExploreMode::Fsm, true, L_VERIFIED, true, true};
    const Arm B4{"B4_full_WALP", ExploreMode::Search, true, L_VERIFIED, true, true};
    const PolicyParams P0 = default_params();

    // ---- E1/E2: 학습 팔은 험한 훈련 흐름 0 에서 규칙을 갱신한 뒤 얼린다(경험 저장은 끔 — 규칙 효과만)
    LearnLog l3, l4;
    CaseBase cbw = *cb0;
    PolicyValidator v3(suiteA, suiteH, B3), v4(suiteA, suiteH, B4);
    const PolicyStore st3 = learn_stream(train_hard(0), &v3, B3, &cbw, false, l3);
    const PolicyStore st4 = learn_stream(train_hard(0), &v4, B4, &cbw, false, l4);
    std::fprintf(stderr, "[eval] 학습 B3 승인 %d/%d  B4 승인 %d/%d\n", l3.approved, l3.proposals, l4.approved, l4.proposals);
    std::vector<ArmRun> arms;
    arms.push_back({B0, P0, 1});
    arms.push_back({B1, P0, 1});
    arms.push_back({B2, P0, 1});
    arms.push_back({B3, st3.current(), st3.current_id()});
    arms.push_back({B4, st4.current(), st4.current_id()});
    Arm a;
    a = B4; a.name = "B4-retrieval"; a.retrieval = false; arms.push_back({a, st4.current(), st4.current_id()});
    a = B4; a.name = "B4-search(=FSM)"; a.mode = ExploreMode::Fsm; arms.push_back({a, st4.current(), st4.current_id()});
    a = B4; a.name = "B4-update"; a.learn = L_NONE; arms.push_back({a, P0, 1});
    a = B4; a.name = "B4-uncertainty_hold"; a.uncertainty = false; arms.push_back({a, st4.current(), st4.current_id()});
    a = B4; a.name = "B4-safety(SIM_ONLY)"; a.safety = false; arms.push_back({a, st4.current(), st4.current_id()});
    // 결함 주입: 계획기가 위험·장애물·낡은 관측을 무시한다. 안전층이 있을 때와 없을 때.
    a = B4; a.name = "B4+planner_defect"; a.planner_defect = true; arms.push_back({a, st4.current(), st4.current_id()});
    a = B4; a.name = "B4+planner_defect-safety(SIM_ONLY)"; a.planner_defect = true; a.safety = false;
    arms.push_back({a, st4.current(), st4.current_id()});

    std::vector<std::vector<Cell>> cells(arms.size(), std::vector<Cell>(C_COUNT));
    for (std::size_t ai = 0; ai < arms.size(); ++ai) {
        for (int c = 0; c < C_COUNT; ++c)
            for (int i = 0; i < N; ++i) {
                const ScenarioConfig sc = make_scen(EVAL0 + std::uint32_t(i), Cond(c));
                RunOpts o;
                o.time_steps = true;
                o.policy_version = arms[ai].version;
                add(cells[ai][c], run_episode(sc, goal_for(sc), arms[ai].arm, arms[ai].params, cb0.get(), o));
            }
        std::fprintf(stderr, "[eval] %-24s C1 %d/%d\n", arms[ai].arm.name.c_str(), cells[ai][0].success, N);
    }

    // ---- E3 (H3): 검증 갱신 vs 무검증 갱신 vs 갱신 없음
    struct H3Row { std::string mode; int stream; std::uint32_t ver; LearnLog log; int suite0, suite1, regress;
                   Rate nom, noisy, act_on, act_off; PolicyParams p; };
    std::vector<H3Row> h3;
    const int NH = std::max(12, N / 2);
    std::vector<ScenarioConfig> hn, hz, ha;
    for (int i = 0; i < NH; ++i) {
        hn.push_back(make_scen(EVAL0 + 700 + std::uint32_t(i), C_NOMINAL));
        hz.push_back(make_scen(EVAL0 + 700 + std::uint32_t(i), C_NOISE));
        ha.push_back(make_scen(EVAL0 + 700 + std::uint32_t(i), C_ACTUATOR));
    }
    for (int base_i = 0; base_i < 2; ++base_i) {
        const Arm base = base_i == 0 ? B1 : B4;
        for (int st = 0; st < S; ++st)
            for (int m = 0; m < 3; ++m) {
                Arm arm = base;
                arm.learn = LearnMode(m);
                H3Row row;
                row.mode = std::string(base_i == 0 ? "B1" : "B4") + (m == 0 ? "/none" : m == 1 ? "/verified" : "/unverified");
                row.stream = st;
                CaseBase cbs = *cb0;
                PolicyValidator val(suiteA, suiteH, arm);
                const PolicyStore store = m == 0 ? PolicyStore() : learn_stream(train_hard(st), &val, arm, &cbs, false, row.log);
                row.p = store.current();
                row.ver = store.current_id();
                const SuiteScore s0 = score_suite(suiteA, arm, P0, cb0.get()), s1 = score_suite(suiteA, arm, row.p, cb0.get());
                row.suite0 = s0.success; row.suite1 = s1.success; row.regress = 0;
                for (std::size_t k = 0; k < suiteA.size(); ++k) row.regress += s0.solved[k] && !s1.solved[k];
                Arm off = arm;
                off.safety = false;
                row.nom = eval_set(hn, arm, row.p, cb0.get(), row.ver);
                row.noisy = eval_set(hz, arm, row.p, cb0.get(), row.ver);
                row.act_on = eval_set(ha, arm, row.p, cb0.get(), row.ver);
                row.act_off = eval_set(ha, off, row.p, cb0.get(), row.ver);
                h3.push_back(row);
            }
        std::fprintf(stderr, "[eval] E3 %s 끝\n", base_i == 0 ? "B1" : "B4");
    }

    // ---- E4 (v0.2 §7B): 환경을 바꾼 뒤 적응. 고정 · 경험만 · 검증 갱신(+경험) · 무검증 갱신(+경험)
    struct AdRow { std::string sys; int stream; LearnLog log; Rate a_before, b_before, a_after, b_after; PolicyParams p; };
    std::vector<AdRow> ad;
    const int NA = std::max(12, N / 2);
    std::vector<ScenarioConfig> evA, evB;
    for (int i = 0; i < NA; ++i) {
        evA.push_back(make_scen(EVAL0 + 1200 + std::uint32_t(i), C_NOMINAL));
        evB.push_back(make_envB(EVAL0 + 1200 + std::uint32_t(i)));
    }
    const Rate a0 = eval_set(evA, B4, P0, cb0.get(), 1), b0 = eval_set(evB, B4, P0, cb0.get(), 1);
    const char* sys_names[4] = {"fixed", "experience_only", "verified_update+experience", "unverified_update+experience"};
    for (int st = 0; st < S; ++st)
        for (int m = 0; m < 4; ++m) {
            AdRow row;
            row.sys = sys_names[m];
            row.stream = st;
            row.a_before = a0;
            row.b_before = b0;
            CaseBase cbs = *cb0;
            Arm arm = B4;
            arm.learn = m == 2 ? L_VERIFIED : m == 3 ? L_UNVERIFIED : L_NONE;
            PolicyValidator val(suiteA, suiteB, arm);
            PolicyStore store;
            if (m > 0) store = learn_stream(train_B(st), &val, arm, &cbs, true, row.log);
            row.p = store.current();
            row.a_after = eval_set(evA, arm, row.p, &cbs, store.current_id());
            row.b_after = eval_set(evB, arm, row.p, &cbs, store.current_id());
            ad.push_back(row);
        }
    std::fprintf(stderr, "[eval] E4 끝\n");

    // ---- E5: 재현성 — 기록 → 재생 → 결정열 비교, 음성 대조(정책을 바꾸면 갈라져야 한다)
    int rep_n = 0, rep_ok = 0, neg_n = 0, neg_diverged = 0;
    for (int i = 0; i < (quick ? 8 : 40); ++i) {
        const ScenarioConfig sc = i % 3 == 2 ? make_envB(EVAL0 + 900 + std::uint32_t(i))
                                             : make_scen(EVAL0 + 900 + std::uint32_t(i), i % 2 ? C_SENSOR : C_ACTUATOR);
        RunOpts o;
        o.record = true;
        o.policy_version = st4.current_id();
        const EpisodeResult r = run_episode(sc, goal_for(sc), B4, st4.current(), cb0.get(), o);
        int fd = -1;
        ++rep_n;
        rep_ok += replay_matches(r, goal_for(sc), B4, st4.current(), cb0.get(), st4.current_id(), &fd);
        PolicyParams alt = st4.current();
        alt.k_confirm = std::uint8_t(alt.k_confirm + 1);
        alt.lambda_t = std::uint16_t(alt.lambda_t + 50);
        ++neg_n;
        neg_diverged += !replay_matches(r, goal_for(sc), B4, alt, cb0.get(), st4.current_id(), &fd);
    }
    const double secs = std::chrono::duration<double>(std::chrono::steady_clock::now() - T0).count();

    // ---- JSON
    std::FILE* f = out.empty() ? stdout : std::fopen(out.c_str(), "w");
    if (!f) { std::fprintf(stderr, "못 쓴다: %s\n", out.c_str()); return 2; }
    std::fprintf(f, "{\"n_per_cell\":%d,\"streams\":%d,\"train_len\":%d,\"seconds\":%.1f,", N, S, TRAIN, secs);
    std::fprintf(f, "\"seeds\":{\"collect\":[%u,%u],\"train\":[%u,%u],\"suiteA\":[%u,%u],\"suiteB\":[%u,%u],\"suiteH\":[%u,%u],\"eval_from\":%u},",
                 COL0, COL0 + NCOL - 1, TRAIN0, TRAIN0 + 100 * S + 50 + TRAIN - 1, SUITE0, SUITE0 + NSUITE - 1, SUITEB0,
                 SUITEB0 + NSUITE - 1, SUITEH0, SUITEH0 + NSUITE - 1, EVAL0);
    std::fprintf(f, "\"case_base\":{\"n\":%d,\"success\":%d,\"zone_hist\":[%d,%d,%d,%d]},", cb0->n, cb_success,
                 zone_hist[0], zone_hist[1], zone_hist[2], zone_hist[3]);
    std::fprintf(f, "\"default_params\":");
    pjson(f, P0);
    std::fprintf(f, ",\"learning\":{\"B3\":{\"version\":%u,\"params\":", st3.current_id());
    pjson(f, st3.current());
    std::fprintf(f, ",\"log\":");
    loglson(f, l3);
    std::fprintf(f, "},\"B4\":{\"version\":%u,\"params\":", st4.current_id());
    pjson(f, st4.current());
    std::fprintf(f, ",\"log\":");
    loglson(f, l4);
    std::fprintf(f, "}},\"arms\":{");
    for (std::size_t ai = 0; ai < arms.size(); ++ai) {
        std::fprintf(f, "%s\"%s\":{", ai ? "," : "", arms[ai].arm.name.c_str());
        for (int c = 0; c < C_COUNT; ++c) {
            std::fprintf(f, "%s\"%s\":", c ? "," : "", kCondName[c]);
            cell_json(f, cells[ai][c], &cells[0][c]);
        }
        std::fprintf(f, "}");
    }
    auto rj = [&](const Rate& r) { std::fprintf(f, "{\"success\":%d,\"n\":%d,\"false_declare\":%d,\"violations\":%d}", r.k, r.n, r.fd, r.viol); };
    std::fprintf(f, "},\"h3\":[");
    for (std::size_t i = 0; i < h3.size(); ++i) {
        const H3Row& r = h3[i];
        std::fprintf(f, "%s{\"mode\":\"%s\",\"stream\":%d,\"version\":%u,\"suite_before\":%d,\"suite_after\":%d,"
                        "\"suite_regressions\":%d,\"nominal\":", i ? "," : "", r.mode.c_str(), r.stream, r.ver, r.suite0,
                     r.suite1, r.regress);
        rj(r.nom); std::fprintf(f, ",\"noisy\":"); rj(r.noisy); std::fprintf(f, ",\"actuator_safety_on\":"); rj(r.act_on);
        std::fprintf(f, ",\"actuator_safety_off\":"); rj(r.act_off); std::fprintf(f, ",\"log\":"); loglson(f, r.log);
        std::fprintf(f, ",\"params\":"); pjson(f, r.p); std::fprintf(f, "}");
    }
    std::fprintf(f, "],\"adaptation\":[");
    for (std::size_t i = 0; i < ad.size(); ++i) {
        const AdRow& r = ad[i];
        std::fprintf(f, "%s{\"system\":\"%s\",\"stream\":%d,\"A_before\":", i ? "," : "", r.sys.c_str(), r.stream);
        rj(r.a_before); std::fprintf(f, ",\"B_before\":"); rj(r.b_before); std::fprintf(f, ",\"A_after\":"); rj(r.a_after);
        std::fprintf(f, ",\"B_after\":"); rj(r.b_after); std::fprintf(f, ",\"log\":"); loglson(f, r.log);
        std::fprintf(f, ",\"params\":"); pjson(f, r.p); std::fprintf(f, "}");
    }
    std::fprintf(f, "],\"replay\":{\"n\":%d,\"identical\":%d,\"negative_control_n\":%d,\"negative_control_diverged\":%d}}\n",
                 rep_n, rep_ok, neg_n, neg_diverged);
    if (f != stdout) std::fclose(f);
    std::fprintf(stderr, "[eval] 끝 %.1fs\n", secs);
    return 0;
}

// ---------------------------------------------------------------- v0.3: SE 원리 자기개선 고리 vs v0.2 검증 갱신
// 같은 **에피소드 예산**에서 비교한다: v0.2 가 훈련 흐름 + 검증에 쓴 에피소드 수를 그대로 SE 고리에 준다.
// 팔: fixed · experience_only · v02_verified · v02_unverified · se_same_data(훈련 흐름만 사냥) · se_full(같은 분포 더 사냥)
// 도메인: B(환경 바뀜: make_envB) · H(험한 조건: make_train). 평가 시드는 훈련·사냥·모음과 겹치지 않는다.
// 끝에 검증기 변이 검사: 해로운 줄 아는 정책 변이를 두 관문에 넣는다(진짜 해로움은 큰 평가 모음으로 따로 잰다).
int cmd_selfimprove(int argc, char** argv) {
    const bool quick = argc > 2 && std::string(argv[2]) == "--quick";
    const int S = std::stoi(arg_value(argc, argv, "--streams", quick ? "1" : "5"));
    const int TRAIN = std::stoi(arg_value(argc, argv, "--train", quick ? "20" : "60"));
    const int NA = std::stoi(arg_value(argc, argv, "--n", quick ? "30" : "150"));
    const std::string out = arg_value(argc, argv, "--out", "");
    const auto T0 = std::chrono::steady_clock::now();
    const std::uint32_t COL0 = 1000, NCOL = 200, TRAIN0 = 2000, SUITE0 = 3000, SUITEB0 = 3100, SUITEH0 = 3200, NSUITE = 20,
                        HUNT0 = 10000;
    const std::uint32_t EVAL0 = std::uint32_t(std::stoul(arg_value(argc, argv, "--eval0", "6000")));
    const bool skip_mut = [&] { for (int k = 2; k < argc; ++k) if (std::string(argv[k]) == "--no-mutation") return true; return false; }();
    if (TRAIN > 100 || NA > 400 || S > 8) { std::fprintf(stderr, "범위 밖\n"); return 2; }
    std::vector<ScenarioConfig> col, suiteA, suiteB, suiteH;
    for (std::uint32_t i = 0; i < NCOL; ++i) col.push_back(make_scen(COL0 + i, C_NOMINAL));
    for (std::uint32_t i = 0; i < NSUITE; ++i) {
        suiteA.push_back(make_scen(SUITE0 + i, C_NOMINAL));
        suiteB.push_back(make_envB(SUITEB0 + i));
        suiteH.push_back(make_train(SUITEH0 + i));
    }
    auto cb0 = std::make_unique<CaseBase>();
    build_case_base(col, *cb0);
    const Arm B4{"B4_full_WALP", ExploreMode::Search, true, L_NONE, true, true};
    const PolicyParams P0 = default_params();
    struct Dom { const char* name; ScenarioConfig (*mk)(std::uint32_t); std::uint32_t train_off; const std::vector<ScenarioConfig>* suite; };
    const Dom doms[2] = {{"B_env_shift", make_envB, 50, &suiteB}, {"H_hard", make_train, 0, &suiteH}};
    std::vector<ScenarioConfig> evA;
    for (int i = 0; i < NA; ++i) evA.push_back(make_scen(EVAL0 + std::uint32_t(i), C_NOMINAL));

    std::FILE* f = out.empty() ? stdout : std::fopen(out.c_str(), "w");
    if (!f) { std::fprintf(stderr, "못 쓴다: %s\n", out.c_str()); return 2; }
    auto rj = [&](const Rate& r) { std::fprintf(f, "{\"success\":%d,\"n\":%d,\"false_declare\":%d,\"violations\":%d}", r.k, r.n, r.fd, r.viol); };
    std::fprintf(f, "{\"streams\":%d,\"train_len\":%d,\"n_eval\":%d,\"seeds\":{\"train\":%u,\"suites\":[%u,%u],\"eval\":[%u,%u],\"hunt\":%u},\"domains\":[",
                 S, TRAIN, NA, TRAIN0, SUITE0, SUITEH0 + NSUITE - 1, EVAL0, EVAL0 + 1000 + std::uint32_t(NA) - 1, HUNT0);
    const std::string dom_only = arg_value(argc, argv, "--domain", "");   // B 또는 H 만(시험을 짧게)
    bool first_dom = true;
    for (int d = 0; d < 2; ++d) {
        const Dom& D = doms[d];
        if (!dom_only.empty() && D.name[0] != dom_only[0]) continue;
        std::vector<ScenarioConfig> evD;
        for (int i = 0; i < NA; ++i) evD.push_back(D.mk(EVAL0 + 1000 + std::uint32_t(i)));
        const Rate a0 = eval_set(evA, B4, P0, cb0.get(), 1), d0 = eval_set(evD, B4, P0, cb0.get(), 1);
        std::fprintf(f, "%s{\"domain\":\"%s\",\"A_before\":", first_dom ? "" : ",", D.name); rj(a0);
        first_dom = false;
        std::fprintf(f, ",\"D_before\":"); rj(d0); std::fprintf(f, ",\"rows\":[");
        int row_i = 0;
        for (int st = 0; st < S; ++st) {
            std::vector<ScenarioConfig> train, hunt_more;
            for (int i = 0; i < TRAIN; ++i) train.push_back(D.mk(TRAIN0 + D.train_off + 100u * std::uint32_t(st) + std::uint32_t(i)));
            hunt_more = train;
            for (int i = 0; i < 2000; ++i) hunt_more.push_back(D.mk(HUNT0 + 5000u * std::uint32_t(d) + 1000u * std::uint32_t(st) + std::uint32_t(i)));
            long budget = 0;
            // se3 = 채택형(배율 이웃 + 패턴 이동 + 무익 정지, 양쪽 진단 없음). se3+twosided 는 양쪽 진단을 더한 것과
            // 거기서 하나씩 뺀 것 — 양쪽 진단은 같은 예산에서 해로웠다(표본이 반으로 줄어 추정이 흔들린다)
            const char* names[11] = {"fixed", "experience_only", "v02_verified", "v02_unverified", "se_same_data", "se_full", "se2_paired",
                                     "se3+twosided", "se3+twosided-scale", "se3+twosided-pattern", "se3"};
            std::vector<ScenarioConfig> pool_new, pool_old;   // se2 의 짝지은 표본(평가·사냥·모음과 겹치지 않는 대역)
            for (int i = 0; i < 4000; ++i) {
                pool_new.push_back(D.mk(100000 + 50000u * std::uint32_t(d) + 5000u * std::uint32_t(st) + std::uint32_t(i)));
                pool_old.push_back(make_scen(200000 + 5000u * std::uint32_t(st) + std::uint32_t(i), C_NOMINAL));
            }
            const std::string only = arg_value(argc, argv, "--only", "");
            // --budget-mult: se2/se3 에 v0.2 예산의 몇 배를 주나(기본 1 = 같은 예산). 예산 한계인지 기전 한계인지 가르려고
            const double bmult = std::stod(arg_value(argc, argv, "--budget-mult", "1"));
            for (int m = 0; m < 11; ++m) {
                // --only a,b,c: 그 팔만(예산을 정하는 v02_verified 는 늘 돈다)
                if (!only.empty() && m != 2 && ("," + only + ",").find("," + std::string(names[m]) + ",") == std::string::npos) continue;
                CaseBase cbs = *cb0;
                Arm arm = B4;
                PolicyStore store;
                LearnLog ll;
                SeLog sl;
                sl.exp_cap = TRAIN;
                long used = 0;
                if (m == 1) { arm.learn = L_NONE; store = learn_stream(train, nullptr, arm, &cbs, true, ll); used = TRAIN; }
                else if (m == 2 || m == 3) {
                    arm.learn = m == 2 ? L_VERIFIED : L_UNVERIFIED;
                    PolicyValidator val(suiteA, *D.suite, arm);
                    store = learn_stream(train, &val, arm, &cbs, true, ll);
                    used = TRAIN + ll.validation_episodes;
                    if (m == 2) budget = used;
                } else if (m >= 6) {
                    Se2Opts o2;
                    if (m >= 7) {   // v3 와 그 한 가지씩 뺀 것
                        o2.max_scale = m == 8 ? 1 : 4;
                        o2.pattern_move = m != 9;
                        o2.two_sided = m != 10;
                        o2.futility = true;
                    }
                    store = se_improve2(hunt_more, pool_new, pool_old, arm, &cbs, true, long(budget * bmult), sl, o2);
                    used = sl.episodes;
                } else if (m >= 4) {
                    store = se_improve(m == 4 ? train : hunt_more, suiteA, *D.suite, arm, &cbs, true, budget, sl);
                    used = sl.episodes;
                }
                const Rate ra = eval_set(evA, arm, store.current(), &cbs, store.current_id());
                const Rate rd = eval_set(evD, arm, store.current(), &cbs, store.current_id());
                std::fprintf(f, "%s{\"system\":\"%s\",\"stream\":%d,\"episodes_used\":%ld,\"budget\":%ld,\"A_after\":", row_i++ ? "," : "",
                             names[m], st, used, m >= 6 ? long(budget * bmult) : m >= 4 ? budget : used);

                rj(ra); std::fprintf(f, ",\"D_after\":"); rj(rd);
                std::fprintf(f, ",\"version\":%u", store.current_id());
                if (m == 2 || m == 3) std::fprintf(f, ",\"approved\":%d,\"failures\":%d,\"proposals\":%d,\"applied_unverified\":%d",
                                                   ll.approved, ll.failures, ll.proposals, ll.applied_unverified);
                if (m >= 4) {
                    std::fprintf(f, ",\"hunted\":%d,\"counterexamples\":%d,\"fixable\":%d,\"unfixable\":%d,\"promoted\":%d,"
                                    "\"rejected_ratchet\":%d,\"rejected_old\":%d,\"rejected_witness\":%d,\"ratchet\":%d,\"events\":[",
                                 sl.hunted, sl.counterexamples, sl.fixable, sl.unfixable, sl.promoted, sl.rejected_ratchet,
                                 sl.rejected_old, sl.rejected_witness, sl.ratchet);
                    for (std::size_t e = 0; e < sl.events.size(); ++e) std::fprintf(f, "%s\"%s\"", e ? "," : "", jesc(sl.events[e]).c_str());
                    std::fprintf(f, "]");
                }
                std::fprintf(f, ",\"params\":"); pjson(f, store.current()); std::fprintf(f, "}");
                std::fprintf(stderr, "[si] %s s%d %-16s used %6ld  A %3d/%d  D %3d/%d\n", D.name, st, names[m], used, ra.k, ra.n, rd.k, rd.n);
            }
        }
        std::fprintf(f, "]}");
    }
    // ---- 검증기 변이 검사(도메인 B 기준)
    if (skip_mut) { std::fprintf(f, "],\"mutation\":null,\"seconds\":%.1f}\n", std::chrono::duration<double>(std::chrono::steady_clock::now() - T0).count()); if (f != stdout) std::fclose(f); return 0; }
    struct Mut { const char* name; PolicyParams p; };
    std::vector<Mut> muts;
    auto mk = [&](const char* nm, auto fn) { PolicyParams p = P0; fn(p); muts.push_back({nm, p}); };
    mk("hasty_confirm(k1,theta50)", [](PolicyParams& p) { p.k_confirm = 1; p.theta_pct = 50; });
    mk("no_reobserve(obsmax0)", [](PolicyParams& p) { p.observe_max = 0; p.stale_observe_max = 0; });
    mk("reserve0", [](PolicyParams& p) { p.reserve_margin = 0; });
    mk("greedy_gain(lambda_g1000,prior100)", [](PolicyParams& p) { p.lambda_g = 1000; p.prior_weight_pct = 100; });
    mk("ignore_candidates(near,far=skip)", [](PolicyParams& p) { p.rule_action[RC_CANDIDATE_NEAR] = RA_SKIP; p.rule_action[RC_CANDIDATE_FAR] = RA_SKIP; });
    mk("move_averse(lambda_t1000)", [](PolicyParams& p) { p.lambda_t = 1000; });
    mk("shallow_plan(depth1,nodes10)", [](PolicyParams& p) { p.depth = 1; p.max_nodes = 10; });
    mk("stubborn_wait(hold,wait20)", [](PolicyParams& p) { p.rule_action[RC_BLOCKED] = RA_HOLD; p.blocked_wait = 20; });
    mk("paranoid_reserve(400)", [](PolicyParams& p) { p.reserve_margin = 400; });
    mk("locked_rule(battery->explore)", [](PolicyParams& p) { p.rule_action[RC_BATTERY_LOW] = RA_EXPLORE; });
    mk("uncertainty_off", [](PolicyParams& p) { p.use_uncertainty = 0; });
    mk("equivalent(stale+0)", [](PolicyParams& p) { (void)p; p.blocker_ttl = 0; });   // 음성 대조: 기본과 같다
    std::vector<ScenarioConfig> bigA, bigB;
    for (int i = 0; i < 200; ++i) { bigA.push_back(make_scen(EVAL0 + 2000 + std::uint32_t(i), C_NOMINAL)); bigB.push_back(make_envB(EVAL0 + 2000 + std::uint32_t(i))); }
    std::vector<ScenarioConfig> bigB2;
    for (int i = 0; i < 200; ++i) bigB2.push_back(make_envB(EVAL0 + 3000 + std::uint32_t(i)));
    const Rate tA0 = eval_set(bigA, B4, P0, cb0.get(), 1), tB0 = eval_set(bigB, B4, P0, cb0.get(), 1);
    const int tB2 = eval_set(bigB2, B4, P0, cb0.get(), 1).k;
    std::fprintf(f, "],\"mutation\":{\"truth_n\":%zu,\"default\":{\"A\":%d,\"B\":%d,\"fd\":%d},\"mutants\":[", bigA.size() + bigB.size(), tA0.k, tB0.k, tA0.fd + tB0.fd);
    for (std::size_t i = 0; i < muts.size(); ++i) {
        const Mut& M = muts[i];
        const char* why = nullptr;
        const bool stat_ok = params_invariants_ok(M.p, &why);
        int dk = 0, dfd = 0, viol = 0, dA = 0, dB = 0, dB2 = 0;
        if (stat_ok) {
            const Rate a = eval_set(bigA, B4, M.p, cb0.get(), 1), b = eval_set(bigB, B4, M.p, cb0.get(), 1);
            dk = a.k + b.k - tA0.k - tB0.k;
            dA = a.k - tA0.k;
            dB = b.k - tB0.k;
            // 독립 대조: 겹치지 않는 두 번째 B 모음에서 한 번 더
            dB2 = eval_set(bigB2, B4, M.p, cb0.get(), 1).k - tB2;
            dfd = a.fd + b.fd - tA0.fd - tB0.fd;
            viol = a.viol + b.viol;
        }
        // 해로움: 정적 위반 · 안전 위반 · 거짓선언 +4 · 또는 400 판에서 -6 이상 손해이면서 **독립 대조(B2)도 같은 쪽**(-3 이하).
        // 첫 판은 대조 없이 -6 만 봤다 — shallow_plan 이 -8 → 대조 +7 로 뒤집혀 '해로움' 이 잡음이었다.
        const bool harmful = !stat_ok || viol > 0 || dfd >= 4 || (dk <= -6 && dB2 <= -3);
        Arm va = B4; va.learn = L_VERIFIED;
        PolicyValidator val(suiteA, suiteB, va);
        const RuleCandidate cand{M.p, 0, 0};
        const ValidationResult v = val.validate(cand, P0, cb0.get());
        // SE 관문(래칫 없음 · 증인 0 — 변이는 반례를 고친 적이 없다): 옛 모음 무회귀 + 새 모음에서 올라야
        std::string se = v.reason;
        if (v.reason.rfind("static:", 0) != 0 && v.reason != "safety_violation") {
            const bool old_ok = v.regressions <= 1 && v.old_after >= v.old_before && v.fd_after <= v.fd_before;
            se = !old_ok ? "old_suite" : (v.new_after - v.new_before) < 1 ? "no_witness" : "approved";
        }
        std::fprintf(f, "%s{\"name\":\"%s\",\"static\":\"%s\",\"true_delta\":%d,\"true_fd_delta\":%d,\"true_viol\":%d,\"dA\":%d,\"dB\":%d,\"dB_control\":%d,\"harmful\":%s,"
                        "\"v02\":\"%s\",\"se\":\"%s\",\"suite\":{\"old\":[%d,%d],\"regress\":%d,\"new\":[%d,%d],\"fd\":[%d,%d]}}",
                     i ? "," : "", M.name, why, dk, dfd, viol, dA, dB, dB2, harmful ? "true" : "false",
                     v.reason.c_str(), se.c_str(), v.old_before, v.old_after, v.regressions, v.new_before, v.new_after, v.fd_before, v.fd_after);
        std::fprintf(stderr, "[mut] %-36s harmful=%d dA %+d dB %+d dB2 %+d dk %+d dfd %+d  v02=%s se=%s\n", M.name, harmful, dA, dB, dB2, dk, dfd, v.reason.c_str(), se.c_str());
    }
    const double secs = std::chrono::duration<double>(std::chrono::steady_clock::now() - T0).count();
    std::fprintf(f, "]},\"seconds\":%.1f}\n", secs);
    if (f != stdout) std::fclose(f);
    std::fprintf(stderr, "[si] 끝 %.1fs\n", secs);
    return 0;
}

// 반사실 진단의 사소한 설명 재기: 이웃 하나를 바꾸면 결과가 '그냥 뒤집히는' 비율이 얼마인가.
// 실패→성공(고침)과 성공→실패(깨짐)를 이웃마다 세고, 큰 모음에서 잰 참 효과와 나란히 적는다.
// 고침이 참 효과와 무관하게 고르게 나오면 "이 이웃이 이 반례를 고쳤다" 는 진단이 아니라 동전이다.
int cmd_diagnose(int argc, char** argv) {
    const int N = std::stoi(arg_value(argc, argv, "--n", "120"));
    const std::uint32_t COL0 = 1000, NCOL = 200, EVAL0 = 6000;
    std::vector<ScenarioConfig> col;
    for (std::uint32_t i = 0; i < NCOL; ++i) col.push_back(make_scen(COL0 + i, C_NOMINAL));
    auto cb0 = std::make_unique<CaseBase>();
    build_case_base(col, *cb0);
    const Arm B4{"B4", ExploreMode::Search, true, L_NONE, true, true};
    PolicyParams P0 = default_params();
    if (arg_value(argc, argv, "--base", "") == "hold") P0.rule_action[RC_BLOCKED] = RA_HOLD;   // 막힘→대기가 이미 들어간 자리에서
    const int max_scale = std::stoi(arg_value(argc, argv, "--scale", "1"));
    std::vector<ScenarioConfig> probe, truth;
    for (int i = 0; i < N; ++i) probe.push_back(make_envB(EVAL0 + 4000 + std::uint32_t(i)));
    for (int i = 0; i < 300; ++i) truth.push_back(make_envB(EVAL0 + 5000 + std::uint32_t(i)));
    std::vector<int> base;
    int nfail = 0;
    for (const auto& sc : probe) {
        RunOpts o;
        base.push_back(run_episode(sc, goal_for(sc), B4, P0, cb0.get(), o).outcome == O_SUCCESS);
        nfail += !base.back();
    }
    const int t0 = eval_set(truth, B4, P0, cb0.get(), 1).k;
    RuleCandidate nb[kMaxNeighbors * 3];
    int nn = policy_neighbors(P0, nb, kMaxNeighbors, 1);
    for (int sc = 2; sc <= max_scale && nn + kMaxNeighbors <= kMaxNeighbors * 3; sc *= 2) nn += policy_neighbors(P0, nb + nn, kMaxNeighbors, sc);
    std::printf("{\"probe_n\":%d,\"probe_fail\":%d,\"truth_n\":300,\"truth_base\":%d,\"neighbors\":[", N, nfail, t0);
    for (int j = 0; j < nn; ++j) {
        int fix = 0, brk = 0;
        for (int i = 0; i < N; ++i) {
            RunOpts o;
            const bool ok = run_episode(probe[i], goal_for(probe[i]), B4, nb[j].params, cb0.get(), o).outcome == O_SUCCESS;
            fix += !base[i] && ok;
            brk += base[i] && !ok;
        }
        const int td = eval_set(truth, B4, nb[j].params, cb0.get(), 1).k - t0;
        std::printf("%s{\"change\":%u,\"fix\":%d,\"break\":%d,\"truth_delta\":%d}", j ? "," : "", nb[j].change_code, fix, brk, td);
        std::fprintf(stderr, "[diag] change %2u  fix %3d/%d  break %3d/%d  truth %+d/300\n", nb[j].change_code, fix, nfail, brk, N - nfail, td);
    }
    std::printf("]}\n");
    return 0;
}

// 지형 재기: 막힘→대기에서 대기 틱을 0..20 으로 쓸어 참 효과 곡선을 본다(한 걸음 고리가 왜 8 에서 멈추나).
// 환경 B 300판 · A 200판, 평가·사냥·검정 표본과 겹치지 않는 대역.
int cmd_landscape(int argc, char** argv) {
    const std::uint32_t T0 = std::uint32_t(std::stoul(arg_value(argc, argv, "--seed0", "70000")));
    const std::uint32_t COL0 = 1000, NCOL = 200;
    std::vector<ScenarioConfig> col, tb, ta;
    for (std::uint32_t i = 0; i < NCOL; ++i) col.push_back(make_scen(COL0 + i, C_NOMINAL));
    for (std::uint32_t i = 0; i < 300; ++i) tb.push_back(make_envB(T0 + i));
    for (std::uint32_t i = 0; i < 200; ++i) ta.push_back(make_scen(T0 + 1000 + i, C_NOMINAL));
    auto cb0 = std::make_unique<CaseBase>();
    build_case_base(col, *cb0);
    const Arm B4{"B4", ExploreMode::Search, true, L_NONE, true, true};
    std::printf("{\"B_n\":300,\"A_n\":200,\"rows\":[");
    int k = 0;
    for (int hold = 0; hold < 2; ++hold)
        for (int w = 0; w <= 20; w += 2) {
            PolicyParams p = default_params();
            p.rule_action[RC_BLOCKED] = hold ? RA_HOLD : RA_REPLAN;
            p.blocked_wait = std::uint8_t(w);
            if (!hold && w != 4) continue;   // 재계획이면 대기값이 안 쓰인다 — 기준 하나만
            const Rate b = eval_set(tb, B4, p, cb0.get(), 1), a = eval_set(ta, B4, p, cb0.get(), 1);
            std::printf("%s{\"blocked\":\"%s\",\"wait\":%d,\"B\":%d,\"B_fd\":%d,\"A\":%d,\"A_fd\":%d,\"viol\":%d}", k++ ? "," : "",
                        hold ? "hold" : "replan", w, b.k, b.fd, a.k, a.fd, a.viol + b.viol);
            std::fprintf(stderr, "[land] %-6s wait %2d  B %3d/300 (fd %d)  A %3d/200 (fd %d)\n", hold ? "hold" : "replan", w, b.k, b.fd, a.k, a.fd);
        }
    std::printf("]}\n");
    return 0;
}

int cmd_mem() {
    std::printf("{\"BeliefState\":%zu,\"Observation\":%zu,\"MapHint\":%zu,\"Deliberator\":%zu,\"Executive\":%zu,"
                "\"SafetySupervisor\":%zu,\"CaseBase\":%zu,\"PolicyStore\":%zu,\"DecisionRecord\":%zu,"
                "\"CommandParser\":%zu,\"StateEstimator\":%zu,\"DistMap\":%zu}\n",
                sizeof(BeliefState), sizeof(Observation), sizeof(MapHint), sizeof(Deliberator), sizeof(Executive),
                sizeof(SafetySupervisor), sizeof(CaseBase), sizeof(PolicyStore), sizeof(DecisionRecord),
                sizeof(CommandParser), sizeof(StateEstimator), sizeof(DistMap));
    return 0;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 2) {
        std::fprintf(stderr, "usage: walp_cli parse|corpus|run|eval|mem ...\n");
        return 2;
    }
    const std::string c = argv[1];
    if (c == "parse" && argc >= 3) return cmd_parse(argv[2]);
    if (c == "corpus" && argc >= 3) return cmd_corpus(argc, argv);
    if (c == "learn") return cmd_learn(argc, argv);
    if (c == "run" && argc >= 3) return cmd_run(argc, argv);
    if (c == "eval") return cmd_eval(argc, argv);
    if (c == "landscape") return cmd_landscape(argc, argv);
    if (c == "diagnose") return cmd_diagnose(argc, argv);
    if (c == "selfimprove") return cmd_selfimprove(argc, argv);
    if (c == "mem") return cmd_mem();
    std::fprintf(stderr, "모르는 명령: %s\n", c.c_str());
    return 2;
}
