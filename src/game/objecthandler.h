#ifndef _OBJECTHANDLER_
#define _OBJECTHANDLER_
#include <ultra64.h>
#include <bondtypes.h>
#include <image.h>

struct bondstruct_unk_animation_related {
    char* uselessPointer; // Is incremented like a count when an animation is copied from ROM to RAM but it's never read
    char* animBufferPtr1; 
    char* animBufferPtr2; 
};

struct bondstruct_unk_op07_related {
    s32 unk00;
    s32 unk04;
    s32 unk0C;
};

/*
 * Model manager slots. model.c casts a slot to a Model, so the named fields are
 * Model's leading fields (unk02 = rwdatalen, unk08 = obj, NULL when the slot is
 * free, unk10 = datas) with Model's real types; an anim slot is a whole Model.
 * The layout is unchanged in the 32-bit builds (0x20 and 0xbc bytes), and in a
 * 64-bit build the slots grow with Model instead of being overrun by it.
 */
#define MODEL_SLOT_FIELDS        \
    s16               unk00;     \
    s16               unk02;     \
    struct ChrRecord *unk04;     \
    ModelFileHeader  *unk08;     \
    RenderPosView    *unk0c;     \
    void             *unk10;     \
    f32               unk14;     \
    struct Model     *unk18;     \
    ModelNode        *unk1c;

struct AnimModelSlot {
    union {
        Model model;
        struct {
            MODEL_SLOT_FIELDS
        };
    };
};

struct ModelSlot {
    MODEL_SLOT_FIELDS
};

extern struct AnimModelSlot *g_AnimModelSlots;
extern struct ModelSlot *g_ModelSlots;

extern struct ModelHitEntry *g_ModelHitFreeList;
extern s32 g_ModelDistanceDisabled;
extern f32 g_ModelDistanceScale;
extern u32 g_ModelAnimMergingEnabled;
extern s32 D_80036410;
extern struct bondstruct_unk_animation_related* D_80036414;
extern OSMesgQueue *D_80036418;
extern void *D_8003641C;
extern u32 D_800363F0;

extern coord3d D_80036094;
extern coord3d D_800360A0;
extern coord3d D_800360AC;
extern coord3d D_800360B8;
extern coord3d D_80036244;
extern coord3d D_80036254;

extern struct Vertex* (*vtxallocator)(s32 numvertices);
extern void (*g_ModelJointPositionedFunc)(s32 mtxindex, Mtxf *mtx);
extern struct bondstruct_unk_op07_related D_800360C4[];
extern Vertex D_800363E0;
extern Vtx D_800363F8;
extern coord3d D_80036408;

void fileLoad(ModelFileHeader *header,char *name);
void load_object_into_memory_unused_maybe(ModelFileHeader *header,int *recallstring,int *targetloc,int sizeleft);

// tentative signature
PropRecord *chrGiveWeapon(ChrRecord *self, s32 PropID, ITEM_IDS ItemID, s32 flags);

// called with struct ChrRecord->field_20
ModelHitEntry* sub_GAME_7F06B120(ModelHitEntry* head, Model* context);
void sub_GAME_7F06B248(ModelHitEntry *entry);
void drawjointlist(ModelRenderData *arg0, ModelHitEntry *entry);
void sub_GAME_7F06B29C(ModelHitEntry *arg0);
ModelHitEntry *sub_GAME_7F06BB28(ModelHitEntry *modelhit);
s32 probably_damage_detail_blood_effect_related(ModelHitEntry **entryptr, coord3d *raypos, coord3d *raydir, Model **outModel, ModelNode **inoutNode);
s32 sub_GAME_7F06C010(ModelHitEntry **entryptr, coord3d *modelRayStart, coord3d *modelRayDir, Model **outModel, ModelNode **outNode);

void load_object_fill_header(struct ModelFileHeader *objheader, u8 *name, u8* dst, s32 size, struct texpool * buffer);


#endif
