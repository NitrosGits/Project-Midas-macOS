/**
 * PC-port audio stubs — Phase-4 deferral of the AL synthesizer.
 *
 * The real libultra audio library (src/libultra/audio + src/libultrare) is
 * scheduled for Phase 4. Until then every al* symbol the kept game code
 * references is satisfied here so the game runs SILENT without crashing.
 *
 * Design rules used below (each stub carries its own rationale):
 *  - Functions whose results feed loops/derefs get REAL minimal
 *    implementations (alLink/alUnlink, alHeapInit/alHeapDBAlloc).
 *  - Functions that only drive the (never-running) synthesizer are no-ops.
 *  - Query functions return the value that makes every audited caller take
 *    its benign exit path (alCSPGetState -> AL_STOPPED, alEvtqNextEvent ->
 *    positive delta, alCents2Ratio -> 1.0).
 *  - The SFX sound-bank consumed by snd.c sndPlaySfx() cannot be stubbed
 *    with zeros (it is deref'd 3 levels deep before any al* call), so a
 *    minimal fake bank graph is provided (portPcmBank below) and wired in
 *    via the crafted _sfxctl/_instrumentsctl segment blobs in
 *    port/src/segments.c.
 *
 * Callers audited: src/audi.c (amCreateAudioManager/amHandleFrameMessage),
 * src/music.c (musicSeqPlayerInit/musicTrackXPlay/Stop/ApplySeqpVol),
 * src/snd.c (sndNewPlayerInit/sndPlaySfx/sndSetupSound/sndPlayerVoiceHandler).
 */
#include <string.h>
#include <stdint.h>

#include <ultra64.h>
#include <PR/libaudio.h>

/* ==== AUDIO PHASE 1c ===================================================== *
 * The REAL AL library is now compiled (see CMakeLists' audio block): sl.c
 * provides alGlobals/alInit/alClose/alLink/alUnlink; heapinit/heapalloc
 * the heap; event.c the event queues; synthesizer.c + syn*.c the voices;
 * load/resample/env_wip/save/buses/reverb/drvrNew the filter chain.
 * port/src/audio_pc.c executes the emitted Acmd lists and feeds SDL.
 * The stubs that remain below are ONLY the compressed-MIDI sequencer
 * layer (music phase) and the BE-aware alBnkfNew. */

/* ==== bank file ========================================================== */

/* ==== bank file (REAL, audio phase 1) ==================================== */
/*
 * The _sfxctl/_instrumentsctl segments now carry the REAL SGI .ctl images
 * (byte-exact BIG-ENDIAN, revision 'B1'; see port/src/segments.c). This is
 * bnkf.c's patch walk with the BE->native field conversion folded in: on
 * N64 native IS big-endian so SGI reads fields directly; on PC every
 * multibyte field must be swapped exactly once, then offsets promoted to
 * host pointers (i686 build: 4-byte pointers overwrite 4-byte offsets in
 * place, same as N64).
 *
 * Guards against double work: bank/instrument/sound/wavetable records
 * carry a flags byte (SGI's own revisit guard, reused). Envelopes, ADPCM
 * books and loop records have NO flags field and CAN be shared, so their
 * content-swap is tracked in a visited set; the pointer fields referencing
 * them are per-parent and always patched.
 *
 * Format verified against assets/music/sfx.ctl before writing this:
 * revision B1, bankCount 1, bank @0x5BA0 {instCount 1, sampleRate 22050,
 * instArray[0]=0x5778}, instrument {vol 7F, pan 40, bendRange 200,
 * soundCount 261, soundArray at +16} - STANDARD layout (GE's snd.h "Alt"
 * view reads soundArray at +12 with 1-based sfx indices; the addresses
 * coincide, so both views work off this patch).
 */

static u16 portBSwap16(u16 v)
{
    return (u16)((v >> 8) | (v << 8));
}

static u32 portBSwap32(u32 v)
{
    return (v >> 24) | ((v >> 8) & 0xFF00u) | ((v & 0xFF00u) << 8) | (v << 24);
}

#define BNKF_VISIT_MAX 4096

static const void *s_bnkfVisited[BNKF_VISIT_MAX];
static s32 s_bnkfVisitedCount;

