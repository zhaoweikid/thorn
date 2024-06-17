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
# 网络读超时
timeout = 30

async def do_close(w):
    try:
        w.close()
        await w.wait_closed()
    except:
        log.debug(traceback.format_exc())

async def do_write(w, data):
    try:
        w.write(data)
        await w.drain()
        return True
    except:
        log.debug('write error: ', traceback.fromat_exc())
        do_close(w)
        return False

class Connection:
    def __init__(self, addr, name='', r=None, w=None, task=None):
        self.addr = addr
        self.name = name
        self.r = r
        self.w = w
        self.task = task

        self._pings = []

    async def open(self):
        self.r = None
        self.w = None
        tryn = 10

        while tryn > 0:
            try:
                log.warning('connect to %s:%d', self.addr[0], self.addr[1])
                self.r, self.w = await asyncio.open_connection(self.addr[0], self.addr[1])
                log.debug('connected')
                return True
            except:
                log.debug('connect error: %s, try %d', traceback.format_exc(), tryn)
                await asyncio.sleep(3)
                tryn -= 1
        return False

    async def readline(self, timeout=30):
        try:
            ln = await asyncio.wait_for(self.r.readline(), timeout=timeout)
            if not ln:
                log.info('readline 0, close')
                await self.close()
                return ''
        except asyncio.TimeoutError:
            log.info('readline timeout, close')
            await self.close()
            return ''
        except:
            log.debug('readline error: %s', traceback.format_exc())
            return ''

    async def readn(self, n, timeout=30):
        try:
            ln = await asyncio.wait_for(self.r.readexactly(n), timeout=timeout)
            if not ln:
                log.info('readexactly 0, close')
                self.close()
                return ''
            return ln
        except asyncio.TimeoutError:
            log.info('readexactly timeout, close')
            await self.close()
            return ''
        except exceptions.IncompleteReadError:
            log.info('readexactly incomplete, close')
            await self.close()
            return ''
        except:
            log.debug('readexactly error: %s', traceback.format_exc())
            return ''

    async def readn_raise_timeout(self, n, timeout=30):
        try:
            ln = await asyncio.wait_for(self.r.readexactly(n), timeout=timeout)
            if not ln:
                log.info('readexactly 0, close')
                self.close()
                return ''
            return ln
        except asyncio.TimeoutError:
            log.info('readexactly timeout')
            raise
        except exceptions.IncompleteReadError:
            log.info('readexactly incomplete, close')
            await self.close()
            return ''
        except:
            log.debug('readexactly error: %s', traceback.format_exc())
            return ''

    async def write(self, data, name=''):
        if not name:
            name = self.name
        if isinstance(data, str):
            data = data.encode('utf-8')

        pkdata = proto.pack(name, proto.CMD_SEND, data)
        log.debug('s <<< %d', len(pkdata))

        return await do_write(self.w, pkdata)

    async def write_data(self, data):
        if isinstance(data, str):
            data = data.encode('utf-8')
        return await do_write(self.w, data)

    async def close(self):
        global name_conns
        await do_close(self.w)
        self.r = None
        self.w = None
        try:
            name_conns.pop(self.name)
        except:
            pass

    async def send_close(self, name=''):
        if not name:
            name = self.name
        pkdata = proto.cmd_close(name)
        log.debug('s <<< cmd_close %d', len(pkdata))
        return await self.write_data(pkdata)

    async def send_auth(self, user):
        s = proto.auth(user)
        log.debug('s <<< %s', s)
        return await self.write_data(s)
    
    async def send_ping(self):
        global timeout
        now = time.time() 
        tm = str(time.time())

        if self._pings:
            noreply = float(self._pings[0])
            if now-noreply > timeout*3: 
                log.debug('server maybe disconnect, quit')
                await self.close()
                return False
        #log.debug('ping time:%s', tm)
        self._pings.append(tm)
        pkdata = proto.cmd_ping(data=tm)
        log.debug('s <<< cmd_ping %d', len(pkdata))

        return await self.write_data(pkdata)

    async def send_pong(self, name, data):
        pkdata = proto.cmd_pong(name, data)
        return await server.write_data(pkdata)

    def apply_pong(self, name, data):
        now = time.time()
        log.debug('latency:%f', now-float(data))
        pongdata = data.decode('utf-8')
        for i in range(0, len(self._pings)):
            if self._pings[i] == pongdata:
                self._pings = self._pings[i+1:]
                return
        else:
            log.debug('not found ping data, contiue')



async def local_to_server(c, server):
    log.debug('read local %d ...', c.name)
    
    while True:
        try:
            data = await c.r.read(8192*2)
        except:
            log.info(traceback.format_exc())
            data = ''
        
        #log.debug('c >>> %d %s', len(data), data)
        log.debug('c >>> %d', len(data))
        if not data:
            log.info('read 0, conn %d close, quit', c.name)
            await c.close()

            await server.send_close(c.name)
            return
       
        log.debug('s <<< %d ^', len(data))
        if not await server.write(data, c.name):
            return

async def server_to_local(server, local_addr):
    global name_conns, timeout

    log.debug('read from server ...')
    while True:
        try:
            head = await server.readn_raise_timeout(proto.headlen, timeout=timeout)
        except:
            if not await server.send_ping():
                return
            continue

        #log.debug('s >>> %s', head)
        if not head:
            return

        length, name, cmd = proto.unpack_head(head)
        log.debug(f's >>> {head} {length} {name} {cmd}') 
        if length <= 0:
            log.info('length error, thorn close, quit')
            server.close()
            return

        data = await server.readn(length, timeout=timeout*3)
        if not data:
            return

        if cmd == proto.CMD_PING:
            if not await server.send_pong(name, data):
                return
            continue
        elif cmd == proto.CMD_PONG:
            server.apply_pong(name, data)
            continue
        
        #log.debug('s >>> %d %s', len(data), data)
        log.debug('s >>> %d', len(data))
        c = name_conns.get(name, None)
        if not c or c.w.is_closing():
            c = Connection(addr=local_addr, name=name)
            await c.open()
            t = asyncio.create_task(local_to_server(c, server))
            c.task = t
            name_conns[name] = c

        log.debug('c <<< %d ^', len(data))
        if not await c.write_data(data):
            return


async def client(user, server_addr, local_addr):
    while True:
        await asyncio.sleep(1)
        #log.warning('connect to {0}:{1}'.format(*server_addr))

        server = Connection(addr=server_addr)
        if not await server.open():
            return

        while True:
            if not await server.send_auth(user):
                break

            ln = await server.readline(timeout=timeout)
            if not ln:
                break

            log.debug('s >>> %s', ln)
            ret = ln[:2]
            if ret == b'OK':
                break
            else:
                log.info('auth error: %s', ln)
                return
       
        log.info('ok, ready ...')
        await server_to_local(server, local_addr)


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


