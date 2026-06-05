// =============================================================================
// extractor.ts
// 功能：从 PyAnalyzer 的输出目录中读取指定测试用例的分析报告 JSON 文件，
//       并以字符串形式返回其内容，供 builder.ts 解析使用。
// =============================================================================

import {readFile} from 'node:fs/promises'; // 异步文件读取

// 导出默认异步函数
// 参数：g    —— 测试组名（group）
//       c    —— 测试用例名（case）
//       ocwd —— 原始工作目录（传入但本函数实际未使用，预留为备用）
// 返回：报告文件的 UTF-8 字符串内容，或在读取失败时返回 false
export default async (g: string, c: string, ocwd: string) => {
  try {
    // PyAnalyzer 报告文件的路径约定：
    //   <当前工作目录>/tests/pyanalyzer/<g>/<c>/_<c>-report-pyanalyzer.json
    return await readFile(
      `${process.cwd()}/tests/pyanalyzer/${g}/${c}/_${c}-report-pyanalyzer.json`,
      'utf-8'
    );
  } catch (e: any) {
    console.error(e);  // 打印错误（文件不存在或读取权限问题等）
    return false;      // 读取失败时返回 false，调用方据此判断是否继续
  }
};