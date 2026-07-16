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
 *
 */

/**
 * @file
 * DRAMsim3
 */
#ifndef __MEM_DRAMSIM3_HH__
#define __MEM_DRAMSIM3_HH__

#include <cstdint>
#include <functional>
#include <map>
#include <queue>
#include <random>
#include <set>
#include <tuple>
#include <unordered_map>
#include <unordered_set>

#include "mem/abstract_mem.hh"
#include "mem/dramsim3_wrapper.hh"
#include "mem/qport.hh"

extern uint64_t global_rowhammer_attack_mode;
extern uint64_t global_rowhammer_trace_base_addr;
extern std::unordered_set<uint64_t> global_rowhammer_trace_row_bases;

namespace gem5
{

namespace memory
{

class DRAMsim3 : public AbstractMemory
{
  private:
    struct SaltSubarrayState
    {
        int64_t actCtr = 0;
        uint32_t nextBundleRow = 0;
        uint32_t nextRow = 0;
    };

    struct SaltBankState
    {
        // Greedily-tracked hottest subarray (SALT's "current top spot"):
        // updated in O(1) whenever a subarray's counter overtakes it,
        // never by rescanning the bank.
        uint32_t cts = 0;
        // Round-robin cursor selecting which SALT_REFSPREAD subarrays get
        // their counters decayed on each refresh event.
        uint32_t ref_ptr = 0;
    };

    enum AttackMode
    {
        NORMAL = 0,
        SiMRA = 1,
        CoMRA = 2,
        RESET_BYPASS = 3
    };
    AttackMode current_attack_mode = NORMAL;
    AttackMode last_recorded_mode = NORMAL;
    std::map<int, uint64_t> last_acc_tick;
    std::map<int, int> last_acc_row;

    std::default_random_engine rng;
    int get_row_threshold(uint64_t row_base_addr);
    /**
     * The memory port has to deal with its own flow control to avoid
     * having unbounded storage that is implicitly created in the port
     * itself.
     */
    class MemoryPort : public ResponsePort
    {

      private:

        DRAMsim3& mem;

      public:

        MemoryPort(const std::string& _name, DRAMsim3& _memory);

      protected:

        Tick recvAtomic(PacketPtr pkt);

        void recvFunctional(PacketPtr pkt);

        bool recvTimingReq(PacketPtr pkt);

        void recvRespRetry();

        AddrRangeList getAddrRanges() const;

    };

    MemoryPort port;

    std::unordered_map<uint64_t,double> hammer_count;
    std::unordered_map<uint64_t,double> probabilities;
    std::unordered_map<uint64_t, int> per_row_threshold;
    std::unordered_map<uint64_t, int> prac_count;
    std::set<uint64_t> flipped;
    std::minstd_rand para_rng;
    std::unordered_map<uint64_t, SaltSubarrayState> saltSubarrayState;
    std::unordered_map<uint64_t, SaltBankState> saltBankState;

    dramsim3::Config* config;

    uint64_t saltAbos = 0;
    uint64_t saltAboMitigations = 0;
    uint64_t saltRefMitigations = 0;
    uint64_t saltRowsRefreshed = 0;

    /**
     * Callback functions
     */
    std::function<void(uint64_t, bool)> read_cb;
    std::function<void(uint64_t, bool)> write_cb;
    std::function<void(int, int, int)> refresh_cb;

    /**
     * The actual DRAMsim3 wrapper
     */
    DRAMsim3Wrapper wrapper;

    /**
     * Is the connected port waiting for a retry from us
     */
    bool retryReq;

    /**
     * Are we waiting for a retry for sending a response.
     */
    bool retryResp;

    /**
     * Keep track of when the wrapper is started.
     */
    Tick startTick;

    /**
     * Keep track of what packets are outstanding per
     * address, and do so separately for reads and writes. This is
     * done so that we can return the right packet on completion from
     * DRAMSim.
     */
    std::unordered_map<Addr, std::queue<PacketPtr> > outstandingReads;
    std::unordered_map<Addr, std::queue<PacketPtr> > outstandingWrites;

    /**
     * Count the number of outstanding transactions so that we can
     * block any further requests until there is space in DRAMsim3 and
     * the sending queue we need to buffer the response packets.
     */
    unsigned int nbrOutstandingReads;
    unsigned int nbrOutstandingWrites;

