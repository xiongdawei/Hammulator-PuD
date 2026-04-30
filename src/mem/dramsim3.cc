/*
 * Copyright (c) 2013 ARM Limited
 * All rights reserved
 *
 * The license below extends only to copyright in the software and shall
 * not be construed as granting a license to any other intellectual
 * property including but not limited to intellectual property relating
 * to a hardware implementation of the functionality of the software
 * licensed hereunder.  You may use the software subject to the license
 * terms below provided that you ensure that this notice is replicated
 * unmodified and in its entirety in all distributions of the software,
 * modified or unmodified, in source code or in binary form.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are
 * met: redistributions of source code must retain the above copyright
 * notice, this list of conditions and the following disclaimer;
 * redistributions in binary form must reproduce the above copyright
 * notice, this list of conditions and the following disclaimer in the
 * documentation and/or other materials provided with the distribution;
 * neither the name of the copyright holders nor the names of its
 * contributors may be used to endorse or promote products derived from
 * this software without specific prior written permission.
 *
 * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
 * "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
 * LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
 * A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
 * OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
 * SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
 * LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
 * DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
 * THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
 * (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
 * OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
 */

#include "mem/dramsim3.hh"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <random>
#include <string>
#include <unordered_map>

#include "base/callback.hh"
#include "base/trace.hh"
#include "debug/DRAMsim3.hh"
#include "debug/Drain.hh"
#include "debug/PuDHammer.hh"
#include "sim/system.hh"

