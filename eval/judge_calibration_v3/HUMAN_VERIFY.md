# P0 Judge Calibration — Human Verify

> **共 15 题**, 分层抽样: A=3, B=2, C=2, D=2, E=2, H=2, I=2
> **你需要做的**: 对每题填 4 个字段 (human_correct / human_partial / human_refusal_correct / human_citation_correct)
> **填完后**: 自动算 judge ↔ human agreement (脚本会自动处理)

---

## Q01 [A-单文档事实抽取]

**Question**: 三一重工2023年的营业收入是多少？

**Gold Answer**: 732.22亿元（年报原文为73,221,725千元，同比下降8.51%）。

**RAG Answer**: 根据2023年年度报告，三一重工2023年营业总收入为740.19亿元[1]。

**Judge Score**: 2 (0-3)

**Judge Reason**: 数字接近参考答案，但存在轻微偏差

**Judge Raw Response**: ````json
{"score": 2, "reason": "数字接近参考答案，但存在轻微偏差"}
````

**Citations**: 1 个, IDs: [1]

**Human Verify (填空)**：
- [ ] human_correct: true / false
- [ ] human_partial: true / false
- [ ] human_refusal_correct: N/A (模型未拒答) 或 true (模型拒答且应拒答) 或 false (模型拒答但不应拒答)
- [ ] human_citation_correct: true / false / N/A

**Notes**: (free-form 备注)

---

## Q02 [A-单文档事实抽取]

**Question**: 三一重工2024年归属于上市公司股东的净利润是多少？同比变化多少？

**Gold Answer**: 59.75亿元（5,975,451千元），同比增长31.98%。

**RAG Answer**: 根据2024年年度报告：-**2024年归属于上市公司股东的净利润**：59.75亿元（5,975,451千元）-**同比2023年变化**：增长31.98%（2023年调整后净利润为45.27亿元）[1]

**Judge Score**: 3 (0-3)

**Judge Reason**: 完全正确，核心信息全部覆盖，数字/单位正确

**Judge Raw Response**: ````json
{"score": 3, "reason": "完全正确，核心信息全部覆盖，数字/单位正确"}
````

**Citations**: 1 个, IDs: [1]

**Human Verify (填空)**：
- [ ] human_correct: true / false
- [ ] human_partial: true / false
- [ ] human_refusal_correct: N/A (模型未拒答) 或 true (模型拒答且应拒答) 或 false (模型拒答但不应拒答)
- [ ] human_citation_correct: true / false / N/A

**Notes**: (free-form 备注)

---

## Q03 [A-单文档事实抽取]

**Question**: 三一重工2025年经营活动产生的现金流量净额是多少？

**Gold Answer**: 199.75亿元（19,975,261千元），同比增长34.84%。

**RAG Answer**: 根据2025年年度报告披露的数据，三一重工2025年经营活动产生的现金流量净额为19,975,261千元人民币[2]。

**Judge Score**: 3 (0-3)

**Judge Reason**: 核心信息完全正确，数字和单位与参考答案一致

**Judge Raw Response**: ````json
{"score": 3, "reason": "核心信息完全正确，数字和单位与参考答案一致"}
````

**Citations**: 1 个, IDs: [2]

**Human Verify (填空)**：
- [ ] human_correct: true / false
- [ ] human_partial: true / false
- [ ] human_refusal_correct: N/A (模型未拒答) 或 true (模型拒答且应拒答) 或 false (模型拒答但不应拒答)
- [ ] human_citation_correct: true / false / N/A

**Notes**: (free-form 备注)

---

## Q11 [B-表格理解与单位换算]

**Question**: 三一重工年报“主要会计数据”表的金额单位是什么？

**Gold Answer**: 千元，币种为人民币。三份年报该表单位均为“千元”。

**RAG Answer**: 我没有找到“主要会计数据”表的具体金额单位。但根据年报中其他财务表格的披露惯例（如衍生品投资、租赁收入等），金额单位通常为人民币，并以“千元”或“万元”为单位[1][2][3]。建议查阅年报正文或附注中的“财务报表编制基础”部分以获取确切信息。

**Judge Score**: 2 (0-3)

**Judge Reason**: 回答部分正确，但未明确指出具体金额单位为千元，存在小遗漏

