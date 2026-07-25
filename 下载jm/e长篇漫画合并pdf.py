import os
import sys
import re
import shutil
import subprocess
import fitz

# ===================== 配置 =====================
BATCH_SIZE = 100
MAX_SIZE_PER_PDF_MB = 2048
SAFE_MARGIN_MB = 200
SUPPORTED_FORMATS = ('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif', '.tiff', '.tif')
OUTPUT_COMBINE_FILENAME = "漫画合集.pdf"
OUTPUT_PART_PREFIX = "漫画分集"
MIN_IMAGE_SIZE = 1024
MISSING_RECORD = "缺失记录.txt"
TEMP_PDF_DIR = "temp_pdfs"
PROGRESS_LOG = "progress.log"
PROGRESS_STEP = 10
# =================================================

def natural_sort(l):
    convert = lambda text: int(text) if text.isdigit() else text.lower()
    alphanum_key = lambda key: [convert(c) for c in re.split('([0-9]+)', key)]
    return sorted(l, key=alphanum_key)

def get_chapter_folders(root):
    folders = []
    for entry in os.scandir(root):
        if entry.is_dir() and not entry.name.startswith(('temp_', '.')):
            folders.append(entry.name)
    return natural_sort(folders)

def count_chapter_images(folder_path):
    cnt = 0
    for r, d, files in os.walk(folder_path):
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in SUPPORTED_FORMATS:
                try:
                    if os.path.getsize(os.path.join(r, f)) >= MIN_IMAGE_SIZE:
                        cnt += 1
                except:
                    continue
    return cnt

def parse_chapter_num(name):
    match = re.search(r'(\d+)', name)
    return int(match.group(1)) if match else None

def scan_chapter_info(root):
    print("=" * 70)
    print("📌 扫描章节结构...")
    chapter_folder_names = get_chapter_folders(root)
    ch_info = []
    all_ch_nums = []
    for name in chapter_folder_names:
        num = parse_chapter_num(name)
        if num is not None:
            p = count_chapter_images(os.path.join(root, name))
            ch_info.append({"name": name, "num": num, "pages": p, "path": os.path.join(root, name)})
            all_ch_nums.append(num)
    if not all_ch_nums:
        print("❌ 未识别到有效章节文件夹，程序退出")
        sys.exit(1)
    min_ch = min(all_ch_nums)
    max_ch = max(all_ch_nums)
    total_theoretical_ch = max_ch - min_ch + 1
    total_actual_pages = sum([x["pages"] for x in ch_info])
    actual_ch_set = set(all_ch_nums)
    missing_ch = sorted(list(set(range(min_ch, max_ch + 1)) - actual_ch_set))
    print(f"✅ 扫描完成：理论范围 第{min_ch}话 ~ 第{max_ch}话，共 {total_theoretical_ch} 话")
    print(f"✅ 实际存在 {len(ch_info)} 话，总页数 {total_actual_pages} 页")
    print(f"⚠️  缺失 {len(missing_ch)} 话：{missing_ch}")
    print("=" * 70)
    return ch_info, min_ch, max_ch, total_theoretical_ch, total_actual_pages, missing_ch

def check_missing_chapters(ch_info, min_ch, max_ch, missing_ch):
    log = [
        "=" * 70,
        "📚 漫画章节完整性检测报告",
        "=" * 70,
        f"📖 理论范围：第{min_ch}话 ~ 第{max_ch}话，共 {max_ch - min_ch + 1} 话",
        f"✅ 实际存在章节数：{len(ch_info)} 话",
        f"⚠️  缺失章节数：{len(missing_ch)} 话",
        "",
        "【📋 每话页数明细】"
    ]
    for ch in sorted(ch_info, key=lambda x: x["num"]):
        log.append(f"第{ch['num']:>3}话：{ch['pages']:>4} 页")
    log.append("")
    log.append("【❌ 缺失章节列表】")
    if missing_ch:
        for n in missing_ch:
            log.append(f"第{n}话")
    else:
        log.append("✅ 无缺失！")
    log.append("")
    log.append("=" * 70)
    return "\n".join(log)