namespace gem5
{

namespace memory
{

namespace
{

constexpr int kTraceDataRows = 300;
constexpr const char *kSpecialTraceRows[] = {
    "C0", "C1",
    "T0", "T1", "T2", "T3",
    "B_DCC0", "B_DCC0N", "B_DCC1", "B_DCC1N",
    "R0", "R1", "R2"
};

std::string
traceRowLabel(int traceRow)
{
    if (traceRow < kTraceDataRows) {
        return std::to_string(traceRow);
    }

    const int specialIdx = traceRow - kTraceDataRows;
    if (specialIdx >= 0 &&
        specialIdx < static_cast<int>(
            sizeof(kSpecialTraceRows) / sizeof(kSpecialTraceRows[0]))) {
        return kSpecialTraceRows[specialIdx];
    }

    return std::to_string(traceRow);
}

bool
getTraceRow(dramsim3::Config *config, int channel, int rank, int bankgroup,
            int bank, int row, int &traceRow)
{
    const uint64_t rowBase = config->ReverseAddressMapping(
        channel, rank, bankgroup, bank, row, 0);
    if (global_rowhammer_trace_row_bases.find(rowBase) ==
        global_rowhammer_trace_row_bases.end()) {
        return false;
    }

    if (global_rowhammer_trace_base_addr == 0) {
        return false;
    }

    const auto base = config->AddressMapping(global_rowhammer_trace_base_addr);
    traceRow = row - base.row;
    return true;
}

void
logAggressorRow(dramsim3::Config *config, const char *accessType,
                const char *sourceTag, const char *modeTag, uint64_t addr,
                int channel, int rank, int bankgroup, int bank, int row)
{
    const uint64_t rowBase = config->ReverseAddressMapping(
        channel, rank, bankgroup, bank, row, 0);
    int traceRow = 0;
    if (!getTraceRow(config, channel, rank, bankgroup, bank, row, traceRow)) {
        return;
    }
    const std::string rowLabel = traceRowLabel(traceRow);
    DPRINTF(PuDHammer,
            "Row %s, physical address 0x%016lx\n",
            rowLabel.c_str(), rowBase);
}

} // namespace

DRAMsim3::DRAMsim3(const Params &p) :
    AbstractMemory(p),
    port(name() + ".port", *this),
    read_cb(std::bind(&DRAMsim3::readComplete,
                      this, 0, std::placeholders::_1,
                      std::placeholders::_2)),
    write_cb(std::bind(&DRAMsim3::writeComplete,
                       this, 0, std::placeholders::_1,
                       std::placeholders::_2)),
    refresh_cb(std::bind(&DRAMsim3::refreshComplete,
                       this, 0, std::placeholders::_1,
                       std::placeholders::_2, std::placeholders::_3)),
    wrapper(p.configFile, p.filePath, read_cb, write_cb, refresh_cb),
    retryReq(false), retryResp(false), startTick(0),
    nbrOutstandingReads(0), nbrOutstandingWrites(0),
    sendResponseEvent([this]{ sendResponse(); }, name()),
    tickEvent([this]{ tick(); }, name())
{
    DPRINTF(DRAMsim3,
            "Instantiated DRAMsim3 with clock %d ns and queue size %d\n",
            wrapper.clockPeriod(), wrapper.queueSize());

    // Get dram mapping
    config = wrapper.GetConfig();

    para_rng = std::minstd_rand(0);

    rng.seed(config->spatial_variation_seed);
    // Register a callback to compensate for the destructor not
    // being called. The callback prints the DRAMsim3 stats.
    registerExitCallback([this]() {
        wrapper.printStats();
        printMitigationStats();
    });
}

void
DRAMsim3::init()
{
    AbstractMemory::init();

    if (!port.isConnected()) {
        fatal("DRAMsim3 %s is unconnected!\n", name());
    } else {
        port.sendRangeChange();
    }

    if (system()->cacheLineSize() != wrapper.burstSize())
        fatal("DRAMsim3 burst size %d does not match cache line size %d\n",
              wrapper.burstSize(), system()->cacheLineSize());
}

void
DRAMsim3::startup()
{
    startTick = curTick();

    // kick off the clock ticks
    schedule(tickEvent, clockEdge());
}

void
DRAMsim3::resetStats() {
    wrapper.resetStats();
}

void
DRAMsim3::sendResponse()
{
    assert(!retryResp);
    assert(!responseQueue.empty());

    DPRINTF(DRAMsim3, "Attempting to send response\n");

    bool success = port.sendTimingResp(responseQueue.front());
    if (success) {
        responseQueue.pop_front();

        DPRINTF(DRAMsim3, "Have %d read, %d write, %d responses outstanding\n",
                nbrOutstandingReads, nbrOutstandingWrites,
                responseQueue.size());

        if (!responseQueue.empty() && !sendResponseEvent.scheduled())
            schedule(sendResponseEvent, curTick());

        if (nbrOutstanding() == 0)
            signalDrainDone();
    } else {
        retryResp = true;

        DPRINTF(DRAMsim3, "Waiting for response retry\n");

        assert(!sendResponseEvent.scheduled());
    }
}

unsigned int
DRAMsim3::nbrOutstanding() const
{
    return nbrOutstandingReads + nbrOutstandingWrites + responseQueue.size();
}

void
DRAMsim3::tick()
{
    // Only tick when it's timing mode
    if (system()->isTimingMode()) {
        wrapper.tick();

        // is the connected port waiting for a retry, if so check the
        // state and send a retry if conditions have changed
        if (retryReq && nbrOutstanding() < wrapper.queueSize()) {
            retryReq = false;
            port.sendRetryReq();
        }
    } else {
        // erase both hammer and flipped state since we run in a non-timing
        // CPU that does not execute the callbacks
        // therefore we don't know if the memory was written in between and
        // flips should therefore be possible again
        hammer_count.clear();
        flipped.clear();
    }

    schedule(tickEvent,
        curTick() + wrapper.clockPeriod() * sim_clock::as_int::ns);
}

Tick
DRAMsim3::recvAtomic(PacketPtr pkt)
{
    access(pkt);

    // 50 ns is just an arbitrary value at this point
    return pkt->cacheResponding() ? 0 : 50000;
}

void
DRAMsim3::recvFunctional(PacketPtr pkt)
{
    pkt->pushLabel(name());

    functionalAccess(pkt);

    // potentially update the packets in our response queue as well
    for (auto i = responseQueue.begin(); i != responseQueue.end(); ++i)
        pkt->trySatisfyFunctional(*i);

    pkt->popLabel();
}

bool
DRAMsim3::recvTimingReq(PacketPtr pkt)
{
    // if a cache is responding, sink the packet without further action
    if (pkt->cacheResponding()) {
        pendingDelete.reset(pkt);
        return true;
    }

    // we should not get a new request after committing to retry the
    // current one, but unfortunately the CPU violates this rule, so
    // simply ignore it for now
    if (retryReq)
        return false;

    // if we cannot accept we need to send a retry once progress can
    // be made
    bool can_accept = nbrOutstanding() < wrapper.queueSize();

    // keep track of the transaction
    if (pkt->isRead()) {
        if (can_accept) {
            outstandingReads[pkt->getAddr()].push(pkt);

            // we count a transaction as outstanding until it has left the
            // queue in the controller, and the response has been sent
            // back, note that this will differ for reads and writes
            ++nbrOutstandingReads;
        }
    } else if (pkt->isWrite()) {
        if (can_accept) {
            outstandingWrites[pkt->getAddr()].push(pkt);

            ++nbrOutstandingWrites;

            // perform the access for writes
            accessAndRespond(pkt);
        }
    } else {
        // keep it simple and just respond if necessary
        accessAndRespond(pkt);
        return true;
    }

    if (can_accept) {
        // we should never have a situation when we think there is space,
        // and there isn't
        assert(wrapper.canAccept(pkt->getAddr(), pkt->isWrite()));

        DPRINTF(DRAMsim3, "Enqueueing address %lld\n", pkt->getAddr());

        // @todo what about the granularity here, implicit assumption that
        // a transaction matches the burst size of the memory (which we
        // cannot determine without parsing the ini file ourselves)
        wrapper.enqueue(pkt->getAddr(), pkt->isWrite());

        return true;
    } else {
        retryReq = true;
        return false;
    }
}

void
DRAMsim3::recvRespRetry()
{
    DPRINTF(DRAMsim3, "Retrying\n");

    assert(retryResp);
    retryResp = false;
    sendResponse();
}

void
DRAMsim3::accessAndRespond(PacketPtr pkt)
{
    DPRINTF(DRAMsim3, "Access for address %lld\n", pkt->getAddr());

    bool needsResponse = pkt->needsResponse();

    // do the actual memory access which also turns the packet into a
    // response
    access(pkt);

    // turn packet around to go back to requestor if response expected
    if (needsResponse) {
        // access already turned the packet into a response
        assert(pkt->isResponse());
        // Here we pay for xbar additional delay and to process the payload
        // of the packet.
        Tick time = curTick() + pkt->headerDelay + pkt->payloadDelay;
        // Reset the timings of the packet
        pkt->headerDelay = pkt->payloadDelay = 0;

        DPRINTF(DRAMsim3, "Queuing response for address %lld\n",
                pkt->getAddr());

        // queue it to be sent back
        responseQueue.push_back(pkt);

        // if we are not already waiting for a retry, or are scheduled
        // to send a response, schedule an event
        if (!retryResp && !sendResponseEvent.scheduled())
            schedule(sendResponseEvent, time);
    } else {
        // queue the packet for deletion
        pendingDelete.reset(pkt);
    }
}

inline double DRAMsim3::gen_proba(uint64_t addr) {
    auto pp = probabilities.find(addr);
    if (pp == probabilities.end()) {
        std::ranlux48_base gen(addr);
        probabilities[addr] = std::generate_canonical<double, 10>(gen);
        return probabilities[addr];
    } else {
        return pp->second;
    }
}

uint64_t
DRAMsim3::bankKey(int channel, int rank, int bankgroup, int bank) const
{
    return (static_cast<uint64_t>(channel & 0xffff) << 48) |
           (static_cast<uint64_t>(rank & 0xffff) << 32) |
           (static_cast<uint64_t>(bankgroup & 0xffff) << 16) |
           static_cast<uint64_t>(bank & 0xffff);
}

uint64_t
DRAMsim3::subarrayKey(int channel, int rank, int bankgroup, int bank,
                      int subarray) const
{
    return (bankKey(channel, rank, bankgroup, bank) << 16) |
           static_cast<uint64_t>(subarray & 0xffff);
}

int
DRAMsim3::saltRowsPerRef() const
{
    return std::max(1, config->rows / 8192);
}

int
DRAMsim3::subarrayForRow(int row) const
{
    if (config->salt_row_striping) {
        return row % config->salt_subarrays;
    }
    return row / config->salt_rows_per_subarray;
}

int
DRAMsim3::rowIndexInSubarray(int row) const
{
    if (config->salt_row_striping) {
        return row / config->salt_subarrays;
    }
    return row % config->salt_rows_per_subarray;
}

int
DRAMsim3::bankRowFromSubarray(int subarray, int row_index) const
{
    if (config->salt_row_striping) {
        return row_index * config->salt_subarrays + subarray;
    }
    return subarray * config->salt_rows_per_subarray + row_index;
}

DRAMsim3::SaltSubarrayState&
DRAMsim3::getSaltSubarrayState(int channel, int rank, int bankgroup, int bank,
                               int subarray)
{
    return saltSubarrayState[subarrayKey(channel, rank, bankgroup, bank,
                                         subarray)];
}

DRAMsim3::SaltBankState&
DRAMsim3::getSaltBankState(int channel, int rank, int bankgroup, int bank)
{
    return saltBankState[bankKey(channel, rank, bankgroup, bank)];
}

void
DRAMsim3::clearSaltRow(int channel, int rank, int bankgroup, int bank, int row)
{
    uint64_t row_base = config->ReverseAddressMapping(
        channel, rank, bankgroup, bank, row, 0);
    hammer_count.erase(row_base);
    saltRowsRefreshed++;
}

void
DRAMsim3::clearSaltRowsInSubarray(int channel, int rank, int bankgroup,
                                  int bank, int subarray, int count,
                                  bool wrap_rows)
{
    auto &state =
        getSaltSubarrayState(channel, rank, bankgroup, bank, subarray);
    uint32_t &pointer = config->salt_coord_refresh ?
        state.nextRow : state.nextBundleRow;

    int rows_to_clear = count;
    while (rows_to_clear > 0) {
        if (pointer >= static_cast<uint32_t>(config->salt_rows_per_subarray)) {
            pointer = 0;
        }

        int remaining = config->salt_rows_per_subarray - pointer;
        int chunk = wrap_rows ? std::min(rows_to_clear, remaining)
                              : std::min(rows_to_clear, remaining);
        for (int i = 0; i < chunk; ++i) {
            int row = bankRowFromSubarray(subarray, pointer + i);
            clearSaltRow(channel, rank, bankgroup, bank, row);
        }

        pointer += chunk;
        rows_to_clear -= chunk;
        if (!wrap_rows) {
            break;
        }
        if (pointer >= static_cast<uint32_t>(config->salt_rows_per_subarray)) {
            pointer = 0;
        }
    }

    if (!wrap_rows && pointer >=
            static_cast<uint32_t>(config->salt_rows_per_subarray)) {
        pointer = 0;
    }
}

void
DRAMsim3::performSaltRefreshForBank(int channel, int rank, int bankgroup,
                                    int bank)
{
    auto &bank_state = getSaltBankState(channel, rank, bankgroup, bank);
    const int rows_per_ref = saltRowsPerRef();

    for (int i = 0; i < rows_per_ref; ++i) {
        int subarray =
            (bank_state.nextRefSubarray + i) % config->salt_subarrays;
        auto &state =
            getSaltSubarrayState(channel, rank, bankgroup, bank, subarray);
        int row = bankRowFromSubarray(subarray, state.nextRow);
        clearSaltRow(channel, rank, bankgroup, bank, row);
        state.nextRow = (state.nextRow + 1) % config->salt_rows_per_subarray;

        state.refAccumulator += config->salt_apm;
        int reduction = state.refAccumulator / config->salt_bundle_rows;
        state.refAccumulator %= config->salt_bundle_rows;
        state.actCtr = std::max<int64_t>(0, state.actCtr - reduction);
        saltRefMitigations++;
    }

    bank_state.nextRefSubarray =
        (bank_state.nextRefSubarray + rows_per_ref) % config->salt_subarrays;
}

void
DRAMsim3::performSaltAboForBank(int channel, int rank, int bankgroup, int bank)
{
    int selected_subarray = -1;
    int64_t selected_ctr = 0;
    for (int subarray = 0; subarray < config->salt_subarrays; ++subarray) {
        auto &state = getSaltSubarrayState(channel, rank, bankgroup, bank,
                                           subarray);
        if (state.actCtr > selected_ctr) {
            selected_ctr = state.actCtr;
            selected_subarray = subarray;
        }
    }

    if (selected_subarray < 0 || selected_ctr <= 0) {
        return;
    }

    clearSaltRowsInSubarray(channel, rank, bankgroup, bank, selected_subarray,
                            config->salt_bundle_rows,
                            config->salt_coord_refresh);
    auto &state = getSaltSubarrayState(channel, rank, bankgroup, bank,
                                       selected_subarray);
    state.actCtr = std::max<int64_t>(0, state.actCtr - config->salt_apm);
    saltAboMitigations++;
}

void
DRAMsim3::performSaltAbo()
{
    saltAbos++;
    for (int channel = 0; channel < config->channels; ++channel) {
        for (int rank = 0; rank < config->ranks; ++rank) {
            for (int bankgroup = 0;
                 bankgroup < config->bankgroups; ++bankgroup) {
                for (int bank = 0; bank < config->banks_per_group; ++bank) {
                    performSaltAboForBank(channel, rank, bankgroup, bank);
                }
            }
        }
    }
}

bool
DRAMsim3::noteActivation(int channel, int rank, int bankgroup, int bank,
                         int row, bool bufferhit)
{
    uint64_t row_base = config->ReverseAddressMapping(channel, rank, bankgroup,
                                                      bank, row, 0);
    hammer_count.erase(row_base);

    if (bufferhit || !config->salt_enabled) {
        return false;
    }

    auto &state = getSaltSubarrayState(channel, rank, bankgroup, bank,
                                       subarrayForRow(row));
    state.actCtr++;
    return state.actCtr > config->salt_ath;
}

bool
DRAMsim3::notePracActivation(int channel, int rank, int bankgroup, int bank,
                             int row, bool bufferhit)
{
    if (bufferhit || !config->prac_abo_enabled) {
        return false;
    }
    uint64_t row_base = config->ReverseAddressMapping(channel, rank,
                                                      bankgroup, bank, row, 0);
    auto &count = prac_count[row_base];
    count++;
    return count >= config->prac_threshold;
}

void
DRAMsim3::PARA(int channel, int rank, int bankgroup, int bank, int row)
{
    para_calls++;
    for (int dist = -5; dist <= 5; dist++) {
        if (dist == 0 || row + dist < 0 || row + dist >= config->rows) {
            continue;
        }
        double rand = std::generate_canonical<double, 10>(para_rng);
        if (rand > config->para_proba) {
            continue;
        }
        uint64_t target = config->ReverseAddressMapping(channel, rank,
                                                        bankgroup, bank,
                                                        row + dist, 0);
        if (hammer_count.find(target) != hammer_count.end()) {
            para_preventions++;
        }
        hammer_count.erase(target);
    }
}

void
DRAMsim3::PRAC_ABO(int channel, int rank, int bankgroup, int bank, int row)
{
    prac_abos++;
    uint64_t row_base = config->ReverseAddressMapping(channel, rank,
                                                      bankgroup, bank, row, 0);
    prac_count.erase(row_base);

    for (int dist = -config->prac_blast_radius;
         dist <= config->prac_blast_radius; dist++) {
        if (dist == 0 || row + dist < 0 || row + dist >= config->rows) {
            continue;
        }

        int refreshed_row = row + dist;
        uint64_t target = config->ReverseAddressMapping(channel, rank,
                                                        bankgroup, bank,
                                                        refreshed_row, 0);

        if (hammer_count.find(target) != hammer_count.end()) {
            prac_preventions++;
        }
        hammer_count.erase(target);

        for (int sec_dist = -5; sec_dist <= 5; sec_dist++) {
            int sec_row = refreshed_row + sec_dist;

            if (sec_dist == 0 || sec_row < 0 || sec_row >= config->rows) {
                continue;
            }

            double add = 0;
            switch (abs(sec_dist)) {
                case 5: add = config->inc_dist_5; break;
                case 4: add = config->inc_dist_4; break;
                case 3: add = config->inc_dist_3; break;
                case 2: add = config->inc_dist_2; break;
                case 1: add = config->inc_dist_1; break;
            }

            uint64_t sec_target = config->ReverseAddressMapping(channel, rank,
                                                                bankgroup,
                                                                bank, sec_row,
                                                                0);
            hammer_count[sec_target] += add;
        }
    }
    DPRINTF(PuDHammer,
            "PRAC+ABO refreshed row %d in ch %d rank %d bg %d bank %d\n",
            row, channel, rank, bankgroup, bank);
}

void
DRAMsim3::TRR(int channel, int rank, int bankgroup, int bank, int row)
{
    trr_calls++;
    for (int dist = -5; dist <= 5; dist++) {
        if (dist == 0 || row + dist < 0 || row + dist >= config->rows) {
            continue;
        }
        uint64_t target = config->ReverseAddressMapping(channel, rank, bankgroup, bank, row+dist, 0);
        if(trr_count.find(target) == trr_count.end()) {
            trr_count[target] = 1;
        } else {
            trr_count[target]++;
        }

        if (trr_count[target] > config->trr_threshold) {
            if (hammer_count.find(target) != hammer_count.end()) {
                trr_preventions++;
            }
            hammer_count.erase(target);
        }
    }
}

int DRAMsim3::get_row_threshold(uint64_t row_base_addr) {
    if (!config->spatial_variation_enabled) {
        return config->hc_first;
    }

    auto it = per_row_threshold.find(row_base_addr);
    if (it != per_row_threshold.end()) {
        return it->second;
    }

    std::mt19937_64 row_rng(
        static_cast<uint64_t>(config->spatial_variation_seed) ^
        (row_base_addr * 0x9E3779B97F4A7C15ULL));
    const double u = std::generate_canonical<double, 53>(row_rng);

    auto interp_log = [](double x, double x0, double x1,
                         double y0, double y1) {
        if (x1 <= x0 || y0 <= 0.0 || y1 <= 0.0) {
            return y1;
        }
        const double t = (x - x0) / (x1 - x0);
        return std::exp(std::log(y0) + t * (std::log(y1) - std::log(y0)));
    };

    double multiplier = 1.0;
    if (u < 0.01) {
        multiplier = interp_log(
            u, 0.00, 0.01, 1.0, config->spatial_q01_multiplier);
    } else if (u < 0.05) {
        multiplier = interp_log(
            u, 0.01, 0.05,
            config->spatial_q01_multiplier,
            config->spatial_q05_multiplier);
    } else if (u < 0.10) {
        multiplier = interp_log(
            u, 0.05, 0.10,
            config->spatial_q05_multiplier,
            config->spatial_q10_multiplier);
    } else {
        multiplier = interp_log(
            u, 0.10, 1.00,
            config->spatial_q10_multiplier,
            config->spatial_q100_multiplier);
    }

    int new_threshold =
        std::lround(static_cast<double>(config->hc_first) * multiplier);
    if (new_threshold < 100) new_threshold = 100;

    per_row_threshold[row_base_addr] = new_threshold;
    return new_threshold;
}

void DRAMsim3::readComplete(unsigned id, uint64_t addr, bool bufferhit)
{
    DPRINTF(DRAMsim3, "Read to address %lld complete\n", addr);

    current_attack_mode =
        static_cast<AttackMode>(global_rowhammer_attack_mode);

    // if (current_attack_mode != NORMAL) {
    //    DPRINTF(PuDHammer, "Read to address %lld complete\n", addr);
    // }


    if (current_attack_mode != last_recorded_mode) {
        switch (current_attack_mode) {
            case NORMAL:
                DPRINTF(PuDHammer, ">>> Logic Switch: NORMAL\n");
                break;
            case SiMRA:
                DPRINTF(PuDHammer, ">>> Logic Switch: SiMRA\n");
                break;
            case CoMRA:
                DPRINTF(PuDHammer, ">>> Logic Switch: CoMRA\n");
                break;
            case RESET_BYPASS:
                DPRINTF(PuDHammer, ">>> Logic Switch: RESET_BYPASS\n");
                break;
            default:
                DPRINTF(PuDHammer, ">>> Warning: Unknown Mode %lu\n",
                        global_rowhammer_attack_mode);
        }
        last_recorded_mode = current_attack_mode;
    }

    auto a = config->AddressMapping(addr);
    int channel = a.channel;
    int rank = a.rank;
    int bankgroup = a.bankgroup;
    int bank = a.bank;
    int row = a.row;
    int aggressorTraceRow = 0;
    const bool isTraceAggressor =
        getTraceRow(config, channel, rank, bankgroup, bank, row,
                    aggressorTraceRow);
    const char *sourceTag = "system";
    const char *modeTag = "NORMAL";
    switch (current_attack_mode) {
      case SiMRA:
        modeTag = "SiMRA";
        if (isTraceAggressor) {
            sourceTag = "trace";
        }
        break;
      case CoMRA:
        modeTag = "CoMRA";
        if (isTraceAggressor) {
            sourceTag = "trace";
        }
        break;
      case RESET_BYPASS:
        sourceTag = "reset";
        modeTag = "RESET_BYPASS";
        break;
      case NORMAL:
      default:
        break;
    }

    if (!bufferhit && isTraceAggressor &&
        (current_attack_mode == SiMRA || current_attack_mode == CoMRA)) {
        logAggressorRow(config, "Read", sourceTag, modeTag, addr, channel,
                        rank, bankgroup, bank, row);
    }

    // int bank_id = (channel << 12) | (rank << 8 ) | (bankgroup << 4) | bank;

    // uint64_t current_tick = curTick();

    // get the outstanding reads for the address in question
    auto p = outstandingReads.find(addr);
    assert(p != outstandingReads.end());

    // first in first out, which is not necessarily true, but it is
    // the best we can do at this point
    PacketPtr pkt = p->second.front();
    p->second.pop();

    if (p->second.empty())
        outstandingReads.erase(p);

    // no need to check for drain here as the next call will add a
    // response to the response queue straight away
    assert(nbrOutstandingReads != 0);
    --nbrOutstandingReads;

    // perform the actual memory access
    accessAndRespond(pkt);

    if (!isTraceAggressor) {
        // DPRINTF(PuDHammer,
        //         "[source=%s mode=%s] Access outside registered trace rows, "
        //         "skipping hammer count for addr 0x%016lx (dram_row %d)\n",
        //         sourceTag, modeTag, addr, row);
        return;
    }

    bool triggerSalt = noteActivation(channel, rank, bankgroup, bank, row,
                                      bufferhit);
    bool triggerPrac = notePracActivation(channel, rank, bankgroup, bank, row,
                                          bufferhit);

    // no rowhammer effects when rowbuffer hit
    // if (bufferhit) {
    //     DPRINTF(PuDHammer,
    //             "Row buffer hit for address %lld, skipping rowhammer "
    //             "logic\n", addr);
    //     return;
    // }

    for (int dist = -5; dist <= 5; dist++) {
        if (dist == 0 || row + dist < 0 || row + dist >= config->rows) {
            continue;
        }
        double add = 0;
        switch (abs(dist)) {
            case 5:
                add = config->inc_dist_5;
                break;
            case 4:
                add = config->inc_dist_4;
                break;
            case 3:
                add = config->inc_dist_3;
                break;
            case 2:
                add = config->inc_dist_2;
                break;
            case 1:
                add = config->inc_dist_1;
                break;
        }

        if (add == 0) {
            // We don't increment for this distance
            continue;
        }


        // handle temperature
        // --- Temperature
        double temp_multiplier = 1.0;

        if (current_attack_mode == SiMRA &&
            config->temperature > config->baseline_temperature) {
            temp_multiplier = 1.0 + config->temperature_scale_factor *
                (config->temperature - config->baseline_temperature);

            DPRINTF(PuDHammer,
                    "Temperature increased to %.2f, applying multiplier "
                    "%.2f to bit flip probability\n",
                    config->temperature, temp_multiplier);
        }

        if (current_attack_mode == SiMRA) {
            add *= config->SiMRA_weight;
        } else if (current_attack_mode == CoMRA) {
            add *= config->CoMRA_weight;
        }

        // handle tAggOn
        double taggon_multiplier = 1.0;

        double excess_ratio =
            (config->tAggOn - config->tRAS_baseline_ticks) /
            (config->tAggOn_max_ticks - config->tRAS_baseline_ticks);



        add = add * temp_multiplier;

        uint64_t flipped_row_base = config->ReverseAddressMapping(
            channel, rank, bankgroup, bank, row + dist, 0);
        const uint64_t flipped_phys_addr =
            config->ReverseAddressMapping(channel, rank, bankgroup, bank,
                                          row + dist, a.column) +
            (addr & ((1ULL << config->shift_bits) - 1));
        if (hammer_count.find(flipped_row_base) == hammer_count.end()) {
            hammer_count.insert(std::make_pair(flipped_row_base, add));
        } else {
            hammer_count[flipped_row_base] += add;
        }
        //DPRINTF(PuDHammer, "This is from distance %d\n", dist);
        int flippedTraceRow = 0;
        if (getTraceRow(config, channel, rank, bankgroup, bank, row + dist,
                        flippedTraceRow)) {
            const std::string rowLabel = traceRowLabel(flippedTraceRow);
            DPRINTF(PuDHammer,
                    "Row %s, physical address 0x%016lx, increment %.4f, "
                    "hammer count %.4f\n",
                    rowLabel.c_str(), flipped_phys_addr, add,
                    hammer_count[flipped_row_base]);
        } else {
            // DPRINTF(PuDHammer,
            //         "Row base 0x%016lx, physical address 0x%016lx\n",
            //         flipped_row_base, flipped_phys_addr);
        }

        // double row_flip_rate = config->hc_last_bitflip_rate *
        //     (hammer_count[flipped_row_base] - config->hc_first) /
        //     (config->hc_last - config->hc_first);

        const double dynamic_hc_first =
            static_cast<double>(get_row_threshold(flipped_row_base));
        if (hammer_count[flipped_row_base] < dynamic_hc_first) {
            continue;
        }

        const double dynamic_hc_last =
            std::max(static_cast<double>(config->hc_last),
                     dynamic_hc_first + 1.0);

        const double current_hammer_count =
            std::min(hammer_count[flipped_row_base], dynamic_hc_last);

        double normalized_hc = 0.0;
        if (current_hammer_count > dynamic_hc_first) {
            normalized_hc = (current_hammer_count - dynamic_hc_first) /
                (dynamic_hc_last - dynamic_hc_first);
            normalized_hc = std::clamp(normalized_hc, 0.0, 1.0);
        }

        // Use a non-linear burst curve so early hammering causes few flips,
        // while activity close to HC_last ramps up much faster.
        const double non_linear_factor =
            std::pow(normalized_hc, config->non_linear_degree);
        const double row_flip_rate =
            config->hc_last_bitflip_rate * non_linear_factor;

        for (uint64_t quad = flipped_row_base;
             quad < flipped_row_base + config->row_size;
             quad += sizeof(uint64_t)) {
            if (flipped.find(quad) != flipped.end()) {
                // already flipped
                continue;
            }

            // probabilisticly flip quadword
            if (gen_proba(quad) > row_flip_rate) {
                // no flip
                continue;
            }

            flipped.insert(quad);

            // this xor makes sure that this is not the same probability as for gen_proba
            uint64_t mask = 0;
            if (config->flip_mask) {
                mask = config->flip_mask;
            } else {
                std::mt19937 gen(quad ^ 0xcafecafecafecafe);
                double flipped_bits_ran = std::generate_canonical<double, 10>(gen);
                int flipped_bits;
                if (flipped_bits_ran <= config->proba_1_bit_flipped) {
                    flipped_bits = 1;
                } else if (flipped_bits_ran <= config->proba_1_bit_flipped
                                              +config->proba_2_bit_flipped) {
                    flipped_bits = 2;
                } else if (flipped_bits_ran <= config->proba_1_bit_flipped
                                              +config->proba_2_bit_flipped
                                              +config->proba_3_bit_flipped) {
                    flipped_bits = 3;
                } else {
                    flipped_bits = 4;
                }

                std::uniform_int_distribution<> distrib(0, 63);
                for (int j = 0; j<flipped_bits; j++) {
                    int pos;
                    // find position that is not yet taken
                    do {
                        pos = distrib(gen);
                    } while (mask & (((uint64_t)1) << pos));
                    mask |= ((uint64_t)1) << pos;
                }
            }

            // flip with mask
            *(uint64_t*)this->toHostAddr(quad) ^= mask;
            int bitflipTraceRow = 0;
            if (getTraceRow(config, channel, rank, bankgroup, bank, row + dist,
                            bitflipTraceRow)) {
                const std::string rowLabel = traceRowLabel(bitflipTraceRow);
                DPRINTF(PuDHammer,
                        "Bit flip at row %s, address 0x%016lx, mask: "
                        "0x%016lx\n", rowLabel.c_str(), quad, mask);
            } else {
                DPRINTF(PuDHammer,
                        "Bit flip at address 0x%016lx, mask: 0x%016lx\n",
                        quad, mask);
            }
        }
    }

    if (config->para_enabled) {
        PARA(channel, rank, bankgroup, bank, row);
    }
    if (config->trr_enabled) {
        TRR(channel, rank, bankgroup, bank, row);
    }
    if (config->prac_abo_enabled && triggerPrac) {
        PRAC_ABO(channel, rank, bankgroup, bank, row);
    }
    if (config->salt_enabled && triggerSalt) {
        performSaltAbo();
    }
}

void
DRAMsim3::writeComplete(unsigned id, uint64_t addr, bool bufferhit)
{
    DPRINTF(DRAMsim3, "Write to address %lld complete\n", addr);
    current_attack_mode =
        static_cast<AttackMode>(global_rowhammer_attack_mode);

    // get the outstanding reads for the address in question
    auto p = outstandingWrites.find(addr);
    assert(p != outstandingWrites.end());

    // we have already responded, and this is only to keep track of
    // what is outstanding
    p->second.pop();
    if (p->second.empty())
        outstandingWrites.erase(p);

    assert(nbrOutstandingWrites != 0);
    --nbrOutstandingWrites;

    if (nbrOutstanding() == 0)
        signalDrainDone();

    auto a = config->AddressMapping(addr);
    int aggressorTraceRow = 0;
    const bool isTraceAggressor =
        getTraceRow(config, a.channel, a.rank, a.bankgroup, a.bank, a.row,
                    aggressorTraceRow);
    const char *sourceTag = "system";
    const char *modeTag = "NORMAL";
    switch (current_attack_mode) {
      case SiMRA:
        modeTag = "SiMRA";
        if (isTraceAggressor) {
            sourceTag = "trace";
        }
        break;
      case CoMRA:
        modeTag = "CoMRA";
        if (isTraceAggressor) {
            sourceTag = "trace";
        }
        break;
      case RESET_BYPASS:
        sourceTag = "reset";
        modeTag = "RESET_BYPASS";
        break;
      case NORMAL:
      default:
        break;
    }
    // if (!bufferhit) {
    //     logAggressorRow(config, "Write", sourceTag, modeTag, addr,
    //                     a.channel, a.rank, a.bankgroup, a.bank, a.row);
    // }
    if (current_attack_mode == RESET_BYPASS) {
        uint64_t row_base = config->ReverseAddressMapping(
            a.channel, a.rank, a.bankgroup, a.bank, a.row, 0);
        hammer_count.erase(row_base);
        for (uint64_t quad = row_base;
             quad < row_base + config->row_size;
             quad += sizeof(uint64_t)) {
            flipped.erase(quad);
        }
        return;
    }

    bool triggerSalt = noteActivation(a.channel, a.rank, a.bankgroup, a.bank,
                                      a.row, bufferhit);
    bool triggerPrac = notePracActivation(a.channel, a.rank, a.bankgroup,
                                          a.bank, a.row, bufferhit);

    // Refresh the written row and make it flippable again.
    uint64_t row_base = config->ReverseAddressMapping(
        a.channel, a.rank, a.bankgroup, a.bank, a.row, 0);
    hammer_count.erase(row_base);
    for (uint64_t quad = row_base;
         quad < row_base + config->row_size;
         quad += sizeof(uint64_t)) {
        flipped.erase(quad);
    }
    if (triggerPrac) {
        PRAC_ABO(a.channel, a.rank, a.bankgroup, a.bank, a.row);
    }
    if (triggerSalt) {
        performSaltAbo();
    }
}

void
DRAMsim3::refreshComplete(unsigned id, int channel, int bankgroup, int bank)
{
    const int actual_channel = id;
    const int rank = channel;

    if (config->salt_enabled && config->salt_coord_refresh) {
        if (bankgroup == -1 || bank == -1) {
            for (int bg = 0; bg < config->bankgroups; ++bg) {
                for (int ba = 0; ba < config->banks_per_group; ++ba) {
                    performSaltRefreshForBank(actual_channel, rank, bg, ba);
                }
            }
        } else {
            performSaltRefreshForBank(actual_channel, rank, bankgroup, bank);
        }
        DPRINTF(DRAMsim3,
                "SALT-C refresh at channel %d rank %d bankgroup %d bank %d\n",
                actual_channel, rank, bankgroup, bank);
        for (auto it = prac_count.begin(); it != prac_count.end();) {
            dramsim3::Address a = config->AddressMapping(it->first);
            if ((a.channel == actual_channel) &&
                ((a.rank == rank) || (rank == -1)) &&
                ((a.bankgroup == bankgroup) || (bankgroup == -1)) &&
                ((a.bank == bank) || (bank == -1))) {
                it = prac_count.erase(it);
            } else {
                ++it;
            }
        }
        return;
    }

    for (auto it = hammer_count.begin(); it != hammer_count.end();) {
        dramsim3::Address a = config->AddressMapping(it->first);
        if ((a.channel == actual_channel) &&
            ((a.rank == rank) || (rank == -1)) &&
            ((a.bankgroup == bankgroup) || (bankgroup == -1)) &&
            ((a.bank == bank) || (bank == -1))) {
            // NOTE: flipped is not erased here since we dont want to
            // flip mutliple times
            it = hammer_count.erase(it);
        } else {
            ++it;
        }
    }
    for (auto it = prac_count.begin(); it != prac_count.end();) {
        dramsim3::Address a = config->AddressMapping(it->first);
        if ((a.channel == actual_channel) &&
            ((a.rank == rank) || (rank == -1)) &&
            ((a.bankgroup == bankgroup) || (bankgroup == -1)) &&
            ((a.bank == bank) || (bank == -1))) {
            it = prac_count.erase(it);
        } else {
            ++it;
        }
    }
    DPRINTF(DRAMsim3,
            "Refresh at channel %d rank %d bankgroup %d bank %d complete\n",
            actual_channel, rank, bankgroup, bank);
}

void DRAMsim3::printMitigationStats()
{
    DPRINTF(PuDHammer, "=== Rowhammer Mitigation Statistics ===\n");
    DPRINTF(PuDHammer, "SALT Enabled: %s\n",
            config->salt_enabled ? "yes" : "no");
    if (config->salt_enabled) {
        DPRINTF(PuDHammer,
                "SALT Mode: %s (APM=%d ATH=%d subarrays=%d "
                "rows/subarray=%d bundle=%d)\n",
                config->salt_coord_refresh ? "SALT-C" : "SALT",
                config->salt_apm, config->salt_ath, config->salt_subarrays,
                config->salt_rows_per_subarray, config->salt_bundle_rows);
        DPRINTF(PuDHammer, "SALT ABOs: %lu\n", saltAbos);
        DPRINTF(PuDHammer, "SALT ABO Bank Mitigations: %lu\n",
                saltAboMitigations);
        DPRINTF(PuDHammer, "SALT REF Mitigations: %lu\n", saltRefMitigations);
        DPRINTF(PuDHammer, "SALT Rows Refreshed: %lu\n", saltRowsRefreshed);
        DPRINTF(PuDHammer, "\n");
    }
    DPRINTF(PuDHammer, "PRAC_ABO Enabled: %s\n",
            config->prac_abo_enabled ? "yes" : "no");
    if (config->prac_abo_enabled) {
        DPRINTF(PuDHammer, "PRAC Threshold: %d\n", config->prac_threshold);
        DPRINTF(PuDHammer, "PRAC Blast Radius: %d\n",
                config->prac_blast_radius);
        DPRINTF(PuDHammer, "PRAC ABOs: %lu\n", prac_abos);
        DPRINTF(PuDHammer, "PRAC Prevented Bit Flips: %lu\n",
                prac_preventions);
        DPRINTF(PuDHammer, "\n");
    }
    DPRINTF(PuDHammer, "TRR Calls: %lu\n", trr_calls);
    DPRINTF(PuDHammer, "TRR Prevented Bit Flips: %lu\n", trr_preventions);
    if (trr_calls > 0) {
        DPRINTF(PuDHammer, "TRR Prevention Rate: %.2f%% (prevented %lu/%lu)\n",
                (double)trr_preventions / trr_calls * 100,
                trr_preventions, trr_calls);
    }
    DPRINTF(PuDHammer, "\n");
    DPRINTF(PuDHammer, "PARA Calls: %lu\n", para_calls);
    DPRINTF(PuDHammer, "PARA Prevented Bit Flips: %lu\n", para_preventions);
    if (para_calls > 0) {
        DPRINTF(PuDHammer,
                "PARA Prevention Rate: %.2f%% (prevented %lu/%lu)\n",
                (double)para_preventions / para_calls * 100,
                para_preventions, para_calls);
    }
    DPRINTF(PuDHammer, "\n");
    DPRINTF(PuDHammer, "Total Mitigation Activations: %lu\n",
            prac_abos + trr_calls + para_calls);
    DPRINTF(PuDHammer, "Total Prevented Bit Flips: %lu\n",
            prac_preventions + trr_preventions + para_preventions);
    DPRINTF(PuDHammer, "=========================================\n");
}

Port&
DRAMsim3::getPort(const std::string &if_name, PortID idx)
{
    if (if_name != "port") {
        return ClockedObject::getPort(if_name, idx);
    } else {
        return port;
    }
}

DrainState
DRAMsim3::drain()
{
    // check our outstanding reads and writes and if any they need to
    // drain
    return nbrOutstanding() != 0 ? DrainState::Draining : DrainState::Drained;
}

DRAMsim3::MemoryPort::MemoryPort(const std::string& _name,
                                 DRAMsim3& _memory)
    : ResponsePort(_name, &_memory), mem(_memory)
{ }

AddrRangeList
DRAMsim3::MemoryPort::getAddrRanges() const
{
    AddrRangeList ranges;
    ranges.push_back(mem.getAddrRange());
    return ranges;
}

Tick
DRAMsim3::MemoryPort::recvAtomic(PacketPtr pkt)
{
    return mem.recvAtomic(pkt);
}

void
DRAMsim3::MemoryPort::recvFunctional(PacketPtr pkt)
{
    mem.recvFunctional(pkt);
}

bool
DRAMsim3::MemoryPort::recvTimingReq(PacketPtr pkt)
{
    // pass it to the memory controller
    return mem.recvTimingReq(pkt);
}

void
DRAMsim3::MemoryPort::recvRespRetry()
{
    mem.recvRespRetry();
}

} // namespace memory
} // namespace gem5
