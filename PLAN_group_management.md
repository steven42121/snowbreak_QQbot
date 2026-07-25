# 群管理增强功能 - 最终实现计划

## 一、功能概览

| 功能 | 说明 |
|------|------|
| 进群永久禁言 | 新成员加入自动永久禁言，需验证才能发言 |
| 截图验证 | 新人发送游戏UID截图，Windows OCR识别后放行 |
| UID检查 | >1000万正常通过；400-1000万新号通知管理员；<400万无效 |
| 敏感内容检测 | 关键词粗筛 + AstrBot LLM二次判断 |
| 违规惩罚 | 首次全体禁言12h，累计3次永久禁言+踢出 |
| 管理员权限 | 使用QQ群管理员身份，无需额外配置白名单 |

---

## 二、文件变更

### 新增文件
- `astrbot_plugin_group_utils/group_management.py` - 群管理核心逻辑
- `astrbot_plugin_group_utils/verify.py` - UID截图验证模块
- `astrbot_plugin_group_utils/content_moderation.py` - 敏感内容检测模块

### 修改文件
- `astrbot_plugin_group_utils/main.py` - 集成新模块
- `astrbot_plugin_group_utils/_conf_schema.json` - 添加配置项
- `astrbot_plugin_group_utils/requirements.txt` - 添加winocr依赖

---

## 三、详细实现

### 3.1 依赖 (requirements.txt)

```
Pillow
winocr
httpx
```

### 3.2 配置项 (_conf_schema.json新增)

```json
{
  "group_management": {
    "type": "object",
    "description": "群管理配置",
    "properties": {
      "enabled": {
        "type": "boolean",
        "default": true,
        "description": "启用群管理功能"
      },
      "welcome_message": {
        "type": "string",
        "default": "欢迎加入！请发送游戏内个人资料截图（需包含UID）完成验证"
      },
      "global_mute_duration": {
        "type": "integer",
        "default": 43200,
        "description": "全体禁言时长（秒），默认12小时"
      },
      "ban_threshold": {
        "type": "integer",
        "default": 3,
        "description": "触发永久禁言的违规次数阈值"
      },
      "political_keywords": {
        "type": "array",
        "default": ["习近平", "共产党", "共产主义", "六四", "天安门", "法轮功", "台独", "藏独", "疆独", "港独"]
      },
      "attack_keywords": {
        "type": "array",
        "default": ["死全家", "问候家人", "祖宗十八代", "不得好死", "生孩子没屁眼"]
      },
      "suspicious_keywords": {
        "type": "array",
        "default": ["政治", "政府", "国家", "民主", "自由", "人权"]
      }
    }
  }
}
```

### 3.3 群管理模块 (group_management.py)

