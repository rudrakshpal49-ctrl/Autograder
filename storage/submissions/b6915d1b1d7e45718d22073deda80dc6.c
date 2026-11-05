#include <stdio.h>
static long long ways(int n) {
    long long prev2 = 1, prev1 = 1;
    for (int step = 2; step <= n; ++step) {
        long long now = (prev1 + prev2) % 1000000007LL;
        prev2 = prev1; prev1 = now;
    }
    return prev1;
}
int main(void) {
    int n; if (scanf("%d", &n) != 1 || n < 0 || n > 1000) return 1;
    printf("%lld\n", ways(n));
    return 0;
}
