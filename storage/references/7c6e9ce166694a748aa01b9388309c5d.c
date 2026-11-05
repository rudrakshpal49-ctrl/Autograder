#include <stdio.h>
int main(void) {
    int n, target, count = 0;
    if (scanf("%d %d", &n, &target) != 2 || n < 1 || n > 1000) return 1;
    for (int i = 0, x; i < n; ++i) {
        if (scanf("%d", &x) != 1) return 1;
        if (x == target) ++count;
    }
    printf("%d\n", count);
    return 0;
}
