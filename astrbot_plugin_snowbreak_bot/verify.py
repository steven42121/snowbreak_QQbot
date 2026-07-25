"""
UID截图验证模块
- Windows OCR识别UID
- UID校验规则
"""
import re
import tempfile
from pathlib import Path
from typing import Optional

import httpx
from astrbot.api import logger


class UIDVerifier:
    def __init__(self):
        self._ocr_engine = None
        self._ocr_available = None

    def _get_ocr(self):
        """懒加载OCR引擎"""
        if self._ocr_available is False:
            return None

        if self._ocr_engine is None:
            try:
                from winocr import ocr
                self._ocr_engine = ocr
                self._ocr_available = True
                logger.info("Windows OCR引擎已加载")
            except ImportError:
                self._ocr_available = False
                logger.warning("winocr未安装，OCR功能不可用。请运行: pip install winocr")
                return None
            except Exception as e:
                self._ocr_available = False
                logger.error(f"加载winocr失败: {e}")
                return None
        return self._ocr_engine

    async def recognize_image(self, image_url: str) -> str:
        """从图片URL识别文字"""
        ocr_func = self._get_ocr()
        if ocr_func is None:
            return ""

        try:
            # 下载图片到临时文件
            async with httpx.AsyncClient() as client:
                resp = await client.get(image_url, timeout=30)
                resp.raise_for_status()

            # 保存为临时文件
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                f.write(resp.content)
                temp_path = f.name

            try:
                # Windows OCR识别
                result = ocr_func(temp_path)

                # 合并识别结果
                if hasattr(result, 'lines'):
                    text = "\n".join([line.text for line in result.lines])
                else:
                    text = str(result)

                logger.info(f"OCR识别结果: {text[:200]}...")
                return text
            finally:
                # 清理临时文件
                try:
                    Path(temp_path).unlink(missing_ok=True)
                except Exception:
                    pass

        except httpx.HTTPStatusError as e:
            logger.error(f"下载图片失败: HTTP {e.response.status_code}")
            return ""
        except httpx.RequestError as e:
            logger.error(f"下载图片网络错误: {e}")
            return ""
        except Exception as e:
            logger.error(f"OCR识别失败: {e}")
            return ""

    def extract_uid(self, text: str) -> Optional[int]:
        """从OCR文本中提取UID"""
        if not text:
            return None

        # 匹配UID格式: 7-10位数字
        patterns = [
            r'UID[：:\s]*(\d{7,10})',
            r'Uid[：:\s]*(\d{7,10})',
            r'uid[：:\s]*(\d{7,10})',
            r'(\d{8,10})',  # 兜底：8-10位数字
        ]

        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                try:
                    uid = int(match.group(1))
                    # 验证UID范围
                    if 10000000 <= uid <= 9999999999:
                        return uid
                except (ValueError, IndexError):
                    continue

        return None

    def validate_uid(self, uid: int) -> dict:
        """
        校验UID
        返回: {"valid": bool, "type": str, "message": str}
        """
        if uid >= 100000000:
            return {
                "valid": True,
                "type": "normal",
                "message": "UID校验通过"
            }
        elif uid >= 40000000:
            return {
                "valid": True,
                "type": "new_account",
                "message": "UID校验通过，但这是新号，请管理员注意"
            }
        else:
            return {
                "valid": False,
                "type": "invalid",
                "message": "UID无效，新注册QQ号需联系管理员"
            }
