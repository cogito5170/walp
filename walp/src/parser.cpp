// A. 명령 해석 — 제한 문법(한국어·영어) → GoalSpec. 추측하지 않는다(R3).
//
// 규칙은 셋이다.
//   1. 사전에 없는 말이 하나라도 있으면 거부한다(Unsupported). 모르는 말을 건너뛰면 그 말이
//      부정·조건·다른 동사였을 때 엉뚱한 임무가 시작된다.
//   2. 지시 대상이 없으면(찾을 물체가 없거나 '그거'만 있으면) 되묻는다(Ambiguous).
//   3. 서로 어긋나는 것(색 둘, 카드 아닌 것에 브랜드, 목표 둘)은 InvalidInput.
//
// 힙을 안 쓴다: 입력은 고정 버퍼로 정규화하고, 토막은 (범주, 값) 배열에 쌓는다.
#include "walp/core.hpp"

#include <cstdio>
#include <cstring>

namespace walp {
namespace {

enum Cat : std::uint8_t {
    C_NONE, C_FILLER, C_COLOR, C_BRAND, C_BRANDCARD, C_OBJ, C_ZONE, C_FIND, C_OTHERVERB, C_NEG,
    C_AVOID, C_NUM, C_UNIT_STEP, C_UNIT_TIME, C_UNC_TRIG, C_BLK_TRIG, C_ACT_OBS, C_ACT_REPLAN,
    C_ACT_HOLD, C_ACT_SKIP, C_PRONOUN, C_PLACEHOLDER, C_QWORD,
    C_BADCMP,   // 뜻을 뒤집는 비교어(more than · 이상) — 어디 있든 거부
    C_UNK       // 모르는 말(구절 틀 안에서만 넘어간다 — 아래 '구절 틀')
};

struct Lex { const char* w; Cat c; std::uint8_t v; };

// 긴 것이 먼저 맞도록 segment() 가 최장일치를 한다 — 여기 순서는 상관없다.
const Lex kLex[] = {
    // 내용어(색·브랜드·물체·구역·수식어)는 KnowledgeBase 에서 온다 — data/lexicon.csv
    // 동사
    {"find", C_FIND, 0}, {"locate", C_FIND, 0}, {"seek", C_FIND, 0}, {"search", C_FIND, 0},
    {"탐색", C_FIND, 0}, {"수색", C_FIND, 0},
    {"bring", C_OTHERVERB, 0}, {"fetch", C_OTHERVERB, 0}, {"get", C_OTHERVERB, 0}, {"grab", C_OTHERVERB, 0},
    {"pick", C_OTHERVERB, 0}, {"take", C_OTHERVERB, 0}, {"clean", C_OTHERVERB, 0}, {"move", C_OTHERVERB, 0},
    {"go", C_OTHERVERB, 0}, {"open", C_OTHERVERB, 0}, {"put", C_OTHERVERB, 0}, {"give", C_OTHERVERB, 0},
    {"deliver", C_OTHERVERB, 0}, {"carry", C_OTHERVERB, 0}, {"count", C_OTHERVERB, 0}, {"tell", C_OTHERVERB, 0},
    {"가져", C_OTHERVERB, 0}, {"갖다", C_OTHERVERB, 0}, {"집어", C_OTHERVERB, 0}, {"치워", C_OTHERVERB, 0},
    {"옮겨", C_OTHERVERB, 0}, {"청소", C_OTHERVERB, 0}, {"열어", C_OTHERVERB, 0}, {"가서", C_OTHERVERB, 0},
    {"가줘", C_OTHERVERB, 0}, {"세어", C_OTHERVERB, 0}, {"줍", C_OTHERVERB, 0},
    // 부정 — 문맥 밖에서 나오면 거부
    {"not", C_NEG, 0}, {"no", C_NEG, 0}, {"dont", C_NEG, 0}, {"don't", C_NEG, 0}, {"never", C_NEG, 0},
    {"except", C_NEG, 0}, {"without", C_NEG, 0}, {"but", C_NEG, 0}, {"isn't", C_NEG, 0}, {"isnt", C_NEG, 0},
    {"말고", C_NEG, 0}, {"빼고", C_NEG, 0}, {"아닌", C_NEG, 0}, {"아니고", C_NEG, 0}, {"안", C_NEG, 0},
    {"아니라", C_NEG, 0}, {"제외", C_NEG, 0}, {"마", C_NEG, 0}, {"마라", C_NEG, 0}, {"말아", C_NEG, 0},
    // 피하기(정규화가 '가지 마' 따위를 이것으로 바꿔 둔다)
    {"avoid", C_AVOID, 0}, {"avoidgo", C_AVOID, 0}, {"피해", C_AVOID, 0}, {"피하", C_AVOID, 0},
    // 기한
    {"steps", C_UNIT_STEP, 0}, {"step", C_UNIT_STEP, 0}, {"ticks", C_UNIT_STEP, 0}, {"tick", C_UNIT_STEP, 0},
    {"moves", C_UNIT_STEP, 0}, {"틱", C_UNIT_STEP, 0}, {"스텝", C_UNIT_STEP, 0}, {"걸음", C_UNIT_STEP, 0},
    {"seconds", C_UNIT_TIME, 0}, {"second", C_UNIT_TIME, 0}, {"secs", C_UNIT_TIME, 0}, {"sec", C_UNIT_TIME, 0},
    {"minutes", C_UNIT_TIME, 0}, {"minute", C_UNIT_TIME, 0}, {"mins", C_UNIT_TIME, 0}, {"min", C_UNIT_TIME, 0},
    {"hours", C_UNIT_TIME, 0}, {"hour", C_UNIT_TIME, 0}, {"초", C_UNIT_TIME, 0}, {"분", C_UNIT_TIME, 0},
    {"시간", C_UNIT_TIME, 0},
    {"within", C_FILLER, 0}, {"under", C_FILLER, 0}, {"안에", C_FILLER, 0}, {"이내", C_FILLER, 0},
    {"이내로", C_FILLER, 0}, {"내에", C_FILLER, 0}, {"내로", C_FILLER, 0}, {"안으로", C_FILLER, 0},
    // 조건
    {"uncertain", C_UNC_TRIG, 0}, {"unsure", C_UNC_TRIG, 0}, {"unclear", C_UNC_TRIG, 0},
    {"ambiguous", C_UNC_TRIG, 0}, {"doubt", C_UNC_TRIG, 0}, {"doubtful", C_UNC_TRIG, 0},
    {"blocked", C_BLK_TRIG, 0}, {"stuck", C_BLK_TRIG, 0}, {"obstructed", C_BLK_TRIG, 0},
    {"observe", C_ACT_OBS, 0}, {"reobserve", C_ACT_OBS, 0}, {"re-observe", C_ACT_OBS, 0}, {"recheck", C_ACT_OBS, 0},
    {"look", C_ACT_OBS, 0}, {"check", C_ACT_OBS, 0}, {"rescan", C_ACT_OBS, 0}, {"scan", C_ACT_OBS, 0},
    {"replan", C_ACT_REPLAN, 0}, {"re-plan", C_ACT_REPLAN, 0}, {"reroute", C_ACT_REPLAN, 0}, {"detour", C_ACT_REPLAN, 0},
    {"stop", C_ACT_HOLD, 0}, {"wait", C_ACT_HOLD, 0}, {"hold", C_ACT_HOLD, 0}, {"pause", C_ACT_HOLD, 0},
    {"skip", C_ACT_SKIP, 0}, {"ignore", C_ACT_SKIP, 0},
    {"봐", C_ACT_OBS, 0}, {"보고", C_ACT_OBS, 0}, {"봐줘", C_ACT_OBS, 0}, {"봐봐", C_ACT_OBS, 0},
    // 지시어·질문
    {"it", C_PRONOUN, 0}, {"that", C_PRONOUN, 0}, {"this", C_PRONOUN, 0}, {"them", C_PRONOUN, 0},
    {"those", C_PRONOUN, 0}, {"these", C_PRONOUN, 0}, {"그거", C_PRONOUN, 0}, {"저거", C_PRONOUN, 0},
    {"이거", C_PRONOUN, 0}, {"그것", C_PRONOUN, 0}, {"저것", C_PRONOUN, 0}, {"걔", C_PRONOUN, 0},
    {"one", C_PLACEHOLDER, 0}, {"ones", C_PLACEHOLDER, 0}, {"thing", C_PLACEHOLDER, 0}, {"something", C_PLACEHOLDER, 0},
    {"거", C_PLACEHOLDER, 0}, {"것", C_PLACEHOLDER, 0}, {"물건", C_PLACEHOLDER, 0},
    {"where", C_QWORD, 0}, {"what", C_QWORD, 0}, {"why", C_QWORD, 0}, {"how", C_QWORD, 0}, {"who", C_QWORD, 0},
    {"어디", C_QWORD, 0}, {"어딨", C_QWORD, 0}, {"뭐", C_QWORD, 0}, {"왜", C_QWORD, 0}, {"무슨", C_QWORD, 0},
    // 채움말(뜻 없음)
    {"a", C_FILLER, 0}, {"an", C_FILLER, 0}, {"the", C_FILLER, 0}, {"please", C_FILLER, 0}, {"pls", C_FILLER, 0},
    {"plz", C_FILLER, 0}, {"can", C_FILLER, 0}, {"could", C_FILLER, 0}, {"would", C_FILLER, 0}, {"will", C_FILLER, 0},
    {"you", C_FILLER, 0}, {"me", C_FILLER, 0}, {"i", C_FILLER, 0}, {"im", C_FILLER, 0}, {"i'm", C_FILLER, 0},
    {"my", C_FILLER, 0}, {"our", C_FILLER, 0}, {"need", C_FILLER, 0}, {"want", C_FILLER, 0}, {"to", C_FILLER, 0},
    {"for", C_FILLER, 0}, {"some", C_FILLER, 0}, {"is", C_FILLER, 0}, {"are", C_FILLER, 0}, {"with", C_FILLER, 0},
    {"of", C_FILLER, 0}, {"and", C_FILLER, 0}, {"then", C_FILLER, 0}, {"if", C_FILLER, 0}, {"when", C_FILLER, 0},
    {"whenever", C_FILLER, 0}, {"again", C_FILLER, 0}, {"color", C_FILLER, 0}, {"colour", C_FILLER, 0},
    {"colored", C_FILLER, 0}, {"coloured", C_FILLER, 0}, {"brand", C_FILLER, 0}, {"branded", C_FILLER, 0},
    {"object", C_FILLER, 0}, {"item", C_FILLER, 0}, {"just", C_FILLER, 0}, {"now", C_FILLER, 0},
    {"quickly", C_FILLER, 0}, {"thanks", C_FILLER, 0}, {"thank", C_FILLER, 0}, {"hey", C_FILLER, 0},
    {"robot", C_FILLER, 0}, {"kindly", C_FILLER, 0}, {"in", C_FILLER, 0}, {"from", C_FILLER, 0},
    {"area", C_FILLER, 0}, {"areas", C_FILLER, 0}, {"near", C_FILLER, 0}, {"around", C_FILLER, 0},
    {"somewhere", C_FILLER, 0}, {"there", C_FILLER, 0}, {"it's", C_FILLER, 0}, {"its", C_FILLER, 0},
    {"do", C_FILLER, 0}, {"try", C_FILLER, 0}, {"instead", C_FILLER, 0}, {"route", C_FILLER, 0},
    {"path", C_FILLER, 0}, {"way", C_FILLER, 0}, {"another", C_FILLER, 0}, {"new", C_FILLER, 0},
    {"at", C_FILLER, 0}, {"most", C_FILLER, 0}, {"max", C_FILLER, 0}, {"by", C_FILLER, 0}, {"be", C_FILLER, 0},
    {"up", C_FILLER, 0}, {"ok", C_FILLER, 0}, {"okay", C_FILLER, 0}, {"lost", C_FILLER, 0}, {"looking", C_FILLER, 0},
    {"좀", C_FILLER, 0}, {"제발", C_FILLER, 0}, {"나", C_FILLER, 0}, {"내", C_FILLER, 0}, {"저", C_FILLER, 0},
    {"제", C_FILLER, 0}, {"우리", C_FILLER, 0}, {"한번", C_FILLER, 0}, {"빨리", C_FILLER, 0}, {"어서", C_FILLER, 0},
    {"해", C_FILLER, 0}, {"해줘", C_FILLER, 0}, {"해줄래", C_FILLER, 0}, {"해주세요", C_FILLER, 0}, {"해요", C_FILLER, 0},
    {"하고", C_FILLER, 0}, {"해서", C_FILLER, 0}, {"줘", C_FILLER, 0}, {"주세요", C_FILLER, 0}, {"줄래", C_FILLER, 0},
    {"요", C_FILLER, 0}, {"있는", C_FILLER, 0}, {"있어", C_FILLER, 0}, {"있을", C_FILLER, 0}, {"있던", C_FILLER, 0},
    {"색", C_FILLER, 0}, {"색깔", C_FILLER, 0}, {"색상", C_FILLER, 0}, {"브랜드", C_FILLER, 0}, {"다시", C_FILLER, 0},
    {"그리고", C_FILLER, 0}, {"쪽", C_FILLER, 0}, {"근처", C_FILLER, 0}, {"주변", C_FILLER, 0},
    {"길", C_FILLER, 0}, {"길이", C_FILLER, 0}, {"다른", C_FILLER, 0}, {"경로", C_FILLER, 0},
    {"하나", C_FILLER, 0}, {"개", C_FILLER, 0}, {"씩", C_FILLER, 0}, {"부탁", C_FILLER, 0}, {"부탁해", C_FILLER, 0},
    {"부탁해요", C_FILLER, 0}, {"부탁드려요", C_FILLER, 0}, {"부탁합니다", C_FILLER, 0}, {"로봇", C_FILLER, 0},
    {"잃어버린", C_FILLER, 0}, {"잃어버렸어", C_FILLER, 0}, {"는데", C_FILLER, 0}, {"면", C_FILLER, 0},
    // 조사
    {"를", C_FILLER, 0}, {"을", C_FILLER, 0}, {"은", C_FILLER, 0}, {"는", C_FILLER, 0}, {"이", C_FILLER, 0},
    {"가", C_FILLER, 0}, {"도", C_FILLER, 0}, {"의", C_FILLER, 0}, {"에", C_FILLER, 0}, {"에서", C_FILLER, 0},
    {"로", C_FILLER, 0}, {"으로", C_FILLER, 0}, {"랑", C_FILLER, 0}, {"이랑", C_FILLER, 0}, {"와", C_FILLER, 0},
    {"과", C_FILLER, 0}, {"만", C_FILLER, 0}, {"인", C_FILLER, 0}, {"이고", C_FILLER, 0}, {"고", C_FILLER, 0},
    {"야", C_FILLER, 0}, {"이야", C_FILLER, 0}, {"에요", C_FILLER, 0}, {"예요", C_FILLER, 0}, {"이에요", C_FILLER, 0},
    {"입니다", C_FILLER, 0}, {"이면", C_FILLER, 0},
    // v0.4 구절 틀의 닻(조건·기한·회피를 여러 말투로). 틀 밖에 홀로 나오면 예전처럼 걸린다.
    {"block", C_BLK_TRIG, 0}, {"blocks", C_BLK_TRIG, 0}, {"blocking", C_BLK_TRIG, 0}, {"blockage", C_BLK_TRIG, 0},
    {"obstacle", C_BLK_TRIG, 0}, {"obstacles", C_BLK_TRIG, 0}, {"obstruction", C_BLK_TRIG, 0},
    {"plan", C_ACT_REPLAN, 0}, {"re-route", C_ACT_REPLAN, 0}, {"rethink", C_ACT_REPLAN, 0}, {"짜", C_ACT_REPLAN, 0},
    {"double-check", C_ACT_OBS, 0}, {"re-check", C_ACT_OBS, 0}, {"re-examine", C_ACT_OBS, 0}, {"verify", C_ACT_OBS, 0},
    {"re-scan", C_ACT_OBS, 0}, {"re-look", C_ACT_OBS, 0},
    {"avoiding", C_AVOID, 0}, {"avoids", C_AVOID, 0}, {"skipping", C_AVOID, 0},
    {"tops", C_FILLER, 0}, {"maximum", C_FILLER, 0}, {"limit", C_FILLER, 0}, {"less", C_FILLER, 0},
    {"fewer", C_FILLER, 0}, {"or", C_FILLER, 0}, {"before", C_FILLER, 0}, {"까지", C_FILLER, 0},
    {"한", C_FILLER, 0}, {"번", C_FILLER, 0}, {"더", C_FILLER, 0}, {"새", C_FILLER, 0}, {"새로", C_FILLER, 0},
    {"만약", C_FILLER, 0}, {"혹시", C_FILLER, 0},
    {"unless", C_NEG, 0},
    {"more", C_BADCMP, 0}, {"least", C_BADCMP, 0}, {"over", C_BADCMP, 0}, {"after", C_BADCMP, 0},
    {"longer", C_BADCMP, 0}, {"exceed", C_BADCMP, 0}, {"이상", C_BADCMP, 0}, {"초과", C_BADCMP, 0},
    {"넘게", C_BADCMP, 0}, {"넘어서", C_BADCMP, 0},
};

// 앞머리로만 알아보는 한국어 활용형(뒤는 전부 먹는다). 순서가 중요하다 — 위가 먼저.
struct Pre { const char* p; Cat c; };
const Pre kPrefix[] = {
    {"찾아와", C_OTHERVERB}, {"찾아다", C_OTHERVERB}, {"찾아서", C_OTHERVERB}, {"찾으러", C_OTHERVERB},
    {"찾", C_FIND}, {"탐색", C_FIND}, {"수색", C_FIND},
    {"가져", C_OTHERVERB}, {"갖다", C_OTHERVERB}, {"집어", C_OTHERVERB}, {"치워", C_OTHERVERB},
    {"옮겨", C_OTHERVERB}, {"청소", C_OTHERVERB},
    {"피해", C_AVOID}, {"피하", C_AVOID},
    {"불확실", C_UNC_TRIG}, {"애매", C_UNC_TRIG}, {"모호", C_UNC_TRIG}, {"헷갈", C_UNC_TRIG},
    {"막히", C_BLK_TRIG}, {"막혀", C_BLK_TRIG}, {"막혔", C_BLK_TRIG}, {"막힌", C_BLK_TRIG}, {"가로막", C_BLK_TRIG},
    {"관찰", C_ACT_OBS}, {"관측", C_ACT_OBS}, {"확인", C_ACT_OBS}, {"살펴", C_ACT_OBS},
    {"재계획", C_ACT_REPLAN}, {"계획", C_ACT_REPLAN}, {"재관찰", C_ACT_OBS}, {"재확인", C_ACT_OBS}, {"우회", C_ACT_REPLAN}, {"돌아가", C_ACT_REPLAN}, {"돌아서", C_ACT_REPLAN},
    {"멈춰", C_ACT_HOLD}, {"멈추", C_ACT_HOLD}, {"대기", C_ACT_HOLD}, {"기다", C_ACT_HOLD}, {"정지", C_ACT_HOLD},
    {"건너뛰", C_ACT_SKIP}, {"넘어가", C_ACT_SKIP}, {"무시", C_ACT_SKIP},
    {"어디", C_QWORD}, {"어딨", C_QWORD},
};

// 여러 낱말로 된 말을 한 토막으로 먼저 바꾼다(소문자화 뒤). 부정이 '피하기' 로 쓰인 경우를
// 여기서 가른다 — 남은 부정은 전부 거부된다.
struct Rep { const char* from; const char* to; };
const Rep kRep[] = {
    // v0.4: 'but' + 회피 구절은 부정이 아니다 — 원래 구절보다 먼저 바꾼다(바꾼 뒤엔 빈칸이 둘이 되어 안 맞는다)
    {"but stay away from", " avoidgo "}, {"but keep away from", " avoidgo "}, {"but stay off", " avoidgo "},
    {"but keep off", " avoidgo "}, {"but stay clear of", " avoidgo "}, {"but avoid", " avoid "},
    {"but don't go near", " avoidgo "}, {"but do not go near", " avoidgo "},
    {"without going near", " avoidgo "}, {"without going to", " avoidgo "}, {"without going into", " avoidgo "},
    {"without entering", " avoidgo "},
    {"get blocked", " blocked "}, {"gets blocked", " blocked "}, {"get stuck", " stuck "}, {"gets stuck", " stuck "},
    {"do not go near", " avoidgo "}, {"don't go near", " avoidgo "}, {"dont go near", " avoidgo "},
    {"do not go to", " avoidgo "}, {"don't go to", " avoidgo "}, {"dont go to", " avoidgo "},
    {"do not go into", " avoidgo "}, {"don't go into", " avoidgo "}, {"dont go into", " avoidgo "},
    {"do not enter", " avoidgo "}, {"don't enter", " avoidgo "}, {"dont enter", " avoidgo "},
    {"don't go", " avoidgo "}, {"dont go", " avoidgo "}, {"do not go", " avoidgo "},
    {"stay away from", " avoidgo "}, {"keep away from", " avoidgo "}, {"stay clear of", " avoidgo "},
    {"steer clear of", " avoidgo "}, {"keep off", " avoidgo "}, {"stay off", " avoidgo "},
    {"not sure", " unsure "}, {"not certain", " unsure "}, {"can't tell", " unsure "}, {"cant tell", " unsure "},
    {"observe again", " observe "}, {"observe_again", " observe "}, {"look again", " observe "},
    {"check again", " observe "}, {"take another look", " observe "}, {"look closer", " observe "},
    {"another way", " replan "}, {"another route", " replan "}, {"another path", " replan "},
    {"a different route", " replan "}, {"different route", " replan "}, {"different path", " replan "},
    {"find a way around", " replan "}, {"go around", " replan "}, {"plan again", " replan "},
    {"move on", " skip "},
    {"in the way", " blocked "}, {"in your way", " blocked "}, {"at least", " more "}, {"more than", " more "},
    {"넘기지 말고", " 이내 "}, {"넘기지말고", " 이내 "}, {"넘기지 마", " 이내 "}, {"안 넘게", " 이내 "},
    {"말고 다른 데서", " 피해 "}, {"말고 다른 곳에서", " 피해 "}, {"말고 다른데서", " 피해 "},
    {"search for", " find "}, {"look for", " find "}, {"looking for", " find "}, {"look around for", " find "},
    {"hunt for", " find "}, {"track down", " find "}, {"hunt down", " find "},
    {"확실하지 않으면", " 불확실하면 "}, {"확실치 않으면", " 불확실하면 "}, {"확실하지않으면", " 불확실하면 "},
    {"들어가지 말고", " 피해 "}, {"들어가지 마", " 피해 "}, {"들어가지마", " 피해 "}, {"들어가지말고", " 피해 "},
    {"가지 말고", " 피해 "}, {"가지말고", " 피해 "}, {"가지 마", " 피해 "}, {"가지마", " 피해 "},
    {"가지 말아", " 피해 "}, {"가지말아", " 피해 "}, {"가까이 가지 마", " 피해 "}, {"가까이 가지마", " 피해 "},
    {"다른 길로", " 재계획 "}, {"다른 길", " 재계획 "}, {"다른길로", " 재계획 "}, {"돌아서 가", " 재계획 "},
};

constexpr int kBuf = 512;
constexpr int kMaxSeg = 64;


// 토막: 문법어(범주) 또는 사전 낱말(KB 항목)
struct Seg {
    Cat c;
    std::uint8_t v;
    std::uint16_t num;
    const KBEntry* kb;   // 내용어면 사전 항목
    char text[24];
};

bool starts(const char* s, const char* p) { return std::strncmp(s, p, std::strlen(p)) == 0; }

void copy_trunc(char* dst, const char* src, int len, int cap) {
    int n = len < cap - 1 ? len : cap - 1;
    std::memcpy(dst, src, n);
    dst[n] = 0;
}

// 문자열 안의 한 구절을 바꾼다(고정 버퍼, 넘치면 안 바꾼다). 영어 구절은 낱말 경계에서만.
void replace_all(char* s, const char* from, const char* to) {
    const std::size_t fl = std::strlen(from), tl = std::strlen(to);
    char tmp[kBuf];
    const bool ascii = (unsigned char)from[0] < 0x80;
    char* from_pos = s;
    for (;;) {
        char* p = std::strstr(from_pos, from);
        while (p && ascii) {
            const bool lo = (p == s) || p[-1] == ' ';
            const char r = p[fl];
            if (lo && (r == 0 || r == ' ')) break;
            p = std::strstr(p + 1, from);
        }
        if (!p) return;
        const std::size_t head = std::size_t(p - s);
        if (std::strlen(s) - fl + tl >= kBuf) return;
        std::snprintf(tmp, kBuf, "%.*s%s%s", int(head), s, to, p + fl);
        std::strcpy(s, tmp);
        from_pos = s + head + tl;
    }
}

void normalize(const char* in, char* out) {
    int j = 0;
    for (int i = 0; in[i] && j < kBuf - 2; ++i) {
        unsigned char ch = (unsigned char)in[i];
        if (ch >= 'A' && ch <= 'Z') ch = ch - 'A' + 'a';
        // 문장부호는 빈칸으로. 아포스트로피(don't)와 붙임표(jet-black)는 남긴다.
        if (ch == '.' || ch == ',' || ch == '!' || ch == '?' || ch == ';' || ch == ':' || ch == '"' ||
            ch == '(' || ch == ')' || ch == '[' || ch == ']' || ch == '=' || ch == '~' || ch == '\t' ||
            ch == '\n' || ch == '\r' || ch == '/' || ch == '_')
            ch = ' ';
        out[j++] = char(ch);
    }
    out[j] = 0;
    char tmp[kBuf + 2];
    std::snprintf(tmp, sizeof tmp, " %s ", out);
    int k = 0;
    for (int i = 0; tmp[i] && k < kBuf - 1; ++i) {
        if (tmp[i] == ' ' && k > 0 && out[k - 1] == ' ') continue;
        out[k++] = tmp[i];
    }
    out[k] = 0;
    replace_all(out, "\xE2\x80\x99", "'");
    for (const Rep& r : kRep) replace_all(out, r.from, r.to);
}

struct Ctx {
    const KnowledgeBase* kb;
    const ParserOptions* opt;
};

bool kb_usable(const KBEntry& e, const ParserOptions& o) {
    if (e.rel == REL_SYN && !o.use_syn) return false;
    if ((e.rel == REL_NEAR || e.rel == REL_HYPO || e.rel == REL_HYPER || e.rel == REL_MOD) && !o.use_rel) return false;
    return true;
}

// 최장일치: 문법어 표와 사전을 함께 본다. 같은 길이면 사전(내용어)이 이긴다.
bool longest(const char* s, const Ctx& cx, const Lex** lex, const KBEntry** kbe, std::size_t* len) {
    *lex = nullptr; *kbe = nullptr; *len = 0;
    for (const Lex& l : kLex) {
        const std::size_t n = std::strlen(l.w);
        if (n > *len && std::strncmp(s, l.w, n) == 0) { *lex = &l; *len = n; }
    }
    if (cx.kb)
        for (int i = 0; i < cx.kb->n; ++i) {
            const KBEntry& e = cx.kb->e[i];
            if (!kb_usable(e, *cx.opt)) continue;
            const std::size_t n = std::strlen(e.word);
            if (n >= *len && n > 0 && std::strncmp(s, e.word, n) == 0) { *kbe = &e; *lex = nullptr; *len = n; }
        }
    return *len > 0;
}

bool push(Seg* segs, int& n, Cat c, std::uint8_t v, std::uint16_t num, const KBEntry* kb, const char* t, int tl) {
    if (n >= kMaxSeg) return false;
    segs[n].c = c; segs[n].v = v; segs[n].num = num; segs[n].kb = kb;
    copy_trunc(segs[n].text, t, tl, 24);
    ++n;
    return true;
}

// 한 토큰을 토막으로. 영어 토큰은 통째로만 맞는다('card' 가 'cardigan' 을 먹는 식의 추측을 막는다).
// 한국어는 붙여 쓴 것을 최장일치로 가르고, 활용형은 앞머리로 본다.
bool segment(const char* tok, const Ctx& cx, Seg* segs, int& n, char* bad, int badcap) {
    const bool ascii = (unsigned char)tok[0] < 0x80;
    const std::size_t len = std::strlen(tok);
    if (cx.opt->use_constr && std::strspn(tok, "-") == len) return true;   // '--' 같은 줄표는 문장부호
    if (ascii) {
        std::size_t i = 0;
        std::uint32_t num = 0;
        while (i < len && tok[i] >= '0' && tok[i] <= '9') { num = num * 10 + (tok[i] - '0'); if (num > 99999) num = 99999; ++i; }
        if (i > 0) {
            if (!push(segs, n, C_NUM, 0, std::uint16_t(num), nullptr, tok, int(i))) return false;
            return i == len ? true : segment(tok + i, cx, segs, n, bad, badcap);
        }
        if (cx.kb)
            for (int k = 0; k < cx.kb->n; ++k)
                if (kb_usable(cx.kb->e[k], *cx.opt) && std::strcmp(cx.kb->e[k].word, tok) == 0)
                    return push(segs, n, C_NONE, 0, 0, &cx.kb->e[k], tok, int(len));
        for (const Lex& l : kLex)
            if (std::strcmp(l.w, tok) == 0) return push(segs, n, l.c, l.v, 0, nullptr, tok, int(len));
        if (len > 2 && tok[len - 2] == '\'' && tok[len - 1] == 's') {   // 소유격 's
            char base[48];
            copy_trunc(base, tok, int(len - 2), 48);
            return segment(base, cx, segs, n, bad, badcap);
        }
        if (cx.opt->use_constr) return push(segs, n, C_UNK, 0, 0, nullptr, tok, int(len));
        copy_trunc(bad, tok, int(len), badcap);
        return false;
    }
    const char* p = tok;
    while (*p) {
        if (*p >= '0' && *p <= '9') {
            std::uint32_t num = 0;
            const char* q = p;
            while (*q >= '0' && *q <= '9') { num = num * 10 + (*q - '0'); if (num > 99999) num = 99999; ++q; }
            if (!push(segs, n, C_NUM, 0, std::uint16_t(num), nullptr, p, int(q - p))) return false;
            p = q;
            continue;
        }
        // 사전 낱말이 활용형 앞머리보다 먼저다(예: '체크카드' 가 '확인' 류로 먹히지 않게)
        const Lex* lx; const KBEntry* ke; std::size_t wl;
        longest(p, cx, &lx, &ke, &wl);
        bool pre_hit = false;
        for (const Pre& pr : kPrefix) {
            if (starts(p, pr.p) && std::strlen(pr.p) >= wl) {
                if (!push(segs, n, pr.c, 0, 0, nullptr, p, int(std::strlen(p)))) return false;
                pre_hit = true;
                break;
            }
        }
        if (pre_hit) return true;   // 활용형은 뒤를 전부 먹는다
        if (wl == 0) {
            if (cx.opt->use_constr) return push(segs, n, C_UNK, 0, 0, nullptr, p, int(std::strlen(p)));
            copy_trunc(bad, p, int(std::strlen(p)), badcap);
            return false;
        }
        if (!push(segs, n, ke ? C_NONE : lx->c, ke ? 0 : lx->v, 0, ke, p, int(wl))) return false;
        p += wl;
    }
    return true;
}

int popcount8(std::uint8_t m) { int c = 0; while (m) { c += m & 1; m >>= 1; } return c; }
int lowbit(std::uint8_t m) { for (int i = 0; i < 8; ++i) if (m & (1u << i)) return i; return 0; }

}  // namespace

// ---------------------------------------------------------------- 사전 갱신(충돌 검사)
const KBEntry* kb_find(const KnowledgeBase& kb, const char* word) {
    for (int i = 0; i < kb.n; ++i) if (std::strcmp(kb.e[i].word, word) == 0) return &kb.e[i];
    return nullptr;
}

KbAdd kb_add(KnowledgeBase& kb, const char* word, std::uint8_t cat, std::uint8_t rel, std::uint8_t conf, std::uint8_t mask) {
    if (!word || !*word || std::strlen(word) >= sizeof(KBEntry{}.word)) return KbAdd::Invalid;
    if (cat < KB_COLOR || cat > KB_MOD || rel < REL_CANON || rel > REL_MOD || conf > 100) return KbAdd::Invalid;
    if (cat != KB_MOD && mask == 0) return KbAdd::Invalid;
    for (const char* c = word; *c; ++c) if (*c == ' ' || *c == ',') return KbAdd::Invalid;
    // 문법어와 겹치면 충돌 — '안' 을 색으로 가르치면 부정이 사라진다
    for (const Lex& l : kLex) if (std::strcmp(l.w, word) == 0) return KbAdd::Conflict;
    if (const KBEntry* e = kb_find(kb, word)) {
        if (e->cat == cat && e->mask == mask) return KbAdd::Duplicate;
        return KbAdd::Conflict;   // 같은 말이 다른 뜻 — 기존 지식과 충돌
    }
    if (kb.n >= kMaxKB) return KbAdd::Full;
    KBEntry& e = kb.e[kb.n++];
    std::memset(&e, 0, sizeof e);
    std::strcpy(e.word, word);
    e.cat = cat; e.rel = rel; e.conf = conf; e.mask = mask;
    return KbAdd::Added;
}

// ---------------------------------------------------------------- 해석
Status CommandParser::parse(const char* input, GoalSpec& g) {
    const ParseResult r = parse_detailed(input);
    last_ = r;
    if (r.status == Status::Ok) g = r.cand[0];
    else g = GoalSpec{};
    return r.status;
}

ParseResult CommandParser::parse_detailed(const char* input) {
    ParseResult R{};
    R.status = Status::InvalidInput;
    R.interp = Interp::Unknown;
    R.reason = "";
    auto fail = [&](Status s, Interp in, const char* why) { R.status = s; R.interp = in; R.reason = why; last_ = R; return R; };
    if (!input || !*input) return fail(Status::InvalidInput, Interp::Unknown, "empty");
    if (std::strlen(input) >= kBuf - 64) return fail(Status::InvalidInput, Interp::Unknown, "too_long");

    char norm[kBuf];
    normalize(input, norm);
    Seg segs[kMaxSeg];
    int n = 0;
    const Ctx cx{kb_, &opt_};
    char* save = nullptr;
    char work[kBuf];
    std::strcpy(work, norm);
    for (char* t = strtok_r(work, " ", &save); t; t = strtok_r(nullptr, " ", &save)) {
        if (!segment(t, cx, segs, n, R.token, sizeof R.token))
            return fail(Status::Unsupported, Interp::Unknown, R.token[0] ? "unknown_word" : "too_many_words");
    }

    // ---- v0.4 구절 틀(construction). 조건·기한·회피는 말투가 넓다("if something's blocking you, replan" ·
    // "you have 120 steps" · "막혀 있으면 다시 계획하고"). 틀의 닻(방아쇠+행동, 수+걸음 단위, 회피어+구역)을 찾고,
    // **그 틀 창 안의** 모르는 말만 연결어로 보고 넘어간다. 틀 밖의 모르는 말은 예전처럼 거부한다.
    // 창 안이라도 내용어·찾기 동사·부정·다른 동사는 모르는 말이 아니므로 그대로 판정에 간다.
    std::uint8_t pair_ty[kMaxSeg] = {0};   // 행동 토막이 짝지은 방아쇠 종류(1=불확실, 2=막힘)
    R.tolerated = 0;
    if (opt_.use_constr) {
        bool tol[kMaxSeg] = {false};
        auto hard = [&](int k) {   // 창을 끊는 토막: 내용어 · 동사 · 부정 · 질문
            const Seg& q = segs[k];
            return q.kb || q.c == C_FIND || q.c == C_OTHERVERB || q.c == C_NEG || q.c == C_QWORD || q.c == C_BADCMP;
        };
        auto mark = [&](int lo, int hi) {
            if (lo < 0) lo = 0;
            if (hi > n - 1) hi = n - 1;
            for (int k = lo; k <= hi; ++k) if (segs[k].c == C_UNK && !segs[k].kb) tol[k] = true;
        };
        auto act_ty = [&](Cat c) { return (c == C_ACT_OBS || c == C_ACT_SKIP) ? 1 : (c == C_ACT_REPLAN || c == C_ACT_HOLD) ? 2 : 0; };
        constexpr int kWin = 6;
        for (int i = 0; i < n; ++i) {
            const int want = segs[i].kb ? 0 : act_ty(segs[i].c);
            if (!want) continue;
            const Cat tc = want == 1 ? C_UNC_TRIG : C_BLK_TRIG;
            int best = -1;
            for (int d = 1; d <= kWin && best < 0; ++d)
                for (int side = 0; side < 2; ++side) {
                    const int j = side ? i + d : i - d;
                    if (j < 0 || j >= n || segs[j].kb || segs[j].c != tc) continue;
                    bool clean = true;
                    for (int k = (j < i ? j : i) + 1; k < (j < i ? i : j); ++k)
                        if (hard(k) || (!segs[k].kb && (act_ty(segs[k].c) || segs[k].c == C_UNC_TRIG || segs[k].c == C_BLK_TRIG))) clean = false;
                    if (clean) { best = j; break; }
                }
            if (best < 0) continue;
            pair_ty[i] = std::uint8_t(want);
            int lo = best < i ? best : i, hi = best < i ? i : best;
            for (int b = 0; b < 2 && lo > 0 && !hard(lo - 1) && (segs[lo - 1].c == C_UNK || segs[lo - 1].c == C_FILLER); ++b) --lo;
            if (hi + 1 < n && segs[hi + 1].c == C_UNK && (unsigned char)segs[hi + 1].text[0] >= 0x80) ++hi;   // 한국어 동사 꼬리(세워)
            mark(lo, hi);
        }
        for (int i = 0; i < n; ++i) {   // 기한: 수 [채움말] 걸음단위 — 앞뒤 두 토막까지
            if (segs[i].kb || segs[i].c != C_NUM) continue;
            int j = i + 1;
            while (j < n && segs[j].c == C_FILLER && !segs[j].kb) ++j;
            if (j >= n || segs[j].kb || segs[j].c != C_UNIT_STEP) continue;
            int lo = i, hi = j;
            for (int b = 0; b < 2 && lo > 0 && !hard(lo - 1); ++b) --lo;
            for (int b = 0; b < 2 && hi < n - 1 && !hard(hi + 1); ++b) ++hi;
            mark(lo, hi);
        }
        for (int i = 0; i < n; ++i) {   // 회피: 회피어와 구역이 세 토막 안
            if (segs[i].kb || segs[i].c != C_AVOID) continue;
            for (int j = i - 3; j <= i + 3; ++j) {
                if (j < 0 || j >= n || !segs[j].kb || segs[j].kb->cat != KB_ZONE) continue;
                int lo = j < i ? j : i, hi = j < i ? i : j;
                --lo;
                // 구역 뒤의 모르는 말은 안 넘긴다('avoid the desk lamp' 의 lamp). 회피어가 끝이면(한국어 '바닥 피해
                // 주시고요') 그 뒤 한국어 꼬리 한 토막만.
                if (hi == i && hi + 1 < n && segs[hi + 1].c == C_UNK && (unsigned char)segs[hi + 1].text[0] >= 0x80) ++hi;
                mark(lo, hi);
                break;
            }
        }
        for (int i = 0; i < n; ++i) {
            if (segs[i].c != C_UNK || segs[i].kb) continue;
            if (!tol[i] || R.tolerated >= 3) {
                copy_trunc(R.token, segs[i].text, int(std::strlen(segs[i].text)), int(sizeof R.token));
                return fail(Status::Unsupported, Interp::Unknown, "unknown_word");
            }
            ++R.tolerated;
        }
    }

    int n_find = 0, n_other = 0, n_neg = 0, n_q = 0, n_pron = 0, n_place = 0;
    std::uint8_t obj_m = 0xFF, col_m = 0xFF, brand = 0;
    bool obj_seen = false, col_seen = false, conflict_obj = false, conflict_color = false, conflict_brand = false;
    std::uint32_t avoid = 0, cond = 0;
    int deadline = -1;
    bool avoid_ctx = false, zone_pending = false;
    std::uint8_t pending_zone_mask = 0;
    int trig = 0;
    bool stray_act = false, time_unit = false, num_no_unit = false, n_badcmp = false;
    std::uint8_t conf = 100;
    bool novel = false;
    std::uint8_t rel_used = 0;
    const std::uint8_t all_obj = 0x1E, all_col = 0x1E;   // 비트 1..4

    for (int i = 0; i < n; ++i) {
        const Seg& s = segs[i];
        if (s.kb) {
            const KBEntry& e = *s.kb;
            if (e.rel == REL_NEAR || e.rel == REL_HYPO || e.rel == REL_MOD || e.rel == REL_HYPER) { novel = true; ++rel_used; }
            if (e.rel != REL_HYPER && e.conf < conf) conf = e.conf;
            switch (e.cat) {
            case KB_COLOR:
                col_m &= e.mask; col_seen = true;
                if (!(col_m & all_col)) conflict_color = true;
                break;
            case KB_BRANDCARD:
                obj_m &= std::uint8_t(1u << kCard); obj_seen = true;
                if (!(obj_m & all_obj)) conflict_obj = true;
                [[fallthrough]];
            case KB_BRAND: {
                const std::uint8_t b = std::uint8_t(lowbit(e.mask));
                if (brand && brand != b) conflict_brand = true;
                brand = b;
                break;
            }
            case KB_OBJECT:
                obj_m &= e.mask; obj_seen = true;
                if (!(obj_m & all_obj)) conflict_obj = true;
                break;
            case KB_ZONE:
                if (avoid_ctx) avoid |= e.mask;
                else { zone_pending = true; pending_zone_mask |= e.mask; }
                break;
            default: break;   // 수식어: 목표를 안 바꾼다
            }
            continue;
        }
        switch (s.c) {
        case C_AVOID:
            if (zone_pending) { avoid |= pending_zone_mask; zone_pending = false; pending_zone_mask = 0; }
            avoid_ctx = true;
            break;
        case C_FIND: ++n_find; avoid_ctx = false; break;
        case C_OTHERVERB: ++n_other; break;
        case C_NEG: ++n_neg; break;
        case C_QWORD: ++n_q; break;
        case C_PRONOUN: ++n_pron; break;
        case C_PLACEHOLDER: ++n_place; break;
        case C_NUM: {
            int j = i + 1;
            while (j < n && segs[j].c == C_FILLER && !segs[j].kb) ++j;
            if (j < n && !segs[j].kb && segs[j].c == C_UNIT_STEP) { deadline = s.num; i = j; }
            else if (j < n && !segs[j].kb && segs[j].c == C_UNIT_TIME) { time_unit = true; i = j; }
            else num_no_unit = true;
            break;
        }
        case C_UNIT_STEP: num_no_unit = true; break;
        case C_UNIT_TIME: time_unit = true; break;
        case C_UNC_TRIG: trig = 1; cond |= kCondUncertainStated; avoid_ctx = false; break;
        case C_BLK_TRIG: trig = 2; cond |= kCondBlockedStated; avoid_ctx = false; break;
        case C_ACT_OBS: if (trig != 1 && pair_ty[i] != 1) stray_act = true; break;
        case C_ACT_REPLAN: if (trig != 2 && pair_ty[i] != 2) stray_act = true; break;
        case C_ACT_HOLD: if (trig == 2 || pair_ty[i] == 2) cond |= kCondBlockedHold; else stray_act = true; break;
        case C_ACT_SKIP: if (trig == 1 || pair_ty[i] == 1) cond |= kCondUncertainSkip; else stray_act = true; break;
        case C_BADCMP: n_badcmp = true; break;
        default: break;
        }
    }
    R.relations_used = rel_used;

    // 판정 순서: 범위 밖 → 충돌 → 대상 없음/여럿 → 기한
    if (n_other) return fail(Status::Unsupported, Interp::OutOfScope, "unsupported_verb");
    if (n_q) return fail(Status::Unsupported, Interp::OutOfScope, "question");
    if (n_neg) return fail(Status::Unsupported, Interp::OutOfScope, "negation");
    if (n_badcmp) return fail(Status::Unsupported, Interp::OutOfScope, "unsupported_comparator");
    if (stray_act) return fail(Status::Unsupported, Interp::OutOfScope, "action_without_condition");
    if (time_unit) return fail(Status::Unsupported, Interp::OutOfScope, "time_unit_not_supported");
    if (zone_pending) return fail(Status::Unsupported, Interp::OutOfScope, "location_hint_not_supported");
    if (conflict_obj) return fail(Status::Conflict, Interp::Conflict, "multiple_targets");
    if (conflict_color) return fail(Status::Conflict, Interp::Conflict, "conflicting_color");
    if (conflict_brand) return fail(Status::Conflict, Interp::Conflict, "conflicting_brand");
    if (!obj_seen) return fail(Status::Ambiguous, Interp::Ambiguous, (n_pron || n_place || col_seen || brand) ? "no_referent" : "no_target");
    obj_m &= all_obj;
    // 조합 추론: 브랜드는 카드에만 붙는다 — 상위어(용기 등)의 후보를 좁히는 데도 쓴다
    if (brand) {
        if (!(obj_m & (1u << kCard))) return fail(Status::Conflict, Interp::Conflict, "brand_only_for_cards");
        obj_m = std::uint8_t(1u << kCard);
    }
    if (n_find == 0) return fail(Status::Ambiguous, Interp::Ambiguous, "no_verb");
    if (num_no_unit) return fail(Status::Ambiguous, Interp::Ambiguous, "number_without_unit");
    if (deadline == 0 || deadline > int(GoalManager::kMaxDeadline)) return fail(Status::InvalidInput, Interp::Conflict, "deadline_out_of_range");
    if (avoid_ctx && avoid == 0) return fail(Status::Ambiguous, Interp::Ambiguous, "avoid_without_zone");
    if (col_seen) col_m &= all_col; else col_m = 0;

    // 후보 펼치기: 물체 × 색. 둘 이상이면 되묻는다(추측하지 않는다).
    const int no = popcount8(obj_m), nc = col_seen ? popcount8(col_m) : 1;
    R.n_cand = 0;
    for (int o = 1; o <= 4; ++o) {
        if (!(obj_m & (1u << o))) continue;
        for (int c = col_seen ? 1 : 0; c <= (col_seen ? 4 : 0); ++c) {
            if (col_seen && !(col_m & (1u << c))) continue;
            if (R.n_cand >= 4) break;
            GoalSpec& g = R.cand[R.n_cand++];
            g.goal_type = kGoalFindObject;
            g.target_type = std::uint16_t(o);
            g.required_attributes = pack_attrs(Color(c), Brand(brand));
            g.constraints = avoid | cond;
            g.deadline = deadline > 0 ? Tick(deadline) : 0;
        }
    }
    R.conf = conf;
    if (no * nc > 1) return fail(Status::Ambiguous, Interp::Ambiguous, "multiple_meanings");
    if (conf < opt_.min_conf) return fail(Status::Ambiguous, Interp::Ambiguous, "low_confidence");
    R.status = Status::Ok;
    R.interp = novel ? Interp::NovelComposite : Interp::Known;
    R.reason = "ok";
    last_ = R;
    return R;
}

void goal_to_canon(const GoalSpec& g, char* buf, int n) {
    static const char* kObj[] = {"none", "card", "key", "cup", "box"};
    static const char* kCol[] = {"", "red", "blue", "green", "black"};
    static const char* kBr[] = {"", "visa", "master"};
    static const char* kZ[] = {"floor", "desk", "shelf", "counter"};
    int k = std::snprintf(buf, n, "OK type=%s", g.target_type < kNumObj ? kObj[g.target_type] : "?");
    Color c = attr_color(g.required_attributes);
    Brand b = attr_brand(g.required_attributes);
    if (c && c < kNumColor && k < n) k += std::snprintf(buf + k, n - k, " color=%s", kCol[c]);
    if (b && b < kNumBrand && k < n) k += std::snprintf(buf + k, n - k, " brand=%s", kBr[b]);
    if ((g.constraints & kAvoidMask) && k < n) {
        k += std::snprintf(buf + k, n - k, " avoid=");
        bool first = true;
        for (int z = 0; z < kNumZones; ++z)
            if (g.constraints & (1u << z)) {
                if (k < n) k += std::snprintf(buf + k, n - k, "%s%s", first ? "" : "+", kZ[z]);
                first = false;
            }
    }
    if (g.deadline && k < n) k += std::snprintf(buf + k, n - k, " deadline=%llu", (unsigned long long)g.deadline);
    if ((g.constraints & kCondUncertainStated) && k < n)
        k += std::snprintf(buf + k, n - k, " uncertain=%s", (g.constraints & kCondUncertainSkip) ? "skip" : "observe");
    if ((g.constraints & kCondBlockedStated) && k < n)
        k += std::snprintf(buf + k, n - k, " blocked=%s", (g.constraints & kCondBlockedHold) ? "hold" : "replan");
}

// ---------------------------------------------------------------- B. 목표 관리
Status GoalManager::validate(const GoalSpec& g, const MapHint& m, std::uint16_t& reason) const {
    reason = RS_NONE;
    if (g.goal_type != kGoalFindObject) { reason = RS_UNKNOWN_ACTION; return Status::Unsupported; }
    if (g.target_type == kObjNone || g.target_type >= kNumObj) { reason = RS_NO_EVIDENCE; return Status::InvalidInput; }
    if (attr_color(g.required_attributes) >= kNumColor || attr_brand(g.required_attributes) >= kNumBrand) {
        reason = RS_NO_EVIDENCE; return Status::InvalidInput;
    }
    if (attr_brand(g.required_attributes) != kBrandAny && g.target_type != kCard) {
        reason = RS_NO_EVIDENCE; return Status::InvalidInput;
    }
    if (g.deadline > kMaxDeadline) { reason = RS_DEADLINE; return Status::InvalidInput; }
    const std::uint32_t known = kAvoidMask | kCondUncertainSkip | kCondBlockedHold | kCondUncertainStated | kCondBlockedStated;
    if (g.constraints & ~known) { reason = RS_UNKNOWN_ACTION; return Status::InvalidInput; }
    // 피할 구역 때문에 집에서 한 칸도 못 나가면 임무가 성립하지 않는다
    if (m.w == 0 || m.h == 0) { reason = RS_OUT_OF_BOUNDS; return Status::InvalidInput; }
    const int hc = cell(m.home_x, m.home_y, m.w);
    if (g.constraints & (1u << m.zone[hc])) { reason = RS_AVOID_ZONE; return Status::InvalidInput; }
    return Status::Ok;
}

}  // namespace walp
