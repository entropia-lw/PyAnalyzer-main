# =============================================================================
# analysis.py
# 功能：对指定项目，分别加载多个依赖分析工具（Understand、SourceTrail、PySonar2、
#       Depends、PyCG、PyAnalyzer、ENRE19、PyAnalyzerCFG、CallGraph、Type4Py）
#       输出的依赖关系 JSON，与人工标注的 ground truth 进行精确度（召回率）匹配，
#       将各工具的匹配数与准确率追加写入统一的 CSV 报告文件。
# =============================================================================

import argparse   # 命令行参数解析
import json       # JSON 文件读写
import os         # 文件系统操作

import pandas as pd  # 数据表读写，用于读取/更新 CSV 报告

df = pd.DataFrame()  # 全局空 DataFrame（占位，未实际使用）

# -------------------------------------------------------------------------
# 命令行参数：唯一参数为项目名称
# -------------------------------------------------------------------------
parser = argparse.ArgumentParser()
parser.add_argument('project')     # 位置参数：项目名
args = parser.parse_args()
project_name = args.project        # 取出项目名

# -------------------------------------------------------------------------
# 路径配置：所有输入/输出文件统一放在 result/<project_name>/ 下
# -------------------------------------------------------------------------
input_path = "result/" + project_name              # 工具输出文件所在目录
output_path = "result/" + project_name + "/gt/"   # ground truth 及 CSV 报告目录

# ground truth 依赖文件路径
gt_path = output_path + 'gt_dependency.json'
# 过滤后的 ground truth 依赖文件路径（仅保留目标模块内部的依赖）
filtered_gt_path = output_path + 'filtered_gt_dependency.json'

# 加载完整 ground truth 依赖列表
with open(gt_path, 'r', encoding='utf-8') as gt_file:
    gt = json.load(gt_file)['dependency']

# 加载过滤后的 ground truth 依赖列表
with open(filtered_gt_path, 'r', encoding='utf-8') as filtered_file:
    filtered_gt = json.load(filtered_file)['dependency']


# =============================================================================
# filters：对工具输出的依赖列表进行过滤处理。
#   - 去除 src/dest 为整数 id 的条目（id 尚未被替换为限定名时的脏数据）
#   - 根据 data/rr.json 中指定项目的过滤前缀，将两端均命中过滤规则的条目
#     归为"过滤后"列表，其余情况记录"额外"集合（两端之一未命中）
#   - 将额外集合写入 result/<project>/extra/<tool>.json
# 参数：project_name —— 当前项目名（用于读取过滤规则和构造输出路径）
#       tool         —— 工具名称字符串（用于构造输出文件名）
#       data         —— 工具输出的原始依赖列表
# 返回：(datalist, data_filtered_list)
#   datalist           —— 去除脏数据后的全量依赖列表
#   data_filtered_list —— 两端均在过滤前缀范围内的依赖列表
# =============================================================================
def filters(project_name, tool, data):
    datalist = list()           # 去脏后的全量列表
    data_filtered_list = list() # 满足过滤规则的列表

    # 读取过滤规则：rr.json 中 key 为项目名，value 为允许的前缀列表
    with open("data/rr.json", 'r', encoding='utf8') as filter_items:
        try:
            filters = json.load(filter_items)[project_name]  # 取当前项目的过滤前缀
        except:
            filters = []  # 若无配置则不过滤

    extra = set()  # 记录"溢出"端（一端在过滤范围内，另一端不在）

    for item in data:
        # 跳过 src 或 dest 仍为整数 id 的条目（实体名称替换未完成的脏数据）
        if type(item['src']) == int or type(item['dest']) == int:
            continue

        datalist.append(item)  # 加入全量列表

        # 若存在过滤规则，且两端均以指定前缀开头，则加入过滤后列表
        if item['src'].startswith(tuple(filters)) and \
                item['dest'].startswith(tuple(filters)) and len(filters) != 0:
            data_filtered_list.append(item)
        else:
            # 一端命中：记录另一端（溢出端），便于后续分析覆盖范围
            if item['src'].startswith(tuple(filters)):
                extra.add(item['dest'])
            else:
                extra.add(item['src'])

    # 构造额外集合的输出目录并写入 JSON
    output_path = "result/" + project_name + '/extra/'
    if not os.path.exists(output_path):
        os.makedirs(output_path)
    with open(output_path + tool + '.json', 'w') as result:
        result.write(str(list(extra)))

    return datalist, data_filtered_list


