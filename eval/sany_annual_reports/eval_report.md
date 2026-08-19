# 三一重工年报 RAG 评测报告

评测时间: 2026-08-19 20:14:46

题目总数: 65

## 总体结果
- 已评分: 19/65
- 总分: 32/57 (56.1%)
- 平均分: 1.68/3
- 完全正确(3分): 7 (36.8%)
- 基本正确(2分): 4 (21.1%)
- 部分/完全错误(0-1分): 8 (42.1%)

## 分类统计

| 类别 | 题数 | 总分 | 平均分 | 满分率 |
|------|------|------|--------|--------|
| A-单文档事实抽取 | 10 | 20/30 | 2.00 | 60% |
| B-表格理解与单位换算 | 6 | 7/18 | 1.17 | 17% |
| C-跨文档对比 | 3 | 5/9 | 1.67 | 0% |

## 难度统计

| 难度 | 题数 | 总分 | 平均分 |
|------|------|------|--------|
| 简单 | 10 | 18/30 | 1.80 |
| 中等 | 9 | 14/27 | 1.56 |

## 详细结果

| 题号 | 类别 | 难度 | 问题(摘要) | 得分 | 理由 |
|------|------|------|-----------|------|------|
| Q01 | A-单文档事实抽 | 简单 | 三一重工2023年的营业收入是多少？ | 3 | 回答包含参考答案的核心信息，关键数字和单位正确 |
| Q02 | A-单文档事实抽 | 简单 | 三一重工2024年归属于上市公司股东的净利润是多少... | 3 | 回答完全正确，包含核心信息且关键数字、单位、同比变化均准确无误 |
| Q03 | A-单文档事实抽 | 简单 | 三一重工2025年经营活动产生的现金流量净额是多少... | 3 | 回答包含参考答案的核心信息，关键数字/事实正确，单位正确，没有明显错误 |
| Q04 | A-单文档事实抽 | 简单 | 2025年三一重工国际主营业务收入和占主营业务收入... | 0 | 回答未包含2025年数据，且年份混淆 |
| Q05 | A-单文档事实抽 | 简单 | 三一重工2024年专利申请多少件？其中发明专利多少... | 0 | 未提供2024年数据，且未正确表示无法回答 |
| Q06 | A-单文档事实抽 | 简单 | 截至2023年末，三一重工在职员工合计多少人？其中... | 3 | 回答包含参考答案的核心信息，关键数字/事实正确，单位正确，没有明显错误 |
| Q07 | A-单文档事实抽 | 中等 | 三一重工H股什么时候在哪上市？发行价多少？ | 3 | 回答包含参考答案的所有核心信息，关键数字和单位正确，没有遗漏或错误 |
| Q08 | A-单文档事实抽 | 简单 | 三一重工2023年度利润分配预案是什么？ | 2 | 回答包含核心信息，但遗漏了‘预案尚须提交股东大会审议’及‘扣除回购股份’的限定条 |
| Q09 | A-单文档事实抽 | 简单 | 2024年三一重工起重机械实现销售收入多少？ | 3 | 回答包含参考答案的核心信息，关键数字/事实正确，单位正确，没有明显错误 |
| Q10 | A-单文档事实抽 | 简单 | 三一重工2025年基本每股收益是多少？ | 0 | 未提供2025年基本每股收益信息 |
| Q11 | B-表格理解与单 | 简单 | 三一重工年报“主要会计数据”表的金额单位是什么？ | 1 | 回答涉及相关内容但未明确给出正确答案，且存在单位不确定的错误 |
| Q12 | B-表格理解与单 | 中等 | 截至2025年末，三一重工总资产是多少亿元人民币？ | 0 | 未提供总资产数据 |
| Q13 | B-表格理解与单 | 中等 | 2023年三一重工研发投入合计多少？占营业收入比例... | 1 | 数字与占比均错误，且年份混淆 |
| Q14 | B-表格理解与单 | 中等 | 2024年三一重工研发投入资本化的比重是多少？资本... | 0 | 未提供2024年数据，且混淆了年份 |
| Q15 | B-表格理解与单 | 中等 | 三一重工对娄底中兴液压件有限公司的持股比例是多少？... | 2 | 持股比例正确，但2025年净利润数据缺失且未提供正确年份数据 |
| Q16 | B-表格理解与单 | 中等 | 2024年末三一重工母公司和主要子公司在职员工各多... | 3 | 回答完全匹配参考答案，关键数字和年份正确 |
| Q17 | C-跨文档对比 | 中等 | 三一重工2023-2025年营业收入分别是多少？呈... | 1 | 年份数据部分正确，但2023年数据与参考答案不符，且缺少2025年数据 |
| Q18 | C-跨文档对比 | 中等 | 2023-2025年三一重工归母净利润的变化情况如... | 2 | 回答包含部分正确信息，但缺少2025年数据且未说明调整值差异 |
| Q19 | C-跨文档对比 | 中等 | 三一重工近三年海外（国际）收入分别是多少？ | 2 | 2023年数据为推算值，非官方披露；2022年数据缺失 |
| Q20 | C-跨文档对比 | 中等 | 2023-2025年三一重工挖掘机械销售收入分别是... | 未评 |  |
| Q21 | C-跨文档对比 | 中等 | 三一重工研发人员数量近三年如何变化？占总人数比例如... | 未评 |  |
| Q22 | C-跨文档对比 | 中等 | 三一重工2023-2025年在职员工总数有何变化？ | 未评 |  |
| Q23 | C-跨文档对比 | 中等 | 近三年三一重工经营性现金流净额分别是多少？与净利润... | 未评 |  |
| Q24 | C-跨文档对比 | 困难 | 三一重工2023、2024、2025年报各自披露的... | 未评 |  |
| Q25 | D-计算与多跳推 | 中等 | 2025年三一重工海外四大区域收入分别是多少？合计... | 未评 |  |
| Q26 | D-计算与多跳推 | 中等 | 2025年三一重工每股合计分红多少（含已实施的中期... | 未评 |  |
| Q27 | D-计算与多跳推 | 中等 | 三一重工2023-2025年三年归母净利润累计约为... | 未评 |  |
| Q28 | D-计算与多跳推 | 中等 | 2025年三一重工的归母净利率约为多少？ | 未评 |  |
| Q29 | D-计算与多跳推 | 中等 | 2024年三一重工经营现金流净额约是当年归母净利润... | 未评 |  |
| Q30 | D-计算与多跳推 | 中等 | 三一重工非洲区域收入从2023年到2025年增长了... | 未评 |  |
| Q31 | D-计算与多跳推 | 困难 | 结合三年数据，判断“三一重工盈利改善主要靠国内市场... | 未评 |  |
| Q32 | E-时序与追溯调 | 困难 | 三一重工2023年归母净利润到底是4,527,49... | 未评 |  |
| Q33 | E-时序与追溯调 | 困难 | 2025年三季报…不对，请回答：2024年归母净利... | 未评 |  |
| Q34 | E-时序与追溯调 | 困难 | 三一重工2024年基本每股收益是多少？（注意不同年... | 未评 |  |
| Q35 | E-时序与追溯调 | 困难 | 三一重工在2023年报中预计2024年营业收入增长... | 未评 |  |
| Q36 | E-时序与追溯调 | 中等 | 2023年度分红是什么时候实施的？每股派多少、共派... | 未评 |  |
| Q37 | E-时序与追溯调 | 中等 | 三一重工2025年半年度分红方案是什么？何时发放？ | 未评 |  |
| Q38 | F-口径与概念辨 | 中等 | 2023年三一重工“研发费用”和“研发投入合计”分... | 未评 |  |
| Q39 | F-口径与概念辨 | 中等 | 2025年三一重工归母净利润和扣非归母净利润分别是... | 未评 |  |
| Q40 | F-口径与概念辨 | 中等 | 2023年三一重工“国际收入占主营业务收入比重60... | 未评 |  |
| Q41 | F-口径与概念辨 | 中等 | “2025年度利润分配预案每10股派1.8元”是已... | 未评 |  |
| Q42 | F-口径与概念辨 | 困难 | 2025年报主要会计数据表中，2024年数据为什么... | 未评 |  |
| Q43 | F-口径与概念辨 | 中等 | 2024年报说“国际业务毛利率29.72%，上升0... | 未评 |  |
| Q44 | G-实体消歧 | 简单 | “三一集团”就是三一重工吗？两者什么关系？ | 未评 |  |
| Q45 | G-实体消歧 | 中等 | 三一重工的董事长是谁？梁稳根在公司担任什么职务？ | 未评 |  |
| Q46 | G-实体消歧 | 中等 | 三一重工2025年年报和2023年年报中的境内签字... | 未评 |  |
| Q47 | G-实体消歧 | 中等 | 600031和06031是三一重工的同一只股票吗？ | 未评 |  |
| Q48 | G-实体消歧 | 中等 | 普茨迈斯特（Putzmeister）与三一重工是什... | 未评 |  |
| Q49 | G-实体消歧 | 中等 | 俞宏福在三一重工担任什么职务？2025年税前报酬多... | 未评 |  |
| Q50 | H-错误前提纠偏 | 中等 | 2024年三一重工混凝土机械收入同比增长了多少？ | 未评 |  |
| Q51 | H-错误前提纠偏 | 中等 | 为什么三一重工连续三年加大研发投入金额？ | 未评 |  |
| Q52 | H-错误前提纠偏 | 困难 | 2025年三一重工分红力度明显缩水了吧？年度预案才... | 未评 |  |
| Q53 | H-错误前提纠偏 | 中等 | 三一重工挖掘机2023年是全球销量第一，对吗？ | 未评 |  |
| Q54 | H-错误前提纠偏 | 困难 | 2024年三一重工海外产品覆盖的国家数量比2023... | 未评 |  |
| Q55 | I-拒答与知识边 | 中等 | 三一重工2025年新能源（电动化）产品的收入是多少... | 未评 |  |
| Q56 | I-拒答与知识边 | 中等 | 三一重工2026年的营业收入增长目标是多少？ | 未评 |  |
| Q57 | I-拒答与知识边 | 简单 | 中联重科2025年的营业收入是多少？与三一重工相比... | 未评 |  |
| Q58 | I-拒答与知识边 | 中等 | 三一重工2025年在德国市场的销售额是多少？ | 未评 |  |
| Q59 | I-拒答与知识边 | 中等 | 2025年末三一重工H股的总市值是多少港元？ | 未评 |  |
| Q60 | J-细节与脚注 | 中等 | 截至2025年12月31日，三一重工总股本是多少股... | 未评 |  |
| Q61 | J-细节与脚注 | 中等 | 三一重工的统一客户互动界面MySANY覆盖多少个国... | 未评 |  |
| Q62 | J-细节与脚注 | 中等 | 2025年三一重工在全球市场推广了多少款产品？ | 未评 |  |
| Q63 | J-细节与脚注 | 简单 | 三一重工董事长向文波的出生年月和籍贯是？ | 未评 |  |
| Q64 | J-细节与脚注 | 中等 | 三一重工最近三个会计年度累计现金分红金额是多少（截... | 未评 |  |
| Q65 | J-细节与脚注 | 中等 | 2025年三一重工成立了什么新的全球营销组织？其职... | 未评 |  |