```python
"""
群管理核心模块
- 进群自动禁言
- 解除禁言
- 全体禁言
- 违规记录
"""
import json
import asyncio
from pathlib import Path
from datetime import datetime
from typing import Optional, Tuple

from astrbot.api import logger
from astrbot.core.utils.astrbot_path import get_astrbot_data_path


class GroupManagement:
    def __init__(self):
        self.data_dir = Path(get_astrbot_data_path()) / "plugin_data" / "astrbot_plugin_group_utils"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        # 违规记录文件
        self.violations_file = self.data_dir / "violations.json"
        self.violations = self._load_violations()
        
        # 已验证用户文件
        self.verified_file = self.data_dir / "verified_users.json"
        self.verified_users = self._load_verified()
        
        # 待审核新号（UID 400-1000万）
        self.pending_review_file = self.data_dir / "pending_review.json"
        self.pending_review = self._load_pending()
    
    def _load_violations(self) -> dict:
        if self.violations_file.exists():
            return json.loads(self.violations_file.read_text(encoding="utf-8"))
        return {}
    
    def _save_violations(self):
        self.violations_file.write_text(
            json.dumps(self.violations, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    
    def _load_verified(self) -> dict:
        if self.verified_file.exists():
            return json.loads(self.verified_file.read_text(encoding="utf-8"))
        return {}
    
    def _save_verified(self):
        self.verified_file.write_text(
            json.dumps(self.verified_users, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    
    def _load_pending(self) -> dict:
        if self.pending_review_file.exists():
            return json.loads(self.pending_review_file.read_text(encoding="utf-8"))
        return {}
    
    def _save_pending(self):
        self.pending_review_file.write_text(
            json.dumps(self.pending_review, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    
    def is_verified(self, group_id: int, user_id: int) -> bool:
        """检查用户是否已验证"""
        key = f"{group_id}:{user_id}"
        return key in self.verified_users
    
    def mark_verified(self, group_id: int, user_id: int):
        """标记用户为已验证"""
        key = f"{group_id}:{user_id}"
        self.verified_users[key] = datetime.now().isoformat()
        self._save_verified()
        # 从待审核列表移除
        if key in self.pending_review:
            del self.pending_review[key]
            self._save_pending()
    
    def mark_pending_review(self, group_id: int, user_id: int, uid: int):
        """标记为待审核新号"""
        key = f"{group_id}:{user_id}"
        self.pending_review[key] = {
            "uid": uid,
            "time": datetime.now().isoformat()
        }
        self._save_pending()
    
    def get_violation_count(self, group_id: int, user_id: int) -> int:
        """获取用户违规次数"""
        key = f"{group_id}:{user_id}"
        return self.violations.get(key, {}).get("count", 0)
    
    def record_violation(self, group_id: int, user_id: int, reason: str):
        """记录违规"""
        key = f"{group_id}:{user_id}"
        if key not in self.violations:
            self.violations[key] = {"count": 0, "history": []}
        self.violations[key]["count"] += 1
        self.violations[key]["history"].append({
            "time": datetime.now().isoformat(),
            "reason": reason
        })
        self._save_violations()
    
    async def mute_user(self, platform, group_id: int, user_id: int, duration: int = 0):
        """禁言用户（duration=0为永久禁言）"""
        try:
            client = platform.get_client()
            await client.api.call_action('set_group_ban',
                group_id=group_id,
                user_id=user_id,
                duration=duration
            )
            logger.info(f"已禁言用户 {user_id} 在群 {group_id}，时长 {duration}秒")
            return True
        except Exception as e:
            logger.error(f"禁言失败: {e}")
            return False
    
    async def unmute_user(self, platform, group_id: int, user_id: int):
        """解除禁言"""
        try:
            client = platform.get_client()
            await client.api.call_action('set_group_ban',
                group_id=group_id,
                user_id=user_id,
                duration=0
            )
            logger.info(f"已解除禁言用户 {user_id} 在群 {group_id}")
            return True
        except Exception as e:
            logger.error(f"解除禁言失败: {e}")
            return False
    
    async def global_mute(self, platform, group_id: int, duration: int = 43200):
        """全体禁言"""
        try:
            client = platform.get_client()
            await client.api.call_action('set_group_whole_ban',
                group_id=group_id,
                enable=True
            )
            logger.info(f"已全体禁言群 {group_id}，时长 {duration}秒")
            
            # 定时解除
            asyncio.create_task(self._auto_unmute(platform, group_id, duration))
            return True
        except Exception as e:
            logger.error(f"全体禁言失败: {e}")
            return False
    
    async def _auto_unmute(self, platform, group_id: int, duration: int):
        """定时解除全体禁言"""
        await asyncio.sleep(duration)
        try:
            client = platform.get_client()
            await client.api.call_action('set_group_whole_ban',
                group_id=group_id,
                enable=False
            )
            logger.info(f"已自动解除群 {group_id} 的全体禁言")
        except Exception as e:
            logger.error(f"自动解除全体禁言失败: {e}")
    
    async def kick_user(self, platform, group_id: int, user_id: int):
        """踢出用户"""
        try:
            client = platform.get_client()
            await client.api.call_action('set_group_kick',
                group_id=group_id,
                user_id=user_id,
                reject_add_request=False
            )
            logger.info(f"已踢出用户 {user_id} 从群 {group_id}")
            return True
        except Exception as e:
            logger.error(f"踢出失败: {e}")
            return False
```

