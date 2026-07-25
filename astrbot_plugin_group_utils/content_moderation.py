"""
敏感内容检测模块
- 关键词匹配
- AI二次判断
"""
import re
from typing import Optional
from astrbot.api import logger


class ContentModerator:
    def __init__(self):
        self._llm_provider = None

    def set_llm_provider(self, provider):
        """设置LLM提供者"""
        self._llm_provider = provider

    async def check_content(self, text: str, keywords: list[str]) -> dict:
        """
        检查内容
        返回: {"safe": bool, "reason": str}
        """
        # 第一层：关键词匹配
        for keyword in keywords:
            if keyword in text:
                # 第二层：AI判断
                ai_result = await self._ai_judge(text, keyword)
                if ai_result is not None:
                    if ai_result["is_violation"]:
                        return {
                            "safe": False,
                            "reason": f"AI判断违规：{ai_result['reason']}"
                        }
                    else:
                        return {
                            "safe": True,
                            "reason": f"AI判断正常：{ai_result['reason']}"
                        }

        return {"safe": True, "reason": "无违规内容"}

    async def _ai_judge(self, text: str, keyword: str) -> Optional[dict]:
        """AI判断是否违规"""
        if self._llm_provider is None:
            logger.warning("LLM提供者未设置，仅使用关键词匹配")
            return None

        try:
            prompt = f"""请判断这段话是否涉及广告、色情、政治敏感或其他违规内容。

关键词：{keyword}

内容：{text}

请用JSON格式回答，格式：
{{"is_violation": true/false, "reason": "判断理由"}}

注意：
1. 只返回JSON，不要有其他内容
2. 如果是正常聊天、攻略讨论、游戏相关，即使包含关键词也应判断为正常
3. 如果是广告推销、色情内容、政治敏感，判断为违规"""

            response = await self._llm_provider.text_chat(
                prompt=prompt,
                system_prompt="你是内容审核助手，请严格判断内容是否违规。"
            )

            # 解析JSON
            import json
            import re
            json_match = re.search(r'\{[^{}]+\}', str(response))
            if json_match:
                result = json.loads(json_match.group())
                return result

        except Exception as e:
            logger.error(f"AI判断失败: {e}")

        return None
