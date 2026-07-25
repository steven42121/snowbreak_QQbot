import sys
import os
import re
import img2pdf
import time
import random
from datetime import datetime
from jmcomic import download_album, create_option_by_file, JmcomicException

# ===================== 全局配置 =====================
ROOT_PATH = r"F:\python\jinman"
SAFE_FOLDER = "我喜欢看的"
DOWNLOAD_RECORD_PATH = os.path.join(ROOT_PATH, "下载记录.txt")
USED_IDS_PATH = os.path.join(ROOT_PATH, "已使用ID列表.txt")
JM_CONFIG_PATH = os.path.join(ROOT_PATH, "jm_config.yml")

PYPI_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"
JM_PACKAGES = ["jmcomic", "jmv"]

success_list = []
fail_list = []
new_ids = []
start_time = datetime.now()

# 随机生成ID配置
RANDOM_COUNT = 10
MIN_5 = 10000
MAX_5 = 99999
MIN_6 = 100000
MAX_6 = 999999
MIN_7 = 1000000
MAX_7 = 1399999

# ===================== 编码兼容 =====================
os.environ['PYTHONIOENCODING'] = 'utf-8'
for stream in [sys.stdin, sys.stdout, sys.stderr]:
    if hasattr(stream, 'reconfigure'):
        stream.reconfigure(encoding='utf-8', errors='replace')

def safe_print(*args, sep=' ', end='\n', file=None):
    try:
        print(*args, sep=sep, end=end, file=file)
    except Exception:
        pass

# ===================== 清理旧文件 =====================
def clean_old_config():
    old_yml = os.path.join(os.getcwd(), "option.yml")
    if os.path.exists(old_yml):
        try:
            os.remove(old_yml)
        except:
            pass

# ===================== 依赖更新 =====================
def update_jm_packages():
    safe_print("="*60)
    safe_print("📦 自动更新 jmcomic / jmv")
    safe_print("="*60)
    try:
        import subprocess
        subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "pip", "-i", PYPI_INDEX],
                       check=True, capture_output=True)
    except:
        pass
    for pkg in JM_PACKAGES:
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", pkg, "-i", PYPI_INDEX],
                           capture_output=True)
        except:
            pass
    safe_print("✅ 更新完成\n")

