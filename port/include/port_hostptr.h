/**
 * PORT_IS_HOST_ADDR(v): is this 32-bit word a real host pointer, as opposed to
 * an N64 segment word (0x0Sxxxxxx) or a file offset?
 *
 * On PC, host pointers always live at or above 0x10000000: the Windows image is
 * rebased there, the Linux image sits at 0x08048000 (segments 8/9 are never
 * installed), and both arenas are pinned at 0x20000000+.
 *
 * The original Xbox can't follow that rule: the XBE base is forced to
 * 0x00010000, so the whole image (assets and static buffers) overlaps the N64
 * segment 0/1 word range. There the test is "above 0x10000000 OR inside the
 * image" (xbox_sys.c reads the image size from the XBE header at boot). Heap
 * allocations never hold display-list data, and every other display-list
 * allocation goes through the arenas. See docs/port/xbox-port.md.
 *
 * 64-bit builds (GE_64BIT) keep display lists and the game's 32-bit pointer
 * words at 32 bits, so a host pointer is stored as its offset into the address
 * window: the executable image plus the arenas reserved right after it
 * (pcsched.c portWindowInit), biased by 0x10000000 so it still tests as a host
 * address. portPtr32 encodes and portPtr64 decodes. Memory outside the window
 * (malloc, the stack) cannot be referenced from a display list.
 */
#ifndef PORT_HOSTPTR_H
#define PORT_HOSTPTR_H

#include <stddef.h>
#include <stdint.h>

#ifdef GE_64BIT

#ifdef __cplusplus
extern "C" {
#endif
extern uintptr_t g_PortWindowBase;
void portWindowInit(void);
void *portWindowAlloc(size_t size);
void portWindowBadPtr(const void *p);
#ifdef __cplusplus
}
#endif

/* Encoded words run from the bias up to PORT_WINDOW_NEG; words at or above it
 * decode as sign-extended negatives, so a -1 sentinel survives the trip. */
#define PORT_WINDOW_BIAS 0x10000000u
#define PORT_WINDOW_SIZE 0xE0000000u
#define PORT_WINDOW_NEG  (PORT_WINDOW_BIAS + PORT_WINDOW_SIZE)

/* A value that already fits in 32 bits (a segment word, an encoded pointer, a
 * sign-extended int) passes through: no macOS host pointer lives below 4GB. */
static inline unsigned int portPtr32(const void *p)
{
    uintptr_t v = (uintptr_t) p;
    uintptr_t off;

    if ((v >> 32) == 0 || (v >> 32) == 0xFFFFFFFFu)
    {
        return (unsigned int) v;
    }
    off = v - g_PortWindowBase;
    if (off >= PORT_WINDOW_SIZE)
    {
        portWindowBadPtr(p);
    }
    return (unsigned int) (off + PORT_WINDOW_BIAS);
}

static inline void *portPtr64(unsigned int w)
{
    if (w < PORT_WINDOW_BIAS)
    {
        return (void *) (uintptr_t) w; /* NULL, a segment word or a small offset */
    }
    if (w >= PORT_WINDOW_NEG)
    {
        return (void *) (intptr_t) (int) w; /* -1 and friends */
    }
    return (void *) (g_PortWindowBase + (w - PORT_WINDOW_BIAS));
}

#define PORT_IS_HOST_ADDR(v) ((unsigned int) (v) >= 0x10000000u)

/*
 * PTR32(T): a pointer field in a layout-bound struct (one laid over ROM bytes
 * or stepped through by literal sizes; docs/port/macos-arm64.md). It stays 4
 * bytes and holds the encoded word. The zero-length member only carries T for
 * PGET's result type; any access that skips PGET/PSET fails to compile.
 */
#define PTR32(T) union __attribute__((packed, aligned(4))) { unsigned int w; T *t_[0]; }
#define PGET(f) ((__typeof__((f).t_[0])) portPtr64((f).w))
#define PSET(f, p) ((f).w = portPtr32((const void *) (p)))
#define PTRW(f) ((f).w)

/*
 * PORT_STATIC_PTR32(slot, target): a static initializer can't encode an
 * address (clang won't fold &sym - &base), so the slot is left 0 and this
 * records {&slot, target} in __DATA,__ge_ptr32; portWindowInit writes
 * portPtr32(target) into every slot at boot. PORT_SPTR(x) is the initializer
 * placeholder: the pointer itself in 32-bit builds, 0 here.
 */
struct PortStaticPtr32
{
    unsigned int *slot;
    const void *target;
};
#define PORT_SP32_CAT2(a, b) a##b
#define PORT_SP32_CAT(a, b) PORT_SP32_CAT2(a, b)
#define PORT_STATIC_PTR32(slot, target)                                              \
    static struct PortStaticPtr32 PORT_SP32_CAT(s_PortStaticPtr32_, __COUNTER__)     \
        __attribute__((used, section("__DATA,__ge_ptr32"))) = { (unsigned int *) &(slot), \
                                                               (const void *) (target) }
#define PORT_SPTR(x) 0

#elif defined(GE_XBOX)
#ifdef __cplusplus
extern "C" {
#endif
int portXboxIsHostAddr(unsigned int v);
#ifdef __cplusplus
}
#endif
#define PORT_IS_HOST_ADDR(v) (portXboxIsHostAddr((unsigned int) (v)))
#else
#define PORT_IS_HOST_ADDR(v) ((unsigned int) (v) >= 0x10000000u)
#endif

#ifndef GE_64BIT
/* 32-bit: a pointer is its own 32-bit word */
#define portPtr32(p) ((unsigned int) (uintptr_t) (p))
#define portPtr64(w) ((void *) (uintptr_t) (w))
#define PTR32(T) T *
#define PGET(f) (f)
#define PSET(f, p) ((f) = (p))
#define PTRW(f) (*(unsigned int *) &(f))
#define PORT_STATIC_PTR32(slot, target) struct PortStaticPtr32Unused
#define PORT_SPTR(x) (x)
#endif

#endif
