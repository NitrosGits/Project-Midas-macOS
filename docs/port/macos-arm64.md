# Native macOS arm64 port: plan

Status: in progress (started 2026-10-18). The GE_64BIT build does not compile
yet. See **Progress** at the end.

## Why this is more than a recompile

The PC port has been 32-bit only. Two things in the decompiled code assume
4-byte pointers:

1. **ROM data is used in place.** Model, setup, stan, bg, font, animation,
   audio-bank and text files are loaded as the N64 memory image. The
   `romfile_*.c` converters byte-swap them in place, and the game's own
   promote step turns offsets into pointers inside the same bytes. Every
   struct laid over those bytes (`ModelNode`, `ObjectRecord`, `PadRecord`,
   `StandFileHeader`, `bg_room_data`, `fontchar`, `ALBank`, ...) has
   pointer fields at fixed 4-byte offsets.
2. **Pointer/int punning.** About 1,800 sites store pointers in
   `s32`/`u32`, declare pointer parameters as `s32`, or cast back and forth.
   In the 32-bit builds this is harmless, but the `-w` flag hides every
   warning about it.

Options ruled out (verified on macOS 27, Apple Silicon):

- **Keep all memory below 4 GB.** arm64 macOS kills any executable whose
  `__PAGEZERO` is smaller than 4 GB (exit 137), so `-pagezero_size` and
  low-address `mmap` are both unavailable. Nothing can live below 4 GB.
- **`__ptr32` fields.** Clang accepts `T * __ptr32 __uptr` on arm64 and makes
  it 4 bytes, but conversion is plain truncate/zero-extend, so a pointer
  above 4 GB can't round-trip.
- **Static initializers with link-time offsets.** Clang won't emit
  `&sym - &base` in a C static initializer, even though the Mach-O assembler
  supports 32-bit SUBTRACTOR relocations.
- **16-byte `Gfx` (as in the PD port).** ROM model files carry 8-byte display
  lists that are used in place and addressed by offset. Widening them would
  need a full expand-and-remap converter per file type.
- **Translation layers (Rosetta/Wine).** These are not native, and Rosetta is
  going away after macOS 27.

## Design

### The address window

A 64-bit build keeps every 32-bit pointer word (display lists, ROM-mapped
structs, the game's punned `s32`s) at 32 bits by storing a host pointer as its
**offset into an address window**:

- The window base is the executable's Mach-O header (`_mh_execute_header`). The
  window covers the image (globals, static display lists), which includes a
  1 GB zerofill reservation (`s_WindowArena`, pcsched.c), mapped lazily with
  the executable. The memp arena, the high arena and the ROM image are carved
  from it (`portWindowAlloc`). It can't be mapped after the image at run time:
  macOS treats an mmap address as a hint, and dylibs and malloc's large
  regions take the space after the image, by an amount that changes with ASLR.
- Encoding: `portPtr32(p) = p - base + 0x10000000`. The bias keeps every
  encoded word `>= 0x10000000`, so `PORT_IS_HOST_ADDR` and the segment-word
  tests (`0x0Sxxxxxx`) keep working unchanged. Words that already fit in 32
  bits (NULL, segment words, small ints, sign-extended negatives) pass
  through.
- Decoding: `portPtr64(w)`. Words below the bias pass through, and words at or
  above `0xF0000000` decode as sign-extended negatives (`-1` sentinels).
- Window size: `0xE0000000` (3.5 GB) above the bias.
- Memory outside the window (malloc, thread stacks, dylibs) **cannot** be
  encoded. `portPtr32` calls `portWindowBadPtr` (log and abort) instead of
  truncating, so every violation shows up at the first bad store.

See `port/include/port_hostptr.h`.

### `Gfx` stays 8 bytes

Under `GE_64BIT`, `Gwords` are `unsigned int`. The runtime `g*` macros encode
address operands with `portPtr32`. fast3d decodes them in `seg_addr` with
`portPtr64`. The static `gs*` forms can't encode an address at compile time,
so static display lists with address operands are fixed at startup by the
static relocation table (below). The compiler flags each such site because
the truncating initializer is not a compile-time constant.

### Struct fields: `PTR32(T)`

A pointer field in a **layout-bound** struct becomes `PTR32(T)`:

```c
PTR32(struct ModelNode) Child;   /* 0x14 */

node = PGET(node->Child);        /* decode: typed T * */
PSET(node->Child, other);        /* encode */
```

- 32-bit builds: `PTR32(T)` is `T *`, `PGET(f)` is `(f)` and `PSET(f, p)` is
  `((f) = (p))`, so they generate the same code as before.
- GE_64BIT: `PTR32(T)` is a 4-byte packed union `{ u32 w; T *t_[0]; }`. Any
  use not wrapped in `PGET`/`PSET` fails to compile, so the compiler finds
  every site. Raw word access, for promote/relocation code, is `.w` through
  `PTRW(f)`.

A struct is **layout-bound** when any of these hold:

- it is laid over ROM bytes (directly, or embedded in a record that is);
- the code steps through it by literal sizes or word counts (for example,
  setup object lists walked by `setupGetObjectSize`);
- it is shared with the N64 build's binary formats (saves, ramrom
  recordings).

Every other struct may simply grow. Its pointers stay native.

### Finding every site

