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

        # 群管理模块
        self.group_management = GroupManagement()
        self.uid_verifier = UIDVerifier()
        self.content_moderator = ContentModerator()

        # botpy patch 相关
        self._bridge_installed = False
        self._bindings: list = []
        self._intent_patches: list = []
        self._bound_clients: set = set()

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

        # 安装 botpy 补丁（类方法，只需安装一次）
        self._install_parser_patch()

    @filter.on_platform_loaded()
    async def on_platform_loaded(self):
        """平台加载后绑定"""
        logger.info("[进群禁言] 平台加载完成，开始绑定")
        await self._bind_platforms()

    @filter.on_astrbot_loaded()
    async def on_astrbot_loaded(self):
        """AstrBot加载后绑定"""
        logger.info("[进群禁言] AstrBot加载完成，开始绑定")
        await self._bind_platforms()

    async def terminate(self):
        if self.scheduler_task:
            self.scheduler_task.cancel()
        await self._uninstall_bridge()

    # ==================== botpy 进群事件桥 ====================

    def _install_parser_patch(self):
        """安装 botpy 进群事件解析器（类方法，只需安装一次）"""
        try:
            from botpy.connection import ConnectionState
        except ImportError:
            logger.warning("[进群禁言] botpy 未安装，进群禁言功能不可用")
            return

        for event_type in ("GROUP_MEMBER_ADD", "GROUP_MEMBER_REMOVE"):
            attr = f"parse_{event_type.lower()}"
            if not hasattr(ConnectionState, attr):
                parser = self._make_parser(event_type)
                setattr(ConnectionState, attr, parser)
                self._bridge_installed = True
                logger.info(f"[进群禁言] 已安装解析器: {attr}")

        if self._bridge_installed:
            logger.info("[进群禁言] 已安装进群事件解析桥")

    def _make_parser(self, event_type: str):
        """创建事件解析器"""
        def parser(state, payload):
            event_id = payload.get("id", "")
            timestamp = payload.get("timestamp", "")
            group_openid = payload.get("group_openid", "")
            member_openid = payload.get("member_openid", "")
            op_member_openid = payload.get("op_member_openid", "")

            # 异步处理
            asyncio.create_task(self._handle_member_event(
                event_type, group_openid, member_openid, op_member_openid
            ))

        parser.__name__ = f"parse_{event_type.lower()}"
        parser.__qualname__ = f"ConnectionState.{parser.__name__}"
        setattr(parser, "__qq_group_notice_bridge__", True)
        return parser

    async def _handle_member_event(self, event_type: str, group_openid: str, member_openid: str, op_member_openid: str):
        """处理进群/退群事件"""
        if event_type == "GROUP_MEMBER_ADD":
            logger.info(f"[进群禁言] 新成员加入群 {group_openid}: {member_openid}")
            if self.enable_group_management and self.enable_uid_verify:
                # 等待一下，让 botpy 完成事件处理
                await asyncio.sleep(1)
                # 禁言新人
                success = await self.group_management.mute_user_by_openid(
                    self._get_platform(), group_openid, member_openid, 0
                )
                if success:
                    # 发送验证提示
                    await self._send_verify_prompt(group_openid, member_openid)
        elif event_type == "GROUP_MEMBER_REMOVE":
            logger.info(f"[进群禁言] 成员离开群 {group_openid}: {member_openid}")

    async def _send_verify_prompt(self, group_openid: str, member_openid: str):
        """发送验证提示消息"""
        try:
            platform = self._get_platform()
            if platform is None:
                return

            client = platform.get_client()
            api = getattr(client, "api", None)
            if api is None:
                return

            content = (
                "欢迎新成员！\n"
                "请发送UID截图进行验证（游戏内个人资料截图）\n"
                "验证通过后将自动解除禁言"
            )

            await api.post_group_message(
                group_openid=group_openid,
                msg_type=0,
                content=content
            )
        except Exception as e:
            logger.error(f"[进群禁言] 发送验证提示失败: {e}")

    async def _bind_platforms(self):
        """绑定到 QQ 官方平台"""
        manager = getattr(self.context, "platform_manager", None)
        if manager is None:
            logger.warning("[进群禁言] platform_manager 不存在")
            return

        try:
            adapters = list(manager.get_insts())
            logger.info(f"[进群禁言] 找到 {len(adapters)} 个平台实例")
        except Exception as e:
            logger.error(f"[进群禁言] 获取平台实例失败: {e}")
            adapters = list(getattr(manager, "platform_insts", ()) or ())

        for adapter in adapters:
            platform_name = self._get_platform_name(adapter)
            logger.info(f"[进群禁言] 检查平台: {platform_name}")

            if platform_name not in ("qq_official", "qq_official_webhook"):
                logger.info(f"[进群禁言] 跳过非QQ官方平台: {platform_name}")
                continue

            client = getattr(adapter, "client", None)
            if client is None:
                logger.warning(f"[进群禁言] 平台 {platform_name} 没有 client")
                continue

            if id(client) in self._bound_clients:
                logger.info(f"[进群禁言] 平台 {platform_name} 已绑定过")
                continue

            # 启用 GROUP_MEMBER Intent
            self._enable_intent(client, adapter)

            # 绑定回调
            self._bind_client(client, adapter)

            self._bound_clients.add(id(client))
            logger.info(f"[进群禁言] ✓ 已绑定平台: {platform_name}")

    def _enable_intent(self, client, adapter):
        """启用 GROUP_MEMBER Intent"""
        platform_name = self._get_platform_name(adapter)
        if platform_name != "qq_official":
            return

        intents = getattr(client, "intents", None)
        if not isinstance(intents, int):
            return

        GROUP_MEMBER_INTENT = 1 << 24
        if intents & GROUP_MEMBER_INTENT:
            return

        client.intents = intents | GROUP_MEMBER_INTENT
        self._intent_patches.append((client, intents, client.intents))
        logger.info("[进群禁言] 已启用 GROUP_MEMBER Intents")

        if getattr(client, "_connection", None) is not None:
            logger.warning("[进群禁言] QQ 连接已建立，请重载平台或重启 AstrBot 使新 Intents 生效")

    def _bind_client(self, client, adapter):
        """绑定进群回调"""
        callbacks = {
            "on_group_member_add": "GROUP_MEMBER_ADD",
            "on_group_member_remove": "GROUP_MEMBER_REMOVE",
        }

        for attr, event_type in callbacks.items():
            original = getattr(client, attr, None)
            logger.info(f"[进群禁言] 绑定回调: {attr}, 原始值: {original}")

            async def wrapper(event, _event_type=event_type, _original=original):
                logger.info(f"[进群禁言] 收到事件: {_event_type}")
                member_openid = getattr(event, "member_openid", "")
                group_openid = getattr(event, "group_openid", "")
                op_member_openid = getattr(event, "op_member_openid", "")

                logger.info(f"[进群禁言] 事件详情: group={group_openid}, member={member_openid}")

                if _event_type == "GROUP_MEMBER_ADD":
                    logger.info(f"[进群禁言] 新成员加入: {member_openid} in {group_openid}")
                    if self.enable_group_management and self.enable_uid_verify:
                        await asyncio.sleep(1)
                        success = await self.group_management.mute_user_by_openid(
                            self._get_platform(), group_openid, member_openid, 0
                        )
                        logger.info(f"[进群禁言] 禁言结果: {success}")
                        if success:
                            await self._send_verify_prompt(group_openid, member_openid)

                if _original is not None:
                    result = _original(event)
                    if asyncio.iscoroutine(result):
                        await result

            setattr(wrapper, "__qq_group_notice_bridge__", True)
            setattr(client, attr, wrapper)
            self._bindings.append((client, attr, original, wrapper))
            logger.info(f"[进群禁言] ✓ 已绑定回调: {attr}")

    async def _uninstall_bridge(self):
        """卸载 botpy 补丁"""
        # 恢复回调
        for client, attr, original, wrapper in reversed(self._bindings):
            if getattr(client, attr, None) is wrapper:
                if original is None:
                    try:
                        delattr(client, attr)
                    except AttributeError:
                        pass
                else:
                    setattr(client, attr, original)
        self._bindings.clear()
        self._bound_clients.clear()

        # 恢复 Intent
        for client, original, patched in self._intent_patches:
            if getattr(client, "intents", None) == patched:
                client.intents = original
        self._intent_patches.clear()

        # 移除 parser
        try:
            from botpy.connection import ConnectionState
            for event_type in ("GROUP_MEMBER_ADD", "GROUP_MEMBER_REMOVE"):
                attr = f"parse_{event_type.lower()}"
                current = getattr(ConnectionState, attr, None)
                if current and getattr(current, "__qq_group_notice_bridge__", False):
                    delattr(ConnectionState, attr)
            logger.info("[进群禁言] 已卸载进群事件解析桥")
        except Exception:
            pass

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
        """获取平台名称"""
        try:
            return str(adapter.meta().name)
        except Exception:
            return ""

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
                logger.info(f"已发送定时消息到群 {group_id}")
            except Exception as e:
                logger.error(f"发送消息到群 {group_id} 失败: {e}")

    # ==================== JM 漫画下载 ====================

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
            logger.error("jmcomic库未安装，请运行: pip install jmcomic")
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
            except Exception as e:
                logger.error(f"读取已使用ID失败: {e}")
        ids.add(comic_id)
        try:
            with open(self.used_ids_file, "w", encoding="utf-8") as f:
                for i in sorted(ids):
                    f.write(f"{i}\n")
        except Exception as e:
            logger.error(f"保存已使用ID失败: {e}")

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
        readme_path = Path(__file__).parent / "README.md"
        if readme_path.exists():
            try:
                content = readme_path.read_text(encoding="utf-8")
                lines = []
                for line in content.split("\n"):
                    line = line.strip()
                    if line and not line.startswith("#"):
                        lines.append(line)
                yield event.plain_result("\n".join(lines[:50]))
            except Exception as e:
                logger.error(f"读取README失败: {e}")
                yield event.plain_result("帮助文档读取失败")
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
                        yield event.plain_result(
                            f"UID {uid} 验证通过，但这是新号（UID 400-1000万）\n"
                            "已通知管理员进行审核"
                        )

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
            result = await self.content_moderator.check_content(
                message_str,
                self.moderation_keywords
            )
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
                await self.group_management.global_mute(event.platform, group_id, 43200)
                yield event.plain_result(f"检测到敏感内容，已全体禁言12小时（违规{count}次）")
            else:
                yield event.plain_result("检测到敏感内容，请注意群规")

    # ==================== 工具函数 ====================

    async def _is_admin(self, platform, group_id, user_id) -> bool:
        try:
            client = platform.get_client()
            member_list = await client.api.call_action(
                'get_group_member_list',
                group_id=int(group_id)
            )

            for member in member_list:
                if str(member.get('user_id', '')) == str(user_id):
                    return member.get('role', '') in ['admin', 'owner']
        except Exception as e:
            logger.error(f"检查管理员权限失败: {e}")
        return False
