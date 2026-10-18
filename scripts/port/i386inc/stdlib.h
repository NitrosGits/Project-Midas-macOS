#ifndef STUB_STDLIB_H
#define STUB_STDLIB_H
#include <stddef.h>
void *malloc(size_t);
void *calloc(size_t, size_t);
void *realloc(void *, size_t);
void free(void *);
char *getenv(const char *);
long strtol(const char *, char **, int);
unsigned long strtoul(const char *, char **, int);
double strtod(const char *, char **);
double atof(const char *);
int atoi(const char *);
void exit(int);
void abort(void);
int abs(int);
int rand(void);
void srand(unsigned);
void qsort(void *, size_t, size_t, int (*)(const void *, const void *));
#endif
