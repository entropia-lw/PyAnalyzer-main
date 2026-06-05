"""
rq1_evaluate.py
功能：复现论文 Table 6 中 PyAnalyzer 的实体（Entity）和依赖关系（Dependency）识别准确率结果。
      对 Micro-Benchmark A 的每个测试用例，将 PyAnalyzer 的 JSON 输出与 ground truth
      进行比对，计算全局 Precision / Recall / F1。

评估模式：
  - entity：比较提取的实体集合（类、函数、变量、参数等9种）
  - relation：比较提取的依赖关系三元组（调用、继承、导入等9种）

GT 来源（优先级从高到低）：
  1. 从测试用例的 .py 源码文件用 AST 解析推导（最准确）
  2. 从 micro-benchmark A 的 .md 文档中解析 YAML 断言块

论文参考值（Table 6）：
  Entity  : P=96.9%  R=100.0%  F1=98.4%
  Dep     : P=99.4%  R=98.1%   F1=98.7%
"""

import os, re, json, glob, ast, argparse
from pathlib import Path

# 脚本所在目录（绝对路径），作为所有相对路径的基准
BASE = os.path.dirname(os.path.abspath(__file__))

# =============================================================================
# GROUP_MAP：将测试结果的目录名（小写）映射到 (mode, target_type)。
#   mode         —— 'entity' 或 'relation'，决定使用哪套评估逻辑
#   target_type  —— 具体类型字符串，用于从 PyAnalyzer 输出中过滤对应条目
# =============================================================================
GROUP_MAP = {
    # ---------- 实体类 ----------
    "aliasdefinition":         ("entity",   "alias"),           # 别名实体
    "anonymousfunction":       ("entity",   "anonymousfunction"),# 匿名函数（lambda）实体
    "classattributedefinition":("entity",   "attribute"),       # 类属性实体
    "classdefinition":         ("entity",   "class"),           # 类实体
    "functiondefinition":      ("entity",   "function"),        # 函数实体
    "moduledefinition":        ("entity",   "module"),          # 模块实体
    "packagedefinition":       ("entity",   "package"),         # 包实体
    "parameterdefinition":     ("entity",   "parameter"),       # 参数实体
    "variabledefinition":      ("entity",   "variable"),        # 变量实体
    # ---------- 关系类 ----------
    "alias":                   ("relation", "alias"),           # 别名关系
    "annotate":                ("relation", "annotate"),        # 类型注解关系
    "call":                    ("relation", "call"),            # 函数调用关系
    "contain":                 ("relation", "contain"),         # 包含关系（包→模块）
    "define":                  ("relation", "define"),          # 定义关系
    "import":                  ("relation", "import"),          # 导入关系
    "inherit":                 ("relation", "inherit"),         # 类继承关系
    "set":                     ("relation", "set"),             # 赋值关系
    "use":                     ("relation", "use"),             # 使用关系
}

# Python 内置名称集合 + 常用特殊名称，在 GT 推导时用于过滤"不感兴趣"的符号
import builtins as _bi
PYTHON_BUILTINS = set(dir(_bi)) | {
    'self', 'cls', 'True', 'False', 'None', 'object',
    'int', 'str', 'float', 'bool', 'list', 'dict', 'tuple', 'set',
    'super', 'staticmethod', 'classmethod', 'property', 'input', 'print',
    'range', 'len', 'type', 'isinstance', 'hasattr', 'getattr', 'setattr',
}

# ---------- 实体类型规范化 ----------
# 处理 PyAnalyzer 输出中实体类型字符串的各种写法，统一映射为小写标准名
_ETYPE_ORDER = ['Package', 'Module', 'Variable', 'Parameter', 'Class',
                'Attribute', 'Alias', 'AnonymousFunction', 'Function']
_ETYPE_NORM  = {k: k.lower() for k in _ETYPE_ORDER}              # 默认直接小写
_ETYPE_NORM['AnonymousFunction'] = 'anonymousfunction'            # 特殊处理（避免驼峰拆分问题）

# ---------- 关系类型规范化 ----------
_RTYPE_MAP = {
    'Define':   'define',
    'Use':      'use',
    'Set':      'set',
    'Import':   'import',
    'Call':     'call',
    'Inherit':  'inherit',
    'Contain':  'contain',
    'Annotate': 'annotate',
    'Alias':    'alias',
}


def norm_etype(raw):
    """
    将 PyAnalyzer 输出中原始的实体类型字符串规范化为标准小写名。
    特殊规则：
      - 含 'ClassAttribute' 的视为 'attribute'
      - 恰好等于 'Method' 的视为 'function'（方法本质是函数）
    其余用正则逐一匹配 _ETYPE_ORDER 中的关键词。
    """
    if 'ClassAttribute' in raw: return 'attribute'
    if raw.strip() == 'Method':  return 'function'
    for k in _ETYPE_ORDER:
        if re.search(k, raw): return _ETYPE_NORM[k]
    return None  # 无法匹配时返回 None（该实体将被忽略）


def norm_rtype(raw):
    """
    将 PyAnalyzer 输出中原始的关系类型字符串规范化为标准小写名。
    用正则对 _RTYPE_MAP 中的键逐一匹配。
    """
    for k, v in _RTYPE_MAP.items():
        if re.search(k, raw): return v
    return None  # 无法匹配时返回 None（该关系将被忽略）


