// Copyright 2026 ETH Zurich and University of Bologna.
// Licensed under the Apache License, Version 2.0, see LICENSE for details.
// SPDX-License-Identifier: Apache-2.0

#include "../dram_common/dma.h"

// Some toolchains implement decimal 64-bit formatting with compressed
// instructions, which this core does not support. Log timestamps in hex.
#define PRINTF_DISABLE_SUPPORT_LONG_LONG
#include "softhier_printf.h"

// This sweep deliberately keeps the topology fixed while changing offered load.
static_assert(ARCH_NUM_CLUSTER_X == 4 && ARCH_NUM_CLUSTER_Y == 4,
              "The multi-cluster sweep requires the default 4x4 mesh");
static constexpr uint32_t CONTROL_OFFSET = 0x1000 + 2 * MAX_BYTES;
static constexpr uint32_t BOOT_READY = 0xffffffffu;
static constexpr uint64_t START_DELAY_PS = 10000000; // Allow 10 us for the release fan-out.

enum class Scenario
{
    Local,
    Remote,
    Shared
};
static const char *scenario_name(Scenario scenario)
{
    switch (scenario)
    {
    case Scenario::Local:
        return "local";
    case Scenario::Remote:
        return "remote";
    case Scenario::Shared:
        return "shared";
    }
    return "unknown";
}

static uint32_t target_channel(Scenario scenario, uint32_t cluster)
{
    if (scenario == Scenario::Local)
        return cluster;
    if (scenario == Scenario::Shared)
        return ARCH_NUM_CLUSTER - 1;
    // A half-mesh shift is a permutation: every destination is distinct and
    // every source is four Manhattan hops from its destination on a 4x4 mesh.
    const uint32_t x = (cluster % ARCH_NUM_CLUSTER_X + ARCH_NUM_CLUSTER_X / 2) % ARCH_NUM_CLUSTER_X;
    const uint32_t y = (cluster / ARCH_NUM_CLUSTER_X + ARCH_NUM_CLUSTER_Y / 2) % ARCH_NUM_CLUSTER_Y;
    return y * ARCH_NUM_CLUSTER_X + x;
}

static uint32_t mesh_hops(uint32_t cluster, uint32_t channel)
{
    const int dx = (int)(cluster % ARCH_NUM_CLUSTER_X) - (int)(channel % ARCH_NUM_CLUSTER_X);
    const int dy = (int)(cluster / ARCH_NUM_CLUSTER_X) - (int)(channel / ARCH_NUM_CLUSTER_X);
    return (dx < 0 ? -dx : dx) + (dy < 0 ? -dy : dy);
}

// Each worker owns its timestamps and errors. Cluster 0 owns the release
// fields, and reads results only after every worker has finished the case.
struct Mailbox
{
    uint32_t boot;
    uint32_t release;
    uint64_t deadline_ps;
    uint64_t start_ps;
    uint64_t end_ps;
    uint32_t errors;
};
static constexpr uint32_t READY_OFFSET = CONTROL_OFFSET + sizeof(Mailbox);
static_assert(READY_OFFSET + ARCH_NUM_CLUSTER * sizeof(uint32_t) < ARCH_CLUSTER_TCDM_SIZE - 16,
              "Benchmark control words must fit below the runtime barrier words");
static_assert((ARCH_NUM_CLUSTER + 1) * MAX_BYTES + RING_BYTES <= ARCH_HBM_NODE_ADDR_SPACE,
              "Each channel needs a separate DMA slot for every cluster and a reserved tail");

static volatile Mailbox *mailbox(uint32_t cluster)
{
    return (volatile Mailbox *)remote_cid(cluster, CONTROL_OFFSET);
}

// Only DM cores participate: the runtime's global barrier also waits for the
// compute cores and assumes all clusters are active, so it cannot be used here.
// Workers poll their local TCDM. Arrival flags live in cluster 0's TCDM, keeping
// the coordinator's polling off the NoC while DMA transfers are in flight.
static void synchronize(uint32_t cluster, uint32_t &epoch, bool scheduled = false)
{
    volatile Mailbox *self = (volatile Mailbox *)local(CONTROL_OFFSET);
    volatile uint32_t *ready = (volatile uint32_t *)remote_cid(0, READY_OFFSET);
    asm volatile("fence rw, rw" ::: "memory");
    ready[cluster] = ++epoch;
    if (cluster == 0)
    {
        ready = (volatile uint32_t *)local(READY_OFFSET);
        for (uint32_t i = 0; i < ARCH_NUM_CLUSTER; ++i)
            while (ready[i] != epoch)
            {
            }
        const uint64_t deadline = scheduled ? flex_time_ps() + START_DELAY_PS : 0;
        for (uint32_t i = 0; i < ARCH_NUM_CLUSTER; ++i)
        {
            volatile Mailbox *peer = mailbox(i);
            peer->deadline_ps = deadline;
            asm volatile("fence rw, rw" ::: "memory");
            peer->release = epoch;
        }
    }
    while (self->release != epoch)
    {
    }
    asm volatile("fence rw, rw" ::: "memory");
}