def collect_chapter_images(ch_path):
    images = []
    seen = set()
    for r, d, files in os.walk(ch_path):
        for f in natural_sort(files):
            ext = os.path.splitext(f)[1].lower()
            if ext in SUPPORTED_FORMATS:
                base = os.path.splitext(f)[0]
                if base in seen:
                    continue
                seen.add(base)
                img_path = os.path.join(r, f)
                try:
                    if os.path.getsize(img_path) >= MIN_IMAGE_SIZE:
                        images.append(img_path)
                except:
                    continue
    return images

def merge_batch_with_img2pdf(batch, output_path):
    if not batch:
        return False
    cmd = [sys.executable, "-m", "img2pdf", "--output", output_path] + batch
    subprocess.run(cmd, capture_output=True)
    return os.path.exists(output_path) and os.path.getsize(output_path) > 0

def get_pdf_size_mb(pdf_path):
    if not os.path.exists(pdf_path):
        return 0
    return os.path.getsize(pdf_path) / (1024 * 1024)

def print_safe_progress(desc, current, total, last_printed):
    progress = current / total * 100
    if progress - last_printed >= PROGRESS_STEP or progress >= 100:
        print(f"[{desc}] 已完成：{current}/{total} ({progress:.1f}%)")
        return progress
    return last_printed

def merge_images_to_temp_pdf(image_list, temp_dir, batch_size=100, desc="合并中"):
    if not image_list:
        print(f"⚠️ [{desc}] 无图片，跳过")
        return None, 0
    temp_pdfs = []
    total = len(image_list)
    last = 0
    for i in range(0, total, batch_size):
        batch = image_list[i:i+batch_size]
        tp = os.path.join(temp_dir, f"batch_{i}.pdf")
        if merge_batch_with_img2pdf(batch, tp):
            temp_pdfs.append(tp)
        last = print_safe_progress(desc, i + len(batch), total, last)
    if not temp_pdfs:
        return None, 0
    final = os.path.join(temp_dir, "full_temp.pdf")
    doc = fitz.open()
    last = 0
    for idx, p in enumerate(temp_pdfs, 1):
        with fitz.open(p) as m:
            doc.insert_pdf(m)
        last = print_safe_progress("合并PDF块", idx, len(temp_pdfs), last)
    if doc.page_count == 0:
        doc.close()
        return None, 0
    doc.save(final)
    doc.close()
    for p in temp_pdfs:
        try: os.remove(p)
        except: pass
    return final, get_pdf_size_mb(final)

def merge_chapter_to_pdf(imgs, temp_dir, ch_num):
    if not imgs:
        print(f"⚠️ 第{ch_num}话无图，跳过")
        return None, 0
    path = os.path.join(temp_dir, f"ch_{ch_num}.pdf")
    tmp, sz = merge_images_to_temp_pdf(imgs, temp_dir, 100, f"合并第{ch_num}话")
    if tmp and os.path.exists(tmp):
        os.rename(tmp, path)
        return path, get_pdf_size_mb(path)
    return None, 0

def combine_pdfs(pdf_list, out_path, desc="合并PDF"):
    doc = fitz.open()
    total = len(pdf_list)
    last = 0
    for idx, p in enumerate(pdf_list, 1):
        with fitz.open(p) as m:
            doc.insert_pdf(m)
        last = print_safe_progress(desc, idx, total, last)
    doc.save(out_path)
    doc.close()
    return get_pdf_size_mb(out_path)

