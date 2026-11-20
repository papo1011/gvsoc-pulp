// Copyright 2026 ETH Zurich and University of Bologna.
// Licensed under the Apache License, Version 2.0, see LICENSE for details.
// SPDX-License-Identifier: Apache-2.0
//
// Contiguous HBM reads from 64 B to 512 KiB. Each sample issues eight
// consecutive blocks back to back and waits once, so the DMA always has the
// next copy queued: this measures sustained bandwidth rather than one copy at
// a time.

#include "../dram_common/dma.h"

#define PRINTF_DISABLE_SUPPORT_LONG_LONG
#include "softhier_printf.h"

static constexpr uint32_t FIRST_BYTES = 64;
static constexpr uint32_t LAST_BYTES = 512 * 1024;
static constexpr uint32_t SAMPLES = 3;
// TCDM destination of every timed copy, clear of the staging buffers in dma.h.
static constexpr uint32_t BUFFER = local(0x40000);
// HBM channel 0 offsets: every sample reads its own consecutive window, so no
// block is read twice.
static constexpr uint32_t REGION = 4 * 1024 * 1024;
static constexpr uint32_t REGION_BYTES = 28 * 1024 * 1024;
static constexpr uint32_t PATTERN_MASK = MAX_BYTES / sizeof(uint32_t) - 1;

static_assert(DST + MAX_BYTES <= BUFFER, "Staging and timed buffers must not overlap");
static_assert(0x40000 + LAST_BYTES <= ARCH_CLUSTER_TCDM_SIZE - 16,
              "The timed buffer must fit below the runtime barrier words");
static_assert(REGION + REGION_BYTES <= ARCH_HBM_NODE_ADDR_SPACE,
              "The region must fit in HBM channel 0");
static_assert(REGION % MAX_BYTES == 0, "The region must start on a pattern period");

static void fence()
{
    // The runtime's DMA intrinsics do not have compiler memory clobbers.
    asm volatile("fence rw, rw" ::: "memory");
}

// The first word of every block is tagged with its address, so reading the
// wrong block is detected even though the pattern repeats every 64 KiB.
static uint32_t block_tag(uint32_t offset)
{
    return pattern(offset / sizeof(uint32_t)) ^ 0xa5a5a5a5u;
}

static uint32_t align(uint32_t cursor, uint32_t bytes)
{
    return (cursor + bytes - 1) & ~(bytes - 1);
}

// Fill the region with the 64 KiB staging pattern, then tag every block.
static void initialize_hbm()
{
    volatile uint32_t *source = (volatile uint32_t *)SRC;
    for (uint32_t i = 0; i < MAX_BYTES / sizeof(uint32_t); ++i)
        source[i] = pattern(i);
    for (uint32_t offset = 0; offset < REGION_BYTES; offset += MAX_BYTES)
        copy(ARCH_HBM_START_BASE + REGION + offset, SRC, MAX_BYTES);
    uint32_t cursor = REGION;
    for (uint32_t bytes = FIRST_BYTES; bytes <= LAST_BYTES; bytes *= 2)
        for (uint32_t sample = 0; sample < SAMPLES; ++sample)
        {
            cursor = align(cursor, bytes);
            for (uint32_t repetition = 0; repetition < REPEATS; ++repetition)
            {
                source[0] = block_tag(cursor + repetition * bytes);
                copy(ARCH_HBM_START_BASE + cursor + repetition * bytes, SRC, sizeof(uint32_t));
            }
            cursor += REPEATS * bytes;
        }
}

static uint32_t measure(uint32_t bytes, uint32_t sample, uint32_t offset)
{
    volatile uint32_t *buffer = (volatile uint32_t *)BUFFER;
    for (uint32_t i = 0; i < bytes / sizeof(uint32_t); ++i)
        buffer[i] = ~pattern(i);
    const uint32_t address = ARCH_HBM_START_BASE + offset;
    fence();
    const uint64_t start = flex_time_ps();
    for (uint32_t repetition = 0; repetition < REPEATS; ++repetition)
        flex_dma_async_1d((uint64_t)BUFFER, (uint64_t)(address + repetition * bytes), bytes);
    flex_dma_async_wait_all();
    const uint64_t end = flex_time_ps();
    fence();
    // The copies complete in order, so the buffer holds the last block.
    const uint32_t last = offset + (REPEATS - 1) * bytes;
    const uint32_t errors =
        check(BUFFER, bytes, block_tag(last), 0, last / sizeof(uint32_t), PATTERN_MASK);
    printf("[dram-pipelined] cluster=0 channel=0 operation=dma_read bytes=%u repetitions=%u "
           "sample=%u hbm_offset=%u start_ps=0x%08x%08x end_ps=0x%08x%08x errors=%u\n",
           bytes, REPEATS, sample, offset, (uint32_t)(start >> 32), (uint32_t)start,
           (uint32_t)(end >> 32), (uint32_t)end, errors);
    return errors;
}

int main()
{
    // Returning parks all compute cores and every other cluster in WFI.
    if (flex_get_cluster_id() != 0 || !flex_is_dm_core())
        return 0;

    printf("[dram-pipelined] first_bytes=%u last_bytes=%u samples=%u in_flight=%u\n", FIRST_BYTES,
           LAST_BYTES, SAMPLES, REPEATS);
    initialize_hbm();
    // Let posted setup writes drain before the first timed copy.
    const uint64_t deadline = flex_time_ps() + 100000000; // 100 us, untimed.
    while (flex_time_ps() < deadline)
    {
    }

    uint32_t errors = 0;
    uint32_t cursor = REGION;
    for (uint32_t bytes = FIRST_BYTES; bytes <= LAST_BYTES; bytes *= 2)
        for (uint32_t sample = 0; sample < SAMPLES; ++sample)
        {
            cursor = align(cursor, bytes);
            if (cursor + REPEATS * bytes > REGION + REGION_BYTES)
            {
                printf("SoftHier pipelined DRAM bandwidth: FAILED (region too small)\n");
                flex_eoc(1);
                return 1;
            }
            errors += measure(bytes, sample, cursor);
            cursor += REPEATS * bytes;
        }
    printf("[dram-pipelined] errors=%u\n", errors);
    printf("SoftHier pipelined DRAM bandwidth: %s\n", errors ? "FAILED" : "OK");
    flex_eoc(errors != 0);
    return errors != 0;
}
