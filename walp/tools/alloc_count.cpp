// 전역 operator new 를 갈아 끼워 코어 스텝 안의 힙 할당을 센다(harness.cpp 의 계수기).
// 별도 번역 단위로 둔다 — 같은 파일에서 인라인되면 GCC 가 malloc/free 짝을 new/delete 짝으로
// 오인해 경고를 낸다(-Wmismatched-new-delete 거짓 양성).
#include <cstdlib>
#include <new>

#include "harness.hpp"

void* operator new(std::size_t n) {
    if (walpeval::g_count_allocs) ++walpeval::g_alloc_count;
    if (void* p = std::malloc(n ? n : 1)) return p;
    throw std::bad_alloc();
}
void operator delete(void* p) noexcept { std::free(p); }
void operator delete(void* p, std::size_t) noexcept { std::free(p); }
