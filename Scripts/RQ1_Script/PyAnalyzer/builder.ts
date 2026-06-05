// =============================================================================
// builder.ts
// 功能：将 PyAnalyzer 输出的 dep JSON 字符串解析，并将其中的实体（variables）
//       和关系（cells）分别注册到全局容器 e（实体）和 r（关系）中，
//       供后续 UNIMatcher 进行测试用例匹配。
// =============================================================================

import {e, r} from '../../../slim-container'; // e：实体容器；r：关系容器（全局单例）
import {warn} from '@pyanalyzer/logging';       // 日志工具：输出警告信息
import {buildpyanalyzerName, pyanalyzerNameAnonymous} from '@pyanalyzer/naming'; // 构建标准化名称对象

// 导出默认函数：接收 dep JSON 字符串，解析后注册实体和关系
export default (content: string) => {
  const raw = JSON.parse(content); // 将 JSON 字符串解析为原始对象

  // 关系 id 计数器（手动递增，因为 dep JSON 中关系没有自带 id 字段）
  let relationId = 0;

  // -------------------------------------------------------------------------
  // 第一遍：遍历所有实体（variables），映射类型、构造限定名，注册到实体容器 e
  // -------------------------------------------------------------------------
  for (const ent of raw['variables']) {
    const extra = {} as any;                    // 预留扩展字段（当前未使用）
    let type = ent['category'] as string;       // 原始类型字符串（如 "PythonFunction"）

    // 将 PyAnalyzer 原始类型字符串映射为标准化小写类型名
    if (/Package/.test(type)) {
      type = 'package';
    } else if (/Module/.test(type)) {
      type = 'module';
    } else if (/Variable/.test(type)) {
      type = 'variable';
    } else if (/Function/.test(type) && !/Anonymous/.test(type)) {
      // 普通函数（排除匿名函数）
      type = 'function';
    } else if (/Parameter/.test(type)) {
      type = 'parameter';
    } else if (/Class/.test(type)) {
      type = 'class';
    } else if (/Attribute/.test(type)) {
      type = 'attribute';
    } else if (/Alias/.test(type)) {
      type = 'alias';
    } else if (/AnonymousFunction/.test(type)) {
      // 匿名函数（lambda）
      type = 'anonymousfunction';
    } else {
      // 未能映射的类型：记录警告并跳过
      warn(`Unmapped type pyanalyzer/python/entity/${type}`);
      continue;
    }

    // 构造 fullname（去掉限定名的第一段，即模块/包名前缀）
    const nameSegment = (ent['qualifiedName'] as string).split('.');
    let fullname = '';
    if (nameSegment.length === 1) {
      fullname = nameSegment[0];  // 只有一段：直接使用
    } else {
      // 多段：从第二段开始拼接（跳过第一段模块前缀）
      for (let i = 1; i < nameSegment.length; i++) {
        if (i !== 1) {
          fullname += '.';
        }
        fullname += nameSegment[i];
      }
    }

    // 取限定名的最后一段作为简短名称
    let name: any = nameSegment.at(-1);

    // 检测是否为匿名函数（lambda）：名称末尾是否含 "(数字)" 模式
    const testAnonymity = /\(\d+\)/.exec(name!);
    if (testAnonymity) {
      // 匿名函数：使用专门的匿名函数名称构建器
      name = buildpyanalyzerName<pyanalyzerNameAnonymous>({as: 'Function'});
    } else {
      // 普通实体：直接用名称字符串构建名称对象
      name = buildpyanalyzerName(name);
    }

    // 将实体注册到容器 e
    e.add({
      id: ent['id'] as number,               // 实体唯一 id
      type: type,                             // 标准化类型名
      name: name,                             // 名称对象
      fullname,                               // 去前缀的限定名
      sourceFile: e.getById(ent['belongs_to']),  // 所属文件实体（通过 belongs_to id 查找）
      location: {
        start: {
          line: ent['location']['startLine'],
          column: ent['location']['startColumn'],
        },
        end: {
          line: ent['location']['endLine'],
          column: ent['location']['endColumn'],
        },
      },
      ...extra,  // 扩展字段（预留）
    });
  }

  // -------------------------------------------------------------------------
  // 第二遍：遍历所有关系（cells），映射类型，注册到关系容器 r
  // -------------------------------------------------------------------------
  for (const rel of raw['cells']) {
    const extra = {} as any;
    let fromId = rel['src'];                  // 关系起点实体 id
    let type = rel['values']['kind'];          // 原始关系类型字符串（如 "Call"）
    let toId = rel['dest'];                    // 关系终点实体 id

    // 将 PyAnalyzer 关系类型映射为标准化小写类型名
    if (/Define/.test(type)) {
      type = 'define';
    } else if (/Use/.test(type)) {
      type = 'use';
    } else if (/Set/.test(type)) {
      type = 'set';
    } else if (/Import/.test(type)) {
      type = 'import';
    } else if (/Call/.test(type)) {
      type = 'call';
    } else if (/Inherit/.test(type)) {
      type = 'inherit';
    } else if (/Contain/.test(type)) {
      type = 'contain';
    } else if (/Annotate/.test(type)) {
      type = 'annotate';
    } else if (/Alias/.test(type)) {
      type = 'alias';
    } else {
      // 未能映射的关系类型：记录警告并跳过
      warn(`Unmapped type pyanalyzer/python/relation/${type}`);
      continue;
    }

    // 通过 id 在实体容器中查找起点和终点实体
    const from = e.getById(fromId);
    const to = e.getById(toId);

    if (from && to) {
      // 两端实体均存在：注册关系
      r.add({
        id: relationId++,        // 手动递增的关系 id
        from,                    // 起点实体对象
        to,                      // 终点实体对象
        type,                    // 标准化关系类型
        location: {
          file: undefined,       // 文件信息（dep JSON 中关系不含文件字段，故为 undefined）
          start: {
            line: rel['location']['startLine'],
            column: rel['location']['startCol'],
          },
        },
        ...extra,
      });
    } else {
      // 找不到起点或终点实体：记录警告（可能是实体过滤掉的边缘情况）
      warn(`Cannot find from/to entity that relation ${rel['from']}--${rel['type']}->${rel['to']} depends.`);
    }
  }
};