# =============================================================================
# match_mod1：模式1匹配 —— 用于 Understand / SourceTrail / PyAnalyzer 等工具。
#   柔性匹配（flex）：gt 条目的 src/dest 与工具条目的 src/dest 互为后缀（endswith）
#   严格匹配（strict）：在柔性匹配基础上，还需行号和列号完全一致
# 参数：gt         —— ground truth 依赖列表
#       dependency —— 工具输出的依赖列表
# 返回：(flex_num, strict_num)
# =============================================================================
def match_mod1(gt, dependency):
    flex_num = 0    # 柔性匹配计数
    strict_num = 0  # 严格匹配计数

    for item1 in gt:
        flag1 = False  # 是否命中柔性匹配
        flag2 = False  # 是否命中严格匹配

        for item2 in dependency:
            # 柔性匹配：双向后缀包含（处理路径前缀差异）
            if (item2['src'].endswith(item1['src']) and item2['dest'].endswith(item1['dest'])) or \
                    (item1['src'].endswith(item2['src']) and item1['dest'].endswith(item2['dest'])):
                flag1 = True
                # 在柔性匹配基础上进一步比较位置信息（行号、列号）
                if item1['location']['startLine'] == item2['location']['startLine'] and \
                        item1['location']['startCol'] == item2['location']['startCol']:
                    flag2 = True
                    break  # 严格命中即可退出内层循环

        if flag1: flex_num += 1
        if flag2: strict_num += 1

    return flex_num, strict_num


# =============================================================================
# match_mod2：模式2匹配 —— 用于 PySonar2。
#   柔性匹配：工具条目的 file 字段与 gt 的路径互为后缀，且 dest 互为后缀
#   严格匹配：在柔性匹配基础上，行号和列号完全一致
#   注：此函数中有一处拼写错误（endwith 应为 endswith），为保留原始逻辑未修改。
# =============================================================================
def match_mod2(gt, dependency):
    flex_num = 0
    strict_num = 0
    flex = list()    # 调试用：记录柔性匹配的对照条目（未实际输出）
    strict = list()  # 调试用：记录严格匹配的对照条目（未实际输出）

    for item1 in gt:
        flag1 = False
        flag2 = False

        for item2 in dependency:
            # 柔性匹配：file 路径后缀 + dest 后缀双向判断
            if (item2['file'].endswith(item1['location']['path']) and
                item2['dest'].endswith(item1['dest'])) or \
                    (item1['location']['path'].endswith(item2['file']) and
                     item1['dest'].endwith(item2['dest'])):  # 注：原代码 endwith 为拼写错误
                flag1 = True
                # 记录调试信息（gt 与工具的 src/dest 对照）
                flex_dict = dict()
                flex_dict['gt_src'] = item1['src']
                flex_dict['und_src'] = item2['src']
                flex_dict['gt_dest'] = item1['dest']
                flex_dict['und_dest'] = item2['dest']
                flex.append(flex_dict)

                # 严格匹配：行号和列号一致
                if item1['location']['startLine'] == item2['location']['startLine'] and \
                        item1['location']['startCol'] == item2['location']['startCol']:
                    flag2 = True
                    break

        if flag1: flex_num += 1
        if flag2: strict_num += 1

    return flex_num, strict_num


# =============================================================================
# match_mod3：模式3匹配 —— 用于 Depends。
#   柔性匹配：仅比较 src 和 dest 的最后一段（split('.')[-1]），忽略命名空间前缀
#   严格匹配：在柔性匹配基础上，行号和列号完全一致
# =============================================================================
def match_mod3(gt, dependency):
    flex_num = 0
    strict_num = 0
    flex = list()
    strict = list()

    for item1 in gt:
        flag1 = False
        flag2 = False

        for item2 in dependency:
            # 柔性匹配：只比较最后一级名称（如函数名或类名）
            if item1['src'].split('.')[-1] == item2['src'].split('.')[-1] \
                    and item1['dest'].split('.')[-1] == item2['dest'].split('.')[-1]:
                flag1 = True
                flex_dict = dict()
                flex_dict['gt_src'] = item1['src']
                flex_dict['depends_src'] = item2['src']
                flex_dict['gt_dest'] = item1['dest']
                flex_dict['depends_dest'] = item2['dest']
                flex.append(flex_dict)

                # 严格匹配：行列号一致
                if item1['location']['startLine'] == item2['location']['startLine'] and \
                        item1['location']['startCol'] == item2['location']['startCol']:
                    flag2 = True
                    # 注：此处无 break，会继续遍历（与 mod1/mod2 行为略有不同）

        if flag1: flex_num += 1
        if flag2: strict_num += 1

    return flex_num, strict_num