def strip_top(q):
    """
    去掉限定名（qualified name）的第一段（通常是模块名或包名前缀）。
    例：'mymodule.MyClass.method' → 'MyClass.method'
    若只有一段（无点号），则直接返回。
    """
    s = q.split('.')
    return '.'.join(s[1:]) if len(s) > 1 else s[0]


def last_seg(s):
    """
    取限定名的最后一段（最终简单名）。
    例：'a.b.c' → 'c'
    用于 inherit/import 匹配时的模糊化处理。
    """
    return s.split('.')[-1]


def normalize_last_seg(items):
    """
    对关系三元组集合中每条 (from, rel_type, to) 的 from 和 to 均取最后一段。
    用于 inherit/import 的模糊匹配，消除前缀差异。
    """
    return {(last_seg(f), rt, last_seg(t)) for f, rt, t in items}


# =============================================================================
# 解析 PyAnalyzer JSON 输出
# =============================================================================
def parse_json(json_path):
    """
    读取 PyAnalyzer 生成的 dep JSON（report-pyanalyzer.json 格式）。
    JSON 结构：{"variables": [...实体列表...], "cells": [...关系列表...]}

    返回：
      entities  —— {(type_str, qualified_name_去顶层前缀), ...}
      relations —— {(from_去顶, rel_type, to_去顶), ...}

    过滤规则：
      - 实体 id 为字符串（外部引用实体）：跳过
      - 实体文件路径为绝对路径（内置库实体）：跳过
      - 关系中 inherit → object 的 'object' 基类：跳过（Python 默认基类，无意义）
    """
    with open(json_path, encoding='utf-8') as f:
        data = json.load(f)

    # 判断 id 是否为外部引用（外部实体的 id 为字符串，内部实体为整数）
    is_ext = lambda eid: isinstance(eid, str)

    # 构建 id → qualifiedName 的映射（仅内部实体）
    id2q = {v['id']: v['qualifiedName']
            for v in data['variables'] if not is_ext(v['id'])}

    entities = set()
    for v in data['variables']:
        if is_ext(v['id']): continue                         # 跳过外部引用实体
        if os.path.isabs(v.get('file', '')): continue        # 跳过绝对路径（内置库实体）
        et = norm_etype(v.get('category', ''))               # 规范化实体类型
        if et:
            entities.add((et, strip_top(v['qualifiedName'])))  # 去掉顶层模块前缀后加入

    relations = set()
    for c in data['cells']:
        rt = norm_rtype(c['values']['kind'])  # 规范化关系类型
        if not rt: continue                   # 无法识别的关系类型：跳过
        fid, tid = c['src'], c['dest']
        if is_ext(fid): continue              # 外部实体发出的关系：跳过

        fq = id2q.get(fid)  # 起点实体限定名
        # 终点实体：若 tid 为字符串（外部引用），在 variables 中线性搜索；否则从 id2q 查找
        tq = (next((v['qualifiedName'] for v in data['variables'] if v['id'] == tid), None)
              if is_ext(tid) else id2q.get(tid))

        if fq and tq:
            fs, ts = strip_top(fq), strip_top(tq)
            # 过滤 inherit → object（Python 所有类隐式继承 object，不需要显式记录）
            if rt == 'inherit' and ts.split('.')[-1] == 'object':
                continue
            relations.add((fs, rt, ts))

    return entities, relations


# =============================================================================
# 从 .md 文档加载 Ground Truth（备用方案）
# =============================================================================
_PREFIX_RE = re.compile(r"[A-Za-z]+:'?(.+?)'?\s*$")

def _sp(s):
    """
    去掉 GT 条目中的类型前缀，提取纯名称。
    例：'Function:\'inner\'' → 'inner'
         'variable:outer.a'  → 'outer.a'
    """
    m = _PREFIX_RE.match(s)
    return m.group(1) if m else s


def load_gt_from_md(md_dir, case_name, mode, target_type=None):
    """
    从指定目录的 Markdown 文件中解析 YAML 断言块，提取 Ground Truth。

    参数：
      md_dir      —— 存放 .md 文件的目录（entity/ 或 relation/）
      case_name   —— 测试用例名（用于匹配断言块中的 name 字段）
      mode        —— 'entity' 或 'relation'
      target_type —— 仅提取该类型的条目（如 'function'、'call'）

    返回：
      entity  模式：{(type, qualified_name), ...}
      relation 模式：{(from, rel_type, to), ...}

    依赖 PyYAML，若未安装则返回空集合。
    """
    try:
        import yaml
    except ImportError:
        return set()  # PyYAML 未安装时降级返回空

    def match(n):
        """忽略大小写和下划线，判断断言块 name 与 case_name 是否匹配"""
        return n.lower().replace('_', '') == case_name.lower().replace('_', '')

    gt = set()
    for md in glob.glob(os.path.join(md_dir, '*.md')):
        try:
            text = open(md, encoding='utf-8').read()
        except:
            continue

        # 提取所有 ```yaml ... ``` 代码块
        for blk in re.findall(r'```yaml\n(.*?)```', text, re.DOTALL):
            try:
                b = yaml.safe_load(blk)
                # 跳过非字典或 name 不匹配的块
                if not isinstance(b, dict) or not match(b.get('name', '')):
                    continue

                if mode == 'entity':
                    # 实体模式：提取 entity.items 下的每个条目
                    et = b.get('entity', {}).get('type', '').lower()
                    for item in b.get('entity', {}).get('items', []):
                        if not isinstance(item, dict): continue
                        # 优先用 qualified（限定名），其次用 name（简短名）
                        name = item.get('qualified', item.get('name', ''))
                        if et and name:
                            gt.add((et, name))
                else:
                    # 关系模式：提取 relation.items 下的每个条目
                    rt = b.get('relation', {}).get('type', '').lower()
                    for item in b.get('relation', {}).get('items', []):
                        if not isinstance(item, dict): continue
                        # 条目可以有自己的 type，覆盖顶层 rt
                        item_rt = item.get('type', rt).lower() if item.get('type') else rt
                        if target_type and item_rt != target_type:
                            continue  # 类型不匹配：跳过
                        rf  = _sp(item.get('from', ''))  # 去前缀后的 from 名称
                        rto = _sp(item.get('to', ''))    # 去前缀后的 to 名称
                        if rf and rto:
                            gt.add((rf, item_rt, rto))
            except:
                continue  # 解析失败：忽略此块，继续处理下一个
    return gt