- `scripts/port/layout_audit.py` dumps clang's real record layouts for i386
  (the proven 32-bit layout) and arm64, and lists every record whose offsets
  move. First run: 1,101 records, 297 with moved interior offsets, 66 that
  only grow at the end. That list is the work queue for the struct rework.
- In GE_64BIT builds, `-w` is replaced by errors for every pointer-width
  diagnostic: `-Werror=int-conversion`, `pointer-to-int-cast`,
  `int-to-pointer-cast`, `void-pointer-to-int-cast` and
  `int-to-void-pointer-cast`. Each one is a real truncation on arm64, so none
  can stay silent.

### Fix patterns for punning sites, in order of preference

1. **Fix the type.** A decompiler guessed `s32` where the code means a
   pointer (for example, the font routines' `s32` parameters, about 330
   errors from a handful of prototypes). This helps every build.
2. **`uintptr_t` / `intptr_t`.** For pointer arithmetic done in integers.
3. **`portPtr32` / `portPtr64`.** For a value that really has to be a 32-bit
   word: it lives in a layout-bound field, a display list, an AI script, or
   ROM data.

Never use `(u32)(uintptr_t)p` to silence an error. That still truncates.

### Static relocations

`PORT_STATIC_PTR32(slot, target)` records `{&slot, target}` in the
`__DATA,__ge_ptr32` section. `portWindowInit` walks the section once
(`getsectiondata`) and writes `portPtr32(target)` into each slot. In 32-bit
builds the macro expands to nothing, because the initializer already holds
the pointer.

## Phases

0. **Foundations.** Window reservation and the encode/decode helpers,
   `PTR32`/`PGET`/`PSET`, static relocations, the GE_64BIT warning flags, 8-byte
   `Gfx` encoding in gbi.h and decoding in fast3d.
1. **Port layer compiles.** Mach-O section syntax in `segments.c`,
   `ucode_dl_stubs.c` and `ramrom_data.c` (`.section __DATA,__data`), and
   `gfx_pc.cpp` word types.
2. **Struct rework, family by family.** Convert the layout-bound pointer
   fields to `PTR32` and fix each use, one file family at a time (model, setup,
   stan, bg, fonts and text, animation, audio banks). Re-run the audit after
   each family. After each family, the 32-bit build must still build and run
   the same.
3. **Punning sites.** Work through the remaining pointer-width errors per file
   using the fix patterns above.
4. **Bring-up.** Boot, then title, menus and a level. Fix
   `portWindowBadPtr` aborts, which mostly come from buffers allocated outside
   the window. Check against the 32-bit build with the attract-demo ramrom
   replays, which are deterministic, by comparing state hashes and screenshots.
5. **Packaging.** `.app` bundle, bundled SDL2, ad-hoc codesign.

## Risks

- **Literal sizes and offsets** (`alloc(0x1dc)`, `(u8 *)p + 0x14`) on structs
  that are allowed to grow fail silently. Before letting a struct grow, grep
  for its 32-bit `sizeof` in hex.
- **Variadic calls** on Apple arm64 pass every variadic argument on the stack.
  A variadic function called through an unprototyped declaration gets
  garbage. Implicit declarations are already errors in modern clang.
- **`long`** is 64-bit on LP64. The N64 typedefs use `int`, but any leftover
  `long` in a layout-bound struct shows up in the audit.
- **Alignment.** arm64 aligns `s64`/`f64` to 8, like the N64's MIPS. i386 Linux
  aligns them to 4, so a record can differ for that reason alone. The audit
  reports those records too.

## Progress

### 2026-10-18: foundations and first pass

Error count in `build_mac` (strict flags): **1,600 → 877**, in 71 objects.
`scripts/port/layout_audit.py` gives the struct list, and the census loop is
`ninja -C build_mac -k 0`, then grouping `error:` lines by file.

Done:

- **Window.** `portWindowInit`/`portWindowAlloc`/`portWindowBadPtr` in
  pcsched.c, called first thing in `main`. `portAllocHighRaw` carves from the
  window under GE_64BIT, so the memp arena, high arena and ROM image all land
  inside it.
- **`port_hostptr.h`.** `PTR32`/`PGET`/`PSET`/`PTRW`, `PORT_STATIC_PTR32` (applied
  in `portWindowInit`), and `-1`-safe decoding. `bondtypes.h` gives the N64
  build identity versions.
- **Display and audio lists.** gbi.h `Gwords` are `unsigned int` under GE_64BIT,
  and runtime `g*` address operands go through `GBI_ADDR`. abi.h does the same
  with `ABI_ADDR`. fast3d decodes `seg_addr` and segment bases with `portPtr64`,
  and audio_pc.c decodes `Acmd` addresses.
- **KSEG and PI shims.** `osVirtualToPhysical` and `OS_K0_TO_PHYSICAL` encode,
  `OS_PHYSICAL_TO_K0` decodes, and `osPiStartDma`/`osPiRawStartDma` decode ROM
  addresses.
- **Mach-O.** `ASM_DATA`/`ASM_BSS` in asmsym.h replace ELF `.section` spellings.
  The `#pragma weak` aliases in lv.c and spectrum.c become macros on Apple,
  because Mach-O has no aliases.
- **Flags.** GE_64BIT uses `-Werror` for `int-conversion`, `pointer-to-int-cast`,
  `int-to-pointer-cast`, both void-pointer variants and `shorten-64-to-32`. The
  last one is what makes the mechanical cast widening safe.