# =============================================================================
# match_mod4：模式4匹配 —— 用于 PyCG / ENRE19 / CallGraph。
#   柔性匹配：src/dest 互为后缀（同 mod1），但不做严格匹配
#   注：strict_num 始终为 0（这两个工具不提供位置信息）
# =============================================================================
def match_mod4(gt, dependency):
    flex_num = 0
    strict_num = 0  # 此函数中始终为 0
    flex = list()
    strict = list()

    for item1 in gt:
        for item2 in dependency:
            # 柔性匹配：双向后缀包含
            if (item2['src'].endswith(item1['src']) and item2['dest'].endswith(item1['dest'])) or \
                    (item1['src'].endswith(item2['src']) and item1['dest'].endswith(item2['dest'])):
                flex_num += 1
                flex_dict = dict()
                flex_dict['gt_src'] = item1['src']
                flex_dict['pycg_src'] = item2['src']
                flex_dict['gt_dest'] = item1['dest']
                flex_dict['pycg_dest'] = item2['dest']
                flex.append(flex_dict)
                break  # 找到第一个匹配即算命中，跳出内层循环

    return flex_num, strict_num


# =============================================================================
# analyse_und：分析 Understand 工具的输出结果。
#   1. 读取 Understand 输出的实体 JSON 和依赖 JSON
#   2. 将依赖中的整数 id 替换为实体的限定名（去除路径前缀，统一为点分格式）
#   3. 调用 filters() 过滤，再用 match_mod1() 匹配
#   4. 将结果写入 CSV 报告
# =============================================================================
def analyse_und(project_name, input_path, output_path):
    filter = "D:/Programs/SciTools/conf/understand/python/python3/"  # Understand 内置库路径前缀，需去除
    dependency_path = input_path + '/Understand_' + project_name + '_dependency.json'
    entity_path = input_path + '/Understand_' + project_name + '_entity.json'

    # 加载依赖列表
    with open(dependency_path, 'r', encoding='utf8') as json_file:
        dependency = json.load(json_file)['dependency']

    # 加载实体列表
    with open(entity_path, 'r', encoding='utf8') as entity_file:
        entity = json.load(entity_file)['entity']

    entity_dict = {}
    for item in entity:
        # 去掉 .py 扩展名，转换为模块名
        if item['qualifiedName'].endswith('.py'):
            item['qualifiedName'] = item['qualifiedName'][:-3]
        # 去除内置库路径前缀，将路径分隔符 / 改为点，使其成为点分限定名
        item['qualifiedName'] = item['qualifiedName'].replace(filter, '').replace('/', '.')
        # 去除开头多余的点
        while item['qualifiedName'].startswith('.'):
            item['qualifiedName'] = item['qualifiedName'][1:]
        entity_dict[item['id']] = item  # 建立 id → 实体 的映射

    # 将依赖列表中的 src/dest id 替换为对应的限定名
    for item in dependency:
        if item['src'] in entity_dict and item['dest'] in entity_dict:
            item['src'] = entity_dict[item['src']]['qualifiedName']
            item['dest'] = entity_dict[item['dest']]['qualifiedName']

    # 过滤，并获取全量和过滤后的依赖列表
    dependency, filtered_dependency = filters(project_name, 'und', dependency)

    # 用 mod1 匹配（全量 gt vs 全量 dependency，过滤 gt vs 过滤 dependency）
    flex_num, strict_num = match_mod1(gt, dependency)
    f_flex_num, f_strict_num = match_mod1(filtered_gt, filtered_dependency)

    # 读取已有 CSV 报告，追加 Understand 的统计列
    acc_path = output_path + project_name + '_acc.csv'
    data = pd.read_csv(acc_path)
    data['understand_num'] = [flex_num, strict_num, f_flex_num, f_strict_num]

    # 避免除以 0：len(gt) 最小取 1
    len_gt = max(len(gt), 1)
    len_filtered_gt = max(len(filtered_gt), 1)

    # 计算准确率（保留两位小数字符串）
    data['understand_acc'] = ["{:.2f}".format(flex_num / len_gt),
                              "{:.2f}".format(strict_num / len_gt),
                              "{:.2f}".format(f_flex_num / len_filtered_gt),
                              "{:.2f}".format(f_strict_num / len_filtered_gt)]
    data.to_csv(acc_path)  # 写回 CSV

    # 将全量依赖数据也保存为临时 JSON，便于人工检查
    outfile = output_path + 'understand_temp.json'
    dependency_str = json.dumps(dependency, indent=4)
    with open(outfile, 'w') as json_file:
        json_file.write(dependency_str)


