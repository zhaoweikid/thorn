# coding: utf-8
import os, sys
import struct
import socket
import time
import traceback
import config
from errno import EAGAIN, EBADF, EPIPE, ECONNRESET
import logging
import asyncio
import proto
from zbase3.base import logger

# port: {'stream':(reader, writer), 'no':1}
relay_map = {}

class RelayInfo (object):
    def __init__(self, thorn_r, thorn_w, server, port):
        self.thorn_r = thorn_r
        self.thorn_w = thorn_w
        self.server = server
        self.port = port
        self.sn = 1
        # name: (r, w)
        self.clients = {}

    async def close(self):
        try:
            self.server.close()
            await self.server.wait_closed()

            await self.close_allclient()
            
            self.thorn_w.close()
            await self.thorn_w.wait_closed()
        except:
            log.debug(traceback.format_exc())

    async def close_allclient(self):
        for r,w in list(self.clients.values()):
            r.feed_eof()
            w.close()
            await w.wait_closed()
        
    async def close_client(self, name):
        c = self.clients.get(name)
        if c:
            c[1].close()
            await c[1].wait_closed()
            self.clients.pop(name)


async def from_thorn(th_r, th_w, port):
    '''从thorn读取数据发送给client'''
    global relay_map
    item = relay_map[port]

    try:
        log.debug('wait data from thorn ...')
        while True:
            head = await th_r.readexactly(proto.headlen)
            log.debug('thorn >>> %s', head)
            if not head:
                log.info('thorn read null, thorn close, quit')
                await item.close()
                break
            length, name, cmd = proto.unpack_head(head)

            if length < 0:
                log.info('length error, thorn %d close, quit', name)
                await item.close()
                break

            if length > 0:
                data = await th_r.readexactly(length)
                log.debug('thorn >>> %d', len(data))
            else:
                data = ''
                log.debug('thorn cmd %d not have data', cmd)

            # ping只是用来在thorn和server之间保持连接
            if cmd == proto.CMD_PING:
                th_w.write(proto.cmd_pong(name))
                await th_w.drain()
                continue
            elif cmd == proto.CMD_PONG: # 以后处理
                continue
            elif cmd == proto.CMD_CLOSE:
                log.info('cmd close client %d', name)
                await item.close_client(name) 
                continue

            # 找到转发的client
            cli = item.clients.get(name)
            if not cli:
                log.debug('not found name %d, skip', name)
                continue
            cli_r, cli_w = cli

            if cli_w.is_closing():
                log.info('client %d closed, skip', name)
                continue

            try:
                log.debug('client %d <<< %d ^', name, len(data))        
                cli_w.write(data)
                await cli_w.drain()
            except:
                log.info(traceback.format_exc())
                break
    except:
        log.debug(traceback.format_exc())

    log.debug('from_thorn complete!!!')

async def from_client(cli_r, cli_w, th_w, clisn):
    '''从client转发数据给thorn'''
    log.debug('wait data from client:%d ...', clisn)
    try:
        while True:
            data = await cli_r.read(8192*2)
            log.debug('client %d >>> %d', clisn, len(data))        
            if not data:
                log.info('read 0, client %d close, quit', clisn)
                #if not th_w.is_closing():
                #    th_w.close()
                if not cli_w.is_closing():
                    cli_w.close()
                    await cli_w.wait_closed()
                break
            pkdata = proto.pack(clisn, proto.CMD_SEND, data)
            log.debug('thorn %d <<< %d', clisn, len(pkdata))
            th_w.write(pkdata)
            await th_w.drain()
    except:
        log.debug(traceback.format_exc())

    log.debug('from_client complete!!!')


async def relay_msg(reader, writer):
    try:
        addr = writer._transport._sock.getsockname()
        #log.debug('sockname: %s', addr)
        port = addr[1]

        item = relay_map[port]
        th_r, th_w = item.thorn_r, item.thorn_w
        
        item.sn += 1

        sn = port * 10000 + item.sn % 10000

        item.clients[sn] = [reader, writer]

        log.info('new relay client:%s name:%d', writer._transport._sock.getpeername(), sn)
        t = asyncio.create_task(from_client(reader, writer, th_w, sn))
        await t
            
        log.debug('pop %d', sn)
        item.clients.pop(sn) 
    except:
        log.debug(traceback.format_exc())
    log.debug('relay_msg complete')


async def relay_server(cf, th_r, th_w):
    global relay_map
    port = cf['port']

    log.info('relay server start at {0}:{1}'.format(config.HOST[0], port)) 
    relay_serv = await asyncio.start_server(relay_msg, 
        config.HOST[0], port)

    relay_map[port] = RelayInfo(th_r, th_w, relay_serv, port)
    
    # 从thorn客户端转发消息给接入方
    t = asyncio.create_task(from_thorn(th_r, th_w, port))

    async with relay_serv:
        await relay_serv.serve_forever()

    relay_map.pop(port)

    log.debug('relay_server complete!!!')

async def server_msg(serv_reader, serv_writer):
    global relay_map
    while True:
        ln = await serv_reader.readline()
        log.debug('thorn >> %s', ln)
        if not ln:
            log.debug('read null, close')
            return
        cmd, arg = proto.unpack(ln)
        log.debug(f'cmd:{cmd} arg:{arg}')
        if cmd == b'AUTH':
            cf = config.CLIENT.get(arg.decode('utf-8'))
            if not cf:
                ret = proto.error('client error')
                log.info(f'thorn << {ret}')
                serv_writer.write(ret.encode('utf-8'))
                await serv_writer.drain()
                continue
       
            item = relay_map.get(cf['port'])
            if item: # 踢掉原来的连接
                log.debug('thorn exist, kick')
                await item.close()

            ret = proto.ok()
            log.info(f'thorn << {ret}')
            serv_writer.write(ret.encode('utf-8'))
            await serv_writer.drain()
            
            log.debug('relay_server ...')
            # 进入转发的服务处理过程
            await relay_server(cf, serv_reader, serv_writer)
            log.debug('relay_server quit')
            break
        else:
            ret = proto.error('command error')
            log.info(f'thorn << {ret}')
            serv_writer.write(ret.encode('utf-8'))
            await serv_writer.drain()
    
    log.debug('server_msg complete')

async def server():
    serv = await asyncio.start_server(server_msg, 
            config.HOST[0], config.HOST[1])
    log.info('server start at {0}:{1}'.format(*config.HOST))
    async with serv:
        await serv.serve_forever()

def main():
    global log
    log = logger.install('stdout')

    asyncio.run(server())

if __name__ == '__main__':
    main()


