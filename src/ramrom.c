#include <ultra64.h>
#include "ramrom.h"
#include <macro.h>

/**
 * @file ramrom.c
 * This file contains code to handle reading and writing rom addresses.
 */


OSIoMesg memoryMesgMB;
OSMesg memoryMesg;
OSMesgQueue memoryMesgQueue;

/**
 * 6760	70005B60
 * external
 * romCreateMesgQueue
 * creates a message queue
 */
void romCreateMesgQueue(void)
{
    osCreateMesgQueue(&memoryMesgQueue, &memoryMesg, 1);
}

/**
 * 6790	70005B90
 * doRomCopy
 * invalidate cache and do pi dma
 */
void doRomCopy(void *target, void *source, u32 size)
{
    osInvalDCache(target, size);
    osPiStartDma(&memoryMesgMB, OS_MESG_PRI_NORMAL, OS_READ, portPtr32(source), target, size, &memoryMesgQueue); /* the ROM side is a word */
}

/**
 * 67F0	70005BF0
 * romReceiveMesg
 * receives a message queue
 */
void romReceiveMesg(void)
{
    osRecvMesg(&memoryMesgQueue, NULL, OS_MESG_BLOCK);
}

/**
 * 681C	70005C1C
 * external
 * romCopy
 * copy from rom to ram
 */
void romCopy(void *target, void *source, u32 size)
{
    doRomCopy(target, source, size);
    romReceiveMesg();
}

/**
 * 6844	70005C44
 * external
 * romCopyAligned
 * aligns data, does a romCopy(), then returns aligned pointer to target
 */
#ifdef TARGET_N64
s32 romCopyAligned(void *target, void *source, s32 length)
{
    s32 *target_offset;
    s32 *target_aligned;
    s32 *source_aligned;
    s32 *source_offset;

    source_aligned = align_addr_even((intptr_t)source);
    source_offset = (intptr_t)source - (intptr_t)source_aligned;
    target_aligned = ALIGN16_a((intptr_t)target);
    target_offset = source_offset;
    romCopy(target_aligned, source_aligned, ALIGN16_a((intptr_t)source_offset + length));
    return ((intptr_t)target_aligned + target_offset);
}
#else
/* PORT: the same arithmetic in uintptr_t; it returns the copy's address */
void *romCopyAligned(void *target, void *source, s32 length)
{
    uintptr_t source_aligned = align_addr_even((uintptr_t) source);
    uintptr_t source_offset = (uintptr_t) source - source_aligned;
    uintptr_t target_aligned = ALIGN16_a((uintptr_t) target);

    romCopy((void *) target_aligned, (void *) source_aligned, (u32) ALIGN16_a(source_offset + length));
    return (void *) (target_aligned + source_offset);
}
#endif

/**
 * 68A8	70005CA8
 * doRomWrite
 * actually writes to rom (buffer on Indy)
 */
void doRomWrite(void *source, void *target, u32 size)
{
    osWritebackDCache(source, size);
    osPiStartDma(&memoryMesgMB, OS_MESG_PRI_NORMAL, OS_WRITE, portPtr32(target), source, size, &memoryMesgQueue);
}

/**
 * 6908	70005D08
 * external
 * romWrite
 * let's write to the rom (buffer on Indy)
 */
void romWrite(void *source, void *target, u32 size)
{
    doRomWrite(source, target, size);
    romReceiveMesg();
}