### 3.4 截图验证模块 (verify.py)

```python
"""
UID截图验证模块
- 使用Windows内置OCR识别图片
- 检查UID格式
"""
import re
import asyncio
from pathlib import Path
from typing import Optional, Tuple

from astrbot.api import logger


class UIDVerifier:
    def __init__(self):
        self.ocr_engine = None
        self._init_ocr()
    
    def _init_ocr(self):
        """初始化Windows OCR"""
        try:
            from winocr import WinOCR
            self.ocr_engine = WinOCR()
            logger.info("Windows OCR初始化成功")
        except ImportError:
            logger.error("winocr未安装，请运行: pip install winocr")
        except Exception as e:
            logger.error(f"OCR初始化失败: {e}")
    
    async def extract_uid_from_image(self, image_path: str) -> Optional[int]:
        """从图片中提取UID"""
        if not self.ocr_engine:
            return None
        
        try:
            # 使用Windows OCR识别
            result = await self.ocr_engine.recognize(image_path, lang='zh-CN')
            text = result if isinstance(result, str) else str(result)
            
            logger.info(f"OCR识别结果: {text[:100]}...")
            
            # 匹配UID模式
            # 尘白禁区UID通常是8位数字
            patterns = [
                r'UID[：:\s]*(\d{8,12})',
                r'Uid[：:\s]*(\d{8,12})',
                r'uid[：:\s]*(\d{8,12})',
                r'(\d{8,12})',  # 直接匹配8位以上数字
            ]
            
            for pattern in patterns:
                match = re.search(pattern, text)
                if match:
                    uid = int(match.group(1))
                    if uid >= 4000000:  # 最小有效UID
                        return uid
            
            return None
        except Exception as e:
            logger.error(f"OCR识别失败: {e}")
            return None
    
    def validate_uid(self, uid: int) -> Tuple[bool, str, bool]:
        """
        验证UID
        返回: (是否有效, 消息, 是否需要管理员确认)
        """
        if uid >= 10000000:
            return True, f"UID {uid} 验证通过！", False
        elif uid >= 4000000:
            return True, f"UID {uid} 验证通过（新号，请管理员注意）", True
        else:
            return False, "UID无效，请重新截图", False
```

### 3.5 敏感内容检测模块 (content_moderation.py)

```python
"""
敏感内容检测模块
- 关键词粗筛
- AstrBot LLM二次判断
"""
import re
from typing import Tuple, Optional

from astrbot.api import logger


class ContentModeration:
    def __init__(self, config: dict):
        self.political_keywords = config.get("political_keywords", [])
        self.attack_keywords = config.get("attack_keywords", [])
        self.suspicious_keywords = config.get("suspicious_keywords", [])
        
        # 合并所有直接违规关键词
        self.direct_ban_keywords = self.political_keywords + self.attack_keywords
    
    def check_keywords(self, message: str) -> Tuple[bool, Optional[str]]:
        """
        关键词检查
        返回: (是否违规, 违规类型)
        """
        if not message:
            return False, None
        
        message_lower = message.lower()
        
        # 检查直接违规关键词
        for keyword in self.direct_ban_keywords:
            if keyword in message_lower:
                if keyword in self.political_keywords:
                    return True, "鉴政"
                else:
                    return True, "人身攻击"
        
        # 检查可疑关键词（需要AI二次判断）
        for keyword in self.suspicious_keywords:
            if keyword in message_lower:
                return None, "suspicious"  # 需要AI判断
        
        return False, None
    
    async def ai_check(self, message: str, context) -> bool:
        """
        使用AstrBot LLM进行二次判断
        返回: True=违规, False=正常
        """
        try:
            prompt = f"""判断以下消息是否包含敏感内容（鉴政、人身攻击、恶意言论）。

消息：{message}

只回答"是"或"否"，不要解释。"""
            
            # 使用AstrBot内置LLM
            provider = context.get_using_provider()
            if provider:
                response = await provider.text_chat(prompt)
                return "是" in str(response)
            
            return False
        except Exception as e:
            logger.error(f"AI判断失败: {e}")
            return False
    
    def is_violation(self, message: str, ai_result: Optional[bool] = None) -> Tuple[bool, str]:
        """
        综合判断是否违规
        返回: (是否违规, 违规原因)
        """
        keyword_result, violation_type = self.check_keywords(message)
        
        if keyword_result is True:
            return True, violation_type
        elif keyword_result is None and ai_result is not None:
            # 可疑内容，AI判断
            if ai_result:
                return True, "AI判定违规"
        
        return False, ""
```

