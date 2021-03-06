#!/usr/bin/env python
""" mgtdramshot.py Capture to MGTDRAM

- optional capture to mgtdram
- manage upload
- optional validation
 assumes that clocking has been pre-assigned.

example usage::

       ./mgtdramshot.py --loop=100 --simulate=1 --validate=validate-6x480 \
           --captureblocks=2000 --offloadblocks=0-1999 acq2106_007


usage::

    mgtdramshot.py [-h] [--pre PRE] [--post POST] [--clk CLK] [--trg TRG]
                      [--sim SIM] [--trace TRACE] [--loop LOOP]
                      [--captureblocks CAPTUREBLOCKS]
                      [--offloadblocks OFFLOADBLOCKS] [--validate VALIDATE]
                      [--wait_user WAIT_USER]
                      uut

acq2106 mgtdram test

positional arguments:
  uut                   uut

optional arguments:
  -h, --help            show this help message and exit
  --pre PRE             pre-trigger samples
  --post POST           post-trigger samples
  --clk CLK             int|ext|zclk|xclk,fpclk,SR,[FIN]
  --trg TRG             int|ext,rising|falling
  --sim SIM             s1[,s2,s3..] list of sites to run in simulate mode
  --trace TRACE         1 : enable command tracing
  --loop LOOP           loop count
  --captureblocks CAPTUREBLOCKS
                        number of 4MB blocks to capture
  --offloadblocks OFFLOADBLOCKS
                        block list to upload nnn-nnn
  --validate VALIDATE   program to validate data
  --wait_user WAIT_USER
                        1: force user input each shot

"""

import sys
import datetime
import acq400_hapi
from acq400_hapi import awg_data
import argparse
from subprocess import call
import re
if sys.version_info < (3, 0):
    from future import builtins
    from builtins import input
import socket
import os
import numpy as np
import matplotlib.pyplot as plt
import time

LOG = None


def make_data_dir(directory, verbose):
    try:
        os.makedirs(directory)
    except Exception:
        if verbose:
            print("Tried to create dir but dir already exists")
        pass


def validate_streamed_data(good_data, test_data, cycle):
    # Using this method there is no detectable overhead.

    compare_data = good_data + ((cycle) * good_data[-1])

    if not np.array_equal(test_data, compare_data[0:test_data.size]):
        print("Discrepency in data found in cycle: {}, quitting now.".format(cycle))
        print("Length good: {}, length test: {}".format(good_data.shape, test_data.shape))
        f, (ax1, ax2, ax3) = plt.subplots(3, 1, sharey=True)
        ax1.plot(compare_data)
        ax1.plot(test_data)
        ax2.plot(compare_data)
        ax3.plot(test_data)
        ax1.grid(True)
        ax2.grid(True)
        ax3.grid(True)
        plt.show()
        exit(1)

    return None


def host_pull(args, uut):
    # Connect to port 53991 and pull all data.
    cycle = 0
    total_buf = bytes()
    # set up a RawClient to pull data from the mgtdram host_pull port.
    rc = acq400_hapi.MgtDramPullClient(uut.s0.HN)
    first_run = True

    nchan = uut.nchan()
    data_size = 4

    print("Starting host pull now.")

    for buffer in rc.get_blocks(nchan, ncols=(2**22)/nchan/data_size, data_size=data_size):

        if first_run:
            good_data = buffer
            first_run = False

        if args.save_data == 1:
            root = "./{}/{}".format(args.uut[0], cycle)
            make_data_dir(args.uut[0], 0)
            buffer.tofile(root)
            print("Saved file {} to disk.".format(cycle))
        else:
            print("Block {} pulled, size (in samples): {}.".format(cycle, buffer.size))

        if args.validate != 'no':
            validate_streamed_data(good_data, buffer, cycle)

        cycle += 1
    
    if cycle == 0:
        print("Data offload failed.")
        print("Pulled {} blocks.".format(cycle))
        exit(1)

    print("Data offloaded {} blocks {}".format(cycle, "" if args.validate == 'no' else "and all data validation passed."))
    return 1