def split_by_real_size(ch_info, min_ch, max_ch, temp_dir):
    parts = []
    current = []
    size = 0.0
    limit = MAX_SIZE_PER_PDF_MB - SAFE_MARGIN_MB
    ch_map = {}
    for ch in sorted(ch_info, key=lambda x:x["num"]):
        n = ch["num"]
        p = os.path.join(temp_dir, f"ch_{n}.pdf")
        if os.path.exists(p):
            ch_map[n] = (p, get_pdf_size_mb(p))
    for n in range(min_ch, max_ch+1):
        if n not in ch_map:
            continue
        p, s = ch_map[n]
        if size + s <= limit:
            current.append(p)
            size += s
        else:
            if current:
                start = n - len(current)
                end = n - 1
                parts.append((current.copy(), start, end))
                print(f"   ✅ 第{len(parts)}卷：第{start}~{end}话")
            if s > limit:
                parts.append(([p], n, n))
                print(f"   ⚠️  第{len(parts)+1}卷：第{n}话（超出单卷上限，单独一卷）")
                current = []
                size = 0.0
            else:
                current = [p]
                size = s
    if current:
        start = n - len(current) + 1
        end = n
        parts.append((current.copy(), start, end))
        print(f"   ✅ 第{len(parts)}卷：第{start}~{end}话")
    return parts

