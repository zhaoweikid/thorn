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

# 所有的连接
name_conns = {}

async def from_local(name, rem_w):
    log.debug('read client %d ...', name)
    global name_conns
    loc_r, loc_w = name_conns[name]
    while True:
        try:
            data = await loc_r.read(8192*2)
        except:
            log.info(traceback.format_exc())
            data = ''
        log.debug('c >>> %d', len(data))
        if not data:
            log.info('read 0, conn %d close, quit', name)
            #loc_w.write_eof()
            loc_w.close()
            await loc_w.wait_closed()
            log.debug('remove conn:%d', name)
            name_conns.pop(name)

            pkdata = proto.cmd_close(name)
            log.debug('s <<< cmd_close %d', len(pkdata))
            rem_w.write(pkdata)
            await rem_w.drain()
            return
        
        packdata = proto.pack(name, proto.CMD_SEND, data)
        log.debug('s <<< %d', len(packdata))
        rem_w.write(packdata)
        await rem_w.drain()

async def to_local(rem_r, rem_w, local_addr):
    global name_conns
    loc_r = None
    loc_w = None

    localip, localport = local_addr
    log.debug('read server ...')
    while True:
        try:
            head = await rem_r.readexactly(proto.headlen)
        except exceptions.IncompleteReadError:
            log.info('read server incomplete, thorn close, quit')
            log.debug(traceback.format_exc())
            rem_w.close()
            await rem_w.wait_closed()
            return

        log.debug('s >>> %s', head)
        if not head:
            log.info('read 0, thorn close, quit')
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

        #log.debug('s >>> %d', len(data))
        loc_r, loc_w = None, None
        if name in name_conns:
            loc_r, loc_w = name_conns[name]

        if not loc_w or loc_w.is_closing():
            log.debug('connect to %s:%d', localip, localport)
            loc_r, loc_w = await asyncio.open_connection(localip, localport)
            log.debug('connected')
            name_conns[name] = (loc_r, loc_w)
            t = asyncio.create_task(from_local(name, rem_w))
            #await t

        #log.debug('c <<< %d ^', len(data))
        loc_w.write(data)
        await loc_w.drain()


async def client(user, server_addr, local_addr):
    while True:
        await asyncio.sleep(1)
        log.warning('connect to {0}:{1}'.format(*server_addr))
        rem_r, rem_w = await asyncio.open_connection(server_addr[0], server_addr[1])

        while True:
            s = 'AUTH {0}\r\n'.format(user)
            log.debug('s <<< %s', s)
            rem_w.write(s.encode('utf-8'))

            ln = await rem_r.readline()
            if not ln:
                log.info('readline 0, quit')
                return
            log.debug('s >>> %s', ln)
            ret = ln[:2]
            if ret == b'OK':
                break
            else:
                log.info('auth error: %s', ln)
                return
       
        log.info('ok, ready ...')
        await to_local(rem_r, rem_w, local_addr)


def main():
    localip = '127.0.0.1'
    if len(sys.argv) == 4:
        user = sys.argv[1].strip()
        server = sys.argv[2].strip().split(':')
        if ':' in sys.argv[3]:
            localip, localport = sys.argv[3].strip().split(':')
        else:
            localport = sys.argv[3].strip()
    else:
        print('usage:\n\tpython3 thorn.py username server-ip:server-port local-ip:local-port\n')
        sys.exit(0)
   
    global log
    log = logger.install('stdout')
    server_addr = (server[0], int(server[1]))
    local_addr = (localip, int(localport))

    asyncio.run(client(user, server_addr, local_addr))


if __name__ == '__main__':
    main()