- **`scripts/port/widen_ptr_casts.py`.** Rewrites each flagged `(s32)`/`(u32)`
  pointer cast to `intptr_t`/`uintptr_t` at the exact location (215 so far).
  It locates lines by clang's printed snippet, because gun.c, gunfire.c, lv.c
  and env.c use `#line`.
- **Families converted:**
  - Animation: `ModelAnimation.bitDescriptors`/`bitStream` are `PTR32`.
    `expand_ani_table_entries` has a PC branch, `animation_table_ptrs1` is
    `intptr_t[]`, `loadAnimationFrame` returns `u8 *`, and `Model.unk34/38/64/68`
    are `u8 *`.
  - Monitor scripts: `monword` (pointer-sized under GE_64BIT; the scripts are
    compiled in) and `struct tvcmd`.
  - Struck-anim tables: `ANIMOFS()` initializers, plus a PC branch in
    `initResolveAnimTable`.
  - Fonts: `frontPrintText`, `setFontTables`, `copy_*fonttable` and
    `display_aligned_white_text_to_screen` take real font pointers.
- **Literal sizes fixed:** the vtxstore record size `0x14` is now `sizeof`.
- **Leftovers from the header pass in the working tree.** Its new includes
  exposed prototype conflicts: `bg.h`'s `sub_GAME_7F0B7F84` (EU vs US),
  `bondwalk2.h`, `propobj.h` `sub_GAME_7F0A1DA0`, the `stan.h` prototype-scoped
  struct, options.c's duplicate `WatchContButtonPositions`, extra arguments to
  `romCopy`/`stop_recording_ramrom`, and `dynGetFreeGfx()` with no argument.

Checked: every game source still compiles as i386 under clang with GCC-like
leniency. The only i386 error is spectrum.c's `#pragma weak` alias, which clang
rejects and GCC accepts, so it predates this work. Port sources weren't
compiled for i386 (no i386 libc here). Nothing has run yet.

### 2026-10-18: model-file family

Error count: **877 → 713**, in 65 objects. model.c went from 148 to 0, and
objecthandler_2.c, chr_b.c, gun.c, initplayergaitobject.c and
initweaponanigroups.c are clean. No remaining error involves a model-file
struct.

Done:

- **Converted to `PTR32`** (bondtypes.h): `ModelNode` (`Data`, `Parent`, `Next`,
  `Prev`, `Child`), every pointer field of every `ModelRoData_*` record
  (including the `FirstGroup`/`ChildGroup`/`Header` unions and every
  `BaseAddr`; `Gunfire.BaseAddr` was a `u32`), `ModelRoData_Child.unk04`,
  `Vertex.LinkedTo`/`CollisionRelatedNode`, and the pointer-holding RwData
  records (`ModelRwData_DisplayList_CollisionRecord`,
  `ModelRwData_HeadPlaceholderRecord`). The audit no longer lists any of them:
  296 → 277 records with moved interior offsets.
- **Word arrays.** `ModelNodeWord` is one switch-table entry, and
  `ModelFileHeader.Switches` is `ModelNodeWord *`. `ModelRwWord` is one RwData
  word, and `Model.datas` is `ModelRwWord *`, because `RwDataIndex` counts
  32-bit words and every RwData buffer is `numRecords * 4` bytes. Both are
  plain pointer typedefs in the 32-bit builds.
- **Promote step** (model.c). On PC, `PROMOTE` tests and rewrites the field's
  32-bit word (`PTRW`) and encodes `fileramaddr + offset` with `portPtr32`.
  `PROMOTE_WORD` handles Op11's `u32 unk0c[15]`. `fileramaddr` is `uintptr_t`
  and `vma` is `intptr_t`. The N64 macro is unchanged under `TARGET_N64`.
- **Compiled-in model.** The embedded `player_gait_object` nodes use
  `PORT_SPTR` plus 13 `PORT_STATIC_PTR32` slots, the first users of the static
  relocation table. bondtypes.h gives N64 identity versions of both.
- **`scripts/port/ptr32_rewrite.py`.** Run it before changing a field's type. It
  asks clang-query for every member access to the named fields in every TU, on
  the still-compiling source, and wraps reads in `PGET` and plain assignments
  in `PSET` (`--array` covers subscripts of a pointer to `PTR32` words). It
  maps `#line` locations back to real lines and leaves compound assignments,
  `&field` and macro-body stores for manual work. It rewrote 660 reads and
  44 writes in 14 files. The promote step was rewritten by hand.
