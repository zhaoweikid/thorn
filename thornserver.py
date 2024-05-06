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

# port: reader, writer
relay_map = {}

async def from_thornclient(th_r, th_w, cli_w):
    fd = th_r._transport._sock_fd
    while True:
        head = await th_r.readexactly(proto.headlen)
        log.debug('thorn %d >>> %s', fd, head)
        if not head:
            log.info('thorn read null, thorn close, quit')
            th_w.close()
            await th_w.wait_closed()
            cli_r.feed_eof()
            cli_w.close()
            await cli_w.wait_closed()
            break
        length, name, cmd = proto.unpack_head(head)
        
        if length <= 0:
            log.info('length error, thorn close, quit')
            th_w.close()
            await th_w.wait_closed()
            cli_r.feed_eof()
            cli_w.close()
            await cli_w.wait_closed()
            break

        data = await th_r.readexactly(length)
        log.debug('thorn %d >>> %d', fd, len(data))
        if cli_w.is_closing():
            log.info('client closed!!!')
        try:
            log.debug('client %d <<< %d ^', cli_w._transport._sock_fd, len(data))        
            cli_w.write(data)
            await cli_w.drain()
        except:
            log.info(traceback.format_exc())
            break

    log.debug('from_thornclient complete!!!')

async def to_thornclient(cli_r, cli_w, th_w):
    while True:
        data = await cli_r.read(8192*2)
        log.debug('client %d >>> %d', cli_w._transport._sock_fd, len(data))        
        if not data:
            log.info('read 0, client conn close, quit')
            #if not th_w.is_closing():
            #    th_w.close()
            if not cli_w.is_closing():
                cli_w.close()
                await cli_w.wait_closed()
            break
        packdata = proto.pack(0, proto.CMD_SEND, data)
        log.debug('thorn %d <<< %d', th_w._transport._sock_fd, len(packdata))
        th_w.write(packdata)
        await th_w.drain()

    log.debug('to_thornclient complete!!!')


async def relay_msg(reader, writer):
    addr = writer._transport._sock.getsockname()
    log.debug('sockname: %s', addr)
    port = addr[1]

    thorncli = relay_map[port]
    thstream = thorncli['stream']

    log.info('new relay client: %s', writer._transport._sock.getpeername())
    t1 = asyncio.create_task(to_thornclient(reader, writer, thstream[1]))
    t2 = asyncio.create_task(from_thornclient(thstream[0], thstream[1], writer))  
    await t1

    log.debug('to_thornclient task waited.')
    t2.cancel()
    #await t2
    log.info('task complete')

    #thorncli['server'].close()



async def relay_server(cf, cli_r, cli_w):
    global relay_map
    port = cf['remote']

    item = relay_map.get(port)

    if not item:
        log.info('relay server start at {0}:{1}'.format(config.HOST[0], cf['remote'])) 
        relay_serv = await asyncio.start_server(relay_msg, 
            config.HOST[0], cf['remote'])

        relay_map[port] = {'stream':[cli_r, cli_w], 'server':relay_serv}
        async with relay_serv:
            await relay_serv.serve_forever()
    else:
        item['stream'] = [cli_r, cli_w]
        log.info('relay server reuse at {0}:{1}'.format(config.HOST[0], cf['remote'])) 




async def server_msg(serv_reader, serv_writer):
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

            ret = proto.ok()
            log.info(f'thorn << {ret}')
            serv_writer.write(ret.encode('utf-8'))
            await serv_writer.drain()
            
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