def read_progress_log():
    if not os.path.exists(PROGRESS_LOG):
        return []
    parts = []
    with open(PROGRESS_LOG, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and "|" in line:
                part_num, start, end = line.split("|")
                parts.append((int(part_num), int(start), int(end)))
    return parts

def write_progress_log(part_num, start, end):
    with open(PROGRESS_LOG, "a", encoding="utf-8") as f:
        f.write(f"{part_num}|{start}|{end}\n")

def get_existing_parts():
    existing = []
    for file in os.listdir("."):
        if file.startswith(OUTPUT_PART_PREFIX) and file.endswith(".pdf"):
            match = re.search(rf"{OUTPUT_PART_PREFIX}(\d+)\.pdf", file)
            if match:
                existing.append(int(match.group(1)))
    return sorted(existing)

def get_existing_temp_pdfs(temp_dir, ch_nums):
    existing = set()
    for n in ch_nums:
        if os.path.exists(os.path.join(temp_dir, f"ch_{n}.pdf")):
            existing.add(n)
    return existing

def main():
    current = os.path.abspath(os.path.dirname(sys.argv[0]))
    os.makedirs(TEMP_PDF_DIR, exist_ok=True)
    ch_info, min_ch, max_ch, _, total_pages, missing_ch = scan_chapter_info(current)
    log_txt = check_missing_chapters(ch_info, min_ch, max_ch, missing_ch)
    print(log_txt)
    with open(MISSING_RECORD, "w", encoding="utf-8") as f:
        f.write(log_txt)
    if sys.platform == "win32":
        os.startfile(MISSING_RECORD)

    # ==============================================
    # 1. 补全临时单章PDF（自动检测缺失的ch_xxx.pdf）
    # ==============================================
    print("\n📌 补全临时单章PDF（已存在自动跳过，缺失自动补全）")
    ch_list = sorted(ch_info, key=lambda x:x["num"])
    ch_nums = [x["num"] for x in ch_list]
    existing_temp_nums = get_existing_temp_pdfs(TEMP_PDF_DIR, ch_nums)
    missing_temp_nums = [n for n in ch_nums if n not in existing_temp_nums]
    print(f"✅ 已存在 {len(existing_temp_nums)} 个临时PDF，检测到 {len(missing_temp_nums)} 个缺失：{missing_temp_nums}")

    total_ch = len(ch_list)
    last = 0
    valid_pdfs = []
    for idx, ch in enumerate(ch_list, 1):
        n = ch["num"]
        path = os.path.join(TEMP_PDF_DIR, f"ch_{n}.pdf")
        if os.path.exists(path):
            print(f"✅ 第{n}话 已存在，跳过")
            valid_pdfs.append(path)
            last = print_safe_progress("补全临时单章", idx, total_ch, last)
            continue
        imgs = collect_chapter_images(ch["path"])
        p, _ = merge_chapter_to_pdf(imgs, TEMP_PDF_DIR, n)
        if p:
            valid_pdfs.append(p)
        last = print_safe_progress("补全临时单章", idx, total_ch, last)

    # ==============================================
    # 2. 生成总集：复用单章PDF
    # ==============================================
    final_size_mb = 0
    if os.path.exists(OUTPUT_COMBINE_FILENAME):
        print(f"\n✅ 总集已存在，跳过生成")
        final_size_mb = get_pdf_size_mb(OUTPUT_COMBINE_FILENAME)
    else:
        print("\n📌 生成总集（拼接单章PDF）")
        tmp_total = os.path.join(TEMP_PDF_DIR, "total_temp.pdf")
        final_size_mb = combine_pdfs(valid_pdfs, tmp_total, "合并总集")
        doc = fitz.open(tmp_total)
        doc.save(OUTPUT_COMBINE_FILENAME)
        doc.close()
        os.remove(tmp_total)
        print(f"📦 总集完成：{final_size_mb/1024:.2f} GB")

    # ==============================================
    # 3. 分卷：自动补全缺失的分集
    # ==============================================
    split_choice = 1
    if final_size_mb > MAX_SIZE_PER_PDF_MB:
        print("\n⚠️ 总集超2GB")
        print("1 = 仅保留总集")
        print("2 = 同时生成分集（<2GB，自动补全缺失卷）")
        while True:
            try:
                split_choice = int(input("请选择 1/2："))
                if split_choice in (1,2):
                    break
            except:
                pass
    part_count = 0
    if split_choice == 2:
        print("\n🚀 生成分集（自动补全缺失卷）...")
        parts = split_by_real_size(ch_info, min_ch, max_ch, TEMP_PDF_DIR)
        part_count = len(parts)
        existing_parts = get_existing_parts()
        print(f"📋 已检测到 {len(existing_parts)} 个已存在分卷：{existing_parts}")

        for i, (pdf_list, start, end) in enumerate(parts, 1):
            part_path = f"{OUTPUT_PART_PREFIX}{i}.pdf"
            if i in existing_parts and os.path.exists(part_path):
                print(f"✅ 第{i}卷（第{start}~{end}话）已存在，跳过")
                continue
            print(f"\n📌 生成第{i}卷（第{start}~{end}话）")
            tmp = os.path.join(TEMP_PDF_DIR, f"part_{i}.pdf")
            sz = combine_pdfs(pdf_list, tmp, f"合并第{i}卷")
            os.rename(tmp, part_path)
            write_progress_log(i, start, end)
            print(f"✅ 第{i}卷：{sz:.1f} MB")

    # ==============================================
    # 4. 最后询问是否保留临时文件
    # ==============================================
    print("\n" + "="*50)
    print("❓ 是否保留临时PDF和进度记录？")
    print("1 = 保留（下次可直接补全缺失卷/单章）")
    print("2 = 删除（清理干净）")
    keep_choice = 0
    while True:
        try:
            keep_choice = int(input("请输入 1/2："))
            if keep_choice in (1,2):
                break
        except:
            pass
    if keep_choice == 2:
        print("\n🧹 清理临时文件...")
        try:
            shutil.rmtree(TEMP_PDF_DIR)
            print("✅ temp_pdfs 已删除")
        except:
            print("⚠️ 删除失败，请手动删除")
        if os.path.exists(PROGRESS_LOG):
            try:
                os.remove(PROGRESS_LOG)
                print("✅ progress.log 已删除")
            except:
                print("⚠️ 删除失败，请手动删除")
    else:
        print("\n✅ 临时文件已保留，下次可直接补全！")

    print("\n" + "="*70)
    print("🎉 全部任务完成")
    print(f"✅ 总集：{OUTPUT_COMBINE_FILENAME}")
    if part_count > 0:
        print(f"✅ 分集：共 {part_count} 个")
    print(f"✅ 缺失记录：{MISSING_RECORD}")
    print("="*70)

if __name__ == "__main__":
    main()