def write_console(message):
# explicit flush needed to avoid lockup on Windows.
    sys.stdout.write(message)
    sys.stdout.flush()


class UploadFilter:
    def __init__(self):
        self.okregex = re.compile(r"axi0 start OK ([0-9]{4}) OK")
        self.line = 0

    def __call__ (self, st):
        st = st.rstrip()
        LOG.write("{}\n".format(st))

        if self.okregex.search(st) != None:
            if self.line%10 != 0:
                write_console('.')
            else:
                write_console("{}".format(self.line/10))
            self.line += 1
            if self.line > 100:
                write_console('\n')
                self.line = 0
        else:
            if self.line != 0:
                write_console('\n')
            write_console(">{}\n".format(st))
            self.line = 0


def run_shot(uut, args):
        # always capture over. The offload is zero based anyway, so add another one
    if args.captureblocks:
        uut.s14.mgt_run_shot = str(int(args.captureblocks) + 2)
        uut.run_mgt()

    if args.host_pull == 1:
        # for loop in list(range(1, args.loop + 1)):
        host_pull(args, uut)

    else:
        uut.s14.mgt_offload = args.offloadblocks if args.offloadblocks != 'capture' \
            else '0-{}'.format(args.captureblocks)
        t1 = datetime.datetime.now()
        uut.run_mgt(UploadFilter())
        ttime = datetime.datetime.now()-t1
        mb = args.captureblocks*4
        print("upload {} MB done in {} seconds, {} MB/s\n".\
              format(mb, ttime, mb/ttime.seconds))
        if args.validate != 'no':
            cmd = "{} {}".format(args.validate, uut.uut)
            print("run \"{}\"".format(cmd))
            rc = call(cmd, shell=True, stdin=0, stdout=1, stderr=2)
            if rc != 0:
                print("ERROR called process {} returned {}".format(args.validate, rc))
                exit(1)

def run_shots(args):

    global LOG
    LOG = open("mgtdramshot-{}.log".format(args.uut[0]), "w")
    uut = acq400_hapi.Acq2106_Mgtdram8(args.uut[0])
    acq400_hapi.Acq400UI.exec_args(uut, args)
    uut.s14.mgt_taskset = '1'
    if args.validate != 'no':
        for s in uut.modules:
            uut.modules[s].simulate = 1
    try:
        for ii in range(0, args.loop):
            t1 = datetime.datetime.now()
            print("shot: {} {}".format(ii, t1.strftime("%Y%m%d %H:%M:%S")))
            run_shot(uut, args)
            t2 = datetime.datetime.now()
            print("shot: {} done in {} seconds\n\n".format(ii, (t2-t1).seconds))

            if args.wait_user:
                input("hit return to continue")
    except KeyboardInterrupt:
        print("Keyboard Interrupt, take it all down NOW")
        os._exit(1)

    os._exit(0)

def run_main():
    parser = argparse.ArgumentParser(description='acq2106 mgtdram test')
    acq400_hapi.Acq400UI.add_args(parser)
    parser.add_argument('--loop', type=int, default=1, help="loop count")
    parser.add_argument('--captureblocks', type=int, default="2000", help='number of 4MB blocks to capture')
    parser.add_argument('--offloadblocks', type=str, default="capture", help='block list to upload nnn-nnn')
    parser.add_argument('--validate', type=str, default='no', help='program to validate data')
    parser.add_argument('--wait_user', type=int, default=0, help='1: force user input each shot')

    parser.add_argument('--host_pull', type=int, default=0,
    help='Whether or not to use the HOST PULL method. Default: 0.')

    parser.add_argument('--save_data', type=int, default=1,
    help='Whether or not to save data to a file in 4MB chunks. Default: 0.')

    parser.add_argument('uut', nargs=1, help="uut ")
    run_shots(parser.parse_args())

# execution starts here

if __name__ == '__main__':
    run_main()
