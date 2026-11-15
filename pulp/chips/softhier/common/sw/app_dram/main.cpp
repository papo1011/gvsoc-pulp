// Copyright 2026 ETH Zurich and University of Bologna.
// Licensed under the Apache License, Version 2.0, see LICENSE for details.
// SPDX-License-Identifier: Apache-2.0

#include "../dram_common/dma.h"

// Keep printf independent of 64-bit libgcc helpers: some toolchains build
// those with compressed instructions, which this core does not support.
#define PRINTF_DISABLE_SUPPORT_LONG_LONG
#include "softhier_printf.h"

static constexpr uint32_t RING_NODES = 64;
static constexpr uint32_t STRIDE_WORDS = RING_BYTES / RING_NODES / sizeof(uint32_t);
static constexpr uint32_t READS = 128;

static void begin(const char *location, uint32_t channel, const char *operation, uint32_t bytes,
                  uint32_t repetitions)
{
    printf("[dram] location=%s channel=%u mesh_hops=%u operation=%s bytes=%u repetitions=%u\n",
           location, channel, channel % ARCH_NUM_CLUSTER_X + channel / ARCH_NUM_CLUSTER_X,
           operation, bytes, repetitions);
    flex_timer_start();
    asm volatile("" ::: "memory");
}

static uint32_t benchmark(const char *location, uint32_t channel)
{
    // Reserve the tail of each channel, away from ELF data at its base.
    const uint32_t ring_address = ARCH_HBM_START_BASE + channel * ARCH_HBM_NODE_ADDR_SPACE +
                                  ARCH_HBM_NODE_ADDR_SPACE - MAX_BYTES - RING_BYTES;
    const uint32_t data_address = ring_address + RING_BYTES;
    volatile uint32_t *ring = (volatile uint32_t *)ring_address;
    volatile uint32_t *src = (volatile uint32_t *)SRC;
    uint32_t errors = 0;

    // A full cycle with a non-unit stride: each load depends on the previous one.
    for (uint32_t i = 0; i < RING_NODES; ++i)
        ring[i * STRIDE_WORDS] = ((i + 17) % RING_NODES) * STRIDE_WORDS;
    asm volatile("fence rw, rw" ::: "memory");
    for (uint32_t i = 0; i < RING_NODES; ++i)
        errors += ring[i * STRIDE_WORDS] != ((i + 17) % RING_NODES) * STRIDE_WORDS;
    uint32_t index = 0;
    for (uint32_t i = 0; i < RING_NODES; ++i)
        index = ring[index];
    errors += index != 0;

    begin(location, channel, "dependent_read", sizeof(uint32_t), READS);
    for (uint32_t i = 0; i < READS; ++i)
        index = ring[index];
    // Consume the final load before stopping the timer.
    errors += index != 0;
    flex_timer_end();

    fill_source(0);
    const uint32_t sizes[] = {64, 4096, MAX_BYTES};
    for (uint32_t bytes : sizes)
    {
        // Untimed initialization, readback and warm-up for this transfer size.
        errors += prepare(data_address, bytes);

        begin(location, channel, "dma_read", bytes, REPEATS);
        errors += transfer(data_address, bytes, false);
        flex_timer_end();
        errors += check(DST, bytes, src[0]);

        // Posted writes alone do not measure DRAM completion. Read back each
        // transfer and check a changing word before issuing the next write.
        begin(location, channel, "dma_write_readback", bytes, REPEATS);
        errors += transfer(data_address, bytes, true);
        flex_timer_end();
        errors += check(DST, bytes, src[0]);
    }
    return errors;
}

int main()
{
    // Returning parks all other cores in the startup WFI loop.
    if (flex_get_cluster_id() != 0 || !flex_is_dm_core())
        return 0;

    printf("[dram] cluster=0 core=%u mesh=%ux%u\n", flex_get_core_id(), ARCH_NUM_CLUSTER_X,
           ARCH_NUM_CLUSTER_Y);
    uint32_t errors = benchmark("local", 0);
    errors += benchmark("far", ARCH_NUM_CLUSTER - 1);
    printf("[dram] errors=%u\n", errors);
    printf("SoftHier DRAM: %s\n", errors == 0 ? "OK" : "FAILED");
    // The host must check the log marker as SoftHier v1 ignores this status.
    flex_eoc(errors != 0);
    return errors != 0;
}