## 错误/异常 (45题)
- **Q21**: 500 Server Error: Internal Server Error for url: http://localhost:8000/api/v1/chat/stream
- **Q22**: 500 Server Error: Internal Server Error for url: http://localhost:8000/api/v1/chat/stream
- **Q23**: 500 Server Error: Internal Server Error for url: http://localhost:8000/api/v1/chat/stream
- **Q24**: 500 Server Error: Internal Server Error for url: http://localhost:8000/api/v1/chat/stream
- **Q25**: 500 Server Error: Internal Server Error for url: http://localhost:8000/api/v1/chat/stream
- **Q26**: 500 Server Error: Internal Server Error for url: http://localhost:8000/api/v1/chat/stream
- **Q27**: 500 Server Error: Internal Server Error for url: http://localhost:8000/api/v1/chat/stream
- **Q28**: 500 Server Error: Internal Server Error for url: http://localhost:8000/api/v1/chat/stream
- **Q29**: 500 Server Error: Internal Server Error for url: http://localhost:8000/api/v1/chat/stream
- **Q30**: 500 Server Error: Internal Server Error for url: http://localhost:8000/api/v1/chat/stream
- **Q31**: 500 Server Error: Internal Server Error for url: http://localhost:8000/api/v1/chat/stream
- **Q32**: 500 Server Error: Internal Server Error for url: http://localhost:8000/api/v1/chat/stream
- **Q33**: 500 Server Error: Internal Server Error for url: http://localhost:8000/api/v1/chat/stream
- **Q34**: 500 Server Error: Internal Server Error for url: http://localhost:8000/api/v1/chat/stream
- **Q35**: 500 Server Error: Internal Server Error for url: http://localhost:8000/api/v1/chat/stream
- **Q36**: 500 Server Error: Internal Server Error for url: http://localhost:8000/api/v1/chat/stream
- **Q37**: 500 Server Error: Internal Server Error for url: http://localhost:8000/api/v1/chat/stream
- **Q38**: 500 Server Error: Internal Server Error for url: http://localhost:8000/api/v1/chat/stream
- **Q39**: 500 Server Error: Internal Server Error for url: http://localhost:8000/api/v1/chat/stream
- **Q40**: 500 Server Error: Internal Server Error for url: http://localhost:8000/api/v1/chat/stream
- **Q41**: 500 Server Error: Internal Server Error for url: http://localhost:8000/api/v1/chat/stream
- **Q42**: 500 Server Error: Internal Server Error for url: http://localhost:8000/api/v1/chat/stream
- **Q43**: 500 Server Error: Internal Server Error for url: http://localhost:8000/api/v1/chat/stream
- **Q44**: 500 Server Error: Internal Server Error for url: http://localhost:8000/api/v1/chat/stream
- **Q45**: 500 Server Error: Internal Server Error for url: http://localhost:8000/api/v1/chat/stream
- **Q46**: 500 Server Error: Internal Server Error for url: http://localhost:8000/api/v1/chat/stream
- **Q47**: 500 Server Error: Internal Server Error for url: http://localhost:8000/api/v1/chat/stream
- **Q48**: 500 Server Error: Internal Server Error for url: http://localhost:8000/api/v1/chat/stream
- **Q49**: 500 Server Error: Internal Server Error for url: http://localhost:8000/api/v1/chat/stream
- **Q50**: 500 Server Error: Internal Server Error for url: http://localhost:8000/api/v1/chat/stream
- **Q51**: 500 Server Error: Internal Server Error for url: http://localhost:8000/api/v1/chat/stream
- **Q52**: 500 Server Error: Internal Server Error for url: http://localhost:8000/api/v1/chat/stream
- **Q53**: 500 Server Error: Internal Server Error for url: http://localhost:8000/api/v1/chat/stream
- **Q54**: 500 Server Error: Internal Server Error for url: http://localhost:8000/api/v1/chat/stream
- **Q55**: 500 Server Error: Internal Server Error for url: http://localhost:8000/api/v1/chat/stream
- **Q56**: 500 Server Error: Internal Server Error for url: http://localhost:8000/api/v1/chat/stream
- **Q57**: 500 Server Error: Internal Server Error for url: http://localhost:8000/api/v1/chat/stream
- **Q58**: 500 Server Error: Internal Server Error for url: http://localhost:8000/api/v1/chat/stream
- **Q59**: 500 Server Error: Internal Server Error for url: http://localhost:8000/api/v1/chat/stream
- **Q60**: 500 Server Error: Internal Server Error for url: http://localhost:8000/api/v1/chat/stream
- **Q61**: 500 Server Error: Internal Server Error for url: http://localhost:8000/api/v1/chat/stream
- **Q62**: 500 Server Error: Internal Server Error for url: http://localhost:8000/api/v1/chat/stream
- **Q63**: 500 Server Error: Internal Server Error for url: http://localhost:8000/api/v1/chat/stream
- **Q64**: 500 Server Error: Internal Server Error for url: http://localhost:8000/api/v1/chat/stream
- **Q65**: 500 Server Error: Internal Server Error for url: http://localhost:8000/api/v1/chat/stream