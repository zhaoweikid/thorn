# coding: utf-8
import os, sys
import struct
import time

CMD_PING = 1
CMD_PONG = 2 
CMD_SEND = 10 
CMD_CLOSE = 3

headtypes = 'IIB'
headlen = struct.calcsize(headtypes)

def pack(name, cmd, data):
    # length(4B) + name(4B) + command(1B)
    if isinstance(data, str):
        data = data.encode('utf-8')
    return struct.pack(headtypes, len(data), int(name), cmd) + data

def pack_head(name, cmd, datalen):
    return struct.pack(headtypes, datalen, int(name), cmd)

def unpack_head(rawdata):
    return struct.unpack(headtypes, rawdata)

def auth(user, byte=False):
    s = 'AUTH {}\r\n'.format(user)
    if byte:
        return s.encode('utf-8')
    return s

def cmd_ping(name=4294967295, data=None):
    if not data:
        data = str(time.time())
    return pack(name, CMD_PING, data)

def cmd_pong(name, data):
    return pack(name, CMD_PONG, data)

def cmd_close(name):
    return pack_head(name, CMD_CLOSE, 0)

def ok(byte=False):
    if byte:
        return b'OK\r\n'
    else:
        return 'OK\r\n'

def error(err, byte=False):
    s = 'ERR {}\r\n'.format(err)
    if byte:
        return s.encode('utf-8')
    return s

def unpack(s):
    p = s.strip().split(None, 1)
    if len(p) == 1:
        p.append('')
    elif len(p) != 2:
        raise ValueError('data error')
    return p





