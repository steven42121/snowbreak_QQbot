"""
群聊工具集插件
功能：定时提醒、JM漫画下载、社区内容过滤
"""
import asyncio
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from PIL import Image

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api import logger, AstrBotConfig
import astrbot.api.message_components as Comp
from astrbot.core.utils.astrbot_path import get_astrbot_data_path


class GroupUtilsPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.unified_msg_origins: dict = {}
        self.scheduler_task: Optional[asyncio.Task] = None

        # 下载目录
        self.download_dir = Path(get_astrbot_data_path()) / "plugin_data" / "astrbot_plugin_group_utils" / "jm_downloads"
        self.download_dir.mkdir(parents=True, exist_ok=True)

        # 已使用ID记录
        self.used_ids_file = self.download_dir / "used_ids.txt"

        # 黑名单关键词
        self.blacklist_keywords = config.get("blacklist_keywords", [
            "黑子", "串子", "反串", "带节奏", "引战", "ky",
            "脑残", "智障", "白痴", "废物", "垃圾游戏"
        ])

        # 定时任务配置
        self.schedule_tasks = [
            {"day": 0, "hour": 10, "minute": 0, "msg": "【尘白每周提醒】新一周开始了！记得查看尘白每周商店更新和新活动～"},
            {"day": 1, "hour": 10, "minute": 0, "msg": "【拟想开启提醒】拟想已开启，记得去打哦！"},
            {"day": 3, "hour": 10, "minute": 0, "msg": "【尘白+整活】周四啦～尘白活动继续，整活时间到！v50！"},
            {"day": 6, "hour": 21, "minute": 0, "msg": "【尘白周末提醒】明天就是新的一周了，今晚记得检查商店和活动进度！"},
            {"day": -1, "hour": 22, "minute": 55, "msg": "【睡觉提醒】快11点了，群主喊你睡觉啦！早点休息～"},
        ]

    async def on_load(self):
        logger.info("群聊工具集插件已加载")
        self.scheduler_task = asyncio.create_task(self._scheduler_loop())

    async def terminate(self):
        if self.scheduler_task:
            self.scheduler_task.cancel()

    async def _scheduler_loop(self):
        sent_tasks = set()
        while True:
            try:
                now = datetime.now()
                today_key = now.strftime("%Y-%m-%d")

                for task in self.schedule_tasks:
                    task_key = f"{today_key}_{task['day']}_{task['hour']}_{task['minute']}"
                    if task_key in sent_tasks:
                        continue

                    should_send = False
                    if task["day"] == -1:
                        should_send = now.hour == task["hour"] and now.minute == task["minute"]
                    elif now.weekday() == task["day"]:
                        should_send = now.hour == task["hour"] and now.minute == task["minute"]

                    if should_send:
                        await self._send_scheduled_message(task["msg"])
                        sent_tasks.add(task_key)

                await asyncio.sleep(30)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"定时任务出错: {e}")
                await asyncio.sleep(60)

    async def _send_scheduled_message(self, message: str):
        for group_id, umo in self.unified_msg_origins.items():
            try:
                await self.context.send_message(umo, [Comp.Plain(message)])
                logger.info(f"已发送定时消息到群 {group_id}")
            except Exception as e:
                logger.error(f"发送消息到群 {group_id} 失败: {e}")

    @filter.command("jm")
    async def jm_download(self, event: AstrMessageEvent):
        """JM漫画下载指令"""
        parts = event.message_str.strip().split()
        if len(parts) < 2:
            yield event.plain_result("用法：/jm <漫画ID>")
            return

        try:
            comic_id = int(parts[1])
        except ValueError:
            yield event.plain_result("漫画ID必须是数字")
            return

        yield event.plain_result(f"正在下载漫画 {comic_id}，请稍候...")

        try:
            pdf_path = await self._download_comic(comic_id)
            if pdf_path and pdf_path.exists():
                # 发送PDF文件到群
                yield event.plain_result(f"漫画 {comic_id} 下载完成！")
                yield event.file_result(str(pdf_path), f"{comic_id}.pdf")
            else:
                yield event.plain_result(f"漫画 {comic_id} 下载失败，请检查ID是否正确")
        except Exception as e:
            logger.error(f"下载漫画出错: {e}")
            yield event.plain_result(f"下载出错：{str(e)}")

    async def _download_comic(self, comic_id: int) -> Optional[Path]:
        try:
            from jmcomic import download_album, create_option_by_file, JmcomicException
        except ImportError:
            logger.error("jmcomic库未安装")
            return None

        # 创建配置
        config_path = self.download_dir / "jm_config.yml"
        config_content = f"""
download:
  save_dir: {self.download_dir}
  image_suffix: png
  download_thread: 10
  retry_count: 3
  make_pdf: false
  overwrite: false
  timeout: 60
"""
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(config_content)

        try:
            opt = create_option_by_file(str(config_path))
            loop = asyncio.get_event_loop()
            start_ts = time.time()

            await loop.run_in_executor(None, download_album, str(comic_id), opt)

            # 查找下载的文件夹
            comic_folder = None
            for item in self.download_dir.iterdir():
                if item.is_dir() and item.name.isdigit():
                    if item.stat().st_ctime > start_ts:
                        if comic_folder is None or item.stat().st_ctime > comic_folder.stat().st_ctime:
                            comic_folder = item

            if comic_folder:
                # 生成PDF
                pdf_path = self.download_dir / f"{comic_id}.pdf"
                if self._generate_pdf(comic_folder, pdf_path):
                    self._save_used_id(comic_id)
                    return pdf_path

            return None

        except JmcomicException as e:
            logger.error(f"下载漫画 {comic_id} 失败: {e}")
            return None
        except Exception as e:
            logger.error(f"下载漫画 {comic_id} 出错: {e}")
            return None

    def _natural_sort_key(self, s):
        return [int(c) if c.isdigit() else c.lower() for c in re.split(r"(\d+)", str(s))]

    def _generate_pdf(self, comic_dir: Path, output_path: Path) -> bool:
        """将图片转换为PDF"""
        imgs = []
        for root, _, files in os.walk(comic_dir):
            for f in files:
                if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                    p = os.path.join(root, f)
                    if os.path.getsize(p) > 1024:
                        imgs.append(p)

        imgs.sort(key=self._natural_sort_key)
        if not imgs:
            return False

        try:
            pil_images = []
            for img_path in imgs:
                img = Image.open(img_path)
                if img.mode == "RGBA":
                    img = img.convert("RGB")
                pil_images.append(img)

            if not pil_images:
                return False

            pil_images[0].save(
                str(output_path),
                "PDF",
                save_all=True,
                append_images=pil_images[1:] if len(pil_images) > 1 else []
            )
            return True
        except Exception as e:
            logger.error(f"生成PDF失败: {e}")
            return False

    def _save_used_id(self, comic_id: int):
        ids = set()
        if self.used_ids_file.exists():
            with open(self.used_ids_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.isdigit():
                        ids.add(int(line))
        ids.add(comic_id)
        with open(self.used_ids_file, "w", encoding="utf-8") as f:
            for i in sorted(ids):
                f.write(f"{i}\n")

    @filter.command("jmhelp")
    async def jm_help(self, event: AstrMessageEvent):
        """JM下载帮助"""
        yield event.plain_result("用法：/jm <漫画ID>\n例如：/jm 12345")

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def content_filter(self, event: AstrMessageEvent):
        """社区内容过滤器"""
        if not self.config.get("enable_filter", True):
            return
        message_str = event.message_str
        if not message_str:
            return
        for keyword in self.blacklist_keywords:
            if keyword in message_str:
                yield event.plain_result("检测到不当内容，已被过滤")
                event.stop_event()
                return

    @filter.command("addfilter")
    async def add_filter_word(self, event: AstrMessageEvent):
        """添加过滤关键词"""
        parts = event.message_str.strip().split(maxsplit=1)
        if len(parts) < 2:
            yield event.plain_result("用法：/addfilter <关键词>")
            return
        keyword = parts[1].strip()
        if keyword not in self.blacklist_keywords:
            self.blacklist_keywords.append(keyword)
            self.config["blacklist_keywords"] = self.blacklist_keywords
            self.config.save_config()
            yield event.plain_result(f"已添加：{keyword}")
        else:
            yield event.plain_result("该关键词已存在")

    @filter.command("delfilter")
    async def del_filter_word(self, event: AstrMessageEvent):
        """删除过滤关键词"""
        parts = event.message_str.strip().split(maxsplit=1)
        if len(parts) < 2:
            yield event.plain_result("用法：/delfilter <关键词>")
            return
        keyword = parts[1].strip()
        if keyword in self.blacklist_keywords:
            self.blacklist_keywords.remove(keyword)
            self.config["blacklist_keywords"] = self.blacklist_keywords
            self.config.save_config()
            yield event.plain_result(f"已删除：{keyword}")
        else:
            yield event.plain_result("该关键词不存在")

    @filter.command("listfilter")
    async def list_filter_words(self, event: AstrMessageEvent):
        """列出过滤词"""
        if self.blacklist_keywords:
            yield event.plain_result("过滤词：" + "、".join(self.blacklist_keywords))
        else:
            yield event.plain_result("当前没有过滤词")

    @filter.command("helpgroup")
    async def group_help(self, event: AstrMessageEvent):
        """帮助"""
        # 读取README作为帮助内容
        readme_path = Path(__file__).parent / "README.md"
        if readme_path.exists():
            content = readme_path.read_text(encoding="utf-8")
            # 去掉markdown格式，保留纯文本
            lines = []
            for line in content.split("\n"):
                line = line.strip()
                if line and not line.startswith("#"):
                    lines.append(line)
            yield event.plain_result("\n".join(lines[:50]))  # 限制50行
        else:
            yield event.plain_result(
                "【群聊工具集】\n"
                "/jm <ID> - 下载漫画\n"
                "/jmhelp - 下载帮助\n"
                "/addfilter <词> - 添加过滤词\n"
                "/delfilter <词> - 删除过滤词\n"
                "/listfilter - 查看过滤词\n"
                "/listtask - 查看定时任务\n"
                "/addtask <周几/每天> <时间> <内容> - 添加任务\n"
                "/deltask <编号> - 删除任务\n"
                "/helpgroup - 本帮助"
            )

    @filter.command("listtask")
    async def list_tasks(self, event: AstrMessageEvent):
        """查看定时任务"""
        if not self.schedule_tasks:
            yield event.plain_result("当前没有定时任务")
            return

        weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        lines = ["【定时任务列表】"]
        for i, task in enumerate(self.schedule_tasks):
            day = "每天" if task["day"] == -1 else weekdays[task["day"]]
            time_str = f"{task['hour']:02d}:{task['minute']:02d}"
            lines.append(f"{i+1}. [{day} {time_str}] {task['msg'][:30]}...")
        yield event.plain_result("\n".join(lines))

    @filter.command("addtask")
    async def add_task(self, event: AstrMessageEvent):
        """添加定时任务
        用法：/addtask <周几/每天> <HH:MM> <内容>
        周几：周一~周日 或 0~6
        """
        parts = event.message_str.strip().split(maxsplit=3)
        if len(parts) < 4:
            yield event.plain_result("用法：/addtask <周几/每天> <HH:MM> <内容>\n例如：/addtask 周三 09:30 提醒开会")
            return

        day_str = parts[1]
        time_str = parts[2]
        msg = parts[3]

        # 解析周几
        weekdays = {"周一": 0, "周二": 1, "周三": 2, "周四": 3, "周五": 4, "周六": 5, "周日": 6}
        if day_str == "每天":
            day = -1
        elif day_str in weekdays:
            day = weekdays[day_str]
        elif day_str.isdigit() and 0 <= int(day_str) <= 6:
            day = int(day_str)
        else:
            yield event.plain_result("周几格式错误，请使用：周一~周日、0~6、每天")
            return

        # 解析时间
        try:
            hour, minute = map(int, time_str.split(":"))
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError
        except ValueError:
            yield event.plain_result("时间格式错误，请使用 HH:MM，如 09:30")
            return

        # 添加任务
        new_task = {"day": day, "hour": hour, "minute": minute, "msg": msg}
        self.schedule_tasks.append(new_task)
        self.config["schedule_tasks"] = self.schedule_tasks
        self.config.save_config()

        day_display = "每天" if day == -1 else weekdays.get(day, str(day))
        yield event.plain_result(f"已添加任务：[{day_display} {time_str}] {msg[:30]}...")

    @filter.command("deltask")
    async def del_task(self, event: AstrMessageEvent):
        """删除定时任务"""
        parts = event.message_str.strip().split()
        if len(parts) < 2:
            yield event.plain_result("用法：/deltask <编号>\n用 /listtask 查看编号")
            return

        try:
            idx = int(parts[1]) - 1
        except ValueError:
            yield event.plain_result("编号必须是数字")
            return

        if 0 <= idx < len(self.schedule_tasks):
            removed = self.schedule_tasks.pop(idx)
            self.config["schedule_tasks"] = self.schedule_tasks
            self.config.save_config()
            weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
            day = "每天" if removed["day"] == -1 else weekdays[removed["day"]]
            yield event.plain_result(f"已删除：[{day} {removed['hour']:02d}:{removed['minute']:02d}] {removed['msg'][:30]}...")
        else:
            yield event.plain_result("编号不存在")