# =============================================================================
# AST 工具函数集
# 用于从 Python 源码中推导 Ground Truth 实体和关系
# =============================================================================

def _expr_name(node):
    """
    从 AST 表达式节点提取名称字符串。
    支持：
      - ast.Name：直接取 id（如 'MyClass'）
      - ast.Attribute：递归拼接（如 'obj.attr' → 'obj.attr'）
    其他节点返回空字符串。
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        v = _expr_name(node.value)
        return f"{v}.{node.attr}" if v else node.attr
    return ''


def _mod_name(py_file, test_dir):
    """
    根据 .py 文件相对于测试目录的路径，计算其模块限定名。
    例：test_dir='cases/_global', py_file='cases/_global/sub/mod.py'
        → 'sub.mod'
    """
    rel = Path(py_file).relative_to(test_dir)
    return '.'.join(rel.with_suffix('').parts)  # 去掉 .py 后缀，各部分用点连接


def _collect_module_defs(tree, mod):
    """
    在 AST 树的顶层（不递归进函数/类内部）收集所有符号定义，
    构建 {简单名: 限定名或导入来源} 字典，用于后续解析 inherit/annotate 的引用目标。

    收集内容：
      - 函数定义（FunctionDef / AsyncFunctionDef）
      - 类定义（ClassDef）及其直接方法
      - 顶层赋值（Assign）的目标名
      - import 和 from...import 语句引入的名称
    """
    defs = {}
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defs[node.name] = f"{mod}.{node.name}"

        elif isinstance(node, ast.ClassDef):
            defs[node.name] = f"{mod}.{node.name}"
            # 同时收集类内方法（用于方法级别的解析）
            for item in ast.iter_child_nodes(node):
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    defs[f"{node.name}.{item.name}"] = f"{mod}.{node.name}.{item.name}"

        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    defs[t.id] = f"{mod}.{t.id}"

        elif isinstance(node, ast.Import):
            # import a.b.c → 将 a 映射到 a.b.c；import a as x → 将 x 映射到 a.b.c
            for a in node.names:
                defs[a.asname or a.name.split('.')[0]] = a.name

        elif isinstance(node, ast.ImportFrom):
            # from base import name → 映射为 base.name
            base = node.module or ''
            for a in node.names:
                defs[a.asname or a.name] = f"{base}.{a.name}" if base else a.name
    return defs


def _build_parent_map(tree):
    """
    构建 AST 节点 id → 父节点 的映射字典。
    用于在 _is_annot_ctx 中判断某节点是否处于类型注解上下文中。
    """
    pm = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            pm[id(child)] = node
    return pm


def _is_annot_ctx(node, pm):
    """
    判断给定节点是否出现在类型注解上下文中：
      - AnnAssign 的 annotation 字段（变量注解）
      - FunctionDef/AsyncFunctionDef 的 returns 字段（返回值注解）
      - arg 的 annotation 字段（参数注解）
    用于在 annotate 关系提取时准确定位注解表达式。
    """
    p = pm.get(id(node))
    if not p: return False
    if isinstance(p, ast.AnnAssign) and p.annotation is node: return True
    if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef)) and p.returns is node: return True
    if isinstance(p, ast.arg) and p.annotation is node: return True
    return False


def _collect_assign_targets(targets, scope, collector, target_type):
    """
    递归处理赋值目标列表（targets），按 target_type 决定收集内容：
      'variable' → 收集 (variable, qualified_name) 实体
      'define'   → 收集 (scope, define, qualified_name) 关系
      'set'      → 收集 (scope, set, qualified_name) 关系

    支持的赋值目标类型：
      - ast.Name：简单名称（如 a = 1）
      - ast.Tuple / ast.List：解包赋值（如 a, b = 1, 2）
      - ast.Starred：星号解包（如 *a = ...）
    """
    for t in targets:
        if isinstance(t, ast.Name):
            vq = f"{scope}.{t.id}"
            if target_type == 'variable': collector.add(('variable', vq))
            elif target_type == 'define':  collector.add((scope, 'define', vq))
            elif target_type == 'set':     collector.add((scope, 'set', vq))
        elif isinstance(t, (ast.Tuple, ast.List)):
            # 递归处理元组/列表解包
            _collect_assign_targets(t.elts, scope, collector, target_type)
        elif isinstance(t, ast.Starred) and isinstance(t.value, ast.Name):
            # 处理 *name 形式的星号解包
            vq = f"{scope}.{t.value.id}"
            if target_type == 'variable': collector.add(('variable', vq))
            elif target_type == 'define':  collector.add((scope, 'define', vq))
            elif target_type == 'set':     collector.add((scope, 'set', vq))


def _walk_scope(node, scope, collector, target_type,
                module_defs=None, exclude_self=True, parent_map=None):
    """
    递归遍历 AST 节点，在当前 scope 下按 target_type 收集指定类型的实体或关系。

    参数：
      node        —— 当前 AST 节点（起始为模块根节点）
      scope       —— 当前作用域的限定名（如 'mymod'、'mymod.MyClass'）
      collector   —— 收集结果的集合
      target_type —— 要收集的类型（'function'、'variable'、'call' 等）
      module_defs —— 当前模块顶层符号表（用于解析 inherit/annotate 引用）
      exclude_self —— 是否跳过 self/cls 参数（通常为 True）
      parent_map  —— 节点父级映射（用于注解上下文判断）

    收集逻辑按 target_type 分支处理各种 AST 节点类型。
    """
    for child in ast.iter_child_nodes(node):

        # ── 函数/方法定义 ──
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fn_q = f"{scope}.{child.name}"  # 函数的限定名

            if target_type == 'function':
                collector.add(('function', fn_q))  # 收集函数实体

            elif target_type == 'parameter':
                # 收集所有参数（位置参数、关键字参数、可变参数等）
                all_args = (child.args.posonlyargs + child.args.args + child.args.kwonlyargs)
                for arg in all_args:
                    if exclude_self and arg.arg in ('self', 'cls'): continue  # 跳过 self/cls
                    collector.add(('parameter', f"{fn_q}.{arg.arg}"))
                if child.args.vararg:  # *args
                    collector.add(('parameter', f"{fn_q}.{child.args.vararg.arg}"))
                if child.args.kwarg:   # **kwargs
                    collector.add(('parameter', f"{fn_q}.{child.args.kwarg.arg}"))

            elif target_type == 'define':
                # define 关系：scope 定义了 fn_q
                collector.add((scope, 'define', fn_q))
                # 函数还定义了它的各个参数
                all_args = (child.args.posonlyargs + child.args.args + child.args.kwonlyargs)
                for arg in all_args:
                    if arg.arg in ('self', 'cls'): continue
                    collector.add((fn_q, 'define', f"{fn_q}.{arg.arg}"))
                if child.args.vararg and child.args.vararg.arg not in ('self', 'cls'):
                    collector.add((fn_q, 'define', f"{fn_q}.{child.args.vararg.arg}"))
                if child.args.kwarg and child.args.kwarg.arg not in ('self', 'cls'):
                    collector.add((fn_q, 'define', f"{fn_q}.{child.args.kwarg.arg}"))

            elif target_type == 'annotate':
                # 收集参数的类型注解关系
                all_args = (child.args.posonlyargs + child.args.args + child.args.kwonlyargs)
                for arg in all_args:
                    if exclude_self and arg.arg in ('self', 'cls'): continue
                    if arg.annotation:
                        ann = _expr_name(arg.annotation)  # 注解表达式的名称
                        # 尝试将注解名解析为限定名（通过 module_defs 映射）
                        resolved = (module_defs or {}).get(ann, ann)
                        if resolved:
                            collector.add((f"{fn_q}.{arg.arg}", 'annotate', resolved))

            elif target_type == 'alias':
                pass  # alias 关系在 Import/ImportFrom 节点处理

            # 递归进入函数体（使用函数自身作为新的 scope）
            _walk_scope(child, fn_q, collector, target_type, module_defs, exclude_self, parent_map)

        # ── 类定义 ──
        elif isinstance(child, ast.ClassDef):
            cls_q = f"{scope}.{child.name}"  # 类的限定名

            if target_type == 'class':
                collector.add(('class', cls_q))  # 收集类实体

            elif target_type == 'define':
                collector.add((scope, 'define', cls_q))  # scope 定义了 cls_q

            elif target_type == 'inherit':
                # 收集继承关系：cls_q inherit base
                for base in child.bases:
                    bname = _expr_name(base)  # 基类名表达式
                    if bname and bname not in ('object',):  # 跳过显式继承 object
                        # 尝试解析为限定名
                        resolved = (module_defs or {}).get(bname, bname)
                        collector.add((cls_q, 'inherit', resolved))

            # 递归进入类体（使用类自身作为新的 scope）
            _walk_scope(child, cls_q, collector, target_type, module_defs, exclude_self, parent_map)

        # ── 普通赋值语句 ──
        elif isinstance(child, ast.Assign):
            _collect_assign_targets(child.targets, scope, collector, target_type)

        # ── 带类型注解的赋值（a: int = 1）──
        elif isinstance(child, ast.AnnAssign):
            if isinstance(child.target, ast.Name):
                vq = f"{scope}.{child.target.id}"
                if target_type == 'variable':  collector.add(('variable', vq))
                elif target_type == 'define':   collector.add((scope, 'define', vq))
                elif target_type == 'attribute':collector.add(('attribute', vq))
                # set 关系仅在有赋值值时产生（a: int 无值时不是 set）
                elif target_type == 'set' and child.value:
                    collector.add((scope, 'set', vq))
                elif target_type == 'annotate' and child.annotation:
                    # 变量注解关系：vq annotate annotation_type
                    ann = _expr_name(child.annotation)
                    resolved = (module_defs or {}).get(ann, ann)
                    if resolved:
                        collector.add((vq, 'annotate', resolved))

        # ── 增量赋值（a += 1）—— 产生 set 关系 ──
        elif isinstance(child, ast.AugAssign):
            if isinstance(child.target, ast.Name) and target_type == 'set':
                collector.add((scope, 'set', f"{scope}.{child.target.id}"))

        # ── 海象运算符（a := expr）——产生 variable/define/set ──
        elif isinstance(child, ast.NamedExpr):
            if isinstance(child.target, ast.Name):
                vq = f"{scope}.{child.target.id}"
                if target_type == 'variable': collector.add(('variable', vq))
                elif target_type == 'define':  collector.add((scope, 'define', vq))
                elif target_type == 'set':     collector.add((scope, 'set', vq))

        # ── import 语句 ──
        elif isinstance(child, ast.Import):
            if target_type == 'alias':
                # import a as x → alias 实体 scope.x
                for a in child.names:
                    if a.asname:
                        collector.add(('alias', f"{scope}.{a.asname}"))
            elif target_type == 'import':
                # import 关系：scope import a.name
                for a in child.names:
                    collector.add((scope, 'import', a.name))

        # ── from...import 语句 ──
        elif isinstance(child, ast.ImportFrom):
            base = child.module or ''
            if target_type == 'alias':
                # from x import y as z → alias 实体 scope.z
                for a in child.names:
                    if a.asname:
                        collector.add(('alias', f"{scope}.{a.asname}"))
            elif target_type == 'import':
                # from base import ... → scope import base（模块本身）
                if base:
                    collector.add((scope, 'import', base))
                for a in child.names:
                    if a.name != '*':  # 跳过 import *（无法精确追踪）
                        tgt = f"{base}.{a.name}" if base else a.name
                        collector.add((scope, 'import', tgt))

        # ── for 循环（循环变量也是赋值目标）──
        elif isinstance(child, ast.For):
            if target_type in ('variable', 'define', 'set'):
                _collect_assign_targets([child.target], scope, collector, target_type)
            # 递归处理循环体
            _walk_scope(child, scope, collector, target_type, module_defs, exclude_self, parent_map)

        # ── 表达式语句（主要处理海象运算符用作语句的情况）──
        elif isinstance(child, ast.Expr):
            if isinstance(child.value, ast.NamedExpr):
                t = child.value.target
                if isinstance(t, ast.Name):
                    vq = f"{scope}.{t.id}"
                    if target_type == 'variable': collector.add(('variable', vq))
                    elif target_type == 'define':  collector.add((scope, 'define', vq))
                    elif target_type == 'set':     collector.add((scope, 'set', vq))


def _collect_attributes(tree, mod, collector):
    """
    专门收集类属性（attribute）实体：
      1. 类体内的直接赋值（如 x = 1）
      2. 带注解的类体赋值（如 x: int = 1）
      3. __init__ 方法中 self.attr = ... 形式的实例属性赋值
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            cls_q = f"{mod}.{node.name}"
            for item in node.body:
                # 类体中的普通赋值
                if isinstance(item, ast.Assign):
                    _collect_assign_targets(item.targets, cls_q, collector, 'attribute')
                # 类体中带注解的赋值
                elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    collector.add(('attribute', f"{cls_q}.{item.target.id}"))
                # __init__ 方法中的 self.attr = ... 实例属性
                elif isinstance(item, ast.FunctionDef) and item.name == '__init__':
                    for s in ast.walk(item):
                        if isinstance(s, ast.Assign):
                            for t in s.targets:
                                if (isinstance(t, ast.Attribute) and
                                        isinstance(t.value, ast.Name) and t.value.id == 'self'):
                                    collector.add(('attribute', f"{cls_q}.{t.attr}"))


