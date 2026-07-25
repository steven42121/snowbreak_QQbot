# Snowbreak QQ Bot

尘白禁区QQ机器人 - 基于AstrBot + NapCat的群管理工具

## 功能

### AstrBot插件 (`astrbot_plugin_group_utils`)
- **定时提醒** - 自定义cron定时任务，支持多群提醒
- **JM漫画下载** - 通过QQ消息触发下载JM漫画
- **群内容过滤** - 关键词自动过滤/撤回
- **任务管理** - 添加/删除/查看定时任务
- **帮助系统** - `/helpgroup` 查看帮助

### 命令列表
| 命令 | 说明 |
|------|------|
| `/jm 漫画号` | 下载JM漫画 |
| `/jmhelp` | JM下载帮助 |
| `/listtask` | 查看所有定时任务 |
| `/addtask` | 添加定时任务 |
| `/deltask` | 删除定时任务 |
| `/addfilter` | 添加过滤词 |
| `/delfilter` | 删除过滤词 |
| `/listfilter` | 查看过滤词列表 |
| `/helpgroup` | 查看帮助 |

### JM漫画下载脚本
独立Python脚本，用于批量下载JM漫画并转换为PDF：
- `a手动漫画批量下载器.py` - 批量下载
- `b手动下载&随机10漫画.py` - 手动/随机下载
- `c随机10数字（不重复）.py` - 生成随机数
- `d单文件夹手动转化pdf.py` - 转换PDF
- `e长篇漫画合并pdf.py` - 合并PDF

## 安装

### 1. 安装NapCat (QQ协议端)
参考 [NapCat文档](https://napneko.github.io/)

### 2. 安装AstrBot
```bash
# Windows
astrbot run

# 或使用Docker
docker run -p 6185:6185 -p 6186:6186 soulter/astrbot:latest
```

### 3. 安装插件
将 `astrbot_plugin_group_utils` 文件夹复制到AstrBot插件目录：
```
~/.astrbot/data/plugins/
```

### 4. 配置插件
在AstrBot WebUI中配置插件的cron任务和过滤词。

## 依赖
- Python 3.8+
- AstrBot v4.x
- NapCat
- Pillow (图片处理)

## 项目结构
```
├── astrbot_plugin_group_utils/    # AstrBot插件
│   ├── main.py                    # 插件主代码
│   ├── metadata.yaml              # 插件元数据
│   ├── _conf_schema.json          # 配置schema
│   ├── requirements.txt           # Python依赖
│   └── README.md                  # 插件说明
├── 下载jm/                         # JM漫画下载脚本
├── 尘白禁区攻略/                    # 游戏攻略文档
└── NapCat.Shell/                  # NapCat协议端(已配置)
```

## 注意事项
- 本项目仅供学习交流使用
- 请遵守相关法律法规和平台规则
- 插件不依赖 `img2pdf`/`pikepdf`，避免与其他插件冲突

## License
[AGPL-3.0](LICENSE) - 禁止商用
