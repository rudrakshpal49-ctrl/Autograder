#include <stdio.h>
#define MOD 1000000007LL
int main(void) {
    int n; if (scanf("%d", &n) != 1 || n < 0 || n > 1000) return 1;
    long long ans = 1;
    for (int end = 1; end <= n; ++end) {
        long long a = 1, b = 1;
        for (int i = 2; i <= end; ++i) {
            long long c = (a+b) % MOD; a=b; b=c;
        }
        ans = b;
    }
    printf("%lld\n", ans);
    return 0;
}
