# SPDX-License-Identifier: Apache-2.0
"""Concurrent grants on the narrow request and wide write planes."""

from pathlib import Path

import gvsoc.runner
import gvsoc.systree
from gvrun.parameter import TargetParameter
from pulp.floonoc.floonoc import FlooNoc
from vp.clock_domain import Clock_domain


class Harness(gvsoc.systree.Component):
    def __init__(self, parent, name, write_first):
        super().__init__(parent, name)
        self.add_sources(['test.cpp'])
        self.add_property('write_first', write_first)


class Chip(gvsoc.systree.Component):
    def __init__(self, parent, name=None):
        super().__init__(parent, name)
        write_first = TargetParameter(
            self,
            name='write_first',
            value=False,
            description='Grant the write before the read',
            cast=bool,
        ).get_value()
        clock = Clock_domain(self, 'clock', frequency=1000000000)
        soc = gvsoc.systree.Component(self, 'soc')
        clock.o_CLOCK(soc.i_CLOCK())
        topology = Path(__file__).parent.parent / 'floonoc_collective/floogen/mesh_4x4_unicast.yml'
        noc = FlooNoc(
            soc,
            'noc',
            narrow_width=8,
            wide_width=64,
            ni_outstanding_reqs=4,
            router_input_queue_size=2,
            network_path=str(topology),
        )
        harness = Harness(soc, 'test', write_first)
        harness.itf_bind('read', noc.i_WIDE_INPUT(noc.id_map['cluster_ni_0_0']), signature='io')
        harness.itf_bind('write', noc.i_WIDE_INPUT(noc.id_map['cluster_ni_1_0']), signature='io')
        sink = gvsoc.systree.SlaveItf(harness, 'input', signature='io')
        destination = noc.id_map['cluster_ni_3_3']
        noc.o_NARROW_MAP(sink, 0x20000000, 0x10000, destination, rm_base=True)
        noc.o_WIDE_BIND(sink, destination)


class Target(gvsoc.runner.Target):
    model = Chip
    name = 'test'
    gapy_description = 'FlooNoC simultaneous read/write target backpressure'
