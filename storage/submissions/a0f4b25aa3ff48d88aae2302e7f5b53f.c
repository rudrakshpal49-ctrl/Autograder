#include <stdio.h>
int main(void) {
    int n, target, a[1000], matches = 0;
    if (scanf("%d%d", &n, &target) != 2 || n < 1 || n > 1000) return 1;
    for (int i = 0; i < n; i++) if (scanf("%d", &a[i]) != 1) return 1;
    for (int i = 0; i < n; i++) {
        int found = 0;
        for (int j = 0; j < n; j++) if (j == i && a[j] == target) found = 1;
        matches += found;
    }
    printf("%d\n", matches);
    return 0;
}
