#include <stdio.h>
#include <stdlib.h>
int main(void) {
    int n, t, count = 0;
    if (scanf("%d%d", &n, &t) != 2 || n < 1 || n > 1000) return 1;
    int *a = malloc((size_t)n * sizeof *a);
    if (!a) return 1;
    for (int i = 0; i < n; ++i) if (scanf("%d", &a[i]) != 1) { free(a); return 1; }
    for (int i = 0; i <= n; ++i) if (a[i] == t) count++; /* BUG: a[n] */
    printf("%d\n", count);
    free(a);
    return 0;
}
