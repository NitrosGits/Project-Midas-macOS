#ifndef _GUNFIRE_H_
#define _GUNFIRE_H_
#include <ultra64.h>
#include <bondtypes.h>
void bullet_path_from_screen_center(coord3d* arg0, coord3d* result, enum GUNHAND arg2);
s32 check_cur_player_ammo_amount_in_inventory(AMMOTYPE ammotype);
s32 get_ammo_type_for_weapon(ITEM_IDS weapon);
s32 get_curplay_killcount(void);
f32 get_depth_offset_solo_watch_menu_inventory_page_for_item(ITEM_IDS item);
f32 get_depth_on_solo_watch_menu_page_for_item(ITEM_IDS item);
f32 get_horizontal_offset_on_solo_watch_menu_for_item(ITEM_IDS item);
s32 get_keyanalyzer_flag(void);
f32 get_lateral_position_solo_watch_menu_main_page_for_item(ITEM_IDS item);
u16 * get_ptr_first_title_line_item(ITEM_IDS item);
u16 * get_ptr_long_watch_text_for_item(ITEM_IDS item);
u16 * get_ptr_second_title_line_item(ITEM_IDS item);
f32 get_vertical_offset_on_solo_watch_menu_for_item(ITEM_IDS item);
f32 get_vertical_position_solo_watch_menu_main_page_for_item(ITEM_IDS item);
f32 get_xrotation_solo_watch_menu_for_item(ITEM_IDS item);
f32 get_yrotation_solo_watch_menu_for_item(ITEM_IDS item);
Gfx * gunDrawWatchAmmoDisplay(Gfx *gdl);
void increment_num_deaths(void);
void increment_num_suicides_display_MP(void);
void increment_num_times_killed_MwtGC(void);
Gfx * sub_GAME_7F061E18(Gfx *gdl, BeamRecord *flash, s32 arg2);

#endif
