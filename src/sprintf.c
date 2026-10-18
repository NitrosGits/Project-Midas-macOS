#include <stdarg.h>
#include <ultra64.h>
#include <string.h>
/*#include <stddef.h>*/

int _Printf(u8 *(*prout)(u8 *, const u8 *, size_t), u8 *arg, const u8 *fmt, va_list args); /* printf_port.c */



char *proutSprintf(char *dst, const char *src, size_t count) {
    return (char *) memcpy((u8 *) dst, (u8 *) src, count) + count;
}

int sprintf(char *dst, const char *fmt, ...) {
    s32 written;
    va_list args;
    va_start(args, fmt);
    written = _Printf(proutSprintf, dst, fmt, args);
    if (written >= 0) {
        dst[written] = 0;
    }
    return written-1;
}