static uint32_t data_offset(uint32_t cluster)
{
    // Reserve the channel tail and give each cluster a separate slot so
    // concurrent writers never overwrite another cluster's data.
    return ARCH_HBM_NODE_ADDR_SPACE - (cluster + 2) * MAX_BYTES - RING_BYTES;
}

static uint32_t run_multi(uint32_t cluster)
{
    volatile Mailbox *self = (volatile Mailbox *)local(CONTROL_OFFSET);
    volatile uint32_t *src = (volatile uint32_t *)SRC;
    uint32_t epoch = 0;
    uint32_t errors = 0;
    const uint32_t counts[] = {2, 4, 8, 16};
    const uint32_t sizes[] = {4096, MAX_BYTES};
    const Scenario scenarios[] = {Scenario::Local, Scenario::Remote, Scenario::Shared};

    fill_source(cluster);

    for (uint32_t active : counts)
        for (Scenario scenario : scenarios)
            for (uint32_t bytes : sizes)
                for (uint32_t write = 0; write < 2; ++write)
                {
                    const uint32_t channel = target_channel(scenario, cluster);
                    const uint32_t address = ARCH_HBM_START_BASE +
                                             channel * ARCH_HBM_NODE_ADDR_SPACE +
                                             data_offset(cluster);
                    self->errors = 0;
                    if (cluster < active)
                    {
                        src[0] = pattern(0, cluster);
                        self->errors += prepare(address, bytes, cluster);
                    }

                    // All workers finish preparation before receiving the same
                    // future deadline. Actual start skew is retained in the log.
                    synchronize(cluster, epoch, true);
                    if (cluster < active)
                    {
                        const uint64_t deadline = self->deadline_ps;
                        while (flex_time_ps() < deadline)
                        {
                        }
                        self->start_ps = flex_time_ps();
                        self->errors += transfer(address, bytes, write, cluster);
                        self->end_ps = flex_time_ps();
                        self->errors += check(DST, bytes, src[0], cluster);
                    }
                    synchronize(cluster, epoch);
                    if (cluster == 0)
                    {
                        for (uint32_t i = 0; i < active; ++i)
                        {
                            volatile Mailbox *result = mailbox(i);
                            const uint32_t destination = target_channel(scenario, i);
                            errors += result->errors;
                            printf(
                                "[dram-multi] scenario=%s active_clusters=%u cluster=%u channel=%u "
                                "mesh_hops=%u operation=%s bytes=%u repetitions=%u "
                                "hbm_offset=%u start_ps=0x%08x%08x end_ps=0x%08x%08x errors=%u\n",
                                scenario_name(scenario), active, i, destination,
                                mesh_hops(i, destination),
                                write ? "dma_write_readback" : "dma_read", bytes, REPEATS,
                                data_offset(i), (uint32_t)(result->start_ps >> 32),
                                (uint32_t)result->start_ps, (uint32_t)(result->end_ps >> 32),
                                (uint32_t)result->end_ps, result->errors);
                        }
                    }
                    // Keep the result slots stable until printing is complete;
                    // no worker may prepare the next case during this report.
                    synchronize(cluster, epoch);
                }
    return errors;
}

int main()
{
    if (!flex_is_dm_core())
        return 0;
    const uint32_t cluster = flex_get_cluster_id();
    volatile Mailbox *self = (volatile Mailbox *)local(CONTROL_OFFSET);
    self->release = 0;
    self->errors = 0;
    asm volatile("fence rw, rw" ::: "memory");
    self->boot = BOOT_READY;

    uint32_t errors = 0;
    if (cluster == 0)
    {
        // Initialize arrival flags only after every local mailbox exists,
        // then release the workers together into the sweep.
        for (uint32_t i = 0; i < ARCH_NUM_CLUSTER; ++i)
            while (mailbox(i)->boot != BOOT_READY)
            {
            }
        volatile uint32_t *ready = (volatile uint32_t *)local(READY_OFFSET);
        for (uint32_t i = 0; i < ARCH_NUM_CLUSTER; ++i)
            ready[i] = 0;
        for (uint32_t i = 0; i < ARCH_NUM_CLUSTER; ++i)
            mailbox(i)->release = BOOT_READY;
    }
    while (self->release != BOOT_READY)
    {
    }
    errors += run_multi(cluster);
    if (cluster == 0)
    {
        printf("[dram-multi] errors=%u\n", errors);
        printf("SoftHier DRAM multi: %s\n", errors == 0 ? "OK" : "FAILED");
        flex_eoc(errors != 0);
    }
    return 0;
}
