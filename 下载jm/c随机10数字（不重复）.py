import random
import os
from datetime import datetime

# ========== 配置 ==========
record_file = r"F:\python\jinman\已使用ID列表.txt"
count = 10
min_5 = 10000
max_5 = 99999
min_6 = 100000
max_6 = 999999
min_7 = 1000000
max_7 = 1399999

# ========== 1. 读取已使用数字（只读纯数字） ==========
used_numbers = set()
if os.path.exists(record_file):
    with open(record_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.isdigit():
                used_numbers.add(int(line))

# ========== 2. 生成10个不重复数字 ==========
new_numbers = []
six_count = random.randint(6, 10)
other_count = 10 - six_count

# 六位数
for _ in range(six_count):
    while True:
        num = random.randint(min_6, max_6)
        if num not in used_numbers:
            new_numbers.append(num)
            used_numbers.add(num)
            break

# 五/七位
for _ in range(other_count):
    if random.random() < 0.5:
        while True:
            num = random.randint(min_5, max_5)
            if num not in used_numbers:
                new_numbers.append(num)
                used_numbers.add(num)
                break
    else:
        while True:
            num = random.randint(min_7, max_7)
            if num not in used_numbers:
                new_numbers.append(num)
                used_numbers.add(num)
                break

random.shuffle(new_numbers)

# ========== 3. 写入文件：新数字放最前面，空行分隔批次 ==========
# 先读原有内容
old_lines = []
if os.path.exists(record_file):
    with open(record_file, "r", encoding="utf-8") as f:
        old_lines = [line.rstrip("\n") for line in f]

# 构造新内容：新数字 → 空行 → 旧内容
new_lines = [str(num) for num in new_numbers]
if old_lines:
    # 避免开头多余空行
    if old_lines and old_lines[0] == "":
        old_lines.pop(0)
    new_lines.append("")
    new_lines.extend(old_lines)

# 写入
with open(record_file, "w", encoding="utf-8") as f:
    f.write("\n".join(new_lines) + "\n")

# ========== 控制台输出 ==========
for num in new_numbers:
    print(num)