### 3.6 main.py 集成变更

**新增import：**
```python
from .group_management import GroupManagement
from .verify import UIDVerifier
from .content_moderation import ContentModeration
```

**__init__新增：**
```python
# 群管理模块
self.group_mgmt = GroupManagement()
self.verifier = UIDVerifier()
self.content_mod = ContentModeration(config)
```

**新增监听器 - 进群事件：**
```python
@filter.event_message_type(filter.EventMessageType.ALL)
async def on_raw_event(self, event: AstrMessageEvent):
    """监听原始事件（包括进群通知）"""
    if event.get_platform_name() != "aiocqhttp":
        return
    
    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
    if not isinstance(event, AiocqhttpMessageEvent):
        return
    
    raw = event.raw_message
    if not isinstance(raw, dict):
        return
    
    # 新人进群事件
    if raw.get('post_type') == 'notice' and raw.get('sub_type') == 'increase':
        group_id = raw['group_id']
        user_id = raw['user_id']
        
        # 获取平台实例
        platform_id = event.get_platform_id()
        platform = self.context.get_platform_inst(platform_id)
        
        # 自动禁言
        await self.group_mgmt.mute_user(platform, group_id, user_id, duration=0)
        
        # 发送欢迎消息
        welcome = self.config.get("welcome_message", 
            "欢迎加入！请发送游戏内个人资料截图（需包含UID）完成验证")
        await event.send(Comp.At(qq=user_id) + Comp.Plain(welcome))
```

**新增监听器 - 截图验证：**
```python
@filter.command("verify")
async def verify_account(self, event: AstrMessageEvent):
    """新人验证游戏账号"""
    group_id = event.message_obj.group_id
    user_id = event.get_sender_id()
    
    # 检查是否已验证
    if self.group_mgmt.is_verified(group_id, user_id):
        yield event.plain_result("你已经验证过了")
        return
    
    # 检查最近消息是否有图片
    # （需要从消息链中获取图片）
    # 这里简化处理，假设用户发送 /verify 时附带图片
    
    yield event.plain_result("请发送包含UID的游戏截图")
```