- **Aliases the compiler doesn't flag.** `ModelNode **switches =
  header->Switches` compiles, because incompatible pointer types aren't an
  error here, and then reads 8-byte entries. A `-Wincompatible-pointer-types`
  sweep found 13 such sites (bondview2.c, objecthandler.c, propobj.c,
  objecthandler_2.c, model.c). A grep for `(T **)` casts found the byte-offset
  switch reads in gunfire.c and `(Vertex **) &model->datas[...]` in propobj.c's
  `objDeform`. Rerun that sweep after every family (`ptr32_check.py aliases`).
- **`scripts/port/ptr32_check.py`** runs the post-conversion checks:
  `aliases` (the sweep above), `variants` (JP/EU/DEBUG syntax checks for
  unconverted uses) and `i386` (i386-linux syntax check, plus an `-O2` object
  compare with `--orig <pre-change tree>`). `scripts/port/i386inc` has the libc
  stubs it needs.
- **Other model.c fixes.** `Model.unka0`/`animflipfunc` hold callbacks, so they
  are `intptr_t`. `set_vtxallocator` takes a function pointer, and chr.c's
  allocator returns its block on PC (the N64 version leaves it in `v0`). The
  stub node renderers take `ModelRenderData *`. Texture-table words, which
  texLoad replaces with `osVirtualToPhysical(data)`, are decoded with
  `portPtr64` (model.c, gun.c, objecthandler_2.c).
- **Kept native**, because nothing lays them over ROM bytes: `ModelFileHeader`
  (compiled in; the file starts at `Switches[]`), `ModelSkeleton`, the
  `*ModelFileRecord` tables, and the runtime `Model`, `ModelHitEntry`,
  `ModelRenderData` and `ModelGroupMtxBuildArg`. gunfire.c's
  `ModelHeader`/`ModelHead` are stack prefixes of `Model` and match its 64-bit
  layout, so the audit lists them only because `Model` grows.

Checked:

- `ninja -C build_mac -k 0`: no errors in model.c or from the model structs.
- Syntax checks of every game source with `VERSION_JP`, `VERSION_EU` and
  `DEBUG` show no unconverted use. 7 EU-only sites were fixed.
- i386: all 208 game sources were syntax-checked with clang
  `--target=i386-unknown-linux-gnu`, using stub `stdio.h`/`stdlib.h`/`sched.h`.
  The only failures are lv.c's and spectrum.c's `#pragma weak` aliases, which
  failed the same way before this work. Object code at `-O2` was compared
  against the pre-change tree. 207 objects are identical. model.c differs only
  in `modelPromoteNodeOffsetsToPointers` (register allocation and its jump
  table) and `sub_GAME_7F06DB5C`, where the joint callback now gets the two
  arguments it is declared with.
- Nothing has run yet.

### 2026-10-18: Model runtime sizes

Error count: **713 → 712**. These fixes are for crashes nothing flags at
compile time: `Model` grew to 0x108 bytes, and three places still assumed
0xbc.

- **Model manager slots** (objecthandler.h). `ModelSlot` declares `Model`'s
  leading fields with their real types (`unk08` is `obj`, `unk10` is `datas`),
  and `AnimModelSlot` is a union with a whole `Model`. The two fallback
  allocations in model.c (0x20 and 0xc0 bytes) are now `sizeof` rounded to 16,
  which is the same 0x20/0xc0 in the 32-bit builds.
- **Render-data templates** (gunfire.c). gun.c's `D_80035D00`/`D_80035D04`,
  `watchControllerButtonBases` and `g_DefaultCasingModelRenderData` are `u32`
  words in the 32-bit `ModelRenderData` layout. On PC,
  `portRenderDataFromWords` copies them field by field at all three sites.
  `watchRenderController`'s copy no longer depends on two globals being
  adjacent at link time.
- **Player watch model** (bondview.h; old item 8). The 47 placeholder words at
  `player` + 0x230 are `Model watchmodel`, with its RwData still at
  `field_2EC`. `watch_scale_destination` was `.scale`,
  `pause_watch_related_adjust` was `.animframe1` and `field_23C` was
  `.render_pos`. `sub_GAME_7F07E7CC` uses the fields instead of literal offsets.

Checked: `ptr32_check.py` `aliases` and `variants` are clean. i386 against
67fddf2: 207 objects identical, and gunfire.c differs only in the three
functions that copy the templates.

### 2026-10-18: setup family

Error count: **712 → 611**. prop.c went from 99 to 0. The audit's moved records
went from 277 to 236; the only setup record still moving is `stagesetup`, on
purpose (below).

Also in this batch: gunfire.c's stack `ModelHeader`/`ModelHead` are now the full,
zeroed `Model` on PC, gun.c's fix. This intentionally changes 32-bit behaviour
(no more stack-garbage reads); it is a separate commit.

Done:

- **Converted to `PTR32`**: the path tables (`waypoint`, `waygroup`,
  `PathRecord`/`patrol_path`), `AIListRecord`, `PadRecord`/`BoundPadRecord`
  (`plink`, `stan`), `pname`, `SetupIntroCamera`, and every pointer field of the
  propdef records: `ObjectRecord` (`prop`, `model`, collision block,
  projectile/embedment) and its subtypes (doors, autoguns, monitors with
  `cmdlist`/`tconfig`, vehicles, aircraft, weapons, guards) plus the
  link/lock/safe/tag/rename/objective/criteria records and `textoverride` (a
  view of `RenameObjectRecord`). `ptr32_rewrite.py` rewrote 892 reads and 74
  writes in 23 files.
