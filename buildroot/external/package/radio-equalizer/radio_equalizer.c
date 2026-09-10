/* Lightweight mono LADSPA parametric EQ. ALSA duplicates one instance per channel.
 *
 * Live updates: ALSA's ladspa PCM reads input.controls only once, at PCM-open
 * time, so the historic design had to restart every audio consumer to apply new
 * EQ values. To allow glitch-free live changes this plugin can instead read its
 * 51 control values from a small fixed-size binary runtime file (default
 * /run/radio/equalizer.rt, overridable with RADIO_EQUALIZER_RT). The web layer
 * rewrites that file with an atomic rename and bumps a generation counter;
 * run() re-reads the small block cheaply once per call (never per sample) and
 * recomputes coefficients only when the counter changes. When the file is
 * absent or malformed the plugin transparently falls back to the values ALSA
 * connected to its control ports, preserving the original behaviour. */
#include <math.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>

typedef float LADSPA_Data;
typedef void *LADSPA_Handle;
typedef unsigned long LADSPA_PortDescriptor;
typedef unsigned long LADSPA_PortRangeHintDescriptor;
typedef unsigned long LADSPA_Properties;

#define LADSPA_PORT_INPUT 0x1
#define LADSPA_PORT_OUTPUT 0x2
#define LADSPA_PORT_CONTROL 0x4
#define LADSPA_PORT_AUDIO 0x8
#define LADSPA_HINT_BOUNDED_BELOW 0x1
#define LADSPA_HINT_BOUNDED_ABOVE 0x2
#define LADSPA_HINT_TOGGLED 0x4
#define LADSPA_HINT_LOGARITHMIC 0x10
#define LADSPA_HINT_INTEGER 0x20
#define LADSPA_PROPERTY_HARD_RT_CAPABLE 0x4

typedef struct {
    LADSPA_PortRangeHintDescriptor HintDescriptor;
    LADSPA_Data LowerBound;
    LADSPA_Data UpperBound;
} LADSPA_PortRangeHint;

typedef struct _LADSPA_Descriptor {
    unsigned long UniqueID;
    const char *Label;
    LADSPA_Properties Properties;
    const char *Name;
    const char *Maker;
    const char *Copyright;
    unsigned long PortCount;
    const LADSPA_PortDescriptor *PortDescriptors;
    const char *const *PortNames;
    const LADSPA_PortRangeHint *PortRangeHints;
    void *ImplementationData;
    LADSPA_Handle (*instantiate)(const struct _LADSPA_Descriptor *, unsigned long);
    void (*connect_port)(LADSPA_Handle, unsigned long, LADSPA_Data *);
    void (*activate)(LADSPA_Handle);
    void (*run)(LADSPA_Handle, unsigned long);
    void (*run_adding)(LADSPA_Handle, unsigned long);
    void (*set_run_adding_gain)(LADSPA_Handle, LADSPA_Data);
    void (*deactivate)(LADSPA_Handle);
    void (*cleanup)(LADSPA_Handle);
} LADSPA_Descriptor;

enum { BANDS = 10, INPUT_PORT = 51, OUTPUT_PORT = 52, PORTS = 53 };
enum { CONTROL_VALUES = 51 };
enum { FILTER_LOW_SHELF = 1, FILTER_HIGH_SHELF, FILTER_HIGH_PASS, FILTER_LOW_PASS };

/* Fixed-size runtime file the web layer rewrites for live parameter updates.
 * The layout is stable and endian-native (the writer runs on the same host):
 * magic identifies the format, generation increments on every write, and values
 * mirrors the 51 LADSPA control ports (preamp + 5 fields x 10 bands). */
#define RT_MAGIC 0x52454131u /* "REA1" */
#define RT_DEFAULT_PATH "/run/radio/equalizer.rt"
typedef struct { uint32_t magic; uint32_t generation; float values[CONTROL_VALUES]; } RtBlock;

typedef struct { float b0, b1, b2, a1, a2, z1, z2; } Biquad;
typedef struct {
    unsigned long rate;
    LADSPA_Data *ports[PORTS];
    Biquad filters[BANDS];
    /* Live runtime-file state (best effort; enabled when a valid path exists). */
    char rt_path[256];
    int rt_enabled;
    uint32_t rt_generation;
    int rt_seen;
    float control[CONTROL_VALUES];
} Equalizer;

/* Remember the runtime-file path. The web layer rewrites it with an atomic
 * rename, which swaps in a NEW inode, so run() re-opens the path each time
 * instead of holding a stale mmap/fd. Best effort: if no path is configured the
 * live path stays disabled and run() uses the connected control ports. */
