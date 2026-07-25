import os
import img2pdf
from typing import List

# 支持的图片格式（可扩展）
SUPPORTED_IMAGE_FORMATS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tiff", ".tif")

def get_valid_image_files(folder: str) -> List[str]:
    """
    获取文件夹中所有有效的图片文件
    :param folder: 文件夹路径
    :return: 有效图片文件路径列表
    """
    image_files = []
    
    if not os.path.isdir(folder):
        print(f"❌ 错误：文件夹 '{folder}' 不存在")
        return image_files
    
    # 遍历并筛选图片文件
    for filename in sorted(os.listdir(folder)):
        # 忽略隐藏文件
        if filename.startswith('.'):
            continue
            
        # 检查文件扩展名
        file_ext = os.path.splitext(filename)[1].lower()
        if file_ext in SUPPORTED_IMAGE_FORMATS:
            img_path = os.path.join(folder, filename)
            
            # 检查文件是否为有效文件且非空
            if os.path.isfile(img_path) and os.path.getsize(img_path) > 0:
                image_files.append(img_path)
            else:
                print(f"⚠️  跳过无效文件：{filename}（空文件或非文件）")
    
    return image_files

def images_to_pdf():
    """将文件夹中的图片转换为PDF"""
    print("=== 图片转PDF工具 ===")
    print(f"支持的图片格式：{', '.join(SUPPORTED_IMAGE_FORMATS)}")
    print("提示：可直接拖放文件夹到此处获取路径\n")
    
    # 获取并处理用户输入的文件夹路径
    folder = input("请输入图片文件夹路径：").strip().strip('"').strip("'")
    
    # 获取有效图片文件
    images = get_valid_image_files(folder)
    
    if not images:
        print("❌ 未找到任何有效图片文件")
        return
    
    # 输出PDF路径
    pdf_filename = "图片合并结果.pdf"
    pdf_path = os.path.join(folder, pdf_filename)
    
    # 显示找到的图片信息
    print(f"\n✅ 找到 {len(images)} 张有效图片：")
    for i, img_path in enumerate(images, 1):
        print(f"   {i}. {os.path.basename(img_path)}")
    
    try:
        print("\n🚀 开始转换为PDF...")
        # 转换图片为PDF（去掉了旧版不支持的Layout参数）
        with open(pdf_path, "wb") as f:
            f.write(img2pdf.convert(
                images,
                # 修复透明图片导致的PDF损坏
                with_alphafallback=True,
                # 允许重复文件（防止转换失败）
                allow_duplicates=True
            ))
        
        # 计算文件大小
        pdf_size = os.path.getsize(pdf_path) / (1024 * 1024)  # 转换为MB
        
        print("\n🎉 转换成功！")
        print(f"📄 PDF文件路径：{pdf_path}")
        print(f"📊 文件大小：{pdf_size:.2f} MB")
        
    except img2pdf.ImageOpenError as e:
        print(f"\n❌ 图片打开错误：{str(e)}")
        print("💡 解决方法：检查图片是否损坏，尝试重新保存图片")
    except PermissionError:
        print("\n❌ 权限错误：无法写入PDF文件")
        print("💡 解决方法：确保文件夹有写入权限，关闭已打开的目标PDF文件")
    except Exception as e:
        print(f"\n❌ 转换失败：{str(e)}")
        print("💡 解决方法：")
        print("   1. 图片路径和文件名不要包含特殊字符、中文（建议使用英文/数字）")
        print("   2. 确保所有图片文件都能正常打开")
        print("   3. 建议升级img2pdf库（执行：pip install --upgrade img2pdf）")

if __name__ == "__main__":
    try:
        images_to_pdf()
    except KeyboardInterrupt:
        print("\n\n🛑 用户中断了操作")
    except Exception as e:
        print(f"\n💥 程序意外出错：{str(e)}")
