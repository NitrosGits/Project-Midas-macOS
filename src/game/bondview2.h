#ifndef _BONDVIEW2_H_
#define _BONDVIEW2_H_
#include <ultra64.h>
#include <bondtypes.h>
coord3d * bondviewGetCurrentPlayersPosition3(void);
void bondviewKillCurrentPlayer(void);
void bondviewUpdateCameraMatrices(coord3d* cam_pos, coord3d* cam_look_dir, coord3d* cam_up);
s32 get_obj_collision_flag(void);
s32 playerTick(PropRecord *prop);
void set_BONDdata_outside_watch_menu_flag(s32 arg0);
void set_obj_collision_flag(s32 flag);

#endif
