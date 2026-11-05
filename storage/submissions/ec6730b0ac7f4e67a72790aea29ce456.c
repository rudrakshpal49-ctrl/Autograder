#include <stdio.h>
#define MOD 1000000007LL
int main(void) {
    int n;
    if (scanf("%d", &n) != 1 || n < 0 || n > 1000) return 1;
    long long dp[1001] = {1, 1};
    for (int i = 2; i <= n; ++i) dp[i] = (dp[i-1] + dp[i-2]) % MOD;
    printf("%lld\n", dp[n]);
    return 0;
}