- **Header view.** `struct stagesetup` keeps real pointers for `g_CurrentSetup`.
  The loaded file is `stagesetupfile` (ten `u32` offsets), and so is
  `g_ptrStageSetupFile`. `SETUP_PROMOTE` takes 32-bit words (header offsets,
  or a field's `PTRW`) and returns `(u8 *) base + off`; the compiled-in branch
  decodes with `portPtr64`.
- **Loader** (`proplvreset2`): every in-file pointer is promoted with
  `PSET(f, SETUP_PROMOTE(base, PTRW(f)))`, loop sentinels test `PTRW`, and the
  pad stan lookup goes through a native local. `ailist` fields hold an AI list
  id until they are resolved, so they are read with `PTRW`.
- **Static tables.** `g_GlobalAILists` (18 entries) and
  `g_InitialMonitorAnimController` use `PORT_SPTR` plus `PORT_STATIC_PTR32`.
  `NULL` in initializers of `PTR32` fields became `0`.
- **Sound handle slots.** `sndPlaySfx` stores the playing state through the
  caller's slot and clears it when the sound ends, so 4-byte slots in setup
  records (door, vehicle, aircraft and autogun sounds) are passed as
  `SND_SLOT32(field)`, which tags the address with bit 0. snd.c reads and writes
  slots through `sndSlotGet`/`sndSlotSet`. Untagged native slots work as before.
- **Not pointers after all.** `TankRecord.collision` is the edge count of an
  inline `collision_data` that `sub_GAME_7F03F540` writes at +0x80, so it is a
  plain `s32`. chraction.c read the equipped weapon's item id through the
  weapon prop's `->chr` member (`act_attack.attack_item` overlaying
  `WeaponObjRecord.weaponnum`). `WEAPON_ITEM()` reads `weaponnum` directly on
  PC, which is the same byte on 32-bit builds, and prop.c's static assert on the
  overlay is gone.
- romfile_setup.c stored the resolved credits pointer with `(u32)(uintptr_t)`.
  It now uses `portPtr32`, and bondview_r.c decodes with `portPtr64`.

Checked: `ptr32_check.py` `aliases` and `variants` are clean (19 EU-only sites
fixed). i386 against 0e5b150: 207 objects identical, and prop.c differs only in
`proplvreset2`.

### 2026-10-18: BG and stan

Error count: **611 → 516**. bg.c went from 72 to 0, stan.c from 10 to 0 and
mema.c from 4 to 0, and explosion.c, glass.c and glass2.c are clean too.

Done:

- **BG file records** (bg.h). `bg_portal_data_entry.offset_portal` is promoted
  in place, so it is `PTR32`. `bg_room_data`'s three fields are segment-0x0F
  offset words that are never dereferenced, so they are `u32`.
  `s_bound_info.next` is a bitmask, so it is `u32` too. `BG_SEG_TO_PTR` takes a
  32-bit word and returns `(u8 *) base + off`, or `portPtr64` for a
  pass-through word. `ptr_bg_data`, `ptr_bgdata_offsets` and `gptr_stan` are
  `intptr_t`, and `getIndexOfPORTALID` takes an `intptr_t`.
- **Stan header.** `StanPrefixRecord.ptr_firstroom` and the room table after it
  are 32-bit words: `portStanPack` and romfile_stan.c write them with
  `portPtr32` (romfile_stan.c truncated with `(u32)(uintptr_t)` before).
  stanLoadFile's `prefix->stanfile = file`, which writes `stan_prefix` through
  a struct view of the variable, is a plain assignment on PC.
  `stanDetermineEOF`'s `delta` and `ptr_firstroom_0` are `intptr_t`.
- **mema** (the room-data heap) keeps its `s32` address table, now holding
  window words. Those add, subtract and compare like the addresses, so only
  `memaReset`, `memaAlloc`, `memaFree` and `memaRealloc` convert.
