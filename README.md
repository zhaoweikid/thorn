## thorn

thorn是一个简单的内网穿透工具

thorn为内网服务器上运行，thornserver在外网服务器上运行


启动方式：

内网服务器运行：

python3 thron.py user server-ip:server-port local-ip:local-port

外网服务器运行:

python3 thronserver.py

访问外网服务器的端口会自动转发到内网服务器的实现端口转发


服务器配置，在config.py

