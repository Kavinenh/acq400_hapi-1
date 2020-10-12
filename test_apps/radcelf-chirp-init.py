#!/usr/bin/env python

""" radcelf-chirp-init - set up a chirp ..
    radcelf-chirp-init UUT1
    where UUT1 is the ip-address or host name of the uut
    powerful alternative to embedded shell script.

    no ssh keys or remote execution, nfs mounts required.

    potential enhancements stepping stone to avoid the magic numbers:
    eg
            AD9854.CR.X4 = 00040000
    potential to program real numbers eg
      >>> format(long(0.5 * pow(2,48)), '012x')
     '800000000000'

    seamless integration with data capture (and maybe postprocess and analysis..)
    
    acq1001_427> radcelf-chirp-init.py --help
usage: radcelf-chirp-init.py [-h] [--test TEST] [--debug DEBUG] [--noverify NOVERIFY] [--ddsX DDSX] [--gps_sync GPS_SYNC]
                             [--chirps_per_sec CHIRPS_PER_SEC] [--trigger_adc_dx TRIGGER_ADC_DX]
                             [uuts [uuts ...]]

radcelf-chirp-init

positional arguments:
  uuts                  uut

optional arguments:
  -h, --help            show this help message and exit
  --test TEST           set number of tests to run
  --debug DEBUG         1: trace 2: step
  --noverify NOVERIFY   do not verify (could be waiting gps)
  --ddsX DDSX           ddsA=1, ddsB=2, ddsA+ddsB=3
  --gps_sync GPS_SYNC   syncronize with GPSPPS >1: autotrigger at + gps_sync s
  --chirps_per_sec CHIRPS_PER_SEC
                        chirps per second
  --trigger_adc_dx TRIGGER_ADC_DX
                        trigger ACQ on ddsA or ddsB or dX [X=0,1,2,3,4,5,6]


examples:
    STOP:
        radcelf-chirp-init.py
"""

import sys
import acq400_hapi
import argparse
import time
from builtins import int
from acq400_hapi import AD9854 
from acq400_hapi import Debugger

GPS_SYNC_DDSA = 0x1
GPS_SYNC_DDSB = 0x2
GPS_SYNC_DDSX = 0x3

    
    
def set_upd_clk_fpga(uut, ddsX, value):
    if ddsX == GPS_SYNC_DDSA:
        uut.s2.ddsA_upd_clk_fpga = value
    else:
        uut.s2.ddsB_upd_clk_fpga = value


def freq(sig):
    return float(sig.split(" ")[1])


@Debugger
def init_remapper(uut):
# Program AD9512 secondary clock to choose 25 MHz from the AD9854 remap
    uut.clkdB.CSPD = '02'
    uut.clkdB.UPDATE = '01'




@Debugger        
def init_chirp(uut, ddsX, chirps_per_sec=5, gps_sync=True):
    # SETTING KAKA'AKOS CHIRP
    #
    dds = uut.ddsA if ddsX == GPS_SYNC_DDSA else uut.ddsB

# Program the chirp using Kaka'ako parameters
    set_upd_clk_fpga(uut, ddsX, '1')   # Values are strobed in with normal FPGA IOUPDATE
    dds.CR = acq400_hapi.AD9854.CRX(12)   # '004C0061'
    dds.FTW1 = '172B020C49BA'
    dds.DFR = '0000000021D1'
#    dds.UCR = '01F01FD0'                               # KAKA'AKOS original, deprecated
#    dds.UCR = acq400_hapi.AD9854.UCR(5)                # '01C9C37F'    # 5Hz
    dds.UCR =  acq400_hapi.AD9854.UCR(chirps_per_sec)   # '002DC6BF'    # 50Hz is easier to see on a scope    
    dds.RRCR = '000001'
    dds.IPDMR = '0FFF'
    dds.QPDMR = '0FFF'
    if gps_sync:                                        # new synchronized start
        set_upd_clk_fpga(uut, ddsX, '0')                    # Final value strobed in by PPS linked IOUPDATE
    dds.CR = acq400_hapi.AD9854.CRX(12, chirp=True)     # '004C8761'
    if not gps_sync:                                    # legacy free-starting chirp
        set_upd_clk_fpga(uut, ddsX, '0')                    # IOUPDATE is now an INPUT 
   



@Debugger
def init_trigger(uut, dx='ddsA'):
        uut.s1.trg = '1,{},rising'.format('d3' if dx == 'ddsA' else 'd4' if dx == 'ddsB' else dx)


def _gps_sync(dds, gps_sync_chirp_en, hold_en):
    if gps_sync_chirp_en:
        dds.gps_sync_chirp = 1
        if hold_en:
            dds.gps_engage_hold = 1
            # dds_gps_arm_pps executed later
        else:
            dds.gps_arm_pps = 0
            dds.gps_engage_hold = 0
    else:
        dds.gps_sync_chirp = 0
