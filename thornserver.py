# coding: utf-8
import asyncio
import os, sys
import struct
import socket
import time
import traceback
import config
from errno import EAGAIN, EBADF, EPIPE, ECONNRESET
import proto
import logger
import logging

# port: {'stream':(reader, writer), 'no':1}
relay_map = {}
# 网络读超市时间,s
timeout = 30

class RelayInfo (object):
    def __init__(self, thorn_r, thorn_w, server, port):
        self.thorn_r = thorn_r
        self.thorn_w = thorn_w
        self.server = server
        
        self.thorn_task = None

        self.port = port
        self.sn = 1
        # name: (r, w)
        self.clients = {}

    async def close(self):
        global relay_map
        try:
            log.debug('close relay server')
            self.server.close()
            await self.server.wait_closed()

            await self.close_allclient()
          
            log.debug('close thorn')
            self.thorn_r.feed_eof()
            self.thorn_w.close()
            await self.thorn_w.wait_closed()
            
            log.debug('all closed')
            try:
                relay_map.pop(self.port)
            except:
                pass
        except:
            log.debug(traceback.format_exc())

    async def close_allclient(self):
        log.debug('close allclient')
        try:
            for r,w in list(self.clients.values()):
                r.feed_eof()
                w.close()
                await w.wait_closed()
            self.clients = {}
        except:
            log.debug(traceback.format_exc())
        
    async def close_client(self, name):
        try:
            log.debug('close client:%d', name)
            c = self.clients.get(name)
            if c:
                c[0].feed_eof()
                c[1].close()
                await c[1].wait_closed()
                log.debug('pop:%d', name)
                self.clients.pop(name)
        except:
            log.debug(traceback.format_exc())


async def from_thorn(th_r, th_w, port):
    '''从thorn读取数据发送给client'''
    global relay_map, timeout
    item = relay_map[port]
    
    try:
        log.debug('wait data from thorn ...')
        while True:
            try:
                head = await asyncio.wait_for(th_r.readexactly(proto.headlen), timeout=60)
                log.debug('thorn >>> %s', head)
            except asyncio.TimeoutError:
                log.debug('thorn read timeout, continue')
                continue

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
                try:
                    data = await asyncio.wait_for(th_r.readexactly(length), timeout=timeout*3)
                    log.debug('thorn >>> %d', len(data))
                except asyncio.TimeoutError:
                    log.debug('thorn read body timeout, quit')
                    await item.close()
                    break
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
        await item.close()
    try:
        log.debug('serving: {}'.format(item.server.is_serving()))
        item.thorn_task.cancel()
    except:
        log.debug(traceback.format_exc())
    log.debug('from_thorn complete!!!')

async def from_client(cli_r, cli_w, th_w, clisn, port):
    '''从client转发数据给thorn'''
    log.debug('wait data from client:%d ...', clisn)
    global timeout, relay_map

    item = relay_map[port]

    try:
        while True:
            try:
                data = await asyncio.wait_for(cli_r.read(8192*2), timeout=timeout)
            except asyncio.TimeoutError:
                log.debug('read client %d timeout, continue', clisn)
                continue
            
            log.debug('client %d >>> %d', clisn, len(data))        
            if not data:
                log.info('read 0, client %d close, quit', clisn)
                await item.close_client(clisn)
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
        peer = writer._transport._sock.getpeername()
        port = addr[1]

        item = relay_map[port]
        th_r, th_w = item.thorn_r, item.thorn_w
        
        item.sn += 1
        sn = port * 10000 + item.sn % 10000

        item.clients[sn] = [reader, writer]

        log.info('new relay client:%s name:%d', peer, sn)
        t = asyncio.create_task(from_client(reader, writer, th_w, sn, port))
        await t
            
        log.debug('pop %d', sn)
        try:
            item.clients.pop(sn) 
        except:
            pass
    except:
        log.debug(traceback.format_exc())
    log.debug('relay_msg complete')


async def relay_server(cf, th_r, th_w):
    global relay_map
    port = cf['port']

    log.info('relay server start at {0}:{1}'.format(config.HOST[0], port)) 
    relay_serv = await asyncio.start_server(relay_msg, 
        config.HOST[0], port)

    ri = RelayInfo(th_r, th_w, relay_serv, port)
    relay_map[port] = ri
    
    # 从thorn客户端转发消息给接入方
    t = asyncio.create_task(from_thorn(th_r, th_w, port))
    ri.thorn_task = t

    async with relay_serv:
        try:
            await relay_serv.serve_forever()
        except:
            log.debug(traceback.format_exc())
    log.debug('relay_server closed')   
    try:
        relay_map.pop(port)
    except:
        log.debug(traceback.format_exc())

    log.debug('relay_server complete!!!')

async def server_msg(serv_reader, serv_writer):
    global relay_map

    peer = serv_writer._transport._sock.getpeername()
    log.debug('thorn from %s:%d', peer[0], peer[1])

    timeout = 30
    while True:
        try:
            ln = await asyncio.wait_for(serv_reader.readline(), timeout=timeout)
        except asyncio.TimeoutError:
            log.debug('thorn read timeout:%d, quit', timeout)
            serv_writer.close()
            await serv_writer.wait_closed()
            return
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
    log = logger.install(config.LOGFILE)

    asyncio.run(server())

if __name__ == '__main__':
    main()


