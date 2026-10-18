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
  window covers the image (globals, static display lists) plus one large
  reservation mapped right after it (`portWindowInit`, pcsched.c). The memp
  arena, the high arena and the ROM image are carved from that reservation.
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

1. **BG and stan** (`bg.c` `BG_SEG_TO_PTR`, 72 errors; stan.c).
2. **Fonts and text locals** (options.c's 158 errors are mostly `s32` locals
   holding `struct font *`, `struct fontchar *` and `char *`).
3. **Audio banks** (audio_stubs.c `alBnkfNew`). Choose between `PTR32` in place
   and a native copy at load. Watch the `ALBankAlt`/`ALInstrumentAlt` views,
   whose 1-based `soundArray` trick only lines up with 4-byte pointers.
4. **Static display lists** (rarewarelogo.c, font_dl.c, oddtextures.c) via
   `PORT_STATIC_PTR32`, and `debugmenu.c:55`. The macro names its record with
   `__LINE__`, so two `.inc.c` files included into one TU collide if they use
   it on the same line number. `__COUNTER__` avoids that.
5. **`OS_PHYSICAL_TO_K0(&ptr)` misuse** (bondview2.c, options.c, front.c). Those
   sites pass a pointer where a physical word belongs. Use the pointer
   directly.
