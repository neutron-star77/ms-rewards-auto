#!/bin/sh
# nas_autorun.sh - 放到 NAS 数据卷 /share/CACHEDEV1_DATA/.config/autorun.sh
#
# 作用：QNAP 开机（含固件更新后的首次开机）会自动执行数据卷上的 autorun.sh。
#       固件更新只重置系统分区，数据卷不动，所以本文件在固件更新后依然存在并能触发，
#       进而调用 nas_selfheal.sh 恢复 SSH 公钥 + 两个定时任务 + Web 面板。
#
# 部署（只需在 NAS 上执行一次，固件更新后无需重做）：
#   mkdir -p /share/CACHEDEV1_DATA/.config
#   cp /share/CACHEDEV1_DATA/Web/gadget/microsoft/nas_autorun.sh /share/CACHEDEV1_DATA/.config/autorun.sh
#   chmod +x /share/CACHEDEV1_DATA/.config/autorun.sh
#
# 注意：少数新固件默认不执行 autorun.sh，若发现固件更新后未自动恢复，
#       请再用 QNAP GUI「任务计划 → 用户定义脚本 → 事件=开机」加一条指向
#       /bin/sh /share/CACHEDEV1_DATA/Web/gadget/microsoft/nas_selfheal.sh 的开机任务作兜底。

/bin/sh /share/CACHEDEV1_DATA/Web/gadget/microsoft/nas_selfheal.sh
