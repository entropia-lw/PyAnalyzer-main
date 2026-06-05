# =============================================================================
# S2_code_retrofitted_type.py
# 功能：读取 Type4Py 输出的类型推断 JSON 结果，
# 利用 Python AST 将推断出的参数类型注解（annotation）和返回值类型注解（return type）
# 回写到对应的 .py 源文件中，生成带类型注解的新版本源文件。
# =============================================================================

import glob, os, ast, astor   # glob：文件匹配；os：路径操作；ast：AST解析；astor：AST反序列化
import json                    # 解析 Type4Py 输出的 JSON 文件
import csv                     # CSV 模块
import astunparse               # AST库
from asttokens import ASTTokens # 保留源码 token 位置信息的 AST 库
import codegen  # 另一个 AST 代码生成库


if __name__ == '__main__':

    # -------------------------------------------------------------------------
    # 待处理的项目路径列表（与 S1_trace.py 保持一致）。
    # 此处的 JSON 文件是 S1_trace.py 生成的 Type4Py 分析结果。
    # -------------------------------------------------------------------------
    project_path_list = ["..\Data\RQ2\micro-benchmark B\Benchmark-collectedbyPyCG\args\assigned_call"]

    # 遍历每一个项目路径
    for index_path, project_path in enumerate(project_path_list):

        # 递归收集该目录下所有 .json 文件（即 S1 阶段生成的 Type4Py 结果）
        project_file_list = [f for f in glob.glob(f'{project_path}/**/*.json', recursive=True)]

        # 遍历每一个 JSON 结果文件
        for index, fp in enumerate(project_file_list):

            # 以 UTF-8 编码打开 JSON 文件
            with open(fp, 'r', encoding='utf-8', errors='replace') as f:
                try:
                    # 分离 JSON 文件所在目录与文件名
                    dirname, filename = os.path.split(fp)

                    # -------------------------------------------------------
                    # 构造对应 .py 源文件的读取路径（read_py_path）：
                    # 假设原始 .py 源文件存放在 E:\ 盘的相同相对路径下。
                    # 从 JSON 路径中截取 "testcode\" 之后的相对路径片段，
                    # 然后在 E:\ 下重建目录结构。
                    # -------------------------------------------------------
                    json_path_list = fp.split("testcode\\")[-1].split("\\")  # 截取相对路径并按 \ 分割为列表
                    py_path_dir = "E:\\"  # 原始 .py 文件的根目录（硬编码为 E 盘）
                    for json_index, json_path in enumerate(json_path_list):
                        if json_index != len(json_path_list) - 1:
                            # 除最后一个元素（文件名）外，逐级拼接目录路径
                            py_path_dir = os.path.join(py_path_dir, json_path)

                    # 最终原始 .py 文件路径：将 JSON 文件名的 .json 替换为 .py
                    read_py_path = py_path_dir + "\\" + filename.split(".")[0] + ".py"

                    # 回写后的 .py 文件保存路径：与 JSON 文件同目录，同名但扩展名为 .py
                    save_py_path = dirname + "\\" + filename.split(".")[0] + ".py"

                    # 解析 JSON 文件，获取 Type4Py 的完整分析结果
                    project_dump = json.loads(f.read())

                    # 取顶层函数列表（不在类中的函数）
                    funcs_list = project_dump.get("response").get("funcs")

                    # 取类列表
                    class_list = project_dump.get("response").get("classes")

                    # 将每个类内部的方法列表也合并进 funcs_list，统一处理
                    for class_item in class_list:
                        class_funcs_list = class_item.get("funcs")
                        funcs_list.extend(class_funcs_list)

                    # 打开原始 .py 文件，解析为 AST
                    with open(read_py_path, "r", encoding='utf-8', errors='replace') as py_fp:
                        ast_node = ast.parse(py_fp.read())  # 将源码解析为 AST 树

                        # 遍历 AST 中的所有节点
                        for item in ast.walk(ast_node):

                            # 只处理函数定义节点（FunctionDef）
                            if isinstance(item, ast.FunctionDef):

                                # 在 Type4Py 的函数列表中查找与当前 AST 函数同名的条目
                                for funcs_obj in funcs_list:
                                    if item.name == funcs_obj.get("name"):

                                        # -----------------------------------------
                                        # 处理参数类型注解
                                        # -----------------------------------------
                                        for arg in item.args.args:  # 遍历函数的所有参数
                                            try:
                                                # 从 Type4Py 结果中取该参数的第一个预测类型
                                                # params_p 结构：{参数名: [[类型字符串, 置信度], ...], ...}
                                                arg_type = funcs_obj.get("params_p").get(arg.arg)[0][0]
                                            except (IndexError, TypeError):
                                                # 若该参数无预测结果，则不添加注解
                                                arg_type = None

                                            if arg_type:
                                                # 将类型字符串作为 AST Name 节点，赋给参数的 annotation 字段
                                                arg.annotation = ast.Name(id=arg_type, ctx=ast.Load())

                                        # -----------------------------------------
                                        # 处理返回值类型注解
                                        # -----------------------------------------
                                        try:
                                            # ret_type_p 结构：[[类型字符串, 置信度], ...]
                                            return_type = funcs_obj.get("ret_type_p")[0][0]
                                        except (IndexError, TypeError):
                                            return_type = None

                                        if return_type:
                                            # 将类型字符串作为 AST Name 节点，赋给函数的 returns 字段
                                            item.returns = ast.Name(id=return_type, ctx=ast.Load())

                                        break  # 找到匹配函数后即退出内层循环

                        # 将修改后的 AST 反序列化回源码字符串，并写入目标 .py 文件
                        with open(save_py_path, "w", encoding='utf-8') as py_w_fp:
                            py_w_fp.write(ast.unparse(ast_node))  # ast.unparse：Python 3.9+ 内置的 AST→源码转换
                            print("save_py_path=", save_py_path)  # 打印保存路径，便于确认进度

                except Exception as e:
                    # 捕获所有异常，将错误信息追加记录到日志文件
                    with open("errors.log", "a") as f2:
                        print("error")
                        f2.write(str(e))  # 仅记录异常信息