/* returns 1 the first time ptr is seen, 0 after */
static s32 portBnkfVisit(const void *ptr)
{
    s32 i;

    for (i = 0; i < s_bnkfVisitedCount; i++) {
        if (s_bnkfVisited[i] == ptr) {
            return 0;
        }
    }
    if (s_bnkfVisitedCount < BNKF_VISIT_MAX) {
        s_bnkfVisited[s_bnkfVisitedCount++] = ptr;
    }
    return 1;
}

static void portBnkfPatchSound(ALSound *s, uintptr_t base, uintptr_t table)
{
    ALWaveTable *w;

    if (s->flags) {
        return;
    }
    s->flags = 1;

    PSET(s->envelope, (ALEnvelope *)(base + portBSwap32(PTRW(s->envelope))));
    if (portBnkfVisit(PGET(s->envelope))) {
        PGET(s->envelope)->attackTime = (s32)portBSwap32((u32)PGET(s->envelope)->attackTime);
        PGET(s->envelope)->decayTime = (s32)portBSwap32((u32)PGET(s->envelope)->decayTime);
        PGET(s->envelope)->releaseTime = (s32)portBSwap32((u32)PGET(s->envelope)->releaseTime);
    }

    /* keymap fields are all bytes - promote the pointer, nothing to swap */
    PSET(s->keyMap, (ALKeyMap *)(base + portBSwap32(PTRW(s->keyMap))));

    PSET(s->wavetable, (ALWaveTable *)(base + portBSwap32(PTRW(s->wavetable))));
    w = PGET(s->wavetable);
    if (!w->flags) {
        w->flags = 1;
        PSET(w->base, (u8 *)(table + portBSwap32(PTRW(w->base))));
        w->len = (s32)portBSwap32((u32)w->len);

        if (w->type == AL_ADPCM_WAVE) {
            if (PTRW(w->waveInfo.adpcmWave.book) != 0) {
                ALADPCMBook *book = (ALADPCMBook *)(base + portBSwap32(PTRW(w->waveInfo.adpcmWave.book)));

                PSET(w->waveInfo.adpcmWave.book, book);
                if (portBnkfVisit(book)) {
                    s32 n;

                    book->order = (s32)portBSwap32((u32)book->order);
                    book->npredictors = (s32)portBSwap32((u32)book->npredictors);
                    for (n = 0; n < book->order * book->npredictors * 8; n++) {
                        book->book[n] = (s16)portBSwap16((u16)book->book[n]);
                    }
                }
            }
            if (PTRW(w->waveInfo.adpcmWave.loop) != 0) {
                ALADPCMloop *loop = (ALADPCMloop *)(base + portBSwap32(PTRW(w->waveInfo.adpcmWave.loop)));

                PSET(w->waveInfo.adpcmWave.loop, loop);
                if (portBnkfVisit(loop)) {
                    s32 n;

                    loop->start = portBSwap32(loop->start);
                    loop->end = portBSwap32(loop->end);
                    loop->count = portBSwap32(loop->count);
                    for (n = 0; n < 16; n++) {
                        ((s16 *)loop->state)[n] = (s16)portBSwap16((u16)((s16 *)loop->state)[n]);
                    }
                }
            }
        } else if (w->type == AL_RAW16_WAVE) {
            if (PTRW(w->waveInfo.rawWave.loop) != 0) {
                ALRawLoop *loop = (ALRawLoop *)(base + portBSwap32(PTRW(w->waveInfo.rawWave.loop)));

                PSET(w->waveInfo.rawWave.loop, loop);
                if (portBnkfVisit(loop)) {
                    loop->start = portBSwap32(loop->start);
                    loop->end = portBSwap32(loop->end);
                    loop->count = portBSwap32(loop->count);
                }
            }

            /* RAW16 samples are big-endian s16 in the .tbl but the mixer
             * plays native s16, so swap them once (visited-guarded). ADPCM
             * decodes bytes and needs no swap. */
            if (portBnkfVisit(PGET(w->base))) {
                s16 *smp = (s16 *)PGET(w->base);
                s32 ns = w->len >> 1;
                s32 k;

                for (k = 0; k < ns; k++) {
                    smp[k] = (s16)portBSwap16((u16)smp[k]);
                }
            }
        }
    }
}

