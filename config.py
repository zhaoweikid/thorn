# coding: utf-8
 
#日志文件, 文件路径，或者stdout表示输出到标准输出
LOGFILE = 'stdout'

# 允许连接的ip，为空表示无限制, 每项为一个python正则表达式
ALLOW_IP = []

# 服务监听IP和端口，仅供thron连接
HOST = ('0.0.0.0', 8080)

# 客户端配置，包含映射的端口
CLIENT = {
    'user123': {
        'local': 9090,
        'remote': 2020
    }
}