# =============================================================================
# GT 推导主函数
# =============================================================================
def derive_entity_gt(test_dir, target_type, exclude_self=True):
    """
    从测试用例源码目录（包含 .py 文件）推导指定类型的实体 Ground Truth。

    参数：
      test_dir    —— 测试用例的根目录
      target_type —— 要推导的实体类型（'function'、'class'、'variable' 等）
      exclude_self —— 是否跳过 self/cls 参数（用于 parameter 类型，默认 True）

    返回：{(target_type, qualified_name), ...}

    特殊处理：
      - 'module'：每个 .py 文件对应一个模块实体
      - 'package'：目录名（去掉 _ 前缀）作为包名
      - 'attribute'：使用专用的 _collect_attributes 处理
      - 'anonymousfunction'：统计 AST 中的 Lambda 节点，按顺序编号
    """
    py_files = sorted(glob.glob(os.path.join(test_dir, '**', '*.py'), recursive=True))
    collector = set()
    pkg = Path(test_dir).name.lstrip('_')  # 去掉目录名开头的 _ 得到包名

    for pyf in py_files:
        mod = _mod_name(pyf, test_dir)  # 计算模块限定名

        if target_type == 'module':
            collector.add(('module', mod))  # 每个 .py 文件都是一个 module 实体
            continue
        if target_type == 'package':
            collector.add(('package', pkg))  # 整个目录是一个 package 实体
            continue

        # 解析 AST
        try:
            tree = ast.parse(open(pyf, encoding='utf-8').read())
        except:
            continue

        module_defs = _collect_module_defs(tree, mod)  # 顶层符号表
        parent_map  = _build_parent_map(tree)           # 父节点映射

        if target_type == 'attribute':
            _collect_attributes(tree, mod, collector)

        elif target_type == 'anonymousfunction':
            # 统计 lambda 表达式，按出现顺序编号（lambda_0, lambda_1, ...）
            cnt = 0
            for node in ast.walk(tree):
                if isinstance(node, ast.Lambda):
                    collector.add(('anonymousfunction', f"{mod}.<lambda_{cnt}>"))
                    cnt += 1
        else:
            # 其余类型均使用通用的 _walk_scope 处理
            _walk_scope(tree, mod, collector, target_type, module_defs, exclude_self, parent_map)

    # 只返回 target_type 类型的二元组
    return {item for item in collector
            if isinstance(item, tuple) and len(item) == 2 and item[0] == target_type}


