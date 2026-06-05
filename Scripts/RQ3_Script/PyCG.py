# =============================================================================
# PyCG.py
# 功能：将 PyCG 工具输出的调用图 JSON 转换为统一的依赖关系 JSON 格式，
#       便于与其他工具（如 PyAnalyzer）的输出进行横向对比分析。
# 命令行参数：
#   lang          —— 目标语言（cpp/java/python/js）
#   project_name  —— 项目名称
#   input_path    —— PyCG 输出 JSON 所在目录
#   output_path   —— 转换后依赖 JSON 的输出目录
#   prepath       —— 项目根路径（当前函数中通过命令行参数传入但未在 pycg 函数内使用）
# =============================================================================

import argparse  # 命令行参数解析
import json      # JSON 读写
import os        # 目录创建


def output(info_list: list, json_path: str, type: str):
    """
    将 info_list 包装为 {type: info_list} 结构并写入 JSON 文件。
    参数：
      info_list —— 依赖关系列表
      json_path —— 输出文件路径
      type      —— JSON 顶层字段名（如 "dependency"）
    """
    file = dict()
    file[type] = info_list  # 用 type 作为 key 包装列表

    dependency_str = json.dumps(file, indent=4)
    with open(json_path, 'w') as json_file:
        json_file.write(dependency_str)


def Dependency(src, dest, kind, project):
    """
    构造单条依赖关系字典，统一路径分隔符格式。
    参数：
      src     —— 调用者（原始格式，反斜杠分隔）
      dest    —— 被调用者
      kind    —— 关系类型（PyCG 转换时为空字符串）
      project —— 项目名称（用于替换 ".." 前缀为项目名）
    """
    dependency = dict()
    values = dict()

    # 将反斜杠替换为点（Windows 路径 → 点分限定名）；
    # 将 ".." 替换为项目名（PyCG 输出中用 ".." 表示相对路径根）
    dependency["src"] = src.replace("\\", '.').replace('..', project)
    dependency["dest"] = dest.replace("\\", '.')  # dest 不含 ".." 前缀，无需替换
    values["kind"] = kind      # 关系类型（此处为空字符串）
    dependency["values"] = values

    return dependency


def pycg(project_name, input_path, output_path):
    """
    读取 PyCG 输出的调用图 JSON，转换为统一依赖格式并写出。
    PyCG 调用图格式：{caller: [callee1, callee2, ...], ...}
    """
    with open(input_path, 'r', encoding='utf8') as json_file:
        data = json.load(json_file)  # 加载 PyCG 调用图

    dependencyList = list()

    # 遍历所有调用关系：key 为调用者，values 为被调用者列表
    for key, values in data.items():
        for value in values:
            dependencyList.append(Dependency(key, value, "", project_name))

    # 输出文件路径：pycg_<project_name>_dependency.json
    dependency_json_path = output_path + "pycg_" + project_name + "_dependency.json"
    output(dependencyList, dependency_json_path, "dependency")


# -------------------------------------------------------------------------
# 命令行参数解析
# -------------------------------------------------------------------------
parser = argparse.ArgumentParser()
parser.add_argument('lang', help='Sepcify the target language:cpp, java, python, js')
parser.add_argument('project_name', help='Specify the project name')
parser.add_argument('input_path', help='Specify the json path')
parser.add_argument('output_path', help='Specify the output path')
parser.add_argument('prepath', help='Specify the path your project in')
args = parser.parse_args()

project_name = args.project_name
# 输入文件路径：<input_path>/<project_name>.json（PyCG 输出文件）
input_path = args.input_path + project_name + '.json'
# 输出目录路径：<output_path>/<project_name>/
output_path = args.output_path + project_name + '/'

# 若输出目录不存在则创建
if not os.path.exists(output_path):
    os.makedirs(output_path)

# 执行转换
pycg(project_name, input_path, output_path)