# ===================== 生成配置 =====================
def create_jm_config():
    config_content = f"""
download:
  save_dir: {ROOT_PATH}
  image_suffix: png
  download_thread: 15
  retry_count: 3
  make_pdf: false
  make_cbz: false
  make_zip: false
  overwrite: false
  timeout: 60
client:
  load_timeout: 60
  retry:
    total: 3
    backoff_factor: 1
"""
    os.makedirs(ROOT_PATH, exist_ok=True)
    with open(JM_CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write(config_content)
    return create_option_by_file(JM_CONFIG_PATH)

# ===================== 已使用ID管理 =====================
def load_used_ids():
    if not os.path.exists(USED_IDS_PATH):
        return set()
    with open(USED_IDS_PATH, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip().isdigit())

def save_used_ids(ids):
    existing = load_used_ids()
    for i in ids:
        existing.add(str(i) if isinstance(i, int) else i)
    with open(USED_IDS_PATH, "w", encoding="utf-8") as f:
        for i in sorted(existing, key=int):
            f.write(f"{i}\n")

def get_total_used_count():
    return len(load_used_ids())

# ===================== 随机生成ID功能 =====================
def generate_random_ids():
    safe_print("="*60)
    safe_print("🎲 开始生成随机ID")
    safe_print("="*60)
    
    # 读取已使用数字
    used_numbers = set()
    if os.path.exists(USED_IDS_PATH):
        with open(USED_IDS_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.isdigit():
                    used_numbers.add(int(line))

    # 生成10个不重复数字
    new_numbers = []
    six_count = random.randint(6, 10)
    other_count = RANDOM_COUNT - six_count

    # 生成六位数
    for _ in range(six_count):
        while True:
            num = random.randint(MIN_6, MAX_6)
            if num not in used_numbers:
                new_numbers.append(num)
                used_numbers.add(num)
                break

    # 生成五/七位数
    for _ in range(other_count):
        if random.random() < 0.5:
            # 五位数
            while True:
                num = random.randint(MIN_5, MAX_5)
                if num not in used_numbers:
                    new_numbers.append(num)
                    used_numbers.add(num)
                    break
        else:
            # 七位数
            while True:
                num = random.randint(MIN_7, MAX_7)
                if num not in used_numbers:
                    new_numbers.append(num)
                    used_numbers.add(num)
                    break

    # 打乱顺序
    random.shuffle(new_numbers)

    # 写入文件：新数字放最前面，空行分隔批次
    if os.path.exists(USED_IDS_PATH):
        with open(USED_IDS_PATH, "r", encoding="utf-8") as f:
            old_lines = [line.rstrip("\n") for line in f]
    else:
        old_lines = []

    # 构造新内容
    new_lines = [str(num) for num in new_numbers]
    if old_lines:
        # 避免开头多余空行
        if old_lines and old_lines[0] == "":
            old_lines.pop(0)
        new_lines.append("")
        new_lines.extend(old_lines)

    # 写入文件
    with open(USED_IDS_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(new_lines) + "\n")

    # 控制台输出生成的ID
    safe_print(f"\n✨ 成功生成 {len(new_numbers)} 个随机ID：")
    for idx, num in enumerate(new_numbers, 1):
        safe_print(f"   {idx}. {num}")
    
    # 转换为字符串列表返回
    return [str(num) for num in new_numbers]

# ===================== 手动输入ID功能 =====================
def get_manual_ids():
    safe_print("="*60)
    safe_print("📚 漫画批量下载器（空格/换行全兼容）")
    safe_print("="*60)
    safe_print("📌 输入方式说明：")
    safe_print("  1. 单行输入：a b c d（空格分隔）")
    safe_print("  2. 多行输入：一行一个ID")
    safe_print("  3. 混合输入：先写a b，再换行写c d")
    safe_print("  👉 输完后按【空行 + 回车】确认输入完毕\n")

    lines = []
    while True:
        try:
            line = input().strip()
            if not line:
                break
            lines.append(line)
        except KeyboardInterrupt:
            safe_print("\n❌ 用户中断输入，程序退出")
            sys.exit()

    all_text = " ".join(lines)
    ids = list(set(re.findall(r"\d+", all_text)))
    ids.sort(key=int)
    
    if not ids:
        safe_print("❌ 未检测到有效漫画ID，程序退出")
        sys.exit()
    
    safe_print(f"\n📥 本次待下载ID列表：{ids}")
    return ids

# ===================== 选择ID生成方式 =====================
def select_id_mode():
    safe_print("="*60)
    safe_print("🔧 漫画ID获取方式选择")
    safe_print("="*60)
    safe_print("请选择以下方式之一（输入数字后按回车）：")
    safe_print("  [1] 手动输入漫画ID")
    safe_print("  [2] 自动生成10个随机不重复漫画ID")
    safe_print("="*60)
    
    while True:
        try:
            choice = input("请输入选择（1/2）：").strip()
            if choice == "1":
                safe_print("\n🔤 你选择了手动输入ID模式")
                return get_manual_ids()
            elif choice == "2":
                safe_print("\n🎲 你选择了自动生成随机ID模式")
                return generate_random_ids()
            else:
                safe_print("❌ 无效选择，请输入 1 或 2！")
        except KeyboardInterrupt:
            safe_print("\n❌ 用户中断操作，程序退出")
            sys.exit()
        except Exception as e:
            safe_print(f"❌ 输入异常：{e}，请重新输入！")

# ===================== 工具函数 =====================
def get_latest_folder(start_ts):
    folders = []
    for name in os.listdir(ROOT_PATH):
        p = os.path.join(ROOT_PATH, name)
        if os.path.isdir(p) and name != SAFE_FOLDER:
            if os.path.getctime(p) > start_ts:
                folders.append((p, os.path.getctime(p)))
    if not folders:
        return None
    folders.sort(key=lambda x: x[1], reverse=True)
    return folders[0][0]

def natural_sort_key(s):
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r"(\d+)", s)]

# ===================== PDF生成 =====================
def generate_pdf(comic_dir, album_id):
    imgs = []
    if not comic_dir or not os.path.exists(comic_dir):
        return False, True

    for root, _, files in os.walk(comic_dir):
        for f in files:
            if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                p = os.path.join(root, f)
                if os.path.getsize(p) > 1024:
                    imgs.append(p)

    imgs.sort(key=natural_sort_key)
    if not imgs:
        return False, True

    pdf_path = os.path.join(comic_dir, f"{album_id}.pdf")
    try:
        with open(pdf_path, "wb") as f:
            f.write(img2pdf.convert(imgs))
        return True, False
    except:
        return False, True

