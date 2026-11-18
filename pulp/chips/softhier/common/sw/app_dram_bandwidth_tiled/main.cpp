// Copyright 2026 ETH Zurich and University of Bologna.
// Licensed under the Apache License, Version 2.0, see LICENSE for details.
// SPDX-License-Identifier: Apache-2.0

#include "../dram_common/dma.h"

#define PRINTF_DISABLE_SUPPORT_LONG_LONG
#include "softhier_printf.h"

static constexpr uint32_t TOTALS[] = {512 * 1024,  1020 * 1024, 1024 * 1024, 1028 * 1024,
                                      1032 * 1024, 1036 * 1024, 2048 * 1024};
static constexpr uint32_t LAST_TOTAL = TOTALS[sizeof(TOTALS) / sizeof(TOTALS[0]) - 1];
static constexpr uint32_t HBM_OFFSET = 32 * 1024 * 1024;
static constexpr uint32_t SAMPLES = 3;
static constexpr uint32_t DMA_LIMITS[] = {256 * 1024, 1024 * 1024};
static_assert(ARCH_CLUSTER_TCDM_SIZE == 1024 * 1024, "This comparison assumes a 1 MiB TCDM");
static_assert(HBM_OFFSET + LAST_TOTAL <= ARCH_HBM_NODE_ADDR_SPACE,
              "The complete source must fit in HBM channel 0");

// Prepare one shared source so both tile sizes read exactly the same data.
// Each 64 KiB page has an absolute-address marker followed by the shared pattern.
static void initialize_hbm()
{
    fill_source(0);
    volatile uint32_t *source = (volatile uint32_t *)SRC;
    for (uint32_t offset = 0; offset < LAST_TOTAL; offset += MAX_BYTES)
    {
        source[0] = pattern((HBM_OFFSET + offset) / sizeof(uint32_t));
        copy(ARCH_HBM_START_BASE + HBM_OFFSET + offset, SRC, MAX_BYTES);
    }
    // Setup writes are not timed. Confirm their completion in the DRAM records.
    const uint64_t deadline = flex_time_ps() + 100000000; // 100 us.
    while (flex_time_ps() < deadline)
    {
    }
}

static uint32_t measure(uint32_t total, uint32_t limit, uint32_t sample)
{
    if (total == 0 || limit == 0)
        return 1;
    const uint32_t chunk = total < limit ? total : limit;
    const uint32_t commands = (total + limit - 1) / limit;
    const uint32_t last_offset = (commands - 1) * limit;
    const uint32_t last_bytes = total - last_offset;
    // The 1 MiB case uses the whole TCDM, including its valid physical address 0.
    // Staging data is dead after setup; make zero opaque to C++ null folding.
    uint32_t target = local(limit == ARCH_CLUSTER_TCDM_SIZE ? 0 : 0x20000);
    asm volatile("" : "+r"(target));
    volatile uint32_t *destination = (volatile uint32_t *)target;
    // Poison page boundaries to expose stale data without a large untimed CPU
    // fill. Every word of the final tile is still checked after measurement.
    for (uint32_t page = 0; page < chunk; page += MAX_BYTES)
    {
        const uint32_t page_bytes = chunk - page < MAX_BYTES ? chunk - page : MAX_BYTES;
        destination[page / sizeof(uint32_t)] = 0;
        destination[(page + page_bytes) / sizeof(uint32_t) - 1] =
            ~pattern(page_bytes / sizeof(uint32_t) - 1);
    }

    // A dataset larger than TCDM is read in successive tiles. Wait before reusing
    // the destination: this measures DMA segmentation without a CPU consumer.
    // Keep all checks outside this interval so they do not interrupt the stream.
    const uint64_t start = flex_time_ps();
    for (uint32_t offset = 0; offset < total; offset += chunk)
    {
        // The final copy may be only 4/8/12 KiB. Never round it up: that would
        // read beyond the dataset and hide the cost of crossing the TCDM limit.
        const uint32_t remaining = total - offset;
        copy(target, ARCH_HBM_START_BASE + HBM_OFFSET + offset,
             remaining < chunk ? remaining : chunk);
    }
    const uint64_t end = flex_time_ps();

    // Earlier tiles were overwritten. Check every word of the final tile,
    // including each page's marker; the DRAM audit verifies all earlier reads.
    uint32_t errors = 0;
    for (uint32_t page = 0; page < last_bytes; page += MAX_BYTES)
    {
        const uint32_t page_bytes = last_bytes - page < MAX_BYTES ? last_bytes - page : MAX_BYTES;
        errors += check(target + page, page_bytes,
                        pattern((HBM_OFFSET + last_offset + page) / sizeof(uint32_t)));
    }
    printf("[dram-tiled] cluster=0 channel=0 operation=dma_read bytes=%u "
           "dma_limit_bytes=%u dma_commands=%u last_tile_bytes=%u repetitions=1 sample=%u "
           "hbm_offset=%u "
           "start_ps=0x%08x%08x end_ps=0x%08x%08x errors=%u\n",
           total, limit, commands, last_bytes, sample, HBM_OFFSET, (uint32_t)(start >> 32),
           (uint32_t)start, (uint32_t)(end >> 32), (uint32_t)end, errors);
    return errors;
}

int main()
{
    if (flex_get_cluster_id() != 0 || !flex_is_dm_core())
        return 0;

    // Other cores remain in WFI. This app has no barrier, and stacks/code live
    // outside TCDM. Reject future persistent L1 data before using the whole MiB.
    extern char __l1_data_start[], __l1_end[];
    if (__l1_data_start != __l1_end)
    {
        printf("SoftHier tiled DRAM bandwidth: FAILED (persistent TCDM data)\n");
        flex_eoc(1);
        return 1;
    }
    printf("[dram-tiled] tcdm_bytes=%u max_bytes=%u\n", ARCH_CLUSTER_TCDM_SIZE, LAST_TOTAL);
    initialize_hbm();
    uint32_t errors = 0;
    for (uint32_t total : TOTALS)
        for (uint32_t sample = 0; sample < SAMPLES; ++sample)
            // Alternate order to reduce a systematic advantage from going second.
            // This does not force identical or closed DRAM rows before each case.
            for (uint32_t order = 0; order < 2; ++order)
                errors += measure(total, DMA_LIMITS[(sample + order) % 2], sample);
    printf("[dram-tiled] errors=%u\n", errors);
    printf("SoftHier tiled DRAM bandwidth: %s\n", errors ? "FAILED" : "OK");
    flex_eoc(errors != 0);
    return errors != 0;
}
