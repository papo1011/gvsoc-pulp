// Copyright 2026 ETH Zurich and University of Bologna.
// Licensed under the Apache License, Version 2.0, see LICENSE for details.
// SPDX-License-Identifier: Apache-2.0

// The sequential sweep: eight consecutive blocks per sample, each copy awaited
// before the next. main.cpp sets BANDWIDTH_SEQUENTIAL to 1. The branches for 0
// are the retired "warm" mode (one block reread); they are kept unchanged so
// the binary stays identical to the one that produced assets/ (removing them
// changes register allocation, which shifts small-copy timings by a few ns).

#pragma once

#include "../dram_common/dma.h"

#define PRINTF_DISABLE_SUPPORT_LONG_LONG
#include "softhier_printf.h"

static constexpr uint32_t FIRST_BYTES = 4;
static constexpr uint32_t LAST_BYTES = BANDWIDTH_SEQUENTIAL ? 1024 * 1024 : 512 * 1024;
static constexpr uint32_t SAMPLES = 3;
// A refresh blocks the channel for 450 ns and makes a sample of small copies
// several times slower, so small blocks get more samples to keep the median
// clear of refreshes.
static constexpr uint32_t SMALL_BYTES = 8 * 1024;
static constexpr uint32_t SMALL_SAMPLES = 15;
static constexpr uint32_t samples(uint32_t bytes)
{
    return bytes <= SMALL_BYTES ? SMALL_SAMPLES : SAMPLES;
}
static constexpr uint32_t READ_BUFFER = local(0x20000);
static constexpr uint32_t SEQUENTIAL_START = 32 * 1024 * 1024;
static constexpr uint32_t FULL_TCDM_SOURCE = 8 * 1024 * 1024;
// Each sequential sample gets fresh addresses, including across transfer sizes.
// Keep the original windows above 32 MiB; the three 1 MiB samples use 8..32 MiB.
static constexpr uint32_t REGION_BYTES =
    BANDWIDTH_SEQUENTIAL ? ARCH_HBM_NODE_ADDR_SPACE - FULL_TCDM_SOURCE : LAST_BYTES;
static constexpr uint32_t HBM_OFFSET = ARCH_HBM_NODE_ADDR_SPACE - REGION_BYTES;
static constexpr uint32_t HBM_BUFFER = ARCH_HBM_START_BASE + HBM_OFFSET;
static_assert(0x20000 + 512 * 1024 < ARCH_CLUSTER_TCDM_SIZE - 16,
              "The read buffer must fit below the runtime barrier words");
static_assert(LAST_BYTES <= ARCH_CLUSTER_TCDM_SIZE, "The largest copy must fit in local TCDM");
static_assert(SRC + MAX_BYTES <= READ_BUFFER, "Initialization and read buffers must not overlap");
static_assert(REGION_BYTES < ARCH_HBM_NODE_ADDR_SPACE, "The source must fit in HBM channel 0");

// A 1 MiB read needs the entire local TCDM. Staging data is dead after setup,
// stacks/instructions live elsewhere, and this app never uses barrier words.
// Hide address zero from constant folding: zero is valid memory on this target,
// whereas a compile-time C++ null dereference could be optimized into a trap.
static uint32_t destination_buffer(uint32_t bytes)
{
    uint32_t address = bytes == ARCH_CLUSTER_TCDM_SIZE ? local(0) : READ_BUFFER;
    asm volatile("" : "+r"(address));
    return address;
}

// Align each sample without overlapping another sample's physical source window.
static uint32_t align_window(uint32_t cursor, uint32_t bytes)
{
    if (bytes == 1024 * 1024 && cursor >= SEQUENTIAL_START)
        cursor = FULL_TCDM_SOURCE;
    const uint32_t alignment = bytes < 32 ? 32 : bytes;
    return (cursor + alignment - 1) & ~(alignment - 1);
}

static uint32_t block_tag(uint32_t offset)
{
    return pattern(offset / sizeof(uint32_t)) ^ 0xa5a5a5a5u;
}

// Sequential initialization repeats one staging pattern using DMA, then tags
// every logical source block. This avoids a CPU loop over the entire HBM area
// while still detecting an incorrect final source address during validation.
static void initialize_hbm()
{
    volatile uint32_t *source = (volatile uint32_t *)SRC;
    if (BANDWIDTH_SEQUENTIAL)
        fill_source(0);
    for (uint32_t offset = 0; offset < REGION_BYTES; offset += MAX_BYTES)
    {
        if (!BANDWIDTH_SEQUENTIAL)
            for (uint32_t i = 0; i < MAX_BYTES / sizeof(uint32_t); ++i)
                source[i] = pattern(offset / sizeof(uint32_t) + i);
        copy(HBM_BUFFER + offset, SRC, MAX_BYTES);
    }
    if (BANDWIDTH_SEQUENTIAL)
    {
        uint32_t cursor = BANDWIDTH_SEQUENTIAL ? SEQUENTIAL_START : HBM_OFFSET;
        for (uint32_t bytes = FIRST_BYTES; bytes <= LAST_BYTES; bytes *= 2)
            for (uint32_t sample = 0; sample < samples(bytes); ++sample)
            {
                cursor = align_window(cursor, bytes);
                for (uint32_t repetition = 0; repetition < REPEATS; ++repetition)
                {
                    const uint32_t offset = cursor + repetition * bytes;
                    source[0] = block_tag(offset);
                    copy(ARCH_HBM_START_BASE + offset, SRC, sizeof(uint32_t));
                }
                cursor += REPEATS * bytes;
            }
    }
}