@Debugger
def gps_sync(uut, ddsX=GPS_SYNC_DDSX, gps_sync_chirp_en=False, hold_en=False):
    if ddsX&GPS_SYNC_DDSA:
        _gps_sync(uut.ddsA, gps_sync_chirp_en, hold_en)
    if ddsX&GPS_SYNC_DDSB:
        _gps_sync(uut.ddsB, gps_sync_chirp_en, hold_en)


@Debugger
def reset_counters(uut):  
    uut.s0.SIG_TRG_S2_RESET = 1
    uut.s0.SIG_TRG_S3_RESET = 1    
    
@Debugger
def chirp_off(uut): 
    uut.ddsA.CR = AD9854.CRX_chirp_off()      
    uut.ddsB.CR = AD9854.CRX_chirp_off()
    
    while uut.chirp_freq(0) != 0 and uut.chirp_freq(1) != 0:
        print("waiting for chirp to stop {} {}".format(uut.chirp_freq(0), uut.chirp_freq(1)))
        
    reset_counters(uut)           
       
@Debugger
def radcelf_init(uut, legacy):
    if legacy:
        uut.s2.RADCELF_init = 1
    else:
        uut.radcelf_init()
    


def valid_chirp(freq):
    return freq >= 4 or freq <= 5


def verify_chirp(uut, test):
    retry = 0

    while retry < 10:
        if valid_chirp(uut.chirp_freq(0)) and valid_chirp(uut.chirp_freq(1)):
            print("test:%d  PASS %s %s" %
                  (test, uut.s0.SIG_TRG_S2_FREQ, uut.s0.SIG_TRG_S3_FREQ))
            return True
        else:
            time.sleep(1)
            retry = retry + 1

    return False


def init_dual_chirp(args, uut):
    chirp_off(uut)    
    gps_sync(uut, ddsX=args.ddsX, gps_sync_chirp_en=False)
    gps_sync(uut, ddsX=args.ddsX, gps_sync_chirp_en=args.gps_sync)
    
    init_remapper(uut)    
    gps_sync(uut, gps_sync_chirp_en=args.gps_sync, hold_en=True)
    
    if args.ddsX&GPS_SYNC_DDSA:
        init_chirp(uut, GPS_SYNC_DDSA, chirps_per_sec=args.chirps_per_sec, gps_sync=args.gps_sync!=0)
        
    if args.ddsX&GPS_SYNC_DDSB:
        init_chirp(uut, GPS_SYNC_DDSB, chirps_per_sec=args.chirps_per_sec, gps_sync=args.gps_sync!=0)
    
    gps_sync(uut, ddsX=args.ddsX, gps_sync_chirp_en=args.gps_sync)
    init_trigger(uut, dx=args.trigger_adc_dx)

        
def run_test(args):
    uuts = [acq400_hapi.RAD3DDS(u) for u in args.uuts]

    for test in range(0, args.test):
        for uut in uuts:
            chirp_off(uut)
            
        if args.chirps_per_sec == 0:
            break
        
        for uut in uuts:
            init_dual_chirp(args, uut)            
          
        if args.gps_sync > 1:
            ttime = time.time() + args.gps_sync
            for uut in uuts:
                uut.s2.trigger_at = ttime
            time.sleep(args.gps_sync+1)
        
        if not args.noverify:
            for uut in uuts:
                verify_chirp(uut, test)
                


def run_main():
    parser = argparse.ArgumentParser(description='radcelf-chirp-init')
    parser.add_argument('--test', default=1, type=int,
                        help="set number of tests to run")
    parser.add_argument('--debug', default=0, type=int, help="1: trace 2: step")
    parser.add_argument('--noverify', default=0, type=int, help="do not verify (could be waiting gps)")
    parser.add_argument('--ddsX', default=0x3, type=int, help="ddsA=1, ddsB=2, ddsA+ddsB=3")
    parser.add_argument('--gps_sync', default=0, type=int, help="syncronize with GPSPPS >1: autotrigger at + gps_sync s")
    parser.add_argument('--chirps_per_sec', default=5, type=int, help="chirps per second")
    parser.add_argument('--trigger_adc_dx', default='ddsA', help="trigger ACQ on ddsA or ddsB or dX [X=0,1,2,3,4,5,6]")    
    parser.add_argument('uuts', nargs='*', default=["localhost"], help="uut")
    args = parser.parse_args()
    if args.debug: 
        Debugger.enabled = args.debug
    run_test(args)

# execution starts here


if __name__ == '__main__':
    run_main()
