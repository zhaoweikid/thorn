# coding: utf-8
import asyncio
import os, sys
import struct
import socket
import time
import traceback
from asyncio import exceptions
import config
from errno import EAGAIN, EBADF, EPIPE, ECONNRESET
import proto
import logger
import logging

# 所有的连接
name_conns = {}
timeout = 30

async def from_local(name, serv_w):
    log.debug('read local %d ...', name)
    global name_conns
    loc_r, loc_w = name_conns[name]
    while True:
        try:
            data = await loc_r.read(8192*2)
        except:
            log.info(traceback.format_exc())
            data = ''
        log.debug('c >>> %d %s', len(data), data)
        if not data:
            log.info('read 0, conn %d close, quit', name)
            #loc_w.write_eof()
            loc_w.close()
            await loc_w.wait_closed()
            log.debug('remove conn:%d', name)
            name_conns.pop(name)

            pkdata = proto.cmd_close(name)
            log.debug('s <<< cmd_close %d', len(pkdata))
            serv_w.write(pkdata)
            await serv_w.drain()
            return
        
        packdata = proto.pack(name, proto.CMD_SEND, data)
        log.debug('s <<< %d', len(packdata))
        serv_w.write(packdata)
        await serv_w.drain()

async def server_to_local(serv_r, serv_w, local_addr):
    global name_conns, timeout
    loc_r = None
    loc_w = None

    # ping记录
    pings = []

    localip, localport = local_addr
    log.debug('read from server ...')
    while True:
        try:
            head = await asyncio.wait_for(serv_r.readexactly(proto.headlen), timeout=timeout)
        except asyncio.TimeoutError:
            #log.debug('read server timeout, continue')
            tm = str(time.time())
            #log.debug('ping time:%s', tm)
            pings.append(tm)
            pkdata = proto.cmd_ping(data=tm)
            log.debug('s <<< cmd_ping %d', len(pkdata))
            try:
                serv_w.write(pkdata)
                await serv_w.drain()
            except:
                log.debug('send data to server error, quit')
                log.debug(traceback.format_exc())
                serv_w.close()
                await serv_w.wait_closed()
                return
 
            continue
        except exceptions.IncompleteReadError:
            log.info('read server incomplete, thorn close, quit')
            #log.debug(traceback.format_exc())
            serv_w.close()
            await serv_w.wait_closed()
            return

        log.debug('s >>> %s', head)
        if not head:
            log.info('read 0, thorn close, quit')
            serv_w.close()
            await serv_w.wait_closed()
            return

        length, name, cmd = proto.unpack_head(head)
        log.debug(f'length:{length} name:{name} cmd:{cmd}') 
        if length <= 0:
            log.info('length error, thorn close, quit')
            serv_w.close()
            await serv_w.wait_closed()
            return

        try:
            data = await asyncio.wait_for(serv_r.readexactly(length), timeout=timeout*3)
        except asyncio.TimeoutError:
            log.debug('read body %d from server timeout, quit', length)
            return
        except exceptions.IncompleteReadError:
            log.info('read length incomplete, thorn close, quit')
            serv_w.close()
            await serv_w.wait_closed()
            return

        if cmd == proto.CMD_PING:
            pkdata = proto.cmd_pong(name, data)
            serv_w.write(pkdata)
            await serv_w.drain()
            continue
        elif cmd == proto.CMD_PONG:
            #log.debug('pong data:%s', data)
            now = time.time()
            log.debug('latency:%f', now-float(data))
            pongdata = data.decode('utf-8')
            for i in range(0, len(pings)):
                if pings[i] == pongdata:
                    pings = pings[i+1:]
                    break
            else:
                log.debug('not found ping data, contiue')

            continue
            
        #log.debug('s >>> %d %s', len(data), data)
        loc_r, loc_w = None, None
        if name in name_conns:
            loc_r, loc_w = name_conns[name]

        if not loc_w or loc_w.is_closing():
            log.debug('connect to %s:%d', localip, localport)
            loc_r, loc_w = await asyncio.open_connection(localip, localport)
            log.debug('connected')
            name_conns[name] = (loc_r, loc_w)
            t = asyncio.create_task(from_local(name, serv_w))
            #await t

        log.debug('c <<< %d ^', len(data))
        loc_w.write(data)
        await loc_w.drain()


async def client(user, server_addr, local_addr):
    while True:
        await asyncio.sleep(1)
        log.warning('connect to {0}:{1}'.format(*server_addr))
        serv_r, serv_w = await asyncio.open_connection(server_addr[0], server_addr[1])

        while True:
            s = 'AUTH {0}\r\n'.format(user)
            log.debug('s <<< %s', s)
            serv_w.write(s.encode('utf-8'))

            try:
                ln = await asyncio.wait_for(serv_r.readline(), timeout=30)
                if not ln:
                    log.info('readline 0, restart')
                    try:
                        serv_w.close()
                        await serv_w.wait_closed()
                    except:
                        log.debug(traceback.format_exc())
                    break
            except asyncio.TimeoutError:
                log.info('read server timeout, restart')
                try:
                    serv_w.close()
                    await serv_w.wait_closed()
                except:
                    log.debug(traceback.format_exc())
                break

            log.debug('s >>> %s', ln)
            ret = ln[:2]
            if ret == b'OK':
                break
            else:
                log.info('auth error: %s', ln)
                return
       
        log.info('ok, ready ...')
        await server_to_local(serv_r, serv_w, local_addr)


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


