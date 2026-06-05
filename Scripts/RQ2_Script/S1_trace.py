# =============================================================================
# S1_trace.py
# 功能：自动调用 Type4Py（本地REST服务）对指定项目目录下的所有 .py 文件进行
#       类型推断分析，并将每个文件的分析结果保存为同名 .json 文件。
# =============================================================================

import json
import sys
import csv
import requests
import os, glob

if __name__ == '__main__':

    # -------------------------------------------------------------------------
    # 待分析的项目路径列表。
    # 可在此列表中添加多个项目路径，脚本会依次处理每一个。
    # 路径示例为 PyCG 收集的 micro-benchmark 中的 args/assigned_call 子案例。
    # -------------------------------------------------------------------------
    project_path_list = ["..\Data\RQ2\micro-benchmark B\Benchmark-collectedbyPyCG\args\assigned_call"]

    # 遍历列表中的每一个项目路径
    for index_path, project_path in enumerate(project_path_list):

        print(project_path)  # 打印当前正在处理的项目路径，便于调试跟踪

        # 递归收集该项目目录下所有 .py 文件的完整路径
        project_file_list = [f for f in glob.glob(f'{project_path}/**/*.py', recursive=True)]

        # 取路径最后一段作为项目名称（即目录名）
        project_name = project_path.split("\\")[-1]

        # 获取本脚本所在目录，用于构造结果保存路径
        current_dir = os.path.dirname(__file__)

        # 结果 JSON 文件的顶层保存目录：<脚本目录>/<项目名>
        save_parent_path = os.path.join(current_dir, project_name)

        # 遍历该项目下收集到的每一个 .py 源文件
        for index, fp in enumerate(project_file_list):

            # 以 UTF-8 编码打开源文件，遇到无法解码的字节时用替代符替换
            with open(fp, 'r', encoding='utf-8', errors='replace') as f:
                try:
                    # 向 Type4Py 本地 REST 服务发送 POST 请求：
                    #   - URL：http://localhost:5001/api/predict?tc=0
                    #   - 请求体：源文件的完整文本内容
                    #   - tc=0 表示不使用类型上下文（type context）
                    r = requests.post("http://localhost:5001/api/predict?tc=0", f.read())

                    # 分离文件所在目录与文件名
                    dirname, filename = os.path.split(fp)

                    # 判断当前文件是否直接位于项目根目录下（无子目录层级）
                    if len(dirname.split(project_name + "\\")) == 1:
                        # 文件在项目根目录，直接保存到顶层结果目录
                        save_path_dir = save_parent_path
                    else:
                        # 文件在子目录中，取项目名之后的相对子路径
                        save_children_dir = dirname.split(project_name + "\\")[-1]
                        # 在顶层结果目录下重建相同的子目录结构
                        save_path_dir = os.path.join(save_parent_path, save_children_dir)

                    # 若目标保存目录不存在，则递归创建
                    if not os.path.exists(save_path_dir):
                        os.makedirs(save_path_dir)

                    # 将 .py 扩展名替换为 .json，作为结果文件名
                    save_file_name = filename.split(".")[0] + ".json"

                    # 将 Type4Py 返回的 JSON 结果写入对应 .json 文件
                    with open(save_path_dir + "\\" + save_file_name, "w") as f1:
                        f1.write(json.dumps((r.json())))  # r.json() 解析响应体为 dict，再序列化写入

                except Exception as e:
                    # 捕获所有异常：打印提示，并将出错文件路径及异常信息追加到日志
                    print("error")
                    with open("errors.log", "a") as f2:
                        f2.write(fp)          # 记录出错的文件路径
                        f2.write(str(e))      # 记录具体错误信息
                        continue              # 跳过当前文件，继续处理下一个