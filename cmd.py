# coding: utf-8
import os, sys
import asyncio
import traceback
from zbase3.base import logger

class Command (object):
    def __init__(self, reader, writer, timeout=30):
        self.reader = reader
        self.writer = writer
        self.timeout = timeout

    async def write_result(self, code, msg):
        try:
            s = '{0} {1}\r\n'.format(code, msg)
            self.writer.write(s.encode('utf-8'))
            await self.writer.drain()
        except:
            log.info('write result error: %s', traceback.format_exc())

    async def close(self):
        if self.writer:
            self.writer.close()
            await self.writer.wait_closed()
            self.writer = None

        if self.reader:
            self.reader.feed_eof()
            self.reader = None


    async def run(self):
        log.debug('run ...')
        try:
            peer = self.writer._transport._sock.getpeername()
            log.debug('cmd remote from %s:%d', peer[0], peer[1])
            timeout = self.timeout

            cf = getattr(self, 'cmd_', None)

            while True:
                try:
                    ln = await asyncio.wait_for(self.reader.readline(), timeout=timeout)
                except asyncio.TimeoutError:
                    log.debug('cmd read timeout:%d, quit', timeout)
                    break
               
                ln = ln.decode('utf-8')
                log.debug('>> %s', ln)
                if not ln:
                    log.debug('cmd read null, close')
                    break

                if len(ln) >= 100:
                    err = 'command too long, close'
                    log.debug(err)
                    await self.write_result(1, err)
                    break

                cmdparts = ln.strip().split(None, 1)
                name = cmdparts[0]
                param = ''
                if len(cmdparts) == 2:
                    param = cmdparts[1]
                
                f = getattr(self, 'cmd_'+name, None)
                if not f:
                    if not cf:
                        err = 'cmd name error'
                        log.debug(err)
                        await self.write_result(1, err)
                        break
                    else:
                        f = cf

                ret = f(param)
                if isinstance(ret, (list, tuple)):
                    await self.write_result(*ret)
                elif ret is None:
                    await self.write_result(0)
                else:
                    await self.write_result(1, ret)

            await self.close()
        except:
            log.info(traceback.format_exc())

  

class TestCmd (Command):
    def cmd_(self, msg):
        return msg+'!!!'

async def test():
    global log
    log = logger.install('stdout')
    async def test_msg(reader, writer):
        log.debug('test_msg ...')
        c = TestCmd(reader, writer)
        await c.run()
        log.info('client close')

    port = 9999
    serv = await asyncio.start_server(test_msg, '127.0.0.1', port)
    log.info('server started at:%d ...', port)
    async with serv:
        await serv.serve_forever()


if __name__ == '__main__':
    asyncio.run(test())


