# OpenWRT TeleBot (Python)


## Requirements
- OpenWRT 24.x
- Python 3 (`opkg update && opkg install python3`)
- curl (for helpers)


## Install
```sh
mkdir -p /opt/openwrt-telebot/{bot,config,plugins,helpers,events,init.d}
# copy files from this repo into /opt/openwrt-telebot with the same layout
chmod +x /opt/openwrt-telebot/bot/main.py
chmod +x /opt/openwrt-telebot/plugins/*.sh
chmod +x /opt/openwrt-telebot/helpers/tele-notify


# configure
vi /opt/openwrt-telebot/config/config.json


# service
cp /opt/openwrt-telebot/init.d/openwrt-telebot /etc/init.d/openwrt-telebot
chmod +x /etc/init.d/openwrt-telebot
/etc/init.d/openwrt-telebot enable
/etc/init.d/openwrt-telebot start
