# coding: utf-8
import os, sys
import datetime
import time
from zbase3.base import logger
import logging

log = logging.getLogger()

class ServerLimit (object):
    def __init__(self, max_conn_cc=5, max_num=5, max_num_sec=60):
        # {ip:{count:x, record:[1,2,3], alive:{}}}
        self.ip_info = {}
        # {ip:expire}
        self.deny_ip = {}
        # max connections per ip
        self.max_conn_cc = max_conn_cc

        self.max_num = max_num
        self.max_num_sec = max_num_sec

    def add(self, clientip, connsn, tm=None):
        if not tm:
            tm = datetime.datetime.now()
        ts = tm.timestamp()

        info = self.ip_info.get(clientip)
        if not info:
            info = {'alive':{}, 'rec':[]}
            self.ip_info[clientip] = info
        info['alive'][connsn] = ts

        self._add_rec(info, ts)

        log.info('add info: %s', info)

    def _add_rec(self, info, ts):
        info['rec'].append(ts)

        if len(info['rec']) > self.max_conn_cc*3:
            info['rec'].pop(0)

    def rec_count(self, clientip, sec=60, tm=None):
        if not tm:
            tm = datetime.datetime.now()
        ts = tm.timestamp()

        info = self.ip_info.get(clientip)
        return self._rec_count(info, ts, sec) 

    def _rec_count(self, info, ts, sec=60):
        start = ts - sec
        n = 0
        for i in range(len(info['rec'])-1, -1, -1):
            if info['rec'][i] >= start:
                n += 1
            else:
                break
        return n
        
    def rm(self, clientip, connsn):
        info = self.ip_info.get(clientip)
        if info:
            try:
                info['alive'].pop(connsn)
            except:
                pass

    def check(self, clientip, connsn, tm=None):
        if not tm:
            tm = datetime.datetime.now()
        ts = tm.timestamp()
 
        deny_exp = self.deny_ip.get(clientip)
        if deny_exp:
            if deny_exp > ts:
                log.info('deny %s expire:%d', clientip, deny_exp)
                return False
            self.deny_ip.pop(clientip)

        info = self.ip_info.get(clientip)
        if info:
            if len(info['alive']) > self.max_conn_cc:
                log.info('max conn exceed:%d', len(info['alive']))
                self._add_rec(info, ts)
                return False

            n = self._rec_count(info, self.max_num_sec)
            if n > self.max_num:
                log.info('max num exceed:%d>%d', n, self.max_num)
                self._add_rec(info, ts)
                return False

        self.add(clientip, connsn, tm)
        return True


def test():
    global log
    log = logger.install('stdout')

    s = ServerLimit(5, 6, 10)
    n = 1
    ip = '127.0.0.1' 
    for i in range(0, 20):
        ret = s.check(ip, str(i))
        print(i, 'check:', ret)
        time.sleep(1)
        print('count:', s.rec_count(ip, 10))
        #s.rm(ip, str(i))

    for i in range(0, 3):
        s.rm(ip, str(i))

    for i in range(0, 10):
        ret = s.check(ip, str(i))
        print(i, 'check:', ret)
        time.sleep(1)
        print('count:', s.rec_count(ip, 10))



if __name__ == '__main__':
    test()