# ===================== 下载记录 =====================
def write_combined_record():
    end_time = datetime.now()
    start_str = start_time.strftime("%Y年%m月%d日 %H:%M:%S")
    end_str = end_time.strftime("%Y年%m月%d日 %H:%M:%S")
    total_used = get_total_used_count()

    old_content = ""
    if os.path.exists(DOWNLOAD_RECORD_PATH):
        with open(DOWNLOAD_RECORD_PATH, "r", encoding="utf-8") as f:
            old_content = f.read()

    new_content = f"📚 累计使用ID总数：{total_used} 个\n"
    new_content += "========================================\n\n"
    new_content += f"📌 {start_str} 本次下载ID：\n"
    for aid in new_ids:
        new_content += f"  • {aid}\n"
    new_content += "\n========================================\n\n"
    new_content += "📥 本次下载结果：\n"

    all_records = []
    for rec in success_list:
        all_records.append({
            "id": rec["id"],
            "name": rec["name"],
            "status": "✅ 下载成功",
            "reason": ""
        })
    for rec in fail_list:
        all_records.append({
            "id": rec["id"],
            "name": rec["name"],
            "status": "❌ 下载失败",
            "reason": rec["reason"]
        })

    for idx, rec in enumerate(all_records, 1):
        new_content += f"（{idx}）【{rec['id']}】+【{rec['name']}】+【{rec['status']}】\n"
        if rec["status"].startswith("❌") and rec["reason"]:
            new_content += f"    失败原因：{rec['reason']}\n"

    new_content += "\n========================================\n\n"
    new_content += f"⏱️  开始时间：{start_str}\n"
    new_content += f"⏱️  结束时间：{end_str}\n"
    new_content += f"✅ 成功数量：{len(success_list)} 个\n"
    new_content += f"❌ 失败数量：{len(fail_list)} 个\n\n"

    final_content = new_content + old_content
    with open(DOWNLOAD_RECORD_PATH, "w", encoding="utf-8") as f:
        f.write(final_content)

    try:
        os.startfile(DOWNLOAD_RECORD_PATH)
        os.startfile(USED_IDS_PATH)
    except:
        pass

# ===================== 核心下载功能 =====================
def batch_download(ids):
    global new_ids
    new_ids = ids
    opt = create_jm_config()

    for idx, aid in enumerate(ids, 1):
        safe_print(f"\n🚀 开始处理 {idx}/{len(ids)} → ID：{aid}")
        start_ts = time.time()
        success = False
        error = None

        try:
            # 直接调用API，不走命令行
            download_album(aid, opt)
            success = True
        except Exception as e:
            error = e

        time.sleep(1)
        folder = get_latest_folder(start_ts)
        name = os.path.basename(folder) if folder else "未知漫画"

        if success and folder is not None:
            pdf_ok, pdf_err = generate_pdf(folder, aid)
            if pdf_err:
                fail_list.append({"id": aid, "name": name, "reason": "图片损坏/不完整导致PDF生成失败"})
                safe_print(f"⚠️ ID {aid} PDF生成失败")
            else:
                success_list.append({"id": aid, "name": name})
                safe_print(f"✅ ID {aid} 下载成功（含PDF生成）")
        else:
            # 识别失败原因
            reason = ""
            if isinstance(error, JmcomicException):
                err_str = str(error)
                if "MissingAlbumPhotoException" in err_str or "请求的本子不存在" in err_str:
                    reason = "本子不存在/ID错误/需登录，API请求失败"
                elif "timeout" in err_str or "502" in err_str:
                    reason = "服务器/网络波动导致下载失败"
                else:
                    reason = "下载过程中发生未知错误"
            else:
                reason = "下载过程中发生未知错误"

            fail_list.append({"id": aid, "name": name, "reason": reason})
            safe_print(f"⚠️ ID {aid} 下载失败 | 原因：{reason}")

    save_used_ids(ids)
    write_combined_record()

# ===================== 主程序 =====================
if __name__ == "__main__":
    try:
        clean_old_config()
        # 选择ID生成方式
        comic_ids = select_id_mode()
        # 更新依赖
        update_jm_packages()
        # 批量下载
        batch_download(comic_ids)

        safe_print("\n" + "="*60)
        safe_print(f"🎉 所有任务完成！成功：{len(success_list)} | 失败：{len(fail_list)}")
        safe_print("="*60)
    except Exception as e:
        safe_print(f"\n❌ 程序运行出错：{str(e)}")
        sys.exit(1)
