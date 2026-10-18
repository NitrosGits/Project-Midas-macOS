#if !defined(TARGET_N64) && defined(__cplusplus)
/* PC port: this header shadows the toolchain's <string.h>; C++ TUs (fast3d)
 * defer to the real one, which libstdc++ needs. C TUs keep the minimal stub,
 * because the hosted header's bcopy/bzero/bcmp conflict with PR/os.h. */
#include_next <string.h>
#else

#ifndef _STRING_H_
#define _STRING_H_
#include <PR/ultratypes.h>

extern void *memcpy(void *, const void *, size_t);
extern unsigned char *strchr(const unsigned char *, int);
extern size_t strlen(const unsigned char *);

#ifndef TARGET_N64
/* PC port: the rest of the libc string functions the game and port call.
 * Without prototypes, pointer returns would be truncated to int on 64-bit. */
extern void *memset(void *, int, size_t);
extern void *memmove(void *, const void *, size_t);
extern int memcmp(const void *, const void *, size_t);
extern void *memchr(const void *, int, size_t);
extern int strcmp(const char *, const char *);
extern int strncmp(const char *, const char *, size_t);
extern char *strcpy(char *, const char *);
extern char *strncpy(char *, const char *, size_t);
extern char *strcat(char *, const char *);
extern char *strncat(char *, const char *, size_t);
extern char *strrchr(const char *, int);
extern char *strstr(const char *, const char *);
extern size_t strcspn(const char *, const char *);
extern char *strerror(int);
#endif

#endif
#endif /* TARGET_N64 */
