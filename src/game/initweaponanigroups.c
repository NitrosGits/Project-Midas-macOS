#include <ultra64.h>
#include "model.h"
#include "initactorpropstuff.h"

//uncomment when actor is worked on
//#include "chr.h"
#ifdef TARGET_N64
extern s32 get_ptr_allocated_block_for_vertices;
#else
extern struct Vertex *get_ptr_allocated_block_for_vertices(int param_1);
#endif

void init_weapon_animation_groups_maybe(void) {
    set_vtxallocator(&get_ptr_allocated_block_for_vertices);
    initWeaponAnimGroups();
}