# =============================================================================
# analyse_srctrl：分析 SourceTrail 工具的输出结果。
#   流程与 analyse_und 类似，但实体字段名为 'entities'，依赖字段名为 'relations'。
# =============================================================================
def analyse_srctrl(project_name, input_path, output_path):
    filter = "D:/Programs/SciTools/conf/understand/python/python3/"
    dependency_path = input_path + '/sourcetrail_' + project_name + '_dependency.json'
    entity_path = input_path + '/sourcetrail_' + project_name + '_entity.json'

    with open(dependency_path, 'r', encoding='utf8') as json_file:
        dependency = json.load(json_file)['relations']  # SourceTrail 的依赖字段名为 relations

    with open(entity_path, 'r', encoding='utf8') as entity_file:
        entity = json.load(entity_file)['entities']     # SourceTrail 的实体字段名为 entities

    entity_dict = {}
    for item in entity:
        if item['qualifiedName'].endswith('.py'):
            item['qualifiedName'] = item['qualifiedName'][:-3]
        item['qualifiedName'] = item['qualifiedName'].replace(filter, '').replace('/', '.')
        while item['qualifiedName'].startswith('.'):
            item['qualifiedName'] = item['qualifiedName'][1:]
        entity_dict[item['id']] = item

    for item in dependency:
        if item['src'] in entity_dict and item['dest'] in entity_dict:
            item['src'] = entity_dict[item['src']]['qualifiedName']
            item['dest'] = entity_dict[item['dest']]['qualifiedName']

    dependency, filtered_dependency = filters(project_name, 'srctrl', dependency)

    flex_num, strict_num = match_mod1(gt, dependency)
    f_flex_num, f_strict_num = match_mod1(filtered_gt, filtered_dependency)

    acc = output_path + project_name + '_acc.csv'
    data = pd.read_csv(acc)

    len_gt = max(len(gt), 1)
    len_filtered_gt = max(len(filtered_gt), 1)
    data['srctrl_num'] = [flex_num, strict_num, f_flex_num, f_strict_num]
    data['srctrl_acc'] = ["{:.2f}".format(flex_num / len_gt),
                          "{:.2f}".format(strict_num / len_gt),
                          "{:.2f}".format(f_flex_num / len_filtered_gt),
                          "{:.2f}".format(f_strict_num / len_filtered_gt)]
    data.to_csv(acc, index=False)


# =============================================================================
# analyse_pysonar：分析 PySonar2 工具的输出结果。
#   特殊处理：PySonar2 输出的 dest 字段为文件路径，需转换为点分模块名；
#            src 字段直接设为转换后的 dest（PySonar2 不区分 src 调用者）。
#   使用 match_mod2 进行匹配（基于文件路径而非限定名）。
# =============================================================================
def analyse_pysonar(project_name, input_path, output_path):
    dependency_path = input_path + '/PySonar2_' + project_name + '_dependency.json'
    entity_path = input_path + '/PySonar2_' + project_name + '_entity.json'

    with open(dependency_path, 'r', encoding='utf8') as json_file:
        dependency = json.load(json_file)['dependency']

    for item in dependency:
        # 去掉 .py 扩展名
        if item['dest'].endswith('.py'):
            item['dest'] = item['dest'][:-3]
        # 将路径分隔符替换为点
        item['dest'] = item['dest'].replace('/', '.')
        # 去除开头多余的点
        while item['dest'].startswith('.'):
            item['dest'] = item['dest'][1:]
        item['src'] = item['dest']  # src 与 dest 设为相同（PySonar2 无调用者信息）

    dependency, filtered_dependency = filters(project_name, 'pysonar', dependency)

    flex_num, strict_num = match_mod2(gt, dependency)
    f_flex_num, f_strict_num = match_mod2(filtered_gt, filtered_dependency)

    acc = output_path + project_name + '_acc.csv'
    data = pd.read_csv(acc)
    len_gt = max(len(gt), 1)
    len_filtered_gt = max(len(filtered_gt), 1)
    data['pysonar2_num'] = [flex_num, strict_num, f_flex_num, f_strict_num]
    data['pysonar2_acc'] = ["{:.2f}".format(flex_num / len_gt), "{:.2f}".format(strict_num / len_gt),
                            "{:.2f}".format(f_flex_num / len_filtered_gt),
                            "{:.2f}".format(f_strict_num / len_filtered_gt)]
    data.to_csv(acc, index=False)