static void portBnkfPatchInst(ALInstrument *inst, uintptr_t base, uintptr_t table)
{
    s32 i;

    if (inst->flags) {
        return;
    }
    inst->flags = 1;

    inst->bendRange = (s16)portBSwap16((u16)inst->bendRange);
    inst->soundCount = (s16)portBSwap16((u16)inst->soundCount);

    for (i = 0; i < inst->soundCount; i++) {
        PSET(inst->soundArray[i], (ALSound *)(base + portBSwap32(PTRW(inst->soundArray[i]))));
        portBnkfPatchSound(PGET(inst->soundArray[i]), base, table);
    }
}

static void portBnkfPatchBank(ALBank *bank, uintptr_t base, uintptr_t table)
{
    s32 i;

    if (bank->flags) {
        return;
    }
    bank->flags = 1;

    bank->instCount = (s16)portBSwap16((u16)bank->instCount);
    bank->sampleRate = (s32)portBSwap32((u32)bank->sampleRate);

    if (PTRW(bank->percussion) != 0) {
        PSET(bank->percussion, (ALInstrument *)(base + portBSwap32(PTRW(bank->percussion))));
        portBnkfPatchInst(PGET(bank->percussion), base, table);
    }

    for (i = 0; i < bank->instCount; i++) {
        PSET(bank->instArray[i], (ALInstrument *)(base + portBSwap32(PTRW(bank->instArray[i]))));
        if (PGET(bank->instArray[i]) != (ALInstrument *)base) {
            portBnkfPatchInst(PGET(bank->instArray[i]), base, table);
        }
    }
}

void alBnkfNew(ALBankFile *f, u8 *table)
{
    uintptr_t base = (uintptr_t)f;
    s32 i;

    s_bnkfVisitedCount = 0;

    f->revision = (s16)portBSwap16((u16)f->revision);
    if (f->revision != AL_BANK_VERSION) {
        /* not a BE ctl image (or already patched) - leave it alone */
        return;
    }
    f->bankCount = (s16)portBSwap16((u16)f->bankCount);

    for (i = 0; i < f->bankCount; i++) {
        PSET(f->bankArray[i], (ALBank *)(base + portBSwap32(PTRW(f->bankArray[i]))));
        if (PGET(f->bankArray[i]) != (ALBank *)base) {
            portBnkfPatchBank(PGET(f->bankArray[i]), base, (uintptr_t)table);
        }
    }

    { /* bring-up (GE_DUMP_BOND): prove the parse - bank/inst/sound shape */
        extern void ge_dbg_vec(const char *tag, const float *v, int n);
        ALBank *bank = PGET(f->bankArray[0]);
        float v[7];

        v[0] = (float)f->bankCount;
        v[1] = (float)bank->instCount;
        v[2] = (float)bank->sampleRate;
        v[3] = (float)PGET(bank->instArray[0])->soundCount;
        v[4] = (float)PGET(PGET(PGET(bank->instArray[0])->soundArray[0])->wavetable)->type;
        v[5] = (float)PGET(PGET(PGET(bank->instArray[0])->soundArray[0])->wavetable)->len;
        v[6] = (float)PTRW(PGET(PGET(PGET(bank->instArray[0])->soundArray[0])->wavetable)->base);
        ge_dbg_vec("bnkf banks/insts/rate/sounds/wt0type/len/base", v, 7);
    }
}

/* ==== compressed-midi seq / seq player =================================== */

/**
 * Real alCSeqNew parses a compressed MIDI stream header. The stream our
 * crafted sample table produces is an empty deflate block (decompresses
 * to 0 bytes), so there is nothing to parse; zero the struct so any later
 * reader sees a consistent "empty sequence".
 */
/* (The compressed-MIDI sequencer stubs - alCSeqNew, the alCSP set, and
 * alSeqpSetBank - were retired with the MUSIC PHASE: cseq.c, csplayer.c,
 * the csp wrappers and seqpsetbank.c are compiled for real now, and the
 * real music.sbk feeds them via _musicsampletblSegmentRomStart.) */

/* (alCents2Ratio, alAudioFrame, the alSyn* voice API, and the fake
 * portPcmBank graph were retired with AUDIO PHASE 1c: the real library
 * provides the former, and the real banks replaced the latter.) */
