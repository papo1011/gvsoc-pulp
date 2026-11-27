// Copyright 2026 ETH Zurich and University of Bologna.
// Licensed under the Apache License, Version 2.0, see LICENSE for details.
// SPDX-License-Identifier: Apache-2.0
//
// Embedding lookup: read one table row per token from HBM into the TCDM. The
// rows to read come from the token IDs, so the addresses depend on the data.
// A piece is what one HBM channel holds of an 8 KiB row (hidden 4096, BF16):
// the whole row when the table is split by rows, 256 B to 4 KiB when it is split
// by columns over 32 to 2 channels.
//
// Orders, for every piece size:
//   block       contiguous rows, one DMA for all of them (reference)
//   contiguous  contiguous rows, one DMA per token: the per-token issue cost
//   sorted      random rows in ascending order: the lookup after sorting the IDs
//   random      random rows in token order: the real lookup
//
// One cluster reads its own HBM channel. Each sample issues the DMAs of 64
// tokens back to back and waits once, after an untimed random wait that keeps
// the samples from locking onto the DRAM refresh phase.

#include "../dram_common/dma.h"

#define PRINTF_DISABLE_SUPPORT_LONG_LONG
#include "softhier_printf.h"

enum Order : uint32_t
{
    BLOCK,
    CONTIGUOUS,
    SORTED,
    RANDOM,
    ORDERS
};

static const char *const ORDER_NAMES[] = {"block", "contiguous", "sorted", "random"};
static constexpr uint32_t PIECES[] = {256, 512, 1024, 2048, 4096, 8192};
static constexpr uint32_t TOKENS = 64;
// A 64-token sample of small pieces lasts about 0.3 us, and one in three starts
// while a refresh blocks the channel: enough samples keep the median clear of it.
static constexpr uint32_t SAMPLES = 21;
// HBM channel 0 offsets of the table: every piece size uses the whole region.
static constexpr uint32_t TABLE = 4 * 1024 * 1024;
static constexpr uint32_t TABLE_BYTES = 48 * 1024 * 1024;
static constexpr uint32_t PATTERN_MASK = MAX_BYTES / sizeof(uint32_t) - 1;
// TCDM: token IDs of the current sample, then the rows read.
static constexpr uint32_t IDS = local(0x30000);
static constexpr uint32_t BUFFER = local(0x40000);

static_assert(DST + MAX_BYTES <= IDS, "Staging buffers and token IDs must not overlap");
static_assert(IDS + TOKENS * sizeof(uint32_t) <= BUFFER, "Token IDs and rows must not overlap");
static_assert(0x40000 + TOKENS * 8192 <= ARCH_CLUSTER_TCDM_SIZE - 16,
              "The rows read must fit below the runtime barrier words");
static_assert(TABLE + TABLE_BYTES <= ARCH_HBM_NODE_ADDR_SPACE, "The table must fit in channel 0");
static_assert(TABLE % MAX_BYTES == 0, "The table must start on a pattern period");
static_assert(TOKENS * sizeof(uint32_t) <= MAX_BYTES, "The tags of a sample must fit in SRC");
// The block and contiguous samples of every piece size read their own rows.
static_assert(2 * SAMPLES * TOKENS * (256 + 512 + 1024 + 2048 + 4096 + 8192) <= TABLE_BYTES,
              "The contiguous windows must fit in the table");

static void fence()
{
    // The runtime's DMA intrinsics do not have compiler memory clobbers.
    asm volatile("fence rw, rw" ::: "memory");
}

// The first word of every row is tagged with its offset, so reading the wrong
// row is detected even though the fill pattern repeats every 64 KiB.
static uint32_t row_tag(uint32_t offset)
{
    return pattern(offset / sizeof(uint32_t)) ^ 0xa5a5a5a5u;
}

static uint32_t shift_of(uint32_t value)
{
    uint32_t result = 0;
    while (value >>= 1)
        ++result;
    return result;
}

// Contiguous windows: block and contiguous samples never reread a row, and
// each piece size gets its own part of the table.
static uint32_t window_row(uint32_t piece, Order order, uint32_t sample)
{
    uint32_t bytes = 0;
    for (uint32_t other : PIECES)
        if (other < piece)
            bytes += 2 * SAMPLES * TOKENS * other;
    return bytes / piece + (2 * sample + (order == CONTIGUOUS)) * TOKENS;
}

// Deterministic random IDs, mirrored by summarize.py.
static uint32_t seed(uint32_t piece, Order order, uint32_t sample)
{
    const uint32_t value =
        0x9e3779b9u ^ (piece * 0x85ebca6bu) ^ (order * 0xc2b2ae35u) ^ (sample * 0x27d4eb2fu);
    return value ? value : 1;
}

static uint32_t xorshift(uint32_t &state)
{
    state ^= state << 13;
    state ^= state >> 17;
    state ^= state << 5;
    return state;
}

static void make_ids(uint32_t piece, Order order, uint32_t sample, uint32_t *ids)
{
    if (order == BLOCK || order == CONTIGUOUS)
    {
        const uint32_t first = window_row(piece, order, sample);
        for (uint32_t token = 0; token < TOKENS; ++token)
            ids[token] = first + token;
        return;
    }
    const uint32_t rows = TABLE_BYTES / piece;
    uint32_t state = seed(piece, order, sample);
    for (uint32_t token = 0; token < TOKENS; ++token)
        ids[token] = xorshift(state) % rows;
    if (order == SORTED)
        for (uint32_t i = 1; i < TOKENS; ++i)
            for (uint32_t j = i; j > 0 && ids[j - 1] > ids[j]; --j)
            {
                const uint32_t id = ids[j];
                ids[j] = ids[j - 1];
                ids[j - 1] = id;
            }
}

