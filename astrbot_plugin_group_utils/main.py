# Copyright (c) 2026 Steven - MIT License
# core/bridge.py, core/events.py, core/names.py, core/policy.py
# Based on astrbot_plugin_qq_group_notice by 云云 (MIT License)
"""
尘白禁区QQ机器人
功能：定时提醒、JM漫画下载、社区内容过滤、群管理（进群禁言+欢迎消息）
"""
import asyncio
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Any

from PIL import Image

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api import logger, AstrBotConfig
import astrbot.api.message_components as Comp
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

import sys
sys.path.insert(0, os.path.dirname(__file__))

from group_management import GroupManagement
from verify import UIDVerifier
from content_moderation import ContentModerator
from core.bridge import QQOfficialNoticeBridge


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

        # 欢迎消息配置
        self.enable_welcome = config.get("enable_welcome", True)
        self.welcome_message = config.get("welcome_message", 
            "欢迎新成员！\n请发送UID截图进行验证（游戏内个人资料截图）\n验证通过后将自动解除禁言")

        # 定时任务配置
        self.schedule_tasks = [
            {"day": 0, "hour": 10, "minute": 0, "msg": "【尘白每周提醒】新一周开始了！记得查看尘白每周商店更新和新活动～"},
            {"day": 1, "hour": 10, "minute": 0, "msg": "【拟想开启提醒】拟想已开启，记得去打哦！"},
            {"day": 3, "hour": 10, "minute": 0, "msg": "【尘白+整活】周四啦～尘白活动继续，整活时间到！v50！"},
            {"day": 6, "hour": 21, "minute": 0, "msg": "【尘白周末提醒】明天就是新的一周了，今晚记得检查商店和活动进度！"},
            {"day": -1, "hour": 22, "minute": 55, "msg": "【睡觉提醒】快11点了，群主喊你睡觉啦！早点休息～"},
        ]

        # 群管理模块
        self.group_management = GroupManagement()
        self.uid_verifier = UIDVerifier()
        self.content_moderator = ContentModerator()

        # 进群事件桥（照抄能用的插件）
        self.bridge = QQOfficialNoticeBridge(self._handle_notice, logger.info)
        self.bridge.install_parser_patch()
        logger.info("[尘白机器人] 进群事件桥已安装")

    async def on_load(self):
        logger.info("尘白禁区QQ机器人插件已加载")
        self.scheduler_task = asyncio.create_task(self._scheduler_loop())

        # 尝试设置LLM提供者
        try:
            provider = self.context.get_using_provider()
            if provider:
                self.content_moderator.set_llm_provider(provider)
                logger.info("已设置LLM提供者用于内容审核")
        except Exception as e:
            logger.warning(f"设置LLM提供者失败: {e}")

    @filter.on_platform_loaded()
    async def on_platform_loaded(self):
        await self.bridge.bind_platforms(self.context)
        logger.info("[尘白机器人] 平台绑定完成")

    @filter.on_astrbot_loaded()
    async def on_astrbot_loaded(self):
        await self.bridge.bind_platforms(self.context)

    @filter.on_plugin_loaded()
    async def on_plugin_loaded(self, _metadata: Any):
        await self.bridge.bind_platforms(self.context)

    async def _handle_notice(self, notice_type: str, event: Any, adapter: Any) -> None:
        """处理进群/退群事件"""
        if notice_type == "member_join":
            logger.info(f"[尘白机器人] 收到进群事件")
            
            # 获取群ID和成员ID
            group_id = getattr(event, "group_openid", "") or getattr(event, "group_id", "")
            member_id = getattr(event, "member_openid", "") or getattr(event, "user_openid", "")
            
            logger.info(f"[尘白机器人] 群: {group_id}, 成员: {member_id}")
            
            if not group_id or not member_id:
                logger.warning(f"[尘白机器人] 事件数据不完整: group={group_id}, member={member_id}")
                return

            # 发送欢迎消息
            if self.enable_welcome:
                await self._send_welcome(adapter, event, group_id, member_id)

            # 禁言新成员
            if self.enable_group_management and self.enable_uid_verify:
                await asyncio.sleep(1)
                success = await self.group_management.mute_user_by_openid(
                    self._get_platform(), group_id, member_id, 0
                )
                logger.info(f"[尘白机器人] 禁言结果: {success}")

        elif notice_type == "member_leave":
            logger.info(f"[尘白机器人] 收到退群事件")

    async def _send_welcome(self, adapter: Any, event: Any, group_id: str, member_id: str) -> None:
        """发送欢迎消息"""
        try:
            api = getattr(adapter, "client", None)
            if api is None:
                logger.error("[尘白机器人] 无法获取 API")
                return

            # 获取成员昵称
            member_name = getattr(event, "nickname", "") or getattr(event, "member_nickname", "") or member_id

            # 格式化欢迎消息
            content = self.welcome_message.replace("{member}", member_name)
            content = content.replace("{group}", group_id)

            # 尝试发送消息
            client = getattr(adapter, "client", None)
            if client and hasattr(client, "api"):
                api = client.api
                if hasattr(api, "post_group_message"):
                    await api.post_group_message(
                        group_openid=group_id,
                        msg_type=0,
                        content=content
                    )
                    logger.info(f"[尘白机器人] 已发送欢迎消息到群 {group_id}")
                    return

            # 备用方案：通过 AstrBot 发送
            logger.info(f"[尘白机器人] 尝试通过 AstrBot 发送欢迎消息")

        except Exception as e:
            logger.error(f"[尘白机器人] 发送欢迎消息失败: {e}")

    def _get_platform(self):
        """获取 QQ 官方平台实例"""
        manager = getattr(self.context, "platform_manager", None)
        if manager is None:
            return None

        try:
            adapters = list(manager.get_insts())
        except Exception:
            adapters = list(getattr(manager, "platform_insts", ()) or ())

        for adapter in adapters:
            platform_name = self._get_platform_name(adapter)
            if platform_name in ("qq_official", "qq_official_webhook"):
                return adapter
        return None

    def _get_platform_name(self, adapter):
        try:
            return str(adapter.meta().name)
        except Exception:
            return ""

    async def terminate(self):
        if self.scheduler_task:
            self.scheduler_task.cancel()
        await self.bridge.uninstall()

    # ==================== 定时任务 ====================

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
            except Exception as e:
                logger.error(f"发送消息到群 {group_id} 失败: {e}")

    # ==================== JM 漫画下载 ====================

    @filter.command("jm")
    async def jm_download(self, event: AstrMessageEvent):
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
                yield event.plain_result(f"漫画 {comic_id} 下载完成！")
                yield event.file_result(str(pdf_path), f"{comic_id}.pdf")
            else:
                yield event.plain_result(f"漫画 {comic_id} 下载失败，请检查ID是否正确")
        except Exception as e:
            logger.error(f"下载漫画出错: {e}")
            yield event.plain_result(f"下载出错：{str(e)}")

    async def _download_comic(self, comic_id: int) -> Optional[Path]:
        try:
            from jmcomic import download_album, create_option_by_file
        except ImportError:
            logger.error("jmcomic库未安装")
            return None

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
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(config_content)
        except Exception as e:
            logger.error(f"创建jm配置文件失败: {e}")
            return None

        try:
            opt = create_option_by_file(str(config_path))
            loop = asyncio.get_event_loop()
            start_ts = time.time()

            await loop.run_in_executor(None, download_album, str(comic_id), opt)

            comic_folder = None
            for item in self.download_dir.iterdir():
                if item.is_dir() and item.name.isdigit():
                    if item.stat().st_ctime > start_ts:
                        if comic_folder is None or item.stat().st_ctime > comic_folder.stat().st_ctime:
                            comic_folder = item

            if comic_folder:
                pdf_path = self.download_dir / f"{comic_id}.pdf"
                if self._generate_pdf(comic_folder, pdf_path):
                    self._save_used_id(comic_id)
                    return pdf_path

            return None

        except Exception as e:
            logger.error(f"下载漫画 {comic_id} 出错: {e}")
            return None

    def _natural_sort_key(self, s):
        return [int(c) if c.isdigit() else c.lower() for c in re.split(r"(\d+)", str(s))]

    def _generate_pdf(self, comic_dir: Path, output_path: Path) -> bool:
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
            for img in pil_images:
                img.close()
            return True
        except Exception as e:
            logger.error(f"生成PDF失败: {e}")
            return False

    def _save_used_id(self, comic_id: int):
        ids = set()
        if self.used_ids_file.exists():
            try:
                with open(self.used_ids_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.isdigit():
                            ids.add(int(line))
            except Exception:
                pass
        ids.add(comic_id)
        try:
            with open(self.used_ids_file, "w", encoding="utf-8") as f:
                for i in sorted(ids):
                    f.write(f"{i}\n")
        except Exception:
            pass

    @filter.command("jmhelp")
    async def jm_help(self, event: AstrMessageEvent):
        yield event.plain_result("用法：/jm <漫画ID>\n例如：/jm 12345")

    # ==================== 内容过滤 ====================

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def content_filter(self, event: AstrMessageEvent):
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
        if self.blacklist_keywords:
            yield event.plain_result("过滤词：" + "、".join(self.blacklist_keywords))
        else:
            yield event.plain_result("当前没有过滤词")

    # ==================== 帮助 ====================

    @filter.command("helpgroup")
    async def group_help(self, event: AstrMessageEvent):
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
            "/helpgroup - 本帮助\n"
            "/setwelcome <内容> - 设置欢迎消息\n"
            "/welcome <开关> - 开启/关闭欢迎消息"
        )

    # ==================== 定时任务管理 ====================

    @filter.command("listtask")
    async def list_tasks(self, event: AstrMessageEvent):
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
        parts = event.message_str.strip().split(maxsplit=3)
        if len(parts) < 4:
            yield event.plain_result("用法：/addtask <周几/每天> <HH:MM> <内容>\n例如：/addtask 周三 09:30 提醒开会")
            return

        day_str = parts[1]
        time_str = parts[2]
        msg = parts[3]

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

        try:
            hour, minute = map(int, time_str.split(":"))
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError
        except ValueError:
            yield event.plain_result("时间格式错误，请使用 HH:MM，如 09:30")
            return

        new_task = {"day": day, "hour": hour, "minute": minute, "msg": msg}
        self.schedule_tasks.append(new_task)
        self.config["schedule_tasks"] = self.schedule_tasks
        self.config.save_config()

        day_display = "每天" if day == -1 else weekdays.get(day, str(day))
        yield event.plain_result(f"已添加任务：[{day_display} {time_str}] {msg[:30]}...")

    @filter.command("deltask")
    async def del_task(self, event: AstrMessageEvent):
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

    # ==================== 管理员命令 ====================

    @filter.command("unlock")
    async def unlock_user(self, event: AstrMessageEvent):
        parts = event.message_str.strip().split()
        if len(parts) < 2:
            yield event.plain_result("用法：/unlock <QQ号>")
            return

        try:
            target_user_id = int(parts[1])
        except ValueError:
            yield event.plain_result("QQ号必须是数字")
            return

        group_id = event.message_obj.group_id
        is_admin = await self._is_admin(event.platform, group_id, event.message_obj.sender.user_id)
        if not is_admin:
            yield event.plain_result("只有管理员可以执行此操作")
            return

        success = await self.group_management.unmute_user(event.platform, group_id, target_user_id)
        if success:
            self.group_management.mark_verified(group_id, target_user_id)
            yield event.plain_result(f"已解除用户 {target_user_id} 的禁言")
        else:
            yield event.plain_result("解除禁言失败")

    @filter.command("violations")
    async def show_violations(self, event: AstrMessageEvent):
        group_id = event.message_obj.group_id
        violations = self.group_management.violations

        if not violations:
            yield event.plain_result("当前没有违规记录")
            return

        lines = ["【违规记录】"]
        found = False
        for key, data in violations.items():
            if key.startswith(f"{group_id}:"):
                user_id = key.split(":")[1]
                count = data["count"]
                lines.append(f"QQ {user_id}: {count}次违规")
                found = True

        if not found:
            yield event.plain_result("当前没有违规记录")
        else:
            yield event.plain_result("\n".join(lines))

    # ==================== 欢迎消息设置 ====================

    @filter.command("setwelcome")
    async def set_welcome(self, event: AstrMessageEvent):
        parts = event.message_str.strip().split(maxsplit=1)
        if len(parts) < 2:
            yield event.plain_result("用法：/setwelcome <欢迎消息>\n支持 {member} {group} 占位符")
            return

        self.welcome_message = parts[1].strip()
        self.config["welcome_message"] = self.welcome_message
        self.config.save_config()
        yield event.plain_result(f"欢迎消息已设置：\n{self.welcome_message}")

    @filter.command("welcome")
    async def toggle_welcome(self, event: AstrMessageEvent):
        parts = event.message_str.strip().split()
        if len(parts) < 2:
            yield event.plain_result("用法：/welcome <开/关>")
            return

        switch = parts[1]
        if switch in ("开", "on", "true", "1"):
            self.enable_welcome = True
        elif switch in ("关", "off", "false", "0"):
            self.enable_welcome = False
        else:
            yield event.plain_result("参数错误，请使用：/welcome 开 或 /welcome 关")
            return

        self.config["enable_welcome"] = self.enable_welcome
        self.config.save_config()
        yield event.plain_result(f"欢迎消息已{'开启' if self.enable_welcome else '关闭'}")

    # ==================== 截图验证 ====================

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def screenshot_verify(self, event: AstrMessageEvent):
        if not self.enable_group_management or not self.enable_uid_verify:
            return

        group_id = event.message_obj.group_id
        user_id = event.message_obj.sender.user_id

        if self.group_management.is_verified(group_id, user_id):
            return

        images = event.message_obj.message
        if not images:
            return

        for comp in images:
            if hasattr(comp, 'url') and comp.url:
                text = await self.uid_verifier.recognize_image(comp.url)
                if not text:
                    yield event.plain_result("OCR识别失败，请重新截图")
                    return

                uid = self.uid_verifier.extract_uid(text)

                if uid:
                    result = self.uid_verifier.validate_uid(uid)

                    if result["type"] == "normal":
                        await self.group_management.unmute_user(event.platform, group_id, user_id)
                        self.group_management.mark_verified(group_id, user_id)
                        yield event.plain_result(f"UID {uid} 验证通过，已解除禁言")
                    elif result["type"] == "new_account":
                        self.group_management.mark_pending_review(group_id, user_id, uid)
                        yield event.plain_result(f"UID {uid} 验证通过，但这是新号\n已通知管理员进行审核")
                    else:
                        yield event.plain_result("UID无效，新注册QQ号需联系管理员")
                    return
                else:
                    yield event.plain_result("未识别到有效UID，请重新截图")
                    return

    # ==================== 敏感内容检测 ====================

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def content_moderation(self, event: AstrMessageEvent):
        if not self.enable_group_management or not self.enable_content_moderation:
            return

        message_str = event.message_str
        if not message_str or message_str.startswith("/"):
            return

        try:
            result = await self.content_moderator.check_content(message_str, self.moderation_keywords)
        except Exception as e:
            logger.error(f"内容检测出错: {e}")
            return

        if not result["safe"]:
            group_id = event.message_obj.group_id
            user_id = event.message_obj.sender.user_id
            self.group_management.record_violation(group_id, user_id, result["reason"])
            count = self.group_management.get_violation_count(group_id, user_id)

            if count >= 3:
                await self.group_management.mute_user(event.platform, group_id, user_id, 0)
                await self.group_management.kick_user(event.platform, group_id, user_id)
                yield event.plain_result(f"用户 {user_id} 累计违规{count}次，已永久禁言并踢出")
            elif count >= 1:
                yield event.plain_result(f"检测到敏感内容，已全体禁言12小时（违规{count}次）")
            else:
                yield event.plain_result("检测到敏感内容，请注意群规")

    # ==================== 工具函数 ====================

    async def _is_admin(self, platform, group_id, user_id) -> bool:
        try:
            client = platform.get_client()
            member_list = await client.api.call_action('get_group_member_list', group_id=int(group_id))
            for member in member_list:
                if str(member.get('user_id', '')) == str(user_id):
                    return member.get('role', '') in ['admin', 'owner']
        except Exception as e:
            logger.error(f"检查管理员权限失败: {e}")
        return False
