#include <ultra64.h>
#include <memp.h>
#include "player.h"
#include "bondinv.h"
#include "inititemslots.h"

void reinit_gunheld_totaltime(void) {
    s32 i;
  
    g_CurrentPlayer->equipallguns = FALSE;
    
    for (i = 0; i != 10; i++) {
        g_CurrentPlayer->gunheldarr[i].totaltime = -1;
    }
}

void alloc_additional_item_slots(s32 additionalentries) {
  g_CurrentPlayer->equipmaxitems = additionalentries + 0x1e;
#ifdef GE_64BIT
    /* InvItem is 0x14 bytes with 4-byte pointers, 0x20 here */
    g_CurrentPlayer->p_itemcur     = mempAllocBytesInBank((g_CurrentPlayer->equipmaxitems * sizeof(InvItem) + 0xfU | 0xf) ^ 0xf, MEMPOOL_STAGE);
#else
    g_CurrentPlayer->p_itemcur     = mempAllocBytesInBank((g_CurrentPlayer->equipmaxitems * 0x14 + 0xfU | 0xf) ^ 0xf, MEMPOOL_STAGE);
#endif
  bondinvReinitInv();
}
