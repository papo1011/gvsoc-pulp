// Copyright 2026 ETH Zurich and University of Bologna.
// Licensed under the Apache License, Version 2.0, see LICENSE for details.
// SPDX-License-Identifier: Apache-2.0
//
// Matmul tile loads: the 2D DMA copies of a tiled kernel. Each copy reads `run`
// contiguous bytes (the tile width) from consecutive rows of a matrix whose rows
// are `stride` bytes apart and packs them in the TCDM. The strides are row
// lengths of Llama-3-8B matrices in BF16: 2 KiB = 1024 values (k_proj/v_proj
// outputs, one KV-cache token), 8 KiB = 4096 (hidden), 28 KiB = 14336 (FFN),
// 4 and 6 KiB = 2048 and 3072 (smaller models); +64 B are padded rows. Stride 0
// means stride = run, the same rows packed contiguously: the control that keeps
// the per-row DMA cost and removes the DRAM layout effect.
//
// One cluster reads its own HBM channel. Each sample issues eight 2D copies of
// about 32 KiB back to back and waits once, like the pipelined sweep, after an
// untimed random wait that keeps the samples from locking onto the DRAM refresh.

#include "../dram_common/dma.h"

#define PRINTF_DISABLE_SUPPORT_LONG_LONG
#include "softhier_printf.h"

struct Point
{
    uint32_t run;
    uint32_t stride;
};

static constexpr uint32_t RUNS[] = {32, 64, 128, 256, 512, 1024, 2048};
static constexpr uint32_t STRIDES[] = {0, 2048, 4096, 6144, 8192, 8256, 28672, 28736};
// The FFN activations entering down_proj split over 16 clusters: 896 of the
// 14336 values of each token.
static constexpr Point EXTRA[] = {{1792, 28672}};
static constexpr uint32_t SAMPLES = 5;
static constexpr uint32_t COPY_BYTES = 32 * 1024;
static constexpr uint32_t BUFFER = local(0x40000);
static constexpr uint32_t REGION = 4 * 1024 * 1024;
static constexpr uint32_t REGION_BYTES = 48 * 1024 * 1024;
static constexpr uint32_t PATTERN_MASK = MAX_BYTES / sizeof(uint32_t) - 1;

static_assert(DST + MAX_BYTES <= BUFFER, "Staging and timed buffers must not overlap");
static_assert(0x40000 + REPEATS * COPY_BYTES <= ARCH_CLUSTER_TCDM_SIZE - 16,
              "The packed copies must fit below the runtime barrier words");
static_assert(REGION + REGION_BYTES <= ARCH_HBM_NODE_ADDR_SPACE,
              "The region must fit in HBM channel 0");
static_assert(REGION % MAX_BYTES == 0, "The region must start on a pattern period");

static void fence()
{
    // The runtime's DMA intrinsics do not have compiler memory clobbers.
    asm volatile("fence rw, rw" ::: "memory");
}

// The matrix has `height` rows of `stride` bytes. Its first `height` lines are
// the first column block; the next lines continue in the block to its right, as
// a kernel walking a column of tiles and then the next column.
struct Layout
{
    uint32_t run;
    uint32_t stride;
    uint32_t lines; // per copy
    uint32_t height;
};

static Layout layout(Point point)
{
    Layout result;
    result.run = point.run;
    result.stride = point.stride ? point.stride : point.run;
    result.lines = COPY_BYTES / point.run;
    const uint32_t rows = REGION_BYTES / result.stride;
    result.height = rows / result.lines * result.lines;
    return result;
}

static uint32_t line_offset(const Layout &l, uint32_t line)
{
    return REGION + (line % l.height) * l.stride + (line / l.height) * l.run;
}

static bool fits(const Layout &l)
{
    const uint32_t last = SAMPLES * REPEATS * l.lines - 1;
    return l.height >= l.lines && (last / l.height + 1) * l.run <= l.stride;
}

static void initialize_hbm()
{
    volatile uint32_t *source = (volatile uint32_t *)SRC;
    for (uint32_t i = 0; i < MAX_BYTES / sizeof(uint32_t); ++i)
        source[i] = pattern(i);
    for (uint32_t offset = 0; offset < REGION_BYTES; offset += MAX_BYTES)
        copy(ARCH_HBM_START_BASE + REGION + offset, SRC, MAX_BYTES);
}