**增强敏感内容检测：**
```python
@filter.event_message_type(filter.EventMessageType.ALL)
async def content_filter(self, event: AstrMessageEvent):
    """社区内容过滤器（增强版）"""
    if not self.config.get("enable_filter", True):
        return
    
    message_str = event.message_str
    if not message_str:
        return
    
    # 检查是否是已验证用户（未验证用户不检测）
    group_id = event.message_obj.group_id
    user_id = event.get_sender_id()
    
    if group_id and user_id and not self.group_mgmt.is_verified(group_id, user_id):
        return  # 未验证用户，跳过
    
    # 关键词检查
    keyword_result, violation_type = self.content_mod.check_keywords(message_str)
    
    if keyword_result is True:
        # 直接违规
        yield event.plain_result(f"检测到{violation_type}，已被禁言")
        await self._handle_violation(event, user_id, violation_type)
        event.stop_event()
        return
    elif keyword_result is None:
        # 可疑内容，AI判断
        ai_result = await self.content_mod.ai_check(message_str, self.context)
        if ai_result:
            yield event.plain_result("检测到不当内容，已被禁言")
            await self._handle_violation(event, user_id, "AI判定违规")
            event.stop_event()
            return
    
    # 原有黑名单过滤
    for keyword in self.blacklist_keywords:
        if keyword in message_str:
            yield event.plain_result("检测到不当内容，已被过滤")
            event.stop_event()
            return

async def _handle_violation(self, event: AstrMessageEvent, user_id: int, reason: str):
    """处理违规"""
    group_id = event.message_obj.group_id
    platform_id = event.get_platform_id()
    platform = self.context.get_platform_inst(platform_id)
    
    # 记录违规
    self.group_mgmt.record_violation(group_id, user_id, reason)
    count = self.group_mgmt.get_violation_count(group_id, user_id)
    
    threshold = self.config.get("ban_threshold", 3)
    
    if count >= threshold:
        # 多次违规：永久禁言 + 踢出
        await self.group_mgmt.mute_user(platform, group_id, user_id, duration=0)
        await self.group_mgmt.kick_user(platform, group_id, user_id)
        # 通知管理员
        await self._notify_admins(platform, group_id, 
            f"⚠️ 用户 {user_id} 多次违规（{count}次），已永久禁言并踢出")
    else:
        # 首次违规：全体禁言12小时
        mute_duration = self.config.get("global_mute_duration", 43200)
        await self.group_mgmt.global_mute(platform, group_id, duration=mute_duration)
        await self._notify_admins(platform, group_id,
            f"⚠️ 检测到敏感内容，已全体禁言12小时。违规者：{user_id}，原因：{reason}")

async def _notify_admins(self, platform, group_id: int, message: str):
    """通知管理员"""
    try:
        client = platform.get_client()
        # 获取群成员列表
        member_list = await client.api.call_action('get_group_member_list', group_id=group_id)
        
        # 筛选管理员（admin或owner）
        admins = [m for m in member_list.get('data', []) 
                  if m.get('role') in ['admin', 'owner']]
        
        for admin in admins:
            try:
                # 私聊通知管理员
                await client.api.call_action('send_private_msg',
                    user_id=admin['user_id'],
                    message=f"[群管理通知] 群{group_id}:\n{message}"
                )
            except:
                pass
    except Exception as e:
        logger.error(f"通知管理员失败: {e}")
```

**新增管理员命令：**
```python
@filter.command("unlock")
@filter.permission_type(filter.PermissionType.ADMIN)
async def unlock_user(self, event: AstrMessageEvent):
    """管理员手动解除禁言"""
    parts = event.message_str.strip().split()
    if len(parts) < 2:
        yield event.plain_result("用法：/unlock <QQ号>")
        return
    
    try:
        target_user = int(parts[1])
    except ValueError:
        yield event.plain_result("QQ号必须是数字")
        return
    
    group_id = event.message_obj.group_id
    platform_id = event.get_platform_id()
    platform = self.context.get_platform_inst(platform_id)
    
    # 解除禁言
    if await self.group_mgmt.unmute_user(platform, group_id, target_user):
        # 标记为已验证
        self.group_mgmt.mark_verified(group_id, target_user)
        yield event.plain_result(f"已解除用户 {target_user} 的禁言")
    else:
        yield event.plain_result("解除禁言失败")

@filter.command("violations")
@filter.permission_type(filter.PermissionType.ADMIN)
async def view_violations(self, event: AstrMessageEvent):
    """查看违规记录"""
    group_id = event.message_obj.group_id
    
    # 筛选该群的违规记录
    group_violations = {k: v for k, v in self.group_mgmt.violations.items() 
                        if k.startswith(f"{group_id}:")}
    
    if not group_violations:
        yield event.plain_result("本群无违规记录")
        return
    
    lines = ["【违规记录】"]
    for key, data in group_violations.items():
        user_id = key.split(':')[1]
        lines.append(f"QQ {user_id}: {data['count']}次违规")
    
    yield event.plain_result("\n".join(lines))
```

---

## 四、命令列表（更新后）