    /**
     * Queue to hold response packets until we can send them
     * back. This is needed as DRAMsim3 unconditionally passes
     * responses back without any flow control.
     */
    std::deque<PacketPtr> responseQueue;


    unsigned int nbrOutstanding() const;

    /**
     * When a packet is ready, use the "access()" method in
     * AbstractMemory to actually create the response packet, and send
     * it back to the outside world requestor.
     *
     * @param pkt The packet from the outside world
     */
    void accessAndRespond(PacketPtr pkt);

    void sendResponse();

    /**
     * Event to schedule sending of responses
     */
    EventFunctionWrapper sendResponseEvent;

    /**
     * Progress the controller one clock cycle.
     */
    void tick();

    /**
     * Event to schedule clock ticks
     */
    EventFunctionWrapper tickEvent;

    /**
     * Upstream caches need this packet until true is returned, so
     * hold it for deletion until a subsequent call
     */
    std::unique_ptr<Packet> pendingDelete;

  public:

    typedef DRAMsim3Params Params;
    DRAMsim3(const Params &p);

    /**
     * Read completion callback.
     *
     * @param id Channel id of the responder
     * @param addr Address of the request
     * @param bufferhit If the read was served through the row buffer
     */
    void readComplete(unsigned id, uint64_t addr, bool bufferhit);

    /**
     * Write completion callback.
     *
     * @param id Channel id of the responder
     * @param addr Address of the request
     * @param bufferhit If the write was served through the row buffer
     */
    void writeComplete(unsigned id, uint64_t addr, bool bufferhit);

    /**
     * Refresh completion callback.
     */
    void refreshComplete(unsigned id, int channel, int bankgroup, int bank);


    void PARA(int channel, int rank, int bankgroup, int bank, int row);
    void PRAC_ABO(int channel, int rank, int bankgroup, int bank, int row);

    std::unordered_map<uint64_t,int> trr_count;
    void TRR(int channel, int rank, int bankgroup, int bank, int row);

    double gen_proba(uint64_t addr);

    uint64_t bankKey(int channel, int rank, int bankgroup, int bank) const;
    uint64_t subarrayKey(int channel, int rank, int bankgroup, int bank,
                         int subarray) const;
    int subarrayForRow(int row) const;
    int rowIndexInSubarray(int row) const;
    int bankRowFromSubarray(int subarray, int row_index) const;
    SaltSubarrayState& getSaltSubarrayState(int channel, int rank,
                                            int bankgroup, int bank,
                                            int subarray);
    SaltBankState& getSaltBankState(int channel, int rank, int bankgroup,
                                    int bank);
    void clearSaltRow(int channel, int rank, int bankgroup, int bank, int row);
    void clearSaltRowsInSubarray(int channel, int rank, int bankgroup,
                                 int bank, int subarray, int count,
                                 bool wrap_rows);
    void performSaltRefreshForBank(int channel, int rank, int bankgroup,
                                   int bank);
    void performSaltAboForBank(int channel, int rank, int bankgroup, int bank);
    bool noteActivation(int channel, int rank, int bankgroup, int bank,
                        int row, bool bufferhit);
    bool notePracActivation(int channel, int rank, int bankgroup, int bank,
                            int row, bool bufferhit);

    // Counters for rowhammer mitigation overhead tracking.
    uint64_t prac_abos = 0;
    uint64_t prac_preventions = 0;
    uint64_t trr_calls = 0;
    uint64_t trr_preventions = 0;  // Times TRR cleared hammer_count
    uint64_t para_calls = 0;
    uint64_t para_preventions = 0; // Times PARA cleared hammer_count

    void printMitigationStats();

    DrainState drain() override;

    virtual Port& getPort(const std::string& if_name,
                          PortID idx = InvalidPortID) override;

    void init() override;
    void startup() override;

    void resetStats() override;

  protected:

    Tick recvAtomic(PacketPtr pkt);
    void recvFunctional(PacketPtr pkt);
    bool recvTimingReq(PacketPtr pkt);
    void recvRespRetry();

};

} // namespace memory
} // namespace gem5

#endif // __MEM_DRAMSIM3_HH__
