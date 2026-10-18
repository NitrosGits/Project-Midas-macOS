/* minimal i386 libc stub for the syntax/codegen check only */
#ifndef STUB_STDIO_H
#define STUB_STDIO_H
#include <stddef.h>
#include <stdarg.h>
typedef struct _STUB_FILE FILE;
extern FILE *stdin, *stdout, *stderr;
int printf(const char *, ...);
int fprintf(FILE *, const char *, ...);
int sprintf(char *, const char *, ...);
int snprintf(char *, size_t, const char *, ...);
int vsnprintf(char *, size_t, const char *, va_list);
int vsprintf(char *, const char *, va_list);
int vfprintf(FILE *, const char *, va_list);
int sscanf(const char *, const char *, ...);
FILE *fopen(const char *, const char *);
#ifndef STUB_NO_FILEIO
int fclose(FILE *);
#endif
int fflush(FILE *);
#ifndef STUB_NO_FILEIO
size_t fwrite(const void *, size_t, size_t, FILE *);
#endif
size_t fread(void *, size_t, size_t, FILE *);
int fseek(FILE *, long, int);
long ftell(FILE *);
int fputs(const char *, FILE *);
int puts(const char *);
int fgetc(FILE *);
char *fgets(char *, int, FILE *);
int remove(const char *);
int rename(const char *, const char *);
#define SEEK_SET 0
#define SEEK_CUR 1
#define SEEK_END 2
#define EOF (-1)
#endif
