#include <stdio.h>
#define MOD 1000000007LL
int main(void) {
    int n;
    if (scanf("%d", &n) != 1 || n < 0 || n > 1000) return 1;
    long long a = 1, b = 1;
    for (int i = 2; i <= n; ++i) {
        long long c = (a + b) % MOD;
        a = b; b = c;
    }
    printf("%lld\n", n == 0 ? 1LL : b);
    return 0;
}
