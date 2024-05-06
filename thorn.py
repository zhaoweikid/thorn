# coding: utf-8
import os, sys
import struct
import socket
import time
import traceback
from asyncio import exceptions
import config
from errno import EAGAIN, EBADF, EPIPE, ECONNRESET
import logging
import asyncio
import proto
from zbase3.base import logger

async def from_local(loc_r, loc_w, rem_w):
    log.debug('read data from local ...')
    while True:
        data = await loc_r.read(8192*2)
        log.debug('local >>> %d', len(data))
        if not data:
            log.info('read 0, local conn close, quit')
            #loc_w.write_eof()
            loc_w.close()
            await loc_w.wait_closed()
            return
        
        packdata = proto.pack(0, proto.CMD_SEND, data)
        log.debug('remote <<< %d', len(packdata))
        rem_w.write(packdata)
        await rem_w.drain()

async def to_local(rem_r, rem_w, localport):
    loc_r = None
    loc_w = None
    while True:
        try:
            head = await rem_r.readexactly(proto.headlen)
        except exceptions.IncompleteReadError:
            log.info('read incomplete, thorn close, quit')
            rem_w.close()
            await rem_w.wait_closed()
            return

        log.debug('remote >>> %s', head)
        if not head:
            log.info('read null, thorn close, quit')
            rem_w.close()
            await rem_w.wait_closed()

            return

        length, name, cmd = proto.unpack_head(head)
        log.debug(f'length:{length} name:{name} cmd:{cmd}') 
        if length <= 0:
            log.info('length error, thorn close, quit')
            rem_w.close()
            await rem_w.wait_closed()

            return

        try:
            data = await rem_r.readexactly(length)
        except exceptions.IncompleteReadError:
            log.info('read length incomplete, thorn close, quit')
            rem_w.close()
            await rem_w.wait_closed()

            return

        log.debug('remote >>> %d', len(data))

        if not loc_w or loc_w.is_closing():
            log.debug('connect to local: %d', localport)
            loc_r, loc_w = await asyncio.open_connection('127.0.0.1', localport)
            log.debug('connected')
            t = asyncio.create_task(from_local(loc_r, loc_w, rem_w))
            #await t

        log.debug('local <<< %d ^', len(data))
        loc_w.write(data)
        await loc_w.drain()




async def client(user, server_addr, localport):
    while True:
        await asyncio.sleep(1)
        log.warning('connect to {0}:{1}'.format(*server_addr))
        rem_r, rem_w = await asyncio.open_connection(server_addr[0], server_addr[1])

        while True:
            s = 'AUTH {0}\r\n'.format(user)
            log.debug('remote <<< %s', s)
            rem_w.write(s.encode('utf-8'))

            ln = await rem_r.readline()
            if not ln:
                log.info('readline 0, quit')
                return
            log.debug('remote >>> %s', ln)
            ret = ln[:2]
            if ret == b'OK':
                break
            else:
                log.info('auth error: %s', ln)
                return
       
        log.info('ok, go relay ...')
        await to_local(rem_r, rem_w, localport)


def main():
    if len(sys.argv) == 4:
        user = sys.argv[1].strip()
        server = sys.argv[2].strip().split(':')
        localport = int(sys.argv[3].strip())
    else:
        print('usage:\n\tpython3 thorn.py user remote-server-ip:remote-server-port local-port\n')
        sys.exit(0)
   
    global log
    log = logger.install('stdout')
    server_addr = (server[0], int(server[1]))

    asyncio.run(client(user, server_addr, localport))


if __name__ == '__main__':
    main()


