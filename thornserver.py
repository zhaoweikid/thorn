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

async def from_thornclient(th_r, th_w, port):
    global relay_map

    item = relay_map[port]

    async def closeall():
        try:
            th_w.close()
            await th_w.wait_closed()
            for x in item['clients'].values():
                r, w = x
                r.feed_eof()
                w.close()
                await w.wait_closed()
        
            server = item['server']
            server.close()
            await server.wait_closed()
        except:
            log.debug(traceback.format_exc())


    try:
        fd = th_r._transport._sock_fd
        log.debug('wait data from thorn ...')
        while True:
            head = await th_r.readexactly(proto.headlen)
            log.debug('thorn %d >>> %s', fd, head)
            if not head:
                log.info('thorn read null, thorn close, quit')
                closeall()
                break
            length, name, cmd = proto.unpack_head(head)

           
            if length <= 0:
                log.info('length error, thorn close, quit')
                closeall()
                break

            data = await th_r.readexactly(length)
            log.debug('thorn %d >>> %d', fd, len(data))

            if cmd == proto.CMD_PING:
                th_w.write(proto.cmd_pong(name))
                await th_w.drain()
                continue
            elif cmd == proto.CMD_PONG:
                continue
 
           
            cli = item['clients'].get(name)
            if not cli:
                log.debug('not found name %d, skip', name)
                continue
            cli_r, cli_w = cli

            if cli_w.is_closing():
                log.info('client %d closed, skip', name)
                continue

            try:
                log.debug('client %d <<< %d ^', cli_w._transport._sock_fd, len(data))        
                cli_w.write(data)
                await cli_w.drain()
            except:
                log.info(traceback.format_exc())
                break
    except:
        log.debug(traceback.format_exc())

    log.debug('from_thornclient complete!!!')

async def to_thornclient(cli_r, cli_w, th_w, sn):
    log.debug('wait data from client:%d ...', sn)
    try:
        while True:
            data = await cli_r.read(8192*2)
            log.debug('client %d >>> %d', sn, len(data))        
            if not data:
                log.info('read 0, client conn close, quit')
                #if not th_w.is_closing():
                #    th_w.close()
                if not cli_w.is_closing():
                    cli_w.close()
                    await cli_w.wait_closed()
                break
            packdata = proto.pack(sn, proto.CMD_SEND, data)
            log.debug('thorn %d <<< %d', sn, len(packdata))
            th_w.write(packdata)
            await th_w.drain()
    except:
        log.debug(traceback.format_exc())

    log.debug('to_thornclient complete!!!')


async def relay_msg(reader, writer):
    try:
        addr = writer._transport._sock.getsockname()
        #log.debug('sockname: %s', addr)
        port = addr[1]

        item = relay_map[port]
        th_r, th_w = item['thorn']
        
        item['sn'] += 1

        sn = port * 10000 + item['sn'] % 10000

        item['clients'][sn] = [reader, writer]

        log.info('new relay client:%s name:%d', writer._transport._sock.getpeername(), sn)
        t = asyncio.create_task(to_thornclient(reader, writer, th_w, sn))
        await t

        item['clients'].pop(sn) 
    except:
        log.debug(traceback.format_exc())
    log.debug('relay_msg complete')


async def relay_server(cf, th_r, th_w):
    global relay_map
    port = cf['port']

    log.info('relay server start at {0}:{1}'.format(config.HOST[0], port)) 
    relay_serv = await asyncio.start_server(relay_msg, 
        config.HOST[0], port)

    relay_map[port] = {
        'thorn':[th_r, th_w], 
        'server':relay_serv,
        'sn':1,
        'clients':{} # {no:[r, w]}
    }
    
    # 从thorn客户端转发消息给接入方
    t = asyncio.create_task(from_thornclient(th_r, th_w, port))

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
       
            if cf['port'] not in relay_map:
                ret = proto.ok()
                log.info(f'thorn << {ret}')
                serv_writer.write(ret.encode('utf-8'))
                await serv_writer.drain()
            else:
                ret = proto.error('another thorn connected')
                log.info(f'thorn << {ret}')
                serv_writer.write(ret.encode('utf-8'))
                await serv_writer.drain()
                continue
            
            log.debug('relay_server ...')
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