def derive_alias_relation_gt(test_dir):
    """
    专门推导 alias relation（别名关系）的 Ground Truth。
    与其他关系不同，alias relation 需要考虑作用域，因此单独处理。

    返回：{('alias', qualified_name), ...}
    （注：alias relation 与 alias entity 共用同一格式，均为二元组）
    """
    py_files = sorted(glob.glob(os.path.join(test_dir, '**', '*.py'), recursive=True))
    collector = set()
    for pyf in py_files:
        mod = _mod_name(pyf, test_dir)
        try:
            tree = ast.parse(open(pyf, encoding='utf-8').read())
        except:
            continue
        module_defs = _collect_module_defs(tree, mod)
        _walk_scope(tree, mod, collector, 'alias', module_defs)
    # 只返回 alias 类型的条目
    return {item for item in collector
            if isinstance(item, tuple) and len(item) == 2 and item[0] == 'alias'}


# =============================================================================
# P / R / F1 计算
# =============================================================================
def global_prf1(pred, gt):
    """
    根据预测集合（pred）和 Ground Truth 集合（gt）计算精确率、召回率和 F1。

    计算方式（微平均，即基于全局条目数而非用例数）：
      TP = |pred ∩ gt|
      P  = TP / |pred|     （若 pred 为空则 P=0）
      R  = TP / |gt|       （若 gt 为空则 R=0）
      F1 = 2*P*R / (P+R)   （若 P+R=0 则 F1=0）

    特殊情况：pred 和 gt 均为空时返回 (1.0, 1.0, 1.0)（完美得分）。
    """
    if not pred and not gt: return 1.0, 1.0, 1.0
    tp = len(pred & gt)
    p  = tp / len(pred) if pred else 0.0
    r  = tp / len(gt)   if gt   else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    return p, r, f1