static void rt_open(Equalizer *eq) {
    const char *path = getenv("RADIO_EQUALIZER_RT");
    if (path == NULL || path[0] == '\0') path = RT_DEFAULT_PATH;
    eq->rt_enabled = 0;
    eq->rt_seen = 0;
    if (strlen(path) + 1 > sizeof(eq->rt_path)) return;
    strcpy(eq->rt_path, path);
    eq->rt_enabled = 1;
}

/* Copy the 51 live control values into eq->control when the runtime file is
 * present, valid, and its generation changed. Returns 1 when eq->control holds
 * live values to use, 0 when the caller should fall back to the ALSA ports.
 * Cheap enough to call once per run(): a small fixed-size read (never per
 * sample) with a copy only when the generation actually advanced. Re-opening
 * the path each call is what lets an atomic-rename update become visible. */
static int rt_refresh(Equalizer *eq) {
    RtBlock block;
    ssize_t got;
    int fd;
    if (!eq->rt_enabled) return 0;
    fd = open(eq->rt_path, O_RDONLY);
    if (fd < 0) return eq->rt_seen;
    got = read(fd, &block, sizeof(block));
    close(fd);
    if (got != (ssize_t)sizeof(block) || block.magic != RT_MAGIC) return eq->rt_seen;
    if (!eq->rt_seen || block.generation != eq->rt_generation) {
        memcpy(eq->control, block.values, sizeof(eq->control));
        eq->rt_generation = block.generation;
        eq->rt_seen = 1;
    }
    return 1;
}

static void identity(Biquad *f) {
    f->b0 = 1.0f; f->b1 = f->b2 = f->a1 = f->a2 = 0.0f;
}

static void coefficients(Biquad *f, int type, float frequency, float gain, float q,
                         float rate) {
    const float w0 = 6.28318530717958647692f * frequency / rate;
    const float c = cosf(w0), s = sinf(w0), alpha = s / (2.0f * q);
    const float a = powf(10.0f, gain / 40.0f);
    const float shelf_alpha = s / (2.0f * q);
    const float root = 2.0f * sqrtf(a) * shelf_alpha;
    float b0, b1, b2, a0, a1, a2;
    if (type == FILTER_LOW_SHELF) {
        b0 = a * ((a + 1) - (a - 1) * c + root); b1 = 2 * a * ((a - 1) - (a + 1) * c);
        b2 = a * ((a + 1) - (a - 1) * c - root); a0 = (a + 1) + (a - 1) * c + root;
        a1 = -2 * ((a - 1) + (a + 1) * c); a2 = (a + 1) + (a - 1) * c - root;
    } else if (type == FILTER_HIGH_SHELF) {
        b0 = a * ((a + 1) + (a - 1) * c + root); b1 = -2 * a * ((a - 1) + (a + 1) * c);
        b2 = a * ((a + 1) + (a - 1) * c - root); a0 = (a + 1) - (a - 1) * c + root;
        a1 = 2 * ((a - 1) - (a + 1) * c); a2 = (a + 1) - (a - 1) * c - root;
    } else if (type == FILTER_HIGH_PASS) {
        b0 = (1 + c) * 0.5f; b1 = -(1 + c); b2 = b0;
        a0 = 1 + alpha; a1 = -2 * c; a2 = 1 - alpha;
    } else if (type == FILTER_LOW_PASS) {
        b0 = (1 - c) * 0.5f; b1 = 1 - c; b2 = b0;
        a0 = 1 + alpha; a1 = -2 * c; a2 = 1 - alpha;
    } else {
        b0 = 1 + alpha * a; b1 = -2 * c; b2 = 1 - alpha * a;
        a0 = 1 + alpha / a; a1 = -2 * c; a2 = 1 - alpha / a;
    }
    f->b0 = b0 / a0; f->b1 = b1 / a0; f->b2 = b2 / a0;
    f->a1 = a1 / a0; f->a2 = a2 / a0;
}

