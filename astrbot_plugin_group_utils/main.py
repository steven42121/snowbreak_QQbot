"""
尘白禁区QQ机器人
功能：定时提醒、JM漫画下载、社区内容过滤、群管理
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

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from group_management import GroupManagement
from verify import UIDVerifier
from content_moderation import ContentModerator


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

        # 群管理相关配置
        self.enable_group_management = config.get("enable_group_management", True)
        self.enable_uid_verify = config.get("enable_uid_verify", True)
        self.enable_content_moderation = config.get("enable_content_moderation", True)
        self.moderation_keywords = config.get("moderation_keywords", [
            "黄", "色", "代", "练", "外挂", "代打", "卖号", "QQ群", "加群"
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

        # 初始化群管理模块
        self.group_management = GroupManagement()
        self.uid_verifier = UIDVerifier()
        self.content_moderator = ContentModerator()

        # 尝试设置LLM提供者
        try:
            provider = self.context.get_using_provider()
            if provider:
                self.content_moderator.set_llm_provider(provider)
                logger.info("已设置LLM提供者用于内容审核")
        except Exception as e:
            logger.warning(f"设置LLM提供者失败: {e}")

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
        """社区内容过滤器 + 群管理"""
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

        # 群管理：检测进群事件
        if self.enable_group_management:
            raw = event.message_obj.raw_message
            if raw is not None:
                # 兼容 aiocqhttp (dict) 和 qq_official (object)
                if isinstance(raw, dict):
                    post_type = raw.get("post_type")
                    notice_type = raw.get("notice_type")
                    user_id = raw.get("user_id")
                    group_id = raw.get("group_id")
                else:
                    post_type = getattr(raw, "post_type", None)
                    notice_type = getattr(raw, "notice_type", None)
                    user_id = getattr(raw, "user_id", None)
                    group_id = getattr(raw, "group_id", None)
                if post_type == "notice" and notice_type == "group_increase":
                    await self._on_member_increase(event, user_id, group_id)

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
                "【尘白禁区QQ机器人】\n"
                "/jm <ID> - 下载漫画\n"
                "/jmhelp - 下载帮助\n"
                "/addfilter <词> - 添加过滤词\n"
                "/delfilter <词> - 删除过滤词\n"
                "/listfilter - 查看过滤词\n"
                "/listtask - 查看定时任务\n"
                "/addtask <周几/每天> <时间> <内容> - 添加任务\n"
                "/deltask <编号> - 删除任务\n"
                "/unlock <QQ号> - 解除禁言\n"
                "/violations - 查看违规记录\n"
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

    @filter.command("unlock")
    async def unlock_user(self, event: AstrMessageEvent):
        """解除禁言（管理员）"""
        parts = event.message_str.strip().split()
        if len(parts) < 2:
            yield event.plain_result("用法：/unlock <QQ号>")
            return

        user_id = int(parts[1])
        group_id = event.message_obj.group_id

        # 检查权限
        if not self._is_admin(event.platform, group_id, event.message_obj.sender.user_id):
            yield event.plain_result("只有管理员可以执行此操作")
            return

        success = await self.group_management.unmute_user(event.platform, group_id, user_id)
        if success:
            self.group_management.mark_verified(group_id, user_id)
            yield event.plain_result(f"已解除用户 {user_id} 的禁言")
        else:
            yield event.plain_result("解除禁言失败")

    @filter.command("violations")
    async def show_violations(self, event: AstrMessageEvent):
        """查看违规记录"""
        group_id = event.message_obj.group_id
        violations = self.group_management.violations

        if not violations:
            yield event.plain_result("当前没有违规记录")
            return

        lines = ["【违规记录】"]
        for key, data in violations.items():
            if key.startswith(f"{group_id}:"):
                user_id = key.split(":")[1]
                count = data["count"]
                lines.append(f"QQ {user_id}: {count}次违规")

        yield event.plain_result("\n".join(lines))

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def screenshot_verify(self, event: AstrMessageEvent):
        """截图验证"""
        if not self.enable_group_management or not self.enable_uid_verify:
            return

        group_id = event.message_obj.group_id
        user_id = event.message_obj.sender.user_id

        # 检查是否已验证
        if self.group_management.is_verified(group_id, user_id):
            return

        # 获取消息中的图片
        images = event.message_obj.message
        if not images:
            return

        for comp in images:
            if hasattr(comp, 'url') and comp.url:
                # OCR识别UID
                text = await self.uid_verifier.recognize_image(comp.url)
                uid = self.uid_verifier.extract_uid(text)

                if uid:
                    result = self.uid_verifier.validate_uid(uid)

                    if result["type"] == "normal":
                        # 解除禁言
                        await self.group_management.unmute_user(event.platform, group_id, user_id)
                        self.group_management.mark_verified(group_id, user_id)
                        yield event.plain_result(f"UID {uid} 验证通过，已解除禁言")

                    elif result["type"] == "new_account":
                        # 标记待审核
                        self.group_management.mark_pending_review(group_id, user_id, uid)
                        yield event.plain_result(
                            f"UID {uid} 验证通过，但这是新号（UID 400-1000万）\n"
                            "已通知管理员进行审核"
                        )

                    else:
                        yield event.plain_result("UID无效，新注册QQ号需联系管理员")

                    break
                else:
                    yield event.plain_result("未识别到有效UID，请重新截图")

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def content_moderation(self, event: AstrMessageEvent):
        """敏感内容检测"""
        if not self.enable_group_management or not self.enable_content_moderation:
            return

        message_str = event.message_str
        if not message_str:
            return

        result = await self.content_moderator.check_content(
            message_str,
            self.moderation_keywords
        )

        if not result["safe"]:
            group_id = event.message_obj.group_id
            user_id = event.message_obj.sender.user_id

            # 记录违规
            self.group_management.record_violation(group_id, user_id, result["reason"])

            # 获取违规次数
            count = self.group_management.get_violation_count(group_id, user_id)

            if count >= 3:
                # 永久禁言+踢出
                await self.group_management.mute_user(event.platform, group_id, user_id, 0)
                await self.group_management.kick_user(event.platform, group_id, user_id)
                yield event.plain_result(f"用户 {user_id} 累计违规{count}次，已永久禁言并踢出")
            elif count >= 1:
                # 全体禁言12小时
                await self.group_management.global_mute(event.platform, group_id, 43200)
                yield event.plain_result(f"检测到敏感内容，已全体禁言12小时（违规{count}次）")
            else:
                yield event.plain_result("检测到敏感内容，请注意群规")

    async def _on_member_increase(self, event: AstrMessageEvent, user_id, group_id):
        """处理新成员进群事件"""
        if not self.enable_uid_verify:
            return

        user_id = str(user_id)
        group_id = str(group_id)

        # 禁言新人
        await self.group_management.mute_user(event.platform, group_id, int(user_id), 0)
        await event.send(event.plain_result(
            f"欢迎新成员！\n"
            "请发送UID截图进行验证（游戏内个人资料截图）\n"
            "验证通过后将自动解除禁言"
        ))

    def _is_admin(self, platform, group_id: int, user_id: int) -> bool:
        """检查是否为管理员"""
        try:
            client = platform.get_client()
            member_list = asyncio.run_coroutine_threadsafe(
                client.api.call_action('get_group_member_list', group_id=group_id),
                asyncio.get_event_loop()
            ).result(timeout=10)

            for member in member_list:
                if member['user_id'] == user_id:
                    return member['role'] in ['admin', 'owner']
        except Exception as e:
            logger.error(f"检查管理员权限失败: {e}")
        return False
