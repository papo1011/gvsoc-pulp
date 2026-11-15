// Copyright 2026 ETH Zurich and University of Bologna.
// SPDX-License-Identifier: Apache-2.0

#include <array>
#include <cstring>
#include <vp/vp.hpp>
#include <vp/itf/io.hpp>

// Two requests per plane are essential: responding to the denied request is
// insufficient unless the right upstream router wakes and sends its next one.
class BackpressureTest : public vp::Component
{
  public:
    BackpressureTest(vp::ComponentConf &config)
        : vp::Component(config), start_event(this, start), grant_event(this, grant),
          watchdog_event(this, watchdog)
    {
        traces.new_trace("trace", &trace, vp::DEBUG);
        input.set_req_meth(request);
        new_slave_port("input", &input);
        for (auto *port : {&read, &write})
        {
            port->set_resp_meth(response);
            port->set_grant_meth(granted);
        }
        new_master_port("read", &read);
        new_master_port("write", &write);
        write_first = get_js_config()->get("write_first")->get_bool();
    }

  private:
    void reset(bool active) override
    {
        if (!active)
        {
            start_event.enqueue(10);
            watchdog_event.enqueue(1000);
        }
    }

    static void start(vp::Block *block, vp::ClockEvent *)
    {
        auto *self = static_cast<BackpressureTest *>(block);
        for (int i = 0; i < 4; ++i)
        {
            vp::IoReq &req = self->requests[i];
            req.init();
            req.set_addr(0x20000000 + i * 64);
            req.set_size(64);
            req.set_data(self->data[i].data());
            req.set_is_write(i >= 2);
            auto status = (i >= 2 ? self->write : self->read).req(&req);
            if (status == vp::IO_REQ_OK)
                response(self, &req);
            else if (status != vp::IO_REQ_PENDING && status != vp::IO_REQ_DENIED)
                self->trace.fatal("Unexpected initiator status\n");
        }
    }

    static vp::IoReqStatus request(vp::Block *block, vp::IoReq *req)
    {
        auto *self = static_cast<BackpressureTest *>(block);
        int plane = req->get_is_write();
        if (!self->denied[plane])
        {
            self->denied[plane] = true;
            self->pending[plane] = req;
            // Hold both planes until both have been denied, then exercise
            // grants in either order independently of request arrival order.
            if (self->pending[0] && self->pending[1])
                self->grant_event.enqueue(10);
            return vp::IO_REQ_DENIED;
        }
        if (!req->get_is_write())
            std::memset(req->get_data(), 0x11, req->get_size());
        return vp::IO_REQ_OK;
    }

    static void grant(vp::Block *block, vp::ClockEvent *)
    {
        auto *self = static_cast<BackpressureTest *>(block);
        int plane = self->write_first ^ (self->grant_count != 0);
        vp::IoReq *req = self->pending[plane];
        if (!req->get_is_write())
            std::memset(req->get_data(), 0x11, req->get_size());
        req->get_resp_port()->grant(req);
        req->get_resp_port()->resp(req);
        if (++self->grant_count == 1)
            self->grant_event.enqueue(10);
    }

    static void granted(vp::Block *, vp::IoReq *) {}

    static void response(vp::Block *block, vp::IoReq *req)
    {
        auto *self = static_cast<BackpressureTest *>(block);
        size_t index = req - self->requests.data();
        if (index >= self->requests.size() || self->completed[index])
            self->trace.fatal("Unexpected or duplicate response\n");
        self->completed[index] = true;
        if (++self->response_count == 4)
        {
            if (self->grant_count != 2)
                self->trace.fatal("The test did not exercise both denied planes\n");
            self->time.get_engine()->quit(0);
        }
    }

    static void watchdog(vp::Block *block, vp::ClockEvent *)
    {
        static_cast<BackpressureTest *>(block)->trace.fatal("A denied plane never resumed\n");
    }

    vp::Trace trace;
    vp::IoMaster read, write;
    vp::IoSlave input;
    vp::ClockEvent start_event, grant_event, watchdog_event;
    std::array<vp::IoReq, 4> requests;
    std::array<std::array<uint8_t, 64>, 4> data{};
    std::array<bool, 4> completed{};
    std::array<bool, 2> denied{};
    std::array<vp::IoReq *, 2> pending{};
    int response_count = 0;
    int grant_count = 0;
    bool write_first;
};

extern "C" vp::Component *gv_new(vp::ComponentConf &config)
{
    return new BackpressureTest(config);
}
