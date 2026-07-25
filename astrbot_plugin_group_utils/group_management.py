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
from typing import Optional, Union

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
            try:
                return json.loads(self.violations_file.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _save_violations(self):
        try:
            self.violations_file.write_text(
                json.dumps(self.violations, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        except Exception as e:
            logger.error(f"保存违规记录失败: {e}")

    def _load_verified(self) -> dict:
        if self.verified_file.exists():
            try:
                return json.loads(self.verified_file.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _save_verified(self):
        try:
            self.verified_file.write_text(
                json.dumps(self.verified_users, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        except Exception as e:
            logger.error(f"保存验证记录失败: {e}")

    def _load_pending(self) -> dict:
        if self.pending_review_file.exists():
            try:
                return json.loads(self.pending_review_file.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _save_pending(self):
        try:
            self.pending_review_file.write_text(
                json.dumps(self.pending_review, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        except Exception as e:
            logger.error(f"保存待审核记录失败: {e}")

    def is_verified(self, group_id: Union[str, int], user_id: Union[str, int]) -> bool:
        """检查用户是否已验证"""
        key = f"{group_id}:{user_id}"
        return key in self.verified_users

    def mark_verified(self, group_id: Union[str, int], user_id: Union[str, int]):
        """标记用户为已验证"""
        key = f"{group_id}:{user_id}"
        self.verified_users[key] = datetime.now().isoformat()
        self._save_verified()
        # 从待审核列表移除
        if key in self.pending_review:
            del self.pending_review[key]
            self._save_pending()

    def mark_pending_review(self, group_id: Union[str, int], user_id: Union[str, int], uid: int):
        """标记为待审核新号"""
        key = f"{group_id}:{user_id}"
        self.pending_review[key] = {
            "uid": uid,
            "time": datetime.now().isoformat()
        }
        self._save_pending()

    def get_violation_count(self, group_id: Union[str, int], user_id: Union[str, int]) -> int:
        """获取用户违规次数"""
        key = f"{group_id}:{user_id}"
        return self.violations.get(key, {}).get("count", 0)

    def record_violation(self, group_id: Union[str, int], user_id: Union[str, int], reason: str):
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

    async def mute_user(self, platform, group_id: Union[str, int], user_id: Union[str, int], duration: int = 0) -> bool:
        """禁言用户（duration=0为永久禁言）"""
        try:
            client = platform.get_client()
            await client.api.call_action('set_group_ban',
                group_id=int(group_id),
                user_id=int(user_id),
                duration=duration
            )
            logger.info(f"已禁言用户 {user_id} 在群 {group_id}，时长 {duration}秒")
            return True
        except Exception as e:
            logger.error(f"禁言失败: {e}")
            return False

    async def unmute_user(self, platform, group_id: Union[str, int], user_id: Union[str, int]) -> bool:
        """解除禁言"""
        try:
            client = platform.get_client()
            await client.api.call_action('set_group_ban',
                group_id=int(group_id),
                user_id=int(user_id),
                duration=0
            )
            logger.info(f"已解除禁言用户 {user_id} 在群 {group_id}")
            return True
        except Exception as e:
            logger.error(f"解除禁言失败: {e}")
            return False

    async def global_mute(self, platform, group_id: Union[str, int], duration: int = 43200) -> bool:
        """全体禁言"""
        try:
            client = platform.get_client()
            await client.api.call_action('set_group_whole_ban',
                group_id=int(group_id),
                enable=True
            )
            logger.info(f"已全体禁言群 {group_id}，时长 {duration}秒")

            # 定时解除
            asyncio.create_task(self._auto_unmute(platform, group_id, duration))
            return True
        except Exception as e:
            logger.error(f"全体禁言失败: {e}")
            return False

    async def _auto_unmute(self, platform, group_id: Union[str, int], duration: int):
        """定时解除全体禁言"""
        await asyncio.sleep(duration)
        try:
            client = platform.get_client()
            await client.api.call_action('set_group_whole_ban',
                group_id=int(group_id),
                enable=False
            )
            logger.info(f"已自动解除群 {group_id} 的全体禁言")
        except Exception as e:
            logger.error(f"自动解除全体禁言失败: {e}")

    async def kick_user(self, platform, group_id: Union[str, int], user_id: Union[str, int]) -> bool:
        """踢出用户"""
        try:
            client = platform.get_client()
            await client.api.call_action('set_group_kick',
                group_id=int(group_id),
                user_id=int(user_id),
                reject_add_request=False
            )
            logger.info(f"已踢出用户 {user_id} 从群 {group_id}")
            return True
        except Exception as e:
            logger.error(f"踢出失败: {e}")
            return False