static LADSPA_Handle instantiate(const LADSPA_Descriptor *descriptor, unsigned long rate) {
    Equalizer *eq = calloc(1, sizeof(*eq)); unsigned int i; (void)descriptor;
    if (eq) { eq->rate = rate; for (i = 0; i < BANDS; ++i) identity(&eq->filters[i]); rt_open(eq); }
    return eq;
}
static void connect_port(LADSPA_Handle h, unsigned long p, LADSPA_Data *d) {
    if (p < PORTS) ((Equalizer *)h)->ports[p] = d;
}
static void activate(LADSPA_Handle h) {
    Equalizer *eq = h; unsigned int i;
    for (i = 0; i < BANDS; ++i) eq->filters[i].z1 = eq->filters[i].z2 = 0.0f;
}
static void run(LADSPA_Handle h, unsigned long count) {
    Equalizer *eq = h; LADSPA_Data *input = eq->ports[INPUT_PORT], *output = eq->ports[OUTPUT_PORT];
    /* Prefer live values from the runtime file; fall back to the connected
     * control ports when it is absent. control[0] is preamp, then 5 fields per
     * band (enabled, type, frequency, gain, Q) — the same order as the ports. */
    const int live = rt_refresh(eq);
    const float *ctl = eq->control; unsigned long pos; unsigned int band;
    const float preamp_db = live ? ctl[0] : *eq->ports[0];
    const float preamp = powf(10.0f, preamp_db / 20.0f);
    for (band = 0; band < BANDS; ++band) {
        const unsigned int base = 1 + band * 5;
        const float enabled = live ? ctl[base] : *eq->ports[base];
        if (enabled >= 0.5f) {
            const float type = live ? ctl[base + 1] : *eq->ports[base + 1];
            const float gain = live ? ctl[base + 3] : *eq->ports[base + 3];
            const float q = live ? ctl[base + 4] : *eq->ports[base + 4];
            float frequency = live ? ctl[base + 2] : *eq->ports[base + 2];
            if (frequency > eq->rate * 0.45f) frequency = eq->rate * 0.45f;
            coefficients(&eq->filters[band], (int)(type + 0.5f), frequency,
                         gain, q, (float)eq->rate);
        } else identity(&eq->filters[band]);
    }
    for (pos = 0; pos < count; ++pos) {
        float sample = input[pos] * preamp;
        for (band = 0; band < BANDS; ++band) {
            Biquad *f = &eq->filters[band]; const float next = f->b0 * sample + f->z1;
            f->z1 = f->b1 * sample - f->a1 * next + f->z2; f->z2 = f->b2 * sample - f->a2 * next;
            sample = next;
        }
        output[pos] = sample;
    }
}
static void cleanup(LADSPA_Handle h) {
    free(h);
}

static LADSPA_PortDescriptor port_descriptors[PORTS];
static const char *port_names[PORTS];
static LADSPA_PortRangeHint port_hints[PORTS];
static char generated_names[BANDS][5][32];
static LADSPA_Descriptor descriptor;
static int initialized;

static void initialize_descriptor(void) {
    unsigned int band, field;
    if (initialized) return;
    initialized = 1;
    port_descriptors[0] = LADSPA_PORT_INPUT | LADSPA_PORT_CONTROL; port_names[0] = "Preamp (dB)";
    port_hints[0] = (LADSPA_PortRangeHint){3, -24.0f, 0.0f};
    for (band = 0; band < BANDS; ++band) {
        const char *labels[5] = {"Enabled", "Type", "Frequency (Hz)", "Gain (dB)", "Q"};
        const float low[5] = {0, 0, 20, -15, 0.1f}, high[5] = {1, 4, 20000, 15, 10};
        const unsigned long extras[5] = {LADSPA_HINT_TOGGLED, LADSPA_HINT_INTEGER,
            LADSPA_HINT_LOGARITHMIC, 0, LADSPA_HINT_LOGARITHMIC};
        for (field = 0; field < 5; ++field) {
            const unsigned int port = 1 + band * 5 + field;
            snprintf(generated_names[band][field], sizeof(generated_names[band][field]),
                     "Band %u %s", band + 1, labels[field]);
            port_descriptors[port] = LADSPA_PORT_INPUT | LADSPA_PORT_CONTROL;
            port_names[port] = generated_names[band][field];
            port_hints[port] = (LADSPA_PortRangeHint){3 | extras[field], low[field], high[field]};
        }
    }
    port_descriptors[INPUT_PORT] = LADSPA_PORT_INPUT | LADSPA_PORT_AUDIO;
    port_descriptors[OUTPUT_PORT] = LADSPA_PORT_OUTPUT | LADSPA_PORT_AUDIO;
    port_names[INPUT_PORT] = "Input"; port_names[OUTPUT_PORT] = "Output";
    descriptor = (LADSPA_Descriptor){5891, "radio_equalizer", LADSPA_PROPERTY_HARD_RT_CAPABLE,
        "Raspberry Kitchen Radio Parametric Equalizer", "Raspberry Kitchen Radio contributors",
        "MIT", PORTS, port_descriptors, port_names, port_hints, NULL, instantiate, connect_port,
        activate, run, NULL, NULL, NULL, cleanup};
}

const LADSPA_Descriptor *ladspa_descriptor(unsigned long index) {
    initialize_descriptor(); return index == 0 ? &descriptor : NULL;
}