# =============================================================================
# 主评估流程
# =============================================================================
def evaluate(results_dir, tests_dir, md_entity_dir, md_relation_dir,
             exclude_self=True, verbose=False):
    """
    遍历 PyAnalyzer 结果目录中的所有 JSON 文件，对每个测试用例：
      1. 识别所属 group（entity 或 relation）和目标类型
      2. 从 PyAnalyzer 输出解析预测集合
      3. 从源码 AST 或 .md 文档获取 Ground Truth
      4. 将 pred 和 GT 合并到全局池
    最终对实体和关系分别计算全局 P/R/F1，输出论文 Table 6 对应的格式。

    参数：
      results_dir    —— PyAnalyzer 结果 JSON 所在目录（按 group/case 结构组织）
      tests_dir      —— 测试用例源码目录（按 _group/_case 结构组织）
      md_entity_dir  —— micro-benchmark A 的 entity/ markdown 文档目录
      md_relation_dir—— micro-benchmark A 的 relation/ markdown 文档目录
      exclude_self   —— 是否跳过 self/cls 参数（默认 True）
      verbose        —— 是否打印每个用例的 FP/FN 详情（默认 False）
    """
    # 全局合并池：将所有测试用例的 pred 和 gt 汇总后一次性计算全局指标
    entity_pred_pool = set()  # 所有用例预测到的实体
    entity_gt_pool   = set()  # 所有用例的实体 GT
    rel_pred_pool    = set()  # 所有用例预测到的关系
    rel_gt_pool      = set()  # 所有用例的关系 GT

    entity_cases = relation_cases = skipped = 0
    skipped_list = []

    # 遍历所有 PyAnalyzer 输出 JSON（递归搜索）
    for json_path in sorted(glob.glob(
            os.path.join(results_dir, '**', '*.json'), recursive=True)):

        rel_parts = Path(json_path).relative_to(results_dir).parts
        if len(rel_parts) < 3: continue          # 结构不符合 group/case/xxx.json：跳过

        group, case = rel_parts[0], rel_parts[1]  # 提取 group 名和 case 名
        group_l = group.lower()
        if group_l not in GROUP_MAP:
            skipped_list.append(f"{group}/{case}"); skipped += 1; continue

        mode, target_type = GROUP_MAP[group_l]    # 确定评估模式和目标类型

        pred_ents, pred_rels = parse_json(json_path)  # 解析 PyAnalyzer 预测结果

        # 规范化包名：去掉 _ 前缀（测试目录命名约定）
        pred_ents = {(et, e.lstrip('_') if et == 'package' else e) for et, e in pred_ents}
        # contain 关系的 from 端去掉 _ 前缀
        pred_rels = {(f.lstrip('_') if rt == 'contain' else f, rt, t) for f, rt, t in pred_rels}

        # ── 定位测试用例源码目录 ──
        # 尝试四种命名约定（_ 前缀的组合）以兼容不同路径格式
        test_case_dir = None
        if tests_dir:
            for td in [
                os.path.join(tests_dir, f'_{group}', f'_{case}'),  # _group/_case（最常见）
                os.path.join(tests_dir, f'_{group}',    case),      # _group/case
                os.path.join(tests_dir,   group,      f'_{case}'),  # group/_case
                os.path.join(tests_dir,   group,         case),     # group/case
            ]:
                if os.path.isdir(td): test_case_dir = td; break

        # ── 实体评估 ──
        if mode == 'entity':
            gt = set()
            # 优先从源码 AST 推导 GT
            if test_case_dir:
                gt = derive_entity_gt(test_case_dir, target_type, exclude_self)
            # 备选：从 .md 文档加载 GT
            if not gt and md_entity_dir:
                gt = load_gt_from_md(md_entity_dir, case, 'entity', target_type)
            if not gt:
                skipped_list.append(f"{group}/{case}(no entity GT)"); skipped += 1; continue

            # 从预测实体中过滤出目标类型
            pred = {e for e in pred_ents if e[0] == target_type}

            entity_pred_pool.update(pred)  # 合并到全局池
            entity_gt_pool.update(gt)
            entity_cases += 1

            if verbose:
                p, r, f = global_prf1(pred, gt)
                print(f"\n[entity/{target_type}] {group}/{case}")
                print(f"  P={p:.1%}  R={r:.1%}  F1={f:.1%}  (pred={len(pred)} gt={len(gt)})")
                fp, fn = pred - gt, gt - pred
                if fp: print(f"  FP({len(fp)}): {sorted(fp)[:4]}{'…' if len(fp) > 4 else ''}")
                if fn: print(f"  FN({len(fn)}): {sorted(fn)[:4]}{'…' if len(fn) > 4 else ''}")

        # ── 关系评估 ──
        else:
            gt = set()
            pred_cmp = None  # 用于比较的预测集合（可能经过规范化）
            gt_cmp   = None  # 用于比较的 GT 集合（可能经过规范化）

            if target_type == 'alias':
                # ★ alias relation：从 AST 推导 GT（md 文档命名不匹配）
                if test_case_dir:
                    gt = derive_alias_relation_gt(test_case_dir)
                if not gt and md_relation_dir:
                    gt = load_gt_from_md(md_relation_dir, case, 'relation', target_type)
                pred_cmp = {r for r in pred_rels if r[1] == target_type}
                gt_cmp   = gt

            elif target_type in ('inherit', 'import'):
                # ★ inherit/import：使用 md GT + last_seg 规范化（消除命名空间前缀差异）
                if md_relation_dir:
                    gt = load_gt_from_md(md_relation_dir, case, 'relation', target_type)
                if not gt and test_case_dir:
                    # md 无 GT 时从 AST 推导
                    from_derive = set()
                    _walk_scope_for_relation(test_case_dir, target_type, from_derive, exclude_self)
                    gt = from_derive
                pred_raw = {r for r in pred_rels if r[1] == target_type}
                # 对 pred 和 GT 均取最后一段，消除前缀差异后再比较
                pred_cmp = normalize_last_seg(pred_raw)
                gt_cmp   = normalize_last_seg(gt)

            else:
                # 其他关系：优先 md GT，备选 AST 推导
                if md_relation_dir:
                    gt = load_gt_from_md(md_relation_dir, case, 'relation', target_type)
                if not gt and test_case_dir:
                    gt = _derive_other_relation(test_case_dir, target_type, exclude_self)
                pred_cmp = {r for r in pred_rels if r[1] == target_type}
                gt_cmp   = gt

            if not gt_cmp:
                skipped_list.append(f"{group}/{case}(no relation GT)"); skipped += 1; continue

            rel_pred_pool.update(pred_cmp)  # 合并到全局池
            rel_gt_pool.update(gt_cmp)
            relation_cases += 1

            if verbose:
                p, r, f = global_prf1(pred_cmp, gt_cmp)
                print(f"\n[relation/{target_type}] {group}/{case}")
                print(f"  P={p:.1%}  R={r:.1%}  F1={f:.1%}  (pred={len(pred_cmp)} gt={len(gt_cmp)})")
                fp, fn = pred_cmp - gt_cmp, gt_cmp - pred_cmp
                if fp: print(f"  FP({len(fp)}): {sorted(fp)[:3]}{'…' if len(fp) > 3 else ''}")
                if fn: print(f"  FN({len(fn)}): {sorted(fn)[:3]}{'…' if len(fn) > 3 else ''}")

    # ── 输出汇总结果 ──
    total = entity_cases + relation_cases
    if total == 0: print("[ERROR] No cases."); return

    ep, er, ef = global_prf1(entity_pred_pool, entity_gt_pool)   # 实体 P/R/F1
    dp, dr, df = global_prf1(rel_pred_pool,    rel_gt_pool)       # 关系 P/R/F1

    print("\n" + "=" * 64)
    print(f"  RQ1 PyAnalyzer v10  (entity:{entity_cases}  relation:{relation_cases}  skip:{skipped})")
    print(f"  entity_items:   pred={len(entity_pred_pool):4d}  gt={len(entity_gt_pool):4d}")
    print(f"  relation_items: pred={len(rel_pred_pool):4d}  gt={len(rel_gt_pool):4d}")
    print("=" * 64)
    print(f"  {'Metric':<14}  {'P':>7}  {'R':>7}  {'F1':>7}")
    print(f"  {'-' * 44}")
    print(f"  {'Entity':<14}  {ep * 100:>6.1f}%  {er * 100:>6.1f}%  {ef * 100:>6.1f}%")
    print(f"  {'Dependency':<14}  {dp * 100:>6.1f}%  {dr * 100:>6.1f}%  {df * 100:>6.1f}%")
    print("=" * 64)
    print("  论文 Table 6 参考值 (PyAnalyzer):")
    print("    Entity  : P=96.9%  R=100.0%  F1=98.4%")
    print("    Dep     : P=99.4%  R=98.1%   F1=98.7%")
    print("=" * 64)
    if skipped_list: print(f"\n  跳过: {skipped_list}")