// Fill the table with the 64 KiB staging pattern, then tag every row that a
// sample will read. Tagging just before a sample would leave its DRAM rows open.
static void initialize_hbm()
{
    volatile uint32_t *source = (volatile uint32_t *)SRC;
    for (uint32_t i = 0; i < MAX_BYTES / sizeof(uint32_t); ++i)
        source[i] = pattern(i);
    for (uint32_t offset = 0; offset < TABLE_BYTES; offset += MAX_BYTES)
        copy(ARCH_HBM_START_BASE + TABLE + offset, SRC, MAX_BYTES);
    uint32_t *ids = (uint32_t *)IDS;
    for (uint32_t piece : PIECES)
        for (uint32_t order = 0; order < ORDERS; ++order)
            for (uint32_t sample = 0; sample < SAMPLES; ++sample)
            {
                make_ids(piece, (Order)order, sample, ids);
                for (uint32_t token = 0; token < TOKENS; ++token)
                    source[token] = row_tag(TABLE + ids[token] * piece);
                fence();
                for (uint32_t token = 0; token < TOKENS; ++token)
                    flex_dma_async_1d((uint64_t)(ARCH_HBM_START_BASE + TABLE + ids[token] * piece),
                                      (uint64_t)(SRC + token * sizeof(uint32_t)), sizeof(uint32_t));
                flex_dma_async_wait_all();
                fence();
            }
}

// Another piece size may have tagged a word inside this row: words on a 256 B
// boundary may hold their own tag instead of the pattern.
static uint32_t check_row(uint32_t target, uint32_t offset, uint32_t piece)
{
    const volatile uint32_t *buffer = (const volatile uint32_t *)target;
    uint32_t errors = buffer[0] != row_tag(offset);
    for (uint32_t i = 1; i < piece / sizeof(uint32_t); ++i)
    {
        const uint32_t at = offset + i * sizeof(uint32_t);
        const uint32_t expected = pattern((at / sizeof(uint32_t)) & PATTERN_MASK);
        errors += buffer[i] != expected && (at % PIECES[0] != 0 || buffer[i] != row_tag(at));
    }
    return errors;
}

static uint32_t measure(uint32_t piece, Order order, uint32_t sample)
{
    uint32_t *ids = (uint32_t *)IDS;
    make_ids(piece, order, sample, ids);
    const uint32_t shift = shift_of(piece);
    // Poison what the check reads: the tag of every row and the whole last row.
    for (uint32_t token = 0; token < TOKENS; ++token)
        *(volatile uint32_t *)(BUFFER + (token << shift)) = ~row_tag(TABLE + (ids[token] << shift));
    volatile uint32_t *last = (volatile uint32_t *)(BUFFER + ((TOKENS - 1) << shift));
    for (uint32_t i = 1; i < piece / sizeof(uint32_t); ++i)
        last[i] = ~pattern(i);
    const uint32_t table = ARCH_HBM_START_BASE + TABLE;
    random_idle(order << 28 | sample << 20 | piece);
    fence();
    const uint64_t start = flex_time_ps();
    if (order == BLOCK)
        flex_dma_async_1d((uint64_t)BUFFER, (uint64_t)(table + (ids[0] << shift)), TOKENS << shift);
    else
        for (uint32_t token = 0; token < TOKENS; ++token)
            flex_dma_async_1d((uint64_t)(BUFFER + (token << shift)),
                              (uint64_t)(table + (ids[token] << shift)), 1u << shift);
    flex_dma_async_wait_all();
    const uint64_t end = flex_time_ps();
    fence();
    uint32_t errors = 0;
    for (uint32_t token = 0; token + 1 < TOKENS; ++token)
        errors += *(volatile uint32_t *)(BUFFER + (token << shift)) !=
                  row_tag(TABLE + (ids[token] << shift));
    errors += check_row((uint32_t)last, TABLE + (ids[TOKENS - 1] << shift), piece);
    printf("[dram-embeddings] cluster=0 channel=0 operation=dma_read order=%s piece=%u tokens=%u "
           "sample=%u rows=%u first_id=%u last_id=%u start_ps=0x%08x%08x end_ps=0x%08x%08x "
           "errors=%u\n",
           ORDER_NAMES[order], piece, TOKENS, sample, TABLE_BYTES / piece, ids[0], ids[TOKENS - 1],
           (uint32_t)(start >> 32), (uint32_t)start, (uint32_t)(end >> 32), (uint32_t)end, errors);
    return errors;
}

int main()
{
    // Returning parks all compute cores and every other cluster in WFI.
    if (flex_get_cluster_id() != 0 || !flex_is_dm_core())
        return 0;

    printf("[dram-embeddings] tokens=%u samples=%u table=%u table_bytes=%u\n", TOKENS, SAMPLES,
           TABLE, TABLE_BYTES);
    initialize_hbm();
    // Let posted setup writes drain before the first timed lookup.
    const uint64_t deadline = flex_time_ps() + 100000000; // 100 us, untimed.
    while (flex_time_ps() < deadline)
    {
    }

    uint32_t errors = 0;
    for (uint32_t piece : PIECES)
        for (uint32_t order = 0; order < ORDERS; ++order)
            for (uint32_t sample = 0; sample < SAMPLES; ++sample)
                errors += measure(piece, (Order)order, sample);
    printf("[dram-embeddings] errors=%u\n", errors);
    printf("SoftHier DRAM embeddings: %s\n", errors ? "FAILED" : "OK");
    flex_eoc(errors != 0);
    return errors != 0;
}
