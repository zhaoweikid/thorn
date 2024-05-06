## easyproxy

thron是一个简单的内网穿透工具

thron为内网服务器上运行，thronserver在外网服务器上运行


启动方式：

内网服务器运行：
python3 thron.py

外网服务器运行:
python3 thronserver.py

访问外网服务器的端口会自动转发到内网服务器的指定端口实现端口转发


关于配置，见 config.py
