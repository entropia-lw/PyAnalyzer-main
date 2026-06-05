# =============================================================================
# Understand.py
# 功能：调用 SciTools Understand Python API，从 .und 数据库中提取实体和关系，
#       分别序列化为两个 JSON 文件（实体 JSON 和依赖关系 JSON），
#       供 analysis.py 等下游脚本使用。
# 命令行参数：
#   lang    —— 目标语言（cpp/java/python/js）
#   project —— 项目名称
#   dbpath  —— .und 数据库目录
#   output  —— 输出目录
#   prepath —— 项目根路径（暂未在主逻辑中使用）
#   -p      —— 仅保存包含实体和关系的单一文件（当前代码未实际使用此标志）
# =============================================================================

import json      # JSON 序列化
import argparse  # 命令行参数解析
import re        # 正则表达式（contain 函数）
import understand  # SciTools Understand Python API


def contain(keyword, raw):
    """检测 raw 字符串中是否包含以 keyword 开头的单词（用于类型过滤）。"""
    return bool(re.search(r'(^| )%s' % keyword, raw))


def Entity(entityID, entityName, entityType, entityFile=None,
           startLine=-1, startColumn=-1, endColumn=-1, endLine=-1):
    """
    构造标准实体字典。
    参数：
      entityID   —— 实体唯一 id（来自 Understand 数据库）
      entityName —— 实体限定名（路径分隔符统一为 /）
      entityType —— 实体类型字符串（如 "Function"、"Class"）
      entityFile —— 所属文件相对路径（可选）
      startLine/startColumn/endLine/endColumn —— 源码位置（默认 -1 表示未知）
    """
    entity = dict()
    location = dict()
    entity['id'] = entityID
    entity['qualifiedName'] = entityName.replace('\\', '/')  # 统一路径分隔符
    entity['category'] = entityType

    location['startLine'] = startLine
    location['startColumn'] = startColumn
    location['endColumn'] = endColumn
    location['endLine'] = endLine
    entity['location'] = location

    if entityFile is not None:
        entity['file'] = entityFile.replace('\\', '/')  # 统一路径分隔符

    return entity


def Dependency(dependencyType, dependencySrcID, dependencydestID,
               startLine=-1, startColumn=-1):
    """
    构造标准依赖关系字典。
    参数：
      dependencyType   —— 关系类型（来自 Understand ref.kind().longname()，如 "Call"）
      dependencySrcID  —— 调用者实体 id
      dependencydestID —— 被调用者实体 id
      startLine/startColumn —— 依赖发生的源码位置
    """
    dependency = dict()
    values = dict()
    location = dict()

    dependency['src'] = dependencySrcID
    dependency['dest'] = dependencydestID
    values['kind'] = dependencyType
    dependency['values'] = values
    location['startLine'] = startLine
    location['startCol'] = startColumn
    dependency['location'] = location

    return dependency


def outputAll(entity_list: list, relation_list: list, json_path: str, projectname: str):
    """将实体列表和关系列表合并写入单一 JSON 文件（备用，当前主流程未调用）。"""
    file = dict()
    file["entities"] = entity_list
    file["relations"] = relation_list
    dependency_str = json.dumps(file, indent=4)
    with open(json_path, 'w') as json_file:
        json_file.write(dependency_str)


def output(info_list: list, json_path: str, type: str, projectname: str):
    """将 info_list 包装为 {type: info_list} 结构写入 JSON 文件。"""
    file = dict()
    file[type] = info_list
    dependency_str = json.dumps(file, indent=4)
    with open(json_path, 'w') as json_file:
        json_file.write(dependency_str)


# -------------------------------------------------------------------------
# 命令行参数解析
# -------------------------------------------------------------------------
parser = argparse.ArgumentParser()
parser.add_argument('lang', help='Sepcify the target language:cpp, java, python, js')
parser.add_argument('project', help='Specify the project name')
parser.add_argument('dbpath', help='Specify the database path')
parser.add_argument('output', help='Specify the output path')
parser.add_argument('prepath', help='Specify the path your project in')
parser.add_argument('-p', help='only save a file containing entities and relations',
                    action=argparse.BooleanOptionalAction)
args = parser.parse_args()

print_mode = args.p  # 是否使用简化输出模式（当前逻辑中未实际区分处理）
lang = args.lang

# 校验语言参数
try:
    ['cpp', 'java', 'python', 'js'].index(lang)
except:
    raise ValueError(f'Invalid lang {lang}, only support cpp / java / python')

project_name = args.project
database_path = args.dbpath + project_name + '.und'  # Understand 数据库文件路径
output_path = args.output + project_name + '/'        # 输出目录

print('Openning udb file...')
db = understand.open(database_path)  # 打开 Understand 数据库

ent_list = []  # 最终输出的实体列表