- **Player matrix fields.** `player.field_10E0` (the world projection `Mtx *`)
  and `field_10E4` (the frame's `LookAt *`) were `s32`. Their accessors are now
  typed, which fixed bg.c, explosion.c, glass*.c and part of sky.c.
- Smaller fixes: the portal depth slot (`D_800442FC`) is written directly
  instead of through an `s32` holding its address; texLoadFromGdl's size
  argument; `linkedTile` pointer arithmetic; the locus flag stored in
  `StandTileLocusCallbackRecord.rooms`; and romfile_setup.c's probe now
  reads raw words with `PTRW`.

Checked: `aliases` and `variants` are clean (2 EU-only sites fixed). i386
against 641a1b7: 206 objects identical; bg.c differs only in `load_bg_file`
(`BG_SEG_TO_PTR`), and stan.c in `stanGetLocusField0` (returns 0/1; its one
caller tests `!= 0`) and in `sub_GAME_7F0B1DDC` (operand order of one compare).
The audit lists no BG or stan record laid over a file:
`StandFileHeader`/`StandFileFooter`/`BetaStandTile` only describe compiled-in
stans, and the rest are runtime structs.

Still truncating with `(u32)(uintptr_t)`, outside these families: romdata.c's
text-bank slots, port_textures.c (`entry.index`), gunfire.c's crosshair GBI
word, and the 32-bit-only fallback probes in romfile_model.c.

### 2026-10-18: fonts and text

Error count: **516 → 305**. options.c went from 158 to 0, and language.c,
mpmenu.c and textrelated.c are clean too. front.c went from 20 to 6; what is
left there is the logo DL, video and animation sites.

Done:

- **`scripts/port/retype_locals.py`.** It reads a build log and collects, for
  each `s32`/`u32` variable, the pointer type the code needs there (assigned
  from, initialized from, passed to, assigned to, returned as). It then
  rewrites the variable's declaration, splitting multi-name declarations. It
  uses the source snippet under each error to find the real line after
  `#line`, reports variables with conflicting evidence, and touches globals
  only with `--globals`. Two rounds on options.c and front.c retyped 27
  locals (font, fontchar and string pointers). i386 code is identical for
  every retyped function, so none of them does integer arithmetic on the
  variable.
- **Font segments.** `struct font` is the ROM segment's layout, so
  `fontchar.pixeldata` is `PTR32(u8)`. `load_font_tables` rebases with
  `PSET(.., base + PTRW(..))`, and its segment sizes no longer cast symbol
  addresses to `romptr_t`.
- **Text banks.** Each bank's slot table is 32-bit words. romdata.c stores
  `portPtr32` there (it truncated with `(u32)(uintptr_t)` before), and `langGet`
  decodes with `portPtr64`. `g_LangBanks` is `u32 *[45]`.
- **Strings in ints.** The MP award text fields in `player` are `char *`, and so
  is `frontGetPlayersFavoriteWeaponInHand`'s return. mpmenu.c's rank text gets
  its own `char *ptext`, because the decompiled `q` doubles as a colour.
- **`PORT_K0`/`PORT_PHYS`** (os.h, plan item 4): the N64 macros on N64, and
  the pointer itself on PC. They are used at the 27 sites (options.c,
  bondview2.c, front.c) where `OS_PHYSICAL_TO_K0`/`OS_K0_TO_PHYSICAL` were
  applied to host pointers that are then used as pointers.
- ptr32_rewrite.py: a manual assignment whose LHS end can't be resolved now also
  leaves its LHS read unwrapped (the tool had wrapped it in `PGET`).

Checked: `aliases` and `variants` are clean. i386 against de10b68: 207 objects
identical, and textrelated.c differs only in `load_font_tables`. The audit no
longer lists `font` or `fontchar` (231 records move).

### 2026-10-18: audio banks

Error count: **305 → 266**. audio_stubs.c, music.c, audi.c, snd.c, load.c,
save.c, resample.c and reverb.c are all at 0.

Done:

- **PTR32 in place, not a native copy.** The `.ctl` bank and its records are
  used in place by libultra's players and by snd.c. Keeping 4-byte slots also
  keeps GE's `ALInstrumentAlt` view working: its 1-based `soundArray` at +12
  lines up with `ALInstrument.soundArray` at +16 only with 4-byte slots
  (asserted on arm64). Converted: `ALBankFile.bankArray`,
  `ALBank.percussion/instArray`, `ALInstrument.soundArray`,
  `ALSound.envelope/keyMap/wavetable`, `ALWaveTable.base`, the ADPCM/raw wave
  info `loop`/`book`, the Alt views, and music.c's `RareALSeqData.address`
  (the `.sbk` header, sized by `sizeof`). The anonymous typedefs got struct
  tags so the rewrite tool can name them. libaudio.h has its own identity
  `PTR32` macros on N64. `ptr32_rewrite.py` rewrote 73 reads.
- **Loaders.** `alBnkfNew` (audio_stubs.c) byte-swaps each offset word and
  promotes it with `PSET(f, base + swap(PTRW(f)))`. `musicSeqFileNew` and the
  `.sbk` header swap do the same.
- **Words the RSP or the DMA callback sees.** load.c's `memin` is the DMA
  callback's 32-bit word, so it is built from `PTRW(table->base)`. The save
  filter stores `portPtr32` of its output buffer, so audi.c passes the buffer
  itself instead of an `osVirtualToPhysical` word cast back to `s16 *`. The
  `K0_TO_PHYS` family in R4300.h encodes with `portPtr32` on PC; that is an
  identity on 32-bit builds, and 32-bit words pass through unchanged.
- Smaller fixes: `ALSndPlayer.target` holds an `ALSoundState *`; the int
  parameters passed as `void *` in resample.c; the reverb delay-line
  differences; and audi.c's task sizes and scheduler flag.

Checked: `aliases` and `variants` are clean. i386 against 1014cd9: 206
objects identical. load.c differs only in the operand order of two additions,
and audi.c only in `amHandleFrameMessage` (no `osVirtualToPhysical` call, an
identity shim on 32-bit builds). libultra's own `ALSeqData`/`ALSeqFile` still
move, but GE never uses them (220 records move).

### 2026-10-18: static display lists

Error count: **266 → 228**. rarewarelogo.c and debugmenu.c are at 0.

Done:

- **`GBI_SADDR`** (gbi.h) is the address operand of the static `gs*` macros
  (`gsDma0p/1p/2p`, `gsSPVertex`, `gsImmp1`, `gsSPBranchLessZ*`,
  `gsSPLoadUcodeEx`, `gsSPDma_io`, `gsSetImage`). It is the old cast unless a
  GE_64BIT file defines `PORT_STATIC_DL_RELOCS`. Then a constant operand (a
  segment word) passes through and a host address becomes 0
  (`__builtin_constant_p` folds in static initializers). Every other file
  still fails to compile on a host address, so nothing is zeroed silently.
- **`scripts/port/gen_static_dl_relocs.py`** writes `<file>.relocs.inc`, one
  `PORT_STATIC_PTR32(DL[i].words.w1, target)` per zeroed operand. It takes
  each command's index from clang's AST of the file as an i386 build, so
  multi-command macros such as `gsDPLoadTextureBlock` are counted by the
  compiler. `--check` fails when the file is stale. The output is not `*.c`,
  because CMake globs `src/*.c`. rarewarelogo.c has 32 records and
  debugmenu.c has 1; font_dl.c and oddtextures.c have none (segment
  addresses only).
- `PORT_STATIC_PTR32` names its records with `__COUNTER__`, so includes can't
  collide any more.

Checked: `aliases` and `variants` are clean, and both relocation files are up
to date. i386 against efb7f65: all 208 objects identical.

### 2026-10-19: phase 3, the punning sites

Error count: **228 → 0**. The GE_64BIT build compiles and links:
`build_mac/ProjectMidas` is a native arm64 Mach-O executable. Four commits:

- **Batch 1, fd9151e (228 → 98): the smaller files.** `retype_locals.py`
  retyped 8 locals. Buffer globals and helper prototypes got their real
  types (title.c's buffers, `z_buffer`, `ptr_menu_videobuffer`, rsp.c's
  output buffer, `chrAllocate`, `rzipGetSomething`, `romCopyAligned`, the
  indy commands). ROM DMA addresses are window words, `PHYS_TO_K0/K1` decode
  with `portPtr64`, and `init()` is an empty stub on GE_64BIT.
- **Batch 2, 647067f (98 → 63): image.c and image_bank.c.**
  `texSetBitstring` takes `u8 *`. Buffer alignment is done in `uintptr_t`,
  and the globalDL loads cast to `Gfx *`.
- **Batch 3, c8f6ca9 (63 → 15): bondview2.c and gunfire.c.**
  - `gunDrawSight` takes `Gfx **`. bondview2 passes `&gdl`, so as `s32 *` it
    read and wrote half of the pointer. Incompatible pointer types are not an
    error, so only a review of the uses finds these.
  - `hand.field_A48` is the second `ALSoundState *` slot. `player.field_5C..68`
    are `Mtx *`, and `players_cur_animation` is a `struct ModelAnimation *`.
  - The MP body-animation code no longer reads a firing table's resolved
    animation as an `s32`. On GE_64BIT that union is 8 bytes.
- **Batch 4, 64cb47a (15 → 0, plus the link).**
  - fast3d's GL counts and offsets are narrowed explicitly. env.c passes its
    int setParam values as `(void *)(intptr_t)`.
  - The Apple `-U` list in CMakeLists.txt missed obseg.h's text banks: their
    declarations are indented, which left 84 undefined symbols.
  - The last silenced truncations are fixed: port_textures.c `entry.index` and
    gunfire.c's tinted-crosshair word are encoded with `portPtr32`, and zlib.c's
    in-place overlap tests compare full addresses.

Checked after each batch: `aliases` and `variants` are clean. The i386
objects match the previous commit except two functions in batch 3:
playerTick swaps the operands of one equality compare, and gunDrawSight
differs in register allocation and code motion (a `Gfx **` cursor no longer
aliases the `u32` GBI words). To find which function differs, compile both
trees with `-ffunction-sections` and diff `llvm-objdump -d` per symbol. The
5 i386 syntax errors (lv.c, spectrum.c `#pragma weak` aliases) predate the
port. port/ and fast3d are outside the i386 compare. There, each change is a
same-width cast on 32-bit builds or a `portPtr32`, which is a plain cast
there.

What's left in the source: a grep for `(u32|s32|int|...)(uintptr_t|intptr_t)`
finds only resample.c's `(s32)(intptr_t) param` (libaudio's int passed as
`void *`) and narrowings of pointer *differences*. Punning through a union or
memcpy, or through an incompatible pointer like gunDrawSight's, is invisible to
the compiler. Bring-up will find what remains of those.

