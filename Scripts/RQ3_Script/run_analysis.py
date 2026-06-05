# =============================================================================
# run_analysis.py
# 功能：读取项目列表 JSON，对每个项目依次运行：
#   1. Pyre.py —— 类型推断工具
#   2. analysis.py —— 依赖准确率分析
#   完成后将分析结果 CSV 移动到汇总目录 result/acc/
# =============================================================================

import json    # 读取项目列表 JSON
import os      # 执行系统命令、检查文件是否存在
import shutil  # 文件移动操作


def move_file(source, destination):
    """
    将 source 文件移动到 destination 目录（或路径）。
    异常处理：
      FileNotFoundError —— 源文件不存在时打印 "error"
      PermissionError   —— 权限不足时打印 "no permission"
    """
    try:
        shutil.move(source, destination)
    except FileNotFoundError:
        print("error")
    except PermissionError:
        print("no permission")


# 加载项目列表：每条记录为 {项目名: 其他信息} 的字典
with open('data/project.json') as f:
    data = json.load(f)

for item in data:
    # 取每条记录的第一个 key 作为项目名
    proj = next(iter(item))

    # 运行 Pyre（Facebook Pyre 类型检查器包装脚本），传入项目名
    os.system("python Pyre.py " + proj)

    # 运行 analysis.py，对该项目的所有工具输出进行准确率分析
    os.system("python analysis.py " + proj)

    # 将本项目的准确率 CSV 从 result/<proj>/gt/ 移动到汇总目录 result/acc/
    source_file = 'result/' + proj + '/gt/' + proj + '_acc.csv'
    destination_dir = 'result/acc/'

    # 若目标目录中已存在同名文件，先删除旧文件（避免 shutil.move 报错或追加）
    if os.path.exists(destination_dir + proj + '_acc.csv'):
        os.remove(destination_dir + proj + '_acc.csv')

    move_file(source_file, destination_dir)  # 移动结果文件到汇总目录