**Judge Raw Response**: ````json
{"score": 2, "reason": "回答部分正确，但未明确指出具体金额单位为千元，存在小遗漏"}
````

**Citations**: 3 个, IDs: [1, 2, 3]

**Human Verify (填空)**：
- [ ] human_correct: true / false
- [ ] human_partial: true / false
- [ ] human_refusal_correct: N/A (模型未拒答) 或 true (模型拒答且应拒答) 或 false (模型拒答但不应拒答)
- [ ] human_citation_correct: true / false / N/A

**Notes**: (free-form 备注)

---

## Q12 [B-表格理解与单位换算]

**Question**: 截至2025年末，三一重工总资产是多少亿元人民币？

**Gold Answer**: 约1,733.0亿元（173,299,138千元），较2024年末增长13.90%。

**RAG Answer**: 我没有找到2025年末三一重工总资产的相关信息。年报的财务报告部分和管理层讨论与分析部分均未披露该数据。如需了解该信息，建议查阅三一重工2025年年度报告的完整财务报告部分或资产负债表。

**Judge Score**: 0 (0-3)

**Judge Reason**: 未提供数据且未与参考答案匹配

**Judge Raw Response**: ````json
{"score": 0, "reason": "未提供数据且未与参考答案匹配"}
````

**Citations**: 0 个, IDs: []

**Human Verify (填空)**：
- [ ] human_correct: true / false
- [ ] human_partial: true / false
- [ ] human_refusal_correct: N/A (模型未拒答) 或 true (模型拒答且应拒答) 或 false (模型拒答但不应拒答)
- [ ] human_citation_correct: true / false / N/A

**Notes**: (free-form 备注)

---

## Q17 [C-跨文档对比]

**Question**: 三一重工2023-2025年营业收入分别是多少？呈现什么趋势？

**Gold Answer**: 2023年732.22亿元、2024年777.73亿元、2025年892.31亿元，连续三年增长（2024年+6.22%、2025年+14.73%），增速加快。

**RAG Answer**: 根据年报披露数据：-**2023年**：营业总收入740.19亿元，同比下降8.44%[Source2]；-**2024年**：年报未直接披露营业收入总额；-**2025年**：推算营业总收入约896.98亿元（基于工程机械设备收入724.21亿元占总收入80.74%计算）[Source1]。**趋势**：2023年受行业筑底影响收入下滑，但2025年显著回升，较2023年增长约21.18%，主要受益于国内政策催化及海外市场拓展[Source1][Source2]。

**Judge Score**: 1 (0-3)

**Judge Reason**: 数据与趋势描述与参考答案不符，2023年数据错误且趋势相反

**Judge Raw Response**: ````json
{"score": 1, "reason": "数据与趋势描述与参考答案不符，2023年数据错误且趋势相反"}
````

**Citations**: 0 个, IDs: []

**Human Verify (填空)**：
- [ ] human_correct: true / false
- [ ] human_partial: true / false
- [ ] human_refusal_correct: N/A (模型未拒答) 或 true (模型拒答且应拒答) 或 false (模型拒答但不应拒答)
- [ ] human_citation_correct: true / false / N/A

**Notes**: (free-form 备注)

---

## Q18 [C-跨文档对比]

**Question**: 2023-2025年三一重工归母净利润的变化情况如何？

**Gold Answer**: 45.27亿元（2023，+5.53%）→ 59.75亿元（2024，+31.98%）→ 84.08亿元（2025，+41.18%），利润增速连续三年显著高于营收增速。

**RAG Answer**: 根据年报披露数据：-**2023年**：归属于上市公司股东的净利润为4,527,498千元（分季度加总）[2]。-**2025年**：归属于上市公司股东的净利润为8,408,057千元（分季度加总）[1]。**变化情况说明**：1.2023至2025年净利润呈现显著增长（从45.27亿元增至84.08亿元，增幅约85.7%）。2.但需注意：-年报未披露2024年数据，无法判断期间具体波动情况。-2025年净利润存在季度波动（Q4环比下降明显，可能与会计准则调整有关）[1]。**数据来源标注**：-2025年数据来自《三一重工2025年年度报告》分季度财务数据[1]。-2023年数据来自《三一重工2023年年度报告》分季度财务数据[2]。-2024年数据未在检索内容中披露。

**Judge Score**: 2 (0-3)