# =============================================================================
# analyse_depends：分析 Depends 工具的输出结果。
#   与 analyse_und 类似，但使用 match_mod3（仅比较名称最后一段）。
# =============================================================================
def analyse_depends(project_name, input_path, output_path):
    filter = "D:/Programs/SciTools/conf/understand/python/python3/"
    dependency_path = input_path + '/depends_' + project_name + '_dependency.json'
    entity_path = input_path + '/depends_' + project_name + '_entity.json'

    with open(dependency_path, 'r', encoding='utf8') as json_file:
        dependency = json.load(json_file)['dependency']

    with open(entity_path, 'r', encoding='utf8') as entity_file:
        entity = json.load(entity_file)['entity']

    entity_dict = {}
    for item in entity:
        if item['qualifiedName'].endswith('.py'):
            item['qualifiedName'] = item['qualifiedName'][:-3]
        item['qualifiedName'] = item['qualifiedName'].replace(filter, '').replace('/', '.')
        while item['qualifiedName'].startswith('.'):
            item['qualifiedName'] = item['qualifiedName'][1:]
        entity_dict[item['id']] = item

    for item in dependency:
        if item['src'] in entity_dict and item['dest'] in entity_dict:
            item['src'] = entity_dict[item['src']]['qualifiedName']
            item['dest'] = entity_dict[item['dest']]['qualifiedName']

    dependency, filtered_dependency = filters(project_name, 'depends', dependency)

    # Depends 使用 mod3 匹配（仅比较最后一级名称）
    flex_num, strict_num = match_mod3(gt, dependency)
    f_flex_num, f_strict_num = match_mod3(filtered_gt, filtered_dependency)

    acc = output_path + project_name + '_acc.csv'
    data = pd.read_csv(acc)
    len_gt = max(len(gt), 1)
    len_filtered_gt = max(len(filtered_gt), 1)
    data['depends_num'] = [flex_num, strict_num, f_flex_num, f_strict_num]
    data['depends_acc'] = ["{:.2f}".format(flex_num / len_gt), "{:.2f}".format(strict_num / len_gt),
                           "{:.2f}".format(f_flex_num / len_filtered_gt),
                           "{:.2f}".format(f_strict_num / len_filtered_gt)]
    data.to_csv(acc, index=False)


# =============================================================================
# analyse_pycg：分析 PyCG 工具的输出结果。
#   特殊处理：将 src/dest 中的反斜杠和 '<' 替换为点，统一路径格式；
#            再去除开头多余的点。
#   使用 match_mod4（柔性后缀匹配，无严格匹配）。
# =============================================================================
def analyse_pycg(project_name, input_path, output_path):
    filter = "D:/Programs/SciTools/conf/understand/python/python3/"
    dependency_path = input_path + '/pycg_' + project_name + '_dependency.json'

    with open(dependency_path, 'r', encoding='utf8') as json_file:
        dependency = json.load(json_file)['dependency']

    for item in dependency:
        # 统一路径格式：反斜杠 → 点，< → 点（处理 PyCG 的特殊符号）
        item['src'] = item['src'].replace('\\', '.').replace('<', '.')
        item['dest'] = item['dest'].replace('\\', '.').replace('<', '.')
        # 去除开头多余的点
        while item['src'].startswith('.'):
            item['src'] = item['src'][1:]
        while item['dest'].startswith('.'):
            item['dest'] = item['dest'][1:]

    dependency, filtered_dependency = filters(project_name, 'pycg', dependency)

    flex_num, strict_num = match_mod4(gt, dependency)
    f_flex_num, f_strict_num = match_mod4(filtered_gt, filtered_dependency)

    acc = output_path + project_name + '_acc.csv'
    data = pd.read_csv(acc)
    len_gt = max(len(gt), 1)
    len_filtered_gt = max(len(filtered_gt), 1)
    data['pycg_num'] = [flex_num, strict_num, f_flex_num, f_strict_num]
    data['pycg_acc'] = ["{:.2f}".format(flex_num / len_gt), "{:.2f}".format(strict_num / len_gt),
                        "{:.2f}".format(f_flex_num / len_filtered_gt),
                        "{:.2f}".format(f_strict_num / len_filtered_gt)]
    data.to_csv(acc, index=False)


