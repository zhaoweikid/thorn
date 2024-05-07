# coding: utf-8
import os, sys
import struct

CMD_SEND = 1
CMD_CLOSE = 2


headlen = 9

def pack(name, cmd, data):
    # length(4B) + name(4B) + command(1B)
    return struct.pack('IIB', len(data), int(name), cmd) + data

def pack_head(name, cmd, datalen):
    return struct.pack('IIB', datalen, int(name), cmd)

def unpack_head(rawdata):
    return struct.unpack('IIB', rawdata)

def auth(user, byte=False):
    s = 'AUTH {}\r\n'.format(user)
    if byte:
        return s.encode('utf-8')
    return s

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