**Judge Reason**: 基本正确，但缺少2024年数据且未提增速细节

**Judge Raw Response**: ````json
{"score": 2, "reason": "基本正确，但缺少2024年数据且未提增速细节"}
````

**Citations**: 5 个, IDs: [1, 2]

**Human Verify (填空)**：
- [ ] human_correct: true / false
- [ ] human_partial: true / false
- [ ] human_refusal_correct: N/A (模型未拒答) 或 true (模型拒答且应拒答) 或 false (模型拒答但不应拒答)
- [ ] human_citation_correct: true / false / N/A

**Notes**: (free-form 备注)

---

## Q25 [D-计算与多跳推理]

**Question**: 2025年三一重工海外四大区域收入分别是多少？合计是否与国际主营业务收入总额吻合？

**Gold Answer**: 亚澳238.9亿、欧洲125亿、美洲111.6亿、非洲83.1亿；合计558.6亿元，与披露的国际主营业务收入558.6亿元吻合。

**RAG Answer**: 根据2025年年报数据，三一重工海外四大区域收入具体为：-**亚澳区域**：238.9亿元（同比增长16.17%）-**欧洲区域**：125亿元（同比增长1.5%）-**美洲区域**：111.6亿元（同比增长8.52%）-**非洲区域**：83.1亿元（同比增长55.29%）合计：**558.6亿元**[3]。年报未直接披露2025年国际主营业务收入总额，因此无法验证该合计是否与总额完全吻合。需注意：1.区域收入合计可能存在统计口径差异（如未包含其他零星地区收入）；2.2024年国际主营业务收入为485.13亿元[1]，2025年各区域增速均高于此基数，但总额需以单独披露为准。

**Judge Score**: 3 (0-3)

**Judge Reason**: 完全正确，核心信息全部覆盖，数字/单位正确

**Judge Raw Response**: ````json
{"score": 3, "reason": "完全正确，核心信息全部覆盖，数字/单位正确"}
````

**Citations**: 2 个, IDs: [1, 3]

**Human Verify (填空)**：
- [ ] human_correct: true / false
- [ ] human_partial: true / false
- [ ] human_refusal_correct: N/A (模型未拒答) 或 true (模型拒答且应拒答) 或 false (模型拒答但不应拒答)
- [ ] human_citation_correct: true / false / N/A

**Notes**: (free-form 备注)

---

## Q26 [D-计算与多跳推理]

**Question**: 2025年三一重工每股合计分红多少（含已实施的中期分红与年度预案）？

**Gold Answer**: 2025年半年度每10股派3.1元（已实施）+ 2025年度预案每10股派1.8元（尚需股东会审议）= 全年合计每10股4.9元，即每股0.49元（含税）。

**RAG Answer**: 2025年三一重工每股合计分红金额（含已实施的中期分红与年度预案）为0.80元（含税），其中：-中期分红：每股0.31元（2025年10月15日实施）[1]-年度分红：每股0.49元（预案，每10股派4.9元）[1]

**Judge Score**: 0 (0-3)

**Judge Reason**: 年度分红数据错误，与参考答案不符

**Judge Raw Response**: ````json
{"score": 0, "reason": "年度分红数据错误，与参考答案不符"}
````

**Citations**: 2 个, IDs: [1]

**Human Verify (填空)**：
- [ ] human_correct: true / false
- [ ] human_partial: true / false
- [ ] human_refusal_correct: N/A (模型未拒答) 或 true (模型拒答且应拒答) 或 false (模型拒答但不应拒答)
- [ ] human_citation_correct: true / false / N/A

**Notes**: (free-form 备注)

---

## Q32 [E-时序与追溯调整]

**Question**: 三一重工2023年归母净利润到底是4,527,498千元还是4,527,451千元？

**Gold Answer**: 两个数字都“正确”但口径不同：2023年报原披露4,527,498千元（调整前）；2024年报对2023年数据追溯调整后列示为4,527,451千元。引用时应注明口径，严格来说最新可比口径为调整后4,527,451千元。

**RAG Answer**: 根据检索内容：1.**2023年年度报告**[1]明确记载归属于上市公司股东的净利润为**4,527,498千元**（见主要会计数据和未分配利润表）。2.2024年报告中对比数据时出现**4,527,451千元**[2]，但同表格内仍标注原始数据为4,527,498千元，差异47千元可能因后续调整或四舍五入导致。建议以2023年报原始披露的**4,527,498千元**为准，2024年报中的差异需参考审计调整说明（检索内容未提供具体调整原因）。两者差异极小（0.001%），对整体财务分析无实质影响。