# =============================================================================
# 辅助函数：从源码推导关系 GT（用于 inherit/import/contain 等关系）
# =============================================================================
def _walk_scope_for_relation(test_dir, target_type, collector, exclude_self=True):
    """
    遍历测试目录下所有 .py 文件，推导指定关系类型的 Ground Truth 条目，
    追加到 collector 集合。

    特殊处理 'contain'：直接生成 (pkg, contain, mod) 关系（包 → 模块）。
    """
    py_files = sorted(glob.glob(os.path.join(test_dir, '**', '*.py'), recursive=True))
    pkg = Path(test_dir).name.lstrip('_')  # 去掉 _ 前缀得到包名
    for pyf in py_files:
        mod = _mod_name(pyf, test_dir)
        if target_type == 'contain':
            # contain 关系：包包含模块，直接生成，无需 AST 分析
            collector.add((pkg, 'contain', mod))
            continue
        try:
            tree = ast.parse(open(pyf, encoding='utf-8').read())
        except:
            continue
        module_defs = _collect_module_defs(tree, mod)
        _walk_scope(tree, mod, collector, target_type, module_defs, exclude_self)


def _derive_other_relation(test_dir, target_type, exclude_self=True):
    """
    推导指定类型关系的 Ground Truth（除 alias 和 inherit/import 外的通用处理）。
    内部调用 _walk_scope_for_relation，再过滤出正确类型的三元组。
    """
    collector = set()
    _walk_scope_for_relation(test_dir, target_type, collector, exclude_self)
    # 只返回关系类型匹配的三元组
    return {item for item in collector
            if isinstance(item, tuple) and len(item) == 3 and item[1] == target_type}