### 2026-10-19: phase 4, bring-up through the menus

`build_mac/ProjectMidas` boots with the ROM and renders all of these
correctly:
- the legal screen, the Nintendo and Rare logos, the gun barrel and the cast
  intro;
- with `GE_AUTOSTART=1`, the title, file select, mode select, mission select,
  difficulty and briefing screens.

It then loads solo Dam and ticks the level. Four commits:

- **5280ab8, boot to the attract intro.**
  - The window reservation is zerofill inside the image (see above).
  - animdata.c declared `animation_table_ptrs1/2` as `int32_t[]` against an
    `intptr_t[]` definition. A mismatched `extern` in another file is
    invisible to the compiler.
  - reverb.c negated `u32` delay offsets, which steps 8 GB forward on a
    64-bit pointer.
  - `ALParam` is padded so `ALStartParamAlt` (40 bytes) fits the pool
    element, with static asserts.
  - The three inline-texture menu models come from the ROM, and
    romfile_model.c converts their blobs.
  - `ModelGroupMtxBuildArg` (a view of a `ModelNode`) has PTR32 fields.
  - struct player embeds a real `Model`.
  - Crash aids: `portWindowBadPtr` prints a backtrace, and the arm64 crash
    handler prints the fault pc, lr and address.
- **c9f6968, through the menus.** texReset's pristine snapshot assumed
  GCC's last-to-first layout of oddtextures.c. Its bounds now follow the
  address order.