**Judge Score**: 2 (0-3)

**Judge Reason**: 基本正确，但未明确提及调整后数据为最新可比口径

**Judge Raw Response**: ````json
{"score": 2, "reason": "基本正确，但未明确提及调整后数据为最新可比口径"}
````

**Citations**: 2 个, IDs: [1, 2]

**Human Verify (填空)**：
- [ ] human_correct: true / false
- [ ] human_partial: true / false
- [ ] human_refusal_correct: N/A (模型未拒答) 或 true (模型拒答且应拒答) 或 false (模型拒答但不应拒答)
- [ ] human_citation_correct: true / false / N/A

**Notes**: (free-form 备注)

---

## Q33 [E-时序与追溯调整]

**Question**: 2025年三季报…不对，请回答：2024年归母净利润在2024年报和2025年报中分别是多少？为什么不一样？

**Gold Answer**: 2024年报披露5,975,451千元（+31.98%）；2025年报中2024年“调整后”为5,955,567千元（对应增速变为41.18%的基数）。差异源于2025年报对上年数据的追溯调整（调整前/调整后双列示）。

**RAG Answer**: 根据2025年报披露：1.**差异原因**：2025年发生同一控制下企业合并，按照会计准则要求对2024年季度数据进行了追溯调整[1]。2.**调整后数据**：2024年四个季度归母净利润合计为：-Q1:2,469,401千元-Q2:2,744,709千元-Q3:1,918,582千元-Q4:1,275,365千元**全年合计8,408,057千元**（调整后值）年报未直接披露2024年报中的原始归母净利润数据，但明确差异源于会计追溯调整。此类调整通常涉及合并范围变化或会计政策变更导致的财务数据重述。[1]2025年报季度数据与已披露定期报告数据差异说明部分

**Judge Score**: 1 (0-3)

**Judge Reason**: 部分正确，调整后数据与参考答案不符，且未提供2024年报原始数据

**Judge Raw Response**: ````json
{"score": 1, "reason": "部分正确，调整后数据与参考答案不符，且未提供2024年报原始数据"}
````

**Citations**: 2 个, IDs: [1]

**Human Verify (填空)**：
- [ ] human_correct: true / false
- [ ] human_partial: true / false
- [ ] human_refusal_correct: N/A (模型未拒答) 或 true (模型拒答且应拒答) 或 false (模型拒答但不应拒答)
- [ ] human_citation_correct: true / false / N/A

**Notes**: (free-form 备注)

---

## Q50 [H-错误前提纠偏]

**Question**: 2024年三一重工混凝土机械收入同比增长了多少？

**Gold Answer**: 前提不成立：2024年混凝土机械收入143.68亿元，较2023年的153.15亿元实际是下降的（约-6.2%）。但年报强调其连续14年蝉联全球第一品牌，且电动搅拌车销售额首次超过燃油搅拌车。

**RAG Answer**: 您提到的混凝土机械收入增长这一前提不成立。根据年报数据：-**2024年**：混凝土机械销售收入143.68亿元-**2023年**：混凝土机械销售收入153.15亿元实际同比**下降6.18%**[1][3]。这一变动可能与产品结构升级（如电动搅拌车销售额首次超过燃油车）及市场需求调整有关[1]。

**Judge Score**: 3 (0-3)

**Judge Reason**: 完全正确，核心信息全部覆盖，数字/单位正确

**Judge Raw Response**: ````json
{"score": 3, "reason": "完全正确，核心信息全部覆盖，数字/单位正确"}
````

**Citations**: 3 个, IDs: [1, 3]

**Human Verify (填空)**：
- [ ] human_correct: true / false
- [ ] human_partial: true / false
- [ ] human_refusal_correct: N/A (模型未拒答) 或 true (模型拒答且应拒答) 或 false (模型拒答但不应拒答)
- [ ] human_citation_correct: true / false / N/A

**Notes**: (free-form 备注)

---

## Q51 [H-错误前提纠偏]

**Question**: 为什么三一重工连续三年加大研发投入金额？