| 命令 | 说明 | 权限 |
|------|------|------|
| `/jm <ID>` | 下载JM漫画 | 所有人 |
| `/jmhelp` | JM下载帮助 | 所有人 |
| `/verify` | 验证UID截图 | 新人 |
| `/unlock <QQ号>` | 手动解除禁言 | 管理员 |
| `/violations` | 查看违规记录 | 管理员 |
| `/addfilter <词>` | 添加过滤词 | 管理员 |
| `/delfilter <词>` | 删除过滤词 | 管理员 |
| `/listfilter` | 查看过滤词 | 所有人 |
| `/listtask` | 查看定时任务 | 所有人 |
| `/addtask` | 添加定时任务 | 管理员 |
| `/deltask <编号>` | 删除定时任务 | 管理员 |
| `/helpgroup` | 本帮助 | 所有人 |

---

## 五、核心流程图

```
┌─────────────────────────────────────────────────────────────┐
│                      新人进群流程                            │
└─────────────────────────────────────────────────────────────┘

用户加入群
    │
    ▼
触发 group_increase 事件
    │
    ▼
调用 set_group_ban(duration=0)  # 永久禁言
    │
    ▼
发送欢迎消息 + 验证说明
    │
    ▼
用户发送游戏截图
    │
    ▼
调用 Windows OCR 识别图片
    │
    ├─ UID >= 1000万 → 解禁 → 提示"验证通过"
    │
    ├─ 400万 <= UID < 1000万 → 解禁 → 提示"验证通过（新号，请管理员注意）"
    │                              → 通知管理员
    │
    └─ UID < 400万 或 无法识别 → 提示"UID无效，请重新截图"

┌─────────────────────────────────────────────────────────────┐
│                      敏感内容检测流程                         │
└─────────────────────────────────────────────────────────────┘

群消息
    │
    ▼
关键词匹配（鉴政/人身攻击）
    │
    ├─ 命中直接违规词 → 禁言 + 记录违规
    │
    ├─ 命中可疑词 → AI二次判断
    │       │
    │       ├─ AI判定违规 → 禁言 + 记录违规
    │       └─ AI判定正常 → 放行
    │
    └─ 未命中 → 放行

    │
    ▼ （违规时）
检查累计违规次数
    │
    ├─ < 3次 → 全体禁言12小时 + 通知管理员
    │
    └─ >= 3次 → 永久禁言 + 踢出 + 通知管理员

┌─────────────────────────────────────────────────────────────┐
│                      管理员操作流程                           │
└─────────────────────────────────────────────────────────────┘

管理员收到通知
    │
    ▼
使用 /unlock <QQ号> 解除禁言
    │
    ▼
用户可以正常发言
```

---

## 六、实现顺序

1. ✅ 创建 `group_management.py` - 群管理核心逻辑
2. ✅ 创建 `verify.py` - UID截图验证模块
3. ✅ 创建 `content_moderation.py` - 敏感内容检测模块
4. ⬜ 修改 `main.py` - 集成新模块
5. ⬜ 修改 `_conf_schema.json` - 添加配置项
6. ⬜ 修改 `requirements.txt` - 添加winocr依赖
7. ⬜ 测试和调试

---

## 七、注意事项

### 7.1 Windows OCR依赖
- 需要安装 `winocr`：`pip install winocr`
- 需要Windows 10/11中文语言包
- 首次使用可能需要下载OCR模型

### 7.2 群管理员检测
- 通过OneBot API `get_group_member_list` 获取成员信息
- 检查 `role` 字段：`admin` 或 `owner`
- 无需额外配置白名单

### 7.3 全体禁言限制
- 机器人需要群管理员权限
- 群主/超级管理员无法被禁言
- 建议设置合理的禁言时长（默认12小时）

### 7.4 图片获取
- 用户发送的图片需要从消息链中提取
- 使用 `event.message_obj.message` 获取消息链
- 找到 `Image` 类型的消息段获取图片URL

---

## 八、风险点

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| OCR识别不准 | 正常用户无法验证 | 支持重试 + /unlock手动审核 |
| AI误判 | 正常发言被禁 | 仅对可疑内容触发AI判断 |
| 全体禁言误触发 | 影响所有群成员 | 仅对严重违规触发 |
| 图片下载失败 | 无法验证 | 提示用户重新发送 |