# -------------------------------------------------------------------------
# 第一步：提取文件（File）实体
# -------------------------------------------------------------------------
print('Exporting File entities...')
file_count = 0
for ent in db.ents('File'):
    # 仅保留 C++、C、Java、Python、Web（JS）文件
    if (ent.language() == 'C++') | (ent.language() == 'C') | (ent.language() == 'Java') | \
            (ent.language() == 'Python') | (ent.language() == 'Web'):
        ent_list.append(Entity(ent.id(), ent.relname(), 'File'))  # relname()：相对路径
        file_count += 1
print(f'Total {file_count} files are successfully exported')

print('Exporting entities other that File...')
regular_count = 0

# -------------------------------------------------------------------------
# 第二步：提取包（Package）和命名空间（Namespace）实体
# 这类实体不属于任何具体文件，需单独处理
# -------------------------------------------------------------------------
for ent in db.ents('Package'):
    if (ent.language() == 'Java') | (ent.language() == 'Python'):
        ent_list.append(Entity(ent.id(), ent.longname(), ent.kindname()))  # longname()：完整限定名
        regular_count += 1

for ent in db.ents('Namespace'):
    if (ent.language() == 'C++') | (ent.language() == 'C'):
        ent_list.append(Entity(ent.id(), ent.longname(), ent.kindname()))
        regular_count += 1

# -------------------------------------------------------------------------
# 第三步：提取其余普通实体（函数、类、变量、参数等）
# 通过 Understand 的 Definein 引用获取每个实体的定义位置
# -------------------------------------------------------------------------
unseen_entity_type = set()  # 记录未见过的实体类型（调试用）

# 过滤条件：排除 File、Package、未解析、隐式、未知类型的实体
select = "~File ~Package ~Unresolved ~Implicit ~Unknown"
if lang == "cpp":
    # C++ 额外排除 Namespace（已在上面单独处理）
    select = "~File ~Package ~Namespace ~Unresolved ~Implicit ~Unknown"

for ent in db.ents(select):
    if (ent.language() == 'C++') | (ent.language() == 'C') | \
            (ent.language() == 'Java') | (ent.language() == 'Python') | (ent.language() == 'Web'):

        # 查找该实体的 "Definein" 引用（即实体定义所在的位置）
        decls = ent.refs('Definein')
        if decls:
            # 有定义位置：提取行号和列号
            line = decls[0].line()
            start_column = decls[0].column() + 1          # Understand 列号从 0 开始，转为 1-based
            end_column = start_column + len(ent.simplename())  # 根据名称长度估算结束列
            ent_list.append(
                Entity(ent.id(), ent.longname(), ent.kindname(),
                       decls[0].file().relname(), line, start_column, end_column))
            regular_count += 1
        else:
            # 无定义位置（如外部库实体）：仍记录实体，但无位置信息
            unseen_entity_type.add(ent.kindname())
            ent_list.append(Entity(ent.id(), ent.longname(), ent.kindname()))

rel_list = []  # 最终输出的关系列表

# -------------------------------------------------------------------------
# 第四步：提取所有关系
# 遍历所有实体，对每个实体的前向引用（isforward()）生成依赖关系记录
# -------------------------------------------------------------------------
print('Exporting relations...')
rel_count = 0
for ent in db.ents():
    if (ent.language() == 'C++') | (ent.language() == 'C') | \
            (ent.language() == 'Java') | (ent.language() == 'Python') | (ent.language() == 'Web'):
        # refs('~End')：排除 End 类型的引用（定义结束标记）
        # '~Unknown ~Unresolved ~Implicit'：排除目标实体为未知/未解析/隐式的引用
        for ref in ent.refs('~End', '~Unknown ~Unresolved ~Implicit'):
            if ref.isforward():  # 仅处理前向引用（避免重复记录双向关系）
                rel_list.append(
                    Dependency(
                        ref.kind().longname(),  # 关系类型全名（如 "Python Call"）
                        ref.scope().id(),        # 调用者（所在作用域）的实体 id
                        ref.ent().id(),          # 被引用实体的 id
                        ref.line(),              # 引用发生的行号
                        ref.column()             # 引用发生的列号
                    ))
                rel_count += 1

# 输出调试信息
print("unseen entity type: ")
print(unseen_entity_type)
print(f'Total {regular_count} entities are successfully exported')
print(f'Total {rel_count} relations are successfully exported')

# -------------------------------------------------------------------------
# 第五步：将实体列表和关系列表分别写出为 JSON 文件
# -------------------------------------------------------------------------
print('Saving results to the file...')
# 实体 JSON：Understand_<project_name>_entity.json
output(ent_list, output_path + "Understand_" + project_name + "_entity.json", 'entity', project_name)
# 关系 JSON：Understand_<project_name>_dependency.json
output(rel_list, output_path + "Understand_" + project_name + "_dependency.json", 'dependency', project_name)