static uint32_t measure(uint32_t bytes, uint32_t sample, uint32_t offset)
{
    const uint32_t address = ARCH_HBM_START_BASE + offset;
    const uint32_t target = destination_buffer(bytes);
    volatile uint32_t *destination = (volatile uint32_t *)target;
    for (uint32_t i = 0; i < bytes / sizeof(uint32_t); ++i)
        destination[i] = ~pattern(i);
    uint32_t errors = 0;
    if (!BANDWIDTH_SEQUENTIAL)
    {
        // The original mode intentionally measures repeated, warmed addresses.
        copy(target, address, bytes);
        errors += check(target, bytes, pattern(0));
    }

    random_idle(sample << 24 | bytes);
    // One logical DMA copy at a time. The DMA may still have
    // several internal requests in flight. Timing includes issue and wait costs.
    const uint64_t start = flex_time_ps();
    for (uint32_t repetition = 0; repetition < REPEATS; ++repetition)
        copy(target, address + (BANDWIDTH_SEQUENTIAL ? repetition * bytes : 0), bytes);
    const uint64_t end = flex_time_ps();
    // Only the final block remains in the reused destination. Its absolute
    // source index detects stale data or accidentally rereading the first block.
    const uint32_t word =
        (offset - HBM_OFFSET + (BANDWIDTH_SEQUENTIAL ? (REPEATS - 1) * bytes : 0)) /
        sizeof(uint32_t);
    const uint32_t expected_first =
        BANDWIDTH_SEQUENTIAL ? block_tag(offset + (REPEATS - 1) * bytes) : pattern(word);
    errors += check(target, bytes, expected_first, 0, word,
                    BANDWIDTH_SEQUENTIAL ? MAX_BYTES / sizeof(uint32_t) - 1 : 0xffffffffu);
    printf("[dram-bandwidth] cluster=0 channel=0 operation=dma_read bytes=%u "
           "repetitions=%u sample=%u pattern=%s hbm_offset=%u "
           "start_ps=0x%08x%08x end_ps=0x%08x%08x errors=%u\n",
           bytes, REPEATS, sample, BANDWIDTH_SEQUENTIAL ? "sequential" : "warm", offset,
           (uint32_t)(start >> 32), (uint32_t)start, (uint32_t)(end >> 32), (uint32_t)end, errors);
    return errors;
}

static int run_bandwidth_sweep()
{
    // Returning parks all compute cores and every other cluster in WFI.
    if (flex_get_cluster_id() != 0 || !flex_is_dm_core())
        return 0;

    // Refuse a whole-TCDM destination if future code adds persistent L1 data.
    extern char __l1_data_start[], __l1_end[];
    if (LAST_BYTES == ARCH_CLUSTER_TCDM_SIZE && __l1_data_start != __l1_end)
    {
        printf("SoftHier DRAM bandwidth: FAILED (persistent TCDM data)\n");
        flex_eoc(1);
        return 1;
    }
    printf("[dram-bandwidth] max_bytes=%u small_bytes=%u small_samples=%u\n", LAST_BYTES,
           SMALL_BYTES, SMALL_SAMPLES);
    initialize_hbm();
    if (BANDWIDTH_SEQUENTIAL)
    {
        // Give posted initialization writes time to drain without reading any
        // measured source. The database audit must confirm no setup WR overlaps.
        const uint64_t deadline = flex_time_ps() + 100000000; // 100 us, untimed.
        while (flex_time_ps() < deadline)
        {
        }
    }
    uint32_t errors = 0;
    uint32_t cursor = BANDWIDTH_SEQUENTIAL ? SEQUENTIAL_START : HBM_OFFSET;
    for (uint32_t bytes = FIRST_BYTES; bytes <= LAST_BYTES; bytes *= 2)
        for (uint32_t sample = 0; sample < samples(bytes); ++sample)
        {
            cursor = align_window(cursor, bytes);
            const uint32_t span = BANDWIDTH_SEQUENTIAL ? REPEATS * bytes : bytes;
            if ((uint64_t)cursor + span > (uint64_t)HBM_OFFSET + REGION_BYTES)
            {
                printf("SoftHier DRAM bandwidth: FAILED (source allocation)\n");
                flex_eoc(1);
                return 1;
            }
            errors += measure(bytes, sample, cursor);
            if (BANDWIDTH_SEQUENTIAL)
                cursor += span;
        }
    printf("[dram-bandwidth] errors=%u\n", errors);
    printf("SoftHier DRAM bandwidth: %s\n", errors ? "FAILED" : "OK");
    flex_eoc(errors != 0);
    return errors != 0;
}