- **aaec360, struct views.** A new `ptr32_check.py views` check (below).
  It fixed AttachedObj (a view of ObjectRecord), `object_animation_controller`
  (passed as a MonitorRecord) and the bond-movement locus local (the stan
  walk writes 24 bytes through it).
- **c9e6aaa, solo Dam loads.** Two literal sizes:
  - player.c allocated struct player as 0x2A80 bytes; it is 0x2C60 on
    GE_64BIT.
  - The ModelHitEntry pool was `char[600 * 0x14]`. Its init overran 12000
    bytes into `g_playerPointers`, so solo loads counted two players and
    asked for the multiplayer setup.

Checked after each batch: `aliases`, `variants` and `views` are clean. The
i386 objects match except the intended functions (objecthandler_2.c's
load_object_fill_header, image_bank.c's texReset).

**How to run it** (no debugger needed):

```
cd <scratch dir>   # shots and logs land in the cwd
GE_ROM=<repo>/baserom.u.z64 GE_FRAMES=6000 GE_NOINISAVE=1 GE_NOMSGBOX=1 \
GE_WINDOW=640x480 GE_SHOT=200 <repo>/build_mac/ProjectMidas > out.txt 2>&1
```

- `GE_FRAMES` exits cleanly after n frames.
- `GE_SHOT=n` writes `shot_*.bmp` every n frames.
- `GE_AUTOSTART=1` presses START every 120 polls and screenshots the frame
  before each press.
- Set `fullscreen=windowed` in build_mac/midas.ini so runs don't take over
  the display.
- Don't use `GE_NOSLEEP` for these runs: without pacing the game submits
  no graphics tasks.
- Decode the crash handler's pc/lr and backtrace frames with
  `atos -i -o build_mac/ProjectMidas -l <image> <addresses>`. The image base
  is printed as `ge: window: image at ...`. lldb needs the developer-tools
  authorization, so it isn't used.

**The `views` check.** It lists every incompatible pointer conversion
between two struct types whose GE_64BIT layouts put native pointers in
different places, plus `T *`/`T **` mixups. Pairs reviewed as harmless are in
`VIEWS_REVIEWED`.

Literal sizes are not covered by any check. Those are hand allocations and
pools sized by a literal (`mempAllocBytesInBank(0x2A80)`,
`char pool[600 * 0x14]`) for structs that grow on GE_64BIT.

### 2026-10-19: phase 4, into Dam

Solo Dam loads, renders and plays on arm64. Firing works. A
`GE_STAGE=dam GE_SKIPINTRO=1 GE_DRIVE=3` run (stand still and fire)
completes 3600 frames.

- **e58d71d, literal sizes.** I swept the 32-bit sizes of growing structs
  (from `layout_audit.py --json`) against allocation sizes, ALIGN strides,
  byte pools and `(u8 *)p + offset` accesses. Fixed:
  - bondview2.c carved the body Model with a 0xfb stride, so the rwdata
    after it overwrote `unka0` (the in-level crash).
  - InvItem allocation sizes.
  - struct hand's embedded gun Model head (`field_B68`, `mtxlist` as its
    `render_pos`), now a real head on GE_64BIT.

  Everything else the sweep found was byte buffers or structs that keep
  their layout (listed in the commit).
- **96e15b5, bullet hits.** Four copies of the texture-under-the-hit
  lookup read the SETTIMG word as a raw pointer. They now decode it with
  `portPtr64`.

Run recipes: `GE_STAGE=dam` launches the mission directly. `GE_SKIPINTRO=1`
taps through the intro cameras. `GE_DRIVE=1` wanders, `GE_DRIVE=3` stands
and fires.

### How to continue a family

1. Copy the tree first (`--orig` for the object compare needs it).
2. `python3 scripts/port/ptr32_rewrite.py build_mac Rec.f1,f2 ... [--array
   Rec.arr] [--skip file:a-b]`, before retyping the fields. Rewrite the
   promote step by hand.
3. Retype the fields to `PTR32(T)` (typedef shared word types, because each
   `PTR32(T)` expansion is a distinct type), then `ninja -C build_mac -k 0`.
4. `ptr32_check.py build_mac aliases`, `variants`, then `i386 --orig DIR`. Rerun
   `layout_audit.py`, and add a Progress entry here.

### Next, in order

1. **Phase 4, continued: gameplay.**
   - The first-person gun draws at a huge scale: the barrel and muzzle flash
     fill the screen. Suspects are the gun's render path in gunfire.c
     (`subdraw` on `(Model *)&hand->field_B68`, the render data built by
     `portRenderDataFromWords`, the gun matrix scaling) and any code that
     reaches the hand's Model head by 32-bit offsets.
   - Then play further (`GE_STAGE`, `GE_DRIVE`, `GE_AUTOFIRE`) and compare
     against the 32-bit build with the attract-demo ramrom replays.
2. Open questions from the link:
   - ld reduces `__DATA,__common` alignment from 0x8000 to 0x4000. Find the
     32 KB-aligned common symbol and check that nothing depends on it.
   - gfx_opengl.cpp's framebuffer copy restores `GL_FRAMEBUFFER` with the
     framebuffer *index* (`current_framebuffer`), not its `fbo`. The other
     restores use `framebuffers[current_framebuffer].fbo`. This is the same
     on every build, so it was kept as is.
3. **Phase 5: packaging.** `.app` bundle, bundled SDL2, ad-hoc codesign.