# =============================================================================
# 调试辅助：直接打印单个 JSON 文件的解析内容
# =============================================================================
def inspect_json(json_path):
    """
    解析并打印指定 JSON 文件中的所有实体和关系（调试用）。
    """
    ents, rels = parse_json(json_path)
    print(f"\n=== Entities ({len(ents)}) ===")
    for e in sorted(ents): print(f"  [{e[0]:22s}] {e[1]}")
    print(f"\n=== Relations ({len(rels)}) ===")
    for r in sorted(rels): print(f"  {r[0]:42s} --{r[1]:12s}--> {r[2]}")


# =============================================================================
# 命令行入口
# =============================================================================
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='RQ1 Evaluator v10')
    # PyAnalyzer 结果 JSON 目录（默认 Data/RQ1/results/PyAnalyzer）
    parser.add_argument('--results',
                        default=os.path.join(BASE, 'Data', 'RQ1', 'results', 'PyAnalyzer'))
    # 测试用例源码目录（默认 Data/RQ1/results/tests）
    parser.add_argument('--tests',
                        default=os.path.join(BASE, 'Data', 'RQ1', 'results', 'tests'))
    # micro-benchmark A 实体 markdown 文档目录
    parser.add_argument('--md-entity',
                        default=os.path.join(BASE, 'Data', 'RQ1', 'micro-benchmark A', 'entity'))
    # micro-benchmark A 关系 markdown 文档目录
    parser.add_argument('--md-relation',
                        default=os.path.join(BASE, 'Data', 'RQ1', 'micro-benchmark A', 'relation'))
    # 是否将 self/cls 参数纳入评估（默认排除）
    parser.add_argument('--include-self', action='store_true')
    # 调试模式：直接解析并打印指定 JSON 文件内容
    parser.add_argument('--inspect', default=None)
    # 详细模式：打印每个用例的 FP/FN
    parser.add_argument('-v', '--verbose', action='store_true')
    args = parser.parse_args()

    if args.inspect:
        inspect_json(args.inspect)  # 调试模式
    else:
        evaluate(args.results, args.tests, args.md_entity, args.md_relation,
                 exclude_self=not args.include_self, verbose=args.verbose)