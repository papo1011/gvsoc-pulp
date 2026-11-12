// Copyright 2026 ETH Zurich and University of Bologna.
// Licensed under the Apache License, Version 2.0, see LICENSE for details.
// SPDX-License-Identifier: Apache-2.0

#include <stddef.h>
#include "softhier_runtime.h"
#include "softhier_printf.h"
#include "softhier_dma_pattern.h"

static_assert(ARCH_SHAPE_CATEGORY == 2 && ARCH_IS_TORUS == 0, "This benchmark requires a 2D mesh");
static_assert(ARCH_NUM_CLUSTER_X * ARCH_NUM_CLUSTER_Y == ARCH_NUM_CLUSTER && ARCH_NUM_CLUSTER > 1,
              "The mesh must contain at least two tiles");
static_assert((uint64_t)ARCH_HBM_START_BASE +
                      (uint64_t)ARCH_NUM_CLUSTER * ARCH_HBM_NODE_ADDR_SPACE <=
                  (1ULL << 32),
              "The HBM map must fit in the 32-bit address space");

static constexpr uint32_t MAX_BYTES = 64 * 1024;
static constexpr uint32_t RING_BYTES = 4096;
static constexpr uint32_t RING_NODES = 64;
static constexpr uint32_t STRIDE_WORDS = RING_BYTES / RING_NODES / sizeof(uint32_t);
static constexpr uint32_t READS = 128;
static constexpr uint32_t REPEATS = 8;
static constexpr uint32_t SRC = local(0x1000);
static constexpr uint32_t DST = SRC + MAX_BYTES;
static_assert(0x1000 + 2 * MAX_BYTES < ARCH_CLUSTER_TCDM_SIZE - 16,
              "The buffers must fit below the runtime barrier words");
static_assert(MAX_BYTES + RING_BYTES <= ARCH_HBM_NODE_ADDR_SPACE,
              "The buffers must fit in one HBM channel");

static uint32_t pattern(uint32_t i)
{
    return 0x5a5a0000u ^ (i * 2654435761u);
}

static void copy(uint32_t dst, uint32_t src, uint32_t bytes)
{
    // The runtime's DMA intrinsics do not have compiler memory clobbers.
    asm volatile("fence rw, rw" ::: "memory");
    flex_dma_async_1d((uint64_t)dst, (uint64_t)src, bytes);
    flex_dma_async_wait_all();
    asm volatile("fence rw, rw" ::: "memory");
}

static uint32_t check(uint32_t address, uint32_t bytes, uint32_t first)
{
    const volatile uint32_t *buffer = (const volatile uint32_t *)address;
    uint32_t errors = buffer[0] != first;
    for (uint32_t i = 1; i < bytes / sizeof(uint32_t); ++i)
        errors += buffer[i] != pattern(i);
    return errors;
}

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

    for (uint32_t i = 0; i < MAX_BYTES / sizeof(uint32_t); ++i)
        src[i] = pattern(i);
    const uint32_t sizes[] = {64, 4096, MAX_BYTES};
    for (uint32_t bytes : sizes)
    {
        // Untimed initialization, readback and warm-up for this transfer size.
        copy(data_address, SRC, bytes);
        copy(DST, data_address, bytes);
        errors += check(DST, bytes, src[0]);

        begin(location, channel, "dma_read", bytes, REPEATS);
        for (uint32_t i = 0; i < REPEATS; ++i)
            copy(DST, data_address, bytes);
        flex_timer_end();
        errors += check(DST, bytes, src[0]);

        // Posted writes alone do not measure DRAM completion. Read back each
        // transfer and check a changing word before issuing the next write.
        begin(location, channel, "dma_write_readback", bytes, REPEATS);
        for (uint32_t i = 0; i < REPEATS; ++i)
        {
            src[0] = pattern(i + 1);
            copy(data_address, SRC, bytes);
            copy(DST, data_address, bytes);
            errors += *(volatile uint32_t *)DST != src[0];
        }
        flex_timer_end();
        errors += check(DST, bytes, src[0]);
    }
    return errors;
}

int main()
{
    // Returning parks the other cores in the startup code's WFI loop.
    if (flex_get_cluster_id() != 0 || !flex_is_dm_core())
        return 0;

    printf("[dram] cluster=0 core=%u mesh=%ux%u\n", flex_get_core_id(), ARCH_NUM_CLUSTER_X,
           ARCH_NUM_CLUSTER_Y);
    uint32_t errors = benchmark("local", 0);
    errors += benchmark("far", ARCH_NUM_CLUSTER - 1);
    printf("[dram] errors=%u\n", errors);
    printf("SoftHier DRAM: %s\n", errors == 0 ? "OK" : "FAILED");
    // SoftHier v1 ignores this value; the host must also check the OK marker.
    flex_eoc(errors != 0);
    return errors != 0;
}
