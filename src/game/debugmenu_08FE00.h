#ifndef _DEBUGMENU_08FE00_H_
#define _DEBUGMENU_08FE00_H_
#include <ultra64.h>
#include <bondtypes.h>
void debmenuSetMenu(char **labels, s32 (*positions)[2], s32 *offsets);
s32 get_highlighted_debug_option(void);
void gotoAboveDebugOption(void);
void gotoBelowDebugOption(void);
void gotoLeftDebugOption(void);
void gotoRightDebugOption(void);

#endif
