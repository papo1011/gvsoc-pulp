// Copyright 2026 ETH Zurich and University of Bologna.
// Licensed under the Apache License, Version 2.0, see LICENSE for details.
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include <stddef.h>
#include "softhier_runtime.h"
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
static constexpr uint32_t REPEATS = 8;
static constexpr uint32_t SRC = local(0x1000);
static constexpr uint32_t DST = SRC + MAX_BYTES;
static_assert(0x1000 + 2 * MAX_BYTES < ARCH_CLUSTER_TCDM_SIZE - 16,
              "The buffers must fit below the runtime barrier words");
static_assert(MAX_BYTES + RING_BYTES <= ARCH_HBM_NODE_ADDR_SPACE,
              "The buffers must fit in one HBM channel");

static uint32_t pattern(uint32_t i, uint32_t cluster = 0)
{
    return 0x5a5a0000u ^ (i * 2654435761u) ^ (cluster * 0x01010101u);
}

static void copy(uint32_t dst, uint32_t src, uint32_t bytes)
{
    // The runtime's DMA intrinsics do not have compiler memory clobbers.
    asm volatile("fence rw, rw" ::: "memory");
    flex_dma_async_1d((uint64_t)dst, (uint64_t)src, bytes);
    flex_dma_async_wait_all();
    asm volatile("fence rw, rw" ::: "memory");
}

static uint32_t check(uint32_t address, uint32_t bytes, uint32_t first, uint32_t cluster = 0)
{
    const volatile uint32_t *buffer = (const volatile uint32_t *)address;
    uint32_t errors = buffer[0] != first;
    for (uint32_t i = 1; i < bytes / sizeof(uint32_t); ++i)
        errors += buffer[i] != pattern(i, cluster);
    return errors;
}

static void fill_source(uint32_t cluster)
{
    volatile uint32_t *src = (volatile uint32_t *)SRC;
    for (uint32_t i = 0; i < MAX_BYTES / sizeof(uint32_t); ++i)
        src[i] = pattern(i, cluster);
}

static uint32_t prepare(uint32_t address, uint32_t bytes, uint32_t cluster = 0)
{
    copy(address, SRC, bytes);
    copy(DST, address, bytes);
    return check(DST, bytes, *(volatile uint32_t *)SRC, cluster);
}

// Use the same timed work for baseline and concurrent cases. Readback makes
// posted writes observable, and the changing first word detects stale data.
static uint32_t transfer(uint32_t address, uint32_t bytes, bool write, uint32_t cluster = 0)
{
    volatile uint32_t *src = (volatile uint32_t *)SRC;
    uint32_t errors = 0;
    for (uint32_t i = 0; i < REPEATS; ++i)
    {
        if (write)
        {
            src[0] = pattern(i + 1, cluster);
            copy(address, SRC, bytes);
        }
        copy(DST, address, bytes);
        if (write)
            errors += *(volatile uint32_t *)DST != src[0];
    }
    return errors;
}