# =============================================================================
# analyse_pyanalyzer：分析 PyAnalyzer 工具的输出结果（dep JSON 格式）。
#   dep JSON 结构：{"variables": [...], "cells": [...]}
#   实体字段 'variables'，依赖字段 'cells'。
#   与 analyse_und 处理逻辑类似，但直接使用 PyAnalyzer 原生 JSON 格式。
#   使用 match_mod1。
# =============================================================================
def analyse_pyanalyzer(project_name, input_path, output_path):
    filter = "D:/Programs/SciTools/conf/understand/python/python3/"
    json_path = input_path + '-report-pyanalyzer.json'  # PyAnalyzer 报告文件名规范

    with open(json_path, 'r', encoding='utf8') as json_file:
        data = json.load(json_file)

    entity = data['variables']      # 实体列表
    dependency = data['cells']      # 依赖关系列表

    entity_dict = {}
    for item in entity:
        # 若限定名以 .py 结尾，则去掉后缀（并同时去除内置库路径前缀，统一格式）
        if item['qualifiedName'].endswith('.py'):
            item['qualifiedName'] = item['qualifiedName'][:-3].replace(filter, '').replace('/', '.')
        entity_dict[item['id']] = item

    # 将依赖中的 id 替换为限定名
    for item in dependency:
        if item['src'] in entity_dict and item['dest'] in entity_dict:
            item['src'] = entity_dict[item['src']]['qualifiedName']
            item['dest'] = entity_dict[item['dest']]['qualifiedName']

    dependency, filtered_dependency = filters(project_name, 'pyanalyzer', dependency)

    flex_num, strict_num = match_mod1(gt, dependency)
    f_flex_num, f_strict_num = match_mod1(filtered_gt, filtered_dependency)

    acc = output_path + project_name + '_acc.csv'
    data = pd.read_csv(acc)
    len_gt = max(len(gt), 1)
    len_filtered_gt = max(len(filtered_gt), 1)
    data['pyanalyzer_num'] = [flex_num, strict_num, f_flex_num, f_strict_num]
    data['pyanalyzer_acc'] = ["{:.2f}".format(flex_num / len_gt), "{:.2f}".format(strict_num / len_gt),
                        "{:.2f}".format(f_flex_num / len_filtered_gt),
                        "{:.2f}".format(f_strict_num / len_filtered_gt)]
    data.to_csv(acc, index=False)


# =============================================================================
# analyse_enre19：分析 ENRE19 工具的输出结果。
#   ENRE19 的 JSON 格式：{"variables": {id: name, ...}, "cells": [...]}
#   与其他工具不同，variables 是一个 id→name 的字典（而非列表），
#   依赖中的 src/dest 直接为整数 id，通过索引替换为名称。
#   使用 match_mod4。
# =============================================================================
def analyse_enre19(project_name, input_path, output_path):
    filter = "D:/Programs/SciTools/conf/understand/python/python3/"
    json_path = input_path + '_allExpImp_call.json'  # ENRE19 输出文件名规范

    with open(json_path, 'r', encoding='utf8') as json_file:
        data = json.load(json_file)

    entity = data['variables']    # dict：{id(int或str): name}
    dependency = data['cells']    # list，src/dest 为整数 id

    # 直接用实体字典的值（名称）替换依赖中的 id
    for item in dependency:
        item['src'] = entity[item['src']]   # id → 名称
        item['dest'] = entity[item['dest']]

    dependency, filtered_dependency = filters(project_name, 'enre19', dependency)

    flex_num, strict_num = match_mod4(gt, dependency)
    f_flex_num, f_strict_num = match_mod4(filtered_gt, filtered_dependency)

    acc = output_path + project_name + '_acc.csv'
    data = pd.read_csv(acc)
    len_gt = max(len(gt), 1)
    len_filtered_gt = max(len(filtered_gt), 1)
    data['enre19_num'] = [flex_num, strict_num, f_flex_num, f_strict_num]
    data['enre19_acc'] = ["{:.2f}".format(flex_num / len_gt), "{:.2f}".format(strict_num / len_gt),
                           "{:.2f}".format(f_flex_num / len_filtered_gt),
                           "{:.2f}".format(f_strict_num / len_filtered_gt)]
    data.to_csv(acc, index=False)


