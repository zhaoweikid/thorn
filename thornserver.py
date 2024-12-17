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
# 网络读超时时间,s
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
            if self.server:
                self.server.close()
                await self.server.wait_closed()
                self.server = None

            if self.clients:
                await self.close_allclient()
          
            log.debug('close thorn')
            if self.thorn_r:
                self.thorn_r.feed_eof()

            if self.thorn_w:
                self.thorn_w.close()
                await self.thorn_w.wait_closed()
                self.thorn_r = None
                self.thorn_w = None
            
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
    item = relay_map.get(port)
    if not item:
        log.warning('get relayinfo in relay_map error: %d', port)
        return
    
    try:
        log.debug('wait data from thorn ...')
        while True:
            try:
                start = time.time()
                head = await asyncio.wait_for(th_r.readexactly(proto.headlen), timeout=60)
                log.debug('thorn >>> %s', head)
            except asyncio.TimeoutError:
                log.debug('thorn read timeout, continue')
                if time.time() - start < 1:
                    # 未知问题，快速超时了
                    log.info('timeout too quickly, quit')
                    break
                continue

            if not head:
                log.info('thorn read null, thorn close, quit')
                break

            length, name, cmd = proto.unpack_head(head)

            if length < 0:
                log.info('length error, thorn %d close, quit', name)
                break

            if length > 0:
                try:
                    data = await asyncio.wait_for(th_r.readexactly(length), timeout=timeout*3)
                    log.debug('thorn >>> %d', len(data))
                except asyncio.TimeoutError:
                    log.debug('thorn read body timeout, quit')
                    break
            else:
                data = ''
                log.debug('thorn cmd %d no data', cmd)

            # ping只是用来在thorn和server之间保持连接
            if cmd == proto.CMD_PING:
                log.debug('cmd ping:%s', data)
                th_w.write(proto.cmd_pong(name, data))
                await th_w.drain()
                continue
            elif cmd == proto.CMD_PONG: # 以后处理
                log.debug('cmd pong:%s', data)
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
                log.info('client %s write error', name)
                log.info(traceback.format_exc())
                break
    except:
        log.debug(traceback.format_exc())
    finally:
        await item.close()

        try:
            log.debug('serving: {}'.format(item.server.is_serving()))
            item.thorn_task.cancel()
        except:
            log.debug(traceback.format_exc())

    log.debug('%d from_thorn return!!!', port)

async def from_client(cli_r, cli_w, th_w, clisn, port):
    '''从client转发数据给thorn'''
    log.debug('wait data from client:%d ...', clisn)
    global timeout, relay_map

    item = relay_map.get(port)
    if not item:
        log.warning('get relayinfo in relay_map error: %d', port)
        return
    waitn = 3

    try:
        while True:
            try:
                data = await asyncio.wait_for(cli_r.read(8192*2), timeout=timeout)
            except asyncio.TimeoutError:
                waitn -= 1
                if waitn > 0:
                    log.debug('read client %d timeout, waitn:%d, continue', clisn, waitn)
                    continue
                else:
                    log.debug('read client %d timeout, waitn:%d, close', clisn, waitn) 
                    break
            
            log.debug('client %d >>> %d', clisn, len(data))        
            if not data:
                log.info('read 0, client %d close, quit', clisn)
                break
            waitn = 3
            pkdata = proto.pack(clisn, proto.CMD_SEND, data)
            log.debug('thorn %d <<< %d', clisn, len(pkdata))
            th_w.write(pkdata)
            await th_w.drain()
    except:
        log.debug(traceback.format_exc())
    finally:
        await item.close_client(clisn)

    log.debug('from_client return !!! %d', clisn)


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
        await from_client(reader, writer, th_w, sn, port)
    except:
        log.debug(traceback.format_exc())
    finally:
        if not writer.is_closing():
            log.debug('close writer')
            writer.close()
            await writer.wait_closed()

        if sn in item.clients:
            log.debug('pop %d', sn)
            item.clients.pop(sn)

    log.debug('%d relay_msg complete, close', sn)


async def relay_server(cf, th_r, th_w):
    global relay_map
    port = cf['port']

    log.info('relay server start at {0}:{1}'.format(config.HOST[0], port)) 
    try:
        relay_serv = await asyncio.start_server(relay_msg, 
            config.HOST[0], port)
    except:
        log.info('server %s:%d start failed, quit', config.HOST[0], port)
        log.debug(traceback.format_exc())
        return

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
        finally:
            log.debug('%d relay_server closed', port)   
            try:
                if port in relay_map:
                    try:
                        x = relay_map.pop(port)
                        x.close()
                    except:
                        log.info(traceback.format_exc())
            except:
                log.debug(traceback.format_exc())

    log.debug('%d relay_server complete!!!', port)


async def server_msg(serv_reader, serv_writer):
    global relay_map

    try:
        peer = serv_writer._transport._sock.getpeername()
        log.debug('thorn from %s:%d', peer[0], peer[1])

        timeout = 30

        while True:
            try:
                ln = await asyncio.wait_for(serv_reader.readline(), timeout=timeout)
            except asyncio.TimeoutError:
                log.debug('thorn read timeout:%d, quit', timeout)
                break
            
            log.debug('thorn >> %s', ln)
            if not ln:
                log.debug('thorn read null, close')
                break

            if len(ln) >= 50:
                log.debug('command too long, close')
                break

            if ln[:4] != b'AUTH':
                log.debug('command name error, close')
                break

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
                
                log.debug('command error, close')
                break
    except:
        log.debug(traceback.format_exc())
    finally:
        serv_writer.close()
        await serv_writer.wait_closed()

    log.debug('%s:%d server_msg complete', peer[0], peer[1])

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