// The region repeats the 64 KiB pattern, so a word's expected value follows from
// its HBM offset. Only the last copy is checked; every copy uses the same code path.
static uint32_t check_copy(const Layout &l, uint32_t target, uint32_t first_line)
{
    const volatile uint32_t *buffer = (const volatile uint32_t *)target;
    const uint32_t words = l.run / sizeof(uint32_t);
    uint32_t errors = 0;
    for (uint32_t line = 0; line < l.lines; ++line)
    {
        const uint32_t base = line_offset(l, first_line + line) / sizeof(uint32_t);
        for (uint32_t word = 0; word < words; ++word)
            errors += buffer[line * words + word] != pattern((base + word) & PATTERN_MASK);
    }
    return errors;
}

static uint32_t measure(const Layout &l, uint32_t sample, uint32_t first_line)
{
    const uint32_t copy_bytes = l.lines * l.run;
    const uint32_t last = BUFFER + (REPEATS - 1) * copy_bytes;
    volatile uint32_t *buffer = (volatile uint32_t *)last;
    for (uint32_t i = 0; i < copy_bytes / sizeof(uint32_t); ++i)
        buffer[i] = ~pattern(i);
    random_idle(sample << 28 | l.stride << 12 | l.run);
    fence();
    const uint64_t start = flex_time_ps();
    for (uint32_t repetition = 0; repetition < REPEATS; ++repetition)
    {
        const uint32_t line = first_line + repetition * l.lines;
        flex_dma_async_2d((uint64_t)(BUFFER + repetition * copy_bytes),
                          (uint64_t)(ARCH_HBM_START_BASE + line_offset(l, line)), l.run, l.run,
                          l.stride, l.lines);
    }
    flex_dma_async_wait_all();
    const uint64_t end = flex_time_ps();
    fence();
    const uint32_t errors = check_copy(l, last, first_line + (REPEATS - 1) * l.lines);
    printf("[dram-matmul] cluster=0 channel=0 operation=dma_read_2d run=%u stride=%u "
           "lines=%u repetitions=%u height=%u sample=%u first_line=%u "
           "start_ps=0x%08x%08x end_ps=0x%08x%08x errors=%u\n",
           l.run, l.stride, l.lines, REPEATS, l.height, sample, first_line, (uint32_t)(start >> 32),
           (uint32_t)start, (uint32_t)(end >> 32), (uint32_t)end, errors);
    return errors;
}

static uint32_t sweep(Point point, bool &failed)
{
    const Layout l = layout(point);
    if (!fits(l))
    {
        printf("[dram-matmul] run=%u stride=%u does not fit the region\n", l.run, l.stride);
        failed = true;
        return 0;
    }
    uint32_t errors = 0;
    for (uint32_t sample = 0; sample < SAMPLES; ++sample)
        errors += measure(l, sample, sample * REPEATS * l.lines);
    return errors;
}

int main()
{
    // Returning parks all compute cores and every other cluster in WFI.
    if (flex_get_cluster_id() != 0 || !flex_is_dm_core())
        return 0;

    printf("[dram-matmul] copy_bytes=%u samples=%u in_flight=%u\n", COPY_BYTES, SAMPLES, REPEATS);
    initialize_hbm();
    // Let posted setup writes drain before the first timed copy.
    const uint64_t deadline = flex_time_ps() + 100000000; // 100 us, untimed.
    while (flex_time_ps() < deadline)
    {
    }

    uint32_t errors = 0;
    bool failed = false;
    for (uint32_t stride : STRIDES)
        for (uint32_t run : RUNS)
            if (stride == 0 || run < stride)
                errors += sweep({run, stride}, failed);
    for (Point point : EXTRA)
        errors += sweep(point, failed);
    printf("[dram-matmul] errors=%u\n", errors);
    const bool ok = errors == 0 && !failed;
    printf("SoftHier matmul DRAM bandwidth: %s\n", ok ? "OK" : "FAILED");
    flex_eoc(!ok);
    return !ok;
}