# =============================================================================
# analyse_pyanalyzercfg：分析 PyAnalyzerCFG（含控制流图的 PyAnalyzer 变体）输出。
#   与 analyse_pyanalyzer 逻辑完全相同，区别仅在于 filter 变量（此处声明但未使用）
#   和写入的列名（pyanalyzercfg_num / pyanalyzercfg_acc）。
#   使用 match_mod1。
# =============================================================================
def analyse_pyanalyzercfg(project_name, input_path, output_path):
    filter = "D:/Programs/SciTools/conf/understand/python/python3/"
    json_path = input_path + '-report-pyanalyzer.json'

    with open(json_path, 'r', encoding='utf8') as json_file:
        data = json.load(json_file)

    entity = data['variables']
    dependency = data['cells']

    entity_dict = {}
    for item in entity:
        if item['qualifiedName'].endswith('.py'):
            item['qualifiedName'] = item['qualifiedName'][:-3]
        item['qualifiedName'] = item['qualifiedName'].replace(filter, '').replace('/', '.')
        entity_dict[item['id']] = item

    for item in dependency:
        if item['src'] in entity_dict and item['dest'] in entity_dict:
            item['src'] = entity_dict[item['src']]['qualifiedName']
            item['dest'] = entity_dict[item['dest']]['qualifiedName']

    dependency, filtered_dependency = filters(project_name, 'pyanalyzercfg', dependency)

    flex_num, strict_num = match_mod1(gt, dependency)
    f_flex_num, f_strict_num = match_mod1(filtered_gt, filtered_dependency)

    acc = output_path + project_name + '_acc.csv'
    data = pd.read_csv(acc)
    len_gt = max(len(gt), 1)
    len_filtered_gt = max(len(filtered_gt), 1)
    data['pyanalyzercfg_num'] = [flex_num, strict_num, f_flex_num, f_strict_num]
    data['pyanalyzercfg_acc'] = ["{:.2f}".format(flex_num / len_gt), "{:.2f}".format(strict_num / len_gt),
                           "{:.2f}".format(f_flex_num / len_filtered_gt),
                           "{:.2f}".format(f_strict_num / len_filtered_gt)]
    data.to_csv(acc, index=False)


# =============================================================================
# analyse_callgraph：分析 CFG 调用图工具的输出结果。
#   特殊处理：重新从文件加载 gt 和 filtered_gt（局部变量，覆盖全局）；
#            与 analyse_pycg 的格式处理相同（反斜杠/< → 点）；
#            使用 match_mod4。
# =============================================================================
def analyse_callgraph(project_name, input_path, output_path):
    dependency_path = input_path + '/cfg-callgraph-' + project_name + '-dependency.json'

    # 重新加载 gt（此处为局部变量，覆盖全局 gt 和 filtered_gt）
    with open(gt_path, 'r', encoding='utf-8') as gt_file:
        gt = json.load(gt_file)['dependency']
    with open(filtered_gt_path, 'r', encoding='utf-8') as filtered_file:
        filtered_gt = json.load(filtered_file)['dependency']

    with open(dependency_path, 'r', encoding='utf8') as json_file:
        dependency = json.load(json_file)['dependency']

    for item in dependency:
        item['src'] = item['src'].replace('\\', '.').replace('<', '.')
        item['dest'] = item['dest'].replace('\\', '.').replace('<', '.')
        while item['src'].startswith('.'):
            item['src'] = item['src'][1:]
        while item['dest'].startswith('.'):
            item['dest'] = item['dest'][1:]

    dependency, filtered_dependency = filters(project_name, 'pycg', dependency)  # filter 键复用 pycg

    flex_num, strict_num = match_mod4(gt, dependency)
    f_flex_num, f_strict_num = match_mod4(filtered_gt, filtered_dependency)

    acc = output_path + project_name + '_acc.csv'
    data = pd.read_csv(acc)
    len_gt = max(len(gt), 1)
    len_filtered_gt = max(len(filtered_gt), 1)
    data['callgraph_num'] = [flex_num, strict_num, f_flex_num, f_strict_num]
    data['callgraph_acc'] = ["{:.2f}".format(flex_num / len_gt), "{:.2f}".format(strict_num / len_gt),
                             "{:.2f}".format(f_flex_num / len_filtered_gt),
                             "{:.2f}".format(f_strict_num / len_filtered_gt)]
    data.to_csv(acc, index=False)