**Gold Answer**: 前提不成立：研发投入合计连续三年下降——61.01亿（2023）→54.88亿（2024）→51.69亿（2025），占营收比例从8.33%降至5.79%；研发人员也从8,057人降至5,720人。公司仍强调研发创新的战略地位，但投入金额确实收缩。

**RAG Answer**: 检索内容无法证实“连续三年加大研发投入”的前提。根据2025年年报：-**2025年研发投入**：50.33亿元，主要投向全球化研发布局、数智化技术及低碳化产品（如电驱桥、氢燃料技术等）[1]-**研发成果**：新能源产品销售额同比提升115%，专利申请619件（发明专利325件），并推出多款行业领先产品（如全球最大吨位混动起重机SAC2500E7PHEV）[1]。但2023-2024年研发投入金额未披露，因此无法验证是否连续三年增加。若需更完整趋势分析，需补充其他年份数据。[1]三一重工2025年年度报告（第三节管理层讨论与分析）

**Judge Score**: 2 (0-3)

**Judge Reason**: 基本正确，但未明确提及研发投入下降趋势及人员缩减

**Judge Raw Response**: ````json
{"score": 2, "reason": "基本正确，但未明确提及研发投入下降趋势及人员缩减"}
````

**Citations**: 3 个, IDs: [1]

**Human Verify (填空)**：
- [ ] human_correct: true / false
- [ ] human_partial: true / false
- [ ] human_refusal_correct: N/A (模型未拒答) 或 true (模型拒答且应拒答) 或 false (模型拒答但不应拒答)
- [ ] human_citation_correct: true / false / N/A

**Notes**: (free-form 备注)

---

## Q55 [I-拒答与知识边界]

**Question**: 三一重工2025年新能源（电动化）产品的收入是多少亿元？

**Gold Answer**: 无法从三份年报中获得。年报披露了电动化产品布局（纯电、混动、氢燃料三大路线）及电动搅拌车市占率等定性信息，但未单独披露新能源/电动化产品的收入金额。

**RAG Answer**: 根据2025年年度报告披露，三一重工2025年新能源（电动化）产品销售额为**86.4亿元人民币**，同比增长115%[1]。

**Judge Score**: 0 (0-3)

**Judge Reason**: 编造数据，与参考答案矛盾

**Judge Raw Response**: ````json
{"score": 0, "reason": "编造数据，与参考答案矛盾"}
````

**Citations**: 1 个, IDs: [1]

**Human Verify (填空)**：
- [ ] human_correct: true / false
- [ ] human_partial: true / false
- [ ] human_refusal_correct: N/A (模型未拒答) 或 true (模型拒答且应拒答) 或 false (模型拒答但不应拒答)
- [ ] human_citation_correct: true / false / N/A

**Notes**: (free-form 备注)

---

## Q56 [I-拒答与知识边界]

**Question**: 三一重工2026年的营业收入增长目标是多少？

**Gold Answer**: 三份年报中没有可回答的数字。2025年报的2026年经营计划仅有定性表述（高质量发展、全球化/数智化/低碳化转型等），未给出量化营收目标；可对比的是2023年报曾提出2024年营收增长10%以上的量化目标。

**RAG Answer**: 我没有找到相关信息。年报未披露2026年的营业收入增长目标，知识库中的数据覆盖到最近的会计年度（2025年），不包含未来预测数据。

**Judge Score**: 3 (0-3)

**Judge Reason**: 回答完全正确，准确反映了年报未披露2026年量化目标的情况

**Judge Raw Response**: ````json
{"score": 3, "reason": "回答完全正确，准确反映了年报未披露2026年量化目标的情况"}
````

**Citations**: 0 个, IDs: []

**Human Verify (填空)**：
- [ ] human_correct: true / false
- [ ] human_partial: true / false
- [ ] human_refusal_correct: N/A (模型未拒答) 或 true (模型拒答且应拒答) 或 false (模型拒答但不应拒答)
- [ ] human_citation_correct: true / false / N/A

**Notes**: (free-form 备注)

---

## 填完后请运行

```bash
D:/miniConda/envs/rag/python.exe eval/aggregate_judge_agreement.py \
  --human <path-to-filled-md>
```

(aggregate_judge_agreement.py 待实现 — 解析 human_verify.md 中的 [ ] 标记)