# =============================================================================
# analyse_type4py：分析 Type4Py 工具的输出结果（Type4Py 为类型推断辅助的变体）。
#   特殊处理：重新从文件加载 gt 和 filtered_gt（局部变量）；
#            同时加载两个版本的 dependency（gt_dependency 和 filtered_gt_dependency），
#            直接使用已过滤好的文件，不再调用 filters()；
#            额外将 gt 行数写入 CSV（便于后续计算）。
#   使用 match_mod1。
# =============================================================================
def analyse_type4py(project_name, input_path, output_path):
    dependency_path = input_path + "gt_dependency.json"
    filtered_path = input_path + "filtered_gt_dependency.json"

    # 重新加载 gt（局部变量）
    with open(gt_path, 'r', encoding='utf-8') as gt_file:
        gt = json.load(gt_file)['dependency']
    with open(filtered_gt_path, 'r', encoding='utf-8') as filtered_file:
        filtered_gt = json.load(filtered_file)['dependency']

    # 直接加载 Type4Py 输出的两个版本（全量和过滤后）
    with open(dependency_path, 'r', encoding='utf8') as json_file:
        dependency = json.load(json_file)['dependency']
    with open(filtered_path, 'r', encoding='utf8') as json_file:
        filtered_dependency = json.load(json_file)['dependency']

    flex_num, strict_num = match_mod1(gt, dependency)
    f_flex_num, f_strict_num = match_mod1(filtered_gt, filtered_dependency)

    acc = output_path + project_name + '_acc.csv'
    data = pd.read_csv(acc)
    len_gt = max(len(gt), 1)
    len_filtered_gt = max(len(filtered_gt), 1)
    data['type4py_num'] = [flex_num, strict_num, f_flex_num, f_strict_num]
    data['type4py_acc'] = ["{:.2f}".format(flex_num / len_gt), "{:.2f}".format(strict_num / len_gt),
                           "{:.2f}".format(f_flex_num / len_filtered_gt),
                           "{:.2f}".format(f_strict_num / len_filtered_gt)]

    # 额外记录 gt 行数（完整 gt 和过滤后 gt），方便后续查看绝对数量
    data['gt'] = [len(gt), len(gt), len(filtered_gt), len(filtered_gt)]
    data.to_csv(acc, index=False)


# -------------------------------------------------------------------------
# 初始化 CSV 报告：
#   报告有 4 行，分别对应：flexible、strict、filtered_flexible、filtered_strict
#   第一列为项目名，后续各工具的统计列将逐步追加
# -------------------------------------------------------------------------
data = {
    project_name: ['flexible', 'strict', 'filtered_flexible', 'filtered_strict'],
}
df_acc = pd.DataFrame(data)
acc = output_path + project_name + '_acc.csv'
df_acc.to_csv(acc, index=False, header=True)  # 初始化写出，带列头，不带行索引

# -------------------------------------------------------------------------
# 按顺序调用各工具的分析函数，将结果追加到同一份 CSV 报告中
# -------------------------------------------------------------------------

# 分析 Understand（输入路径：result/<project>）
analyse_und(project_name, input_path, output_path)

# 分析 SourceTrail（输入路径：result/<project>）
analyse_srctrl(project_name, input_path, output_path)

# 分析 PySonar2（输入路径：result/<project>）
analyse_pysonar(project_name, input_path, output_path)

# 分析 Depends（输入路径：result/<project>）
analyse_depends(project_name, input_path, output_path)

# 分析 PyCG（输入路径：result/<project>）
analyse_pycg(project_name, input_path, output_path)

# 分析 PyAnalyzer（输入路径切换为 data/pyanalyzer/<project>）
input_path = "data/pyanalyzer/" + project_name
analyse_pyanalyzer(project_name, input_path, output_path)

# 分析 ENRE19（输入路径切换为 data/enre19/<project>）
input_path = "data/enre19/" + project_name
analyse_enre19(project_name, input_path, output_path)

# 分析 PyAnalyzerCFG（输入路径切换为 data/pyanalyzer-cfg/<project>）
input_path = "data/pyanalyzer-cfg/" + project_name
analyse_pyanalyzercfg(project_name, input_path, output_path)

# 分析 CFG 调用图（输入路径切换回 result/<project>）
input_path = "result/" + project_name
analyse_callgraph(project_name, input_path, output_path)

# 分析 Type4Py（输入路径切换为 result/<project>/type4py/）
input_path = "result/" + project_name + '/type4py/'
analyse_type4py(project_name, input_path, output_path)