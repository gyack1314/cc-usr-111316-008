# 可比投入价格：契约与统计口径

unit（地区时期单元）是稳定名；本包示例同一地区三个月，接口接受多地区。
price、quantity 为正有理数（JSON 可用字符串保留精确值）。单位值是
`sum(price*quantity)/sum(quantity)`。退货（负价或负量）、零价与零量不进入
指数，只进拒绝账，并保留原始字符串与拒绝理由。

## 1. 交易聚合与产品更替

* 同一 `unit×product` 的多笔有效交易先按 `总支出/总数量` 聚合；拆分成交
  不改变单位值。
* `product_map` 把原产品映至统一产品，缺项保持原 ID，映射不可多目标、
  不可映到自身、不可形成链或循环（必须已是冻结终态）。没有人工映射就不
  做强配，产品更替因此表现为共同篮子缩小甚至无边。
* 映射在聚合前应用：同一目标下的多笔交易一起合并。
* 支持 `{"old": "new"}` 简表与带审计说明的
  `{"old": {"target": "new", "rationale": "..."}}` 两种形式。

## 2. 双边比较与可比边

对单元对 (a, b)，只用两端都有的产品（共同篮子）：

* `L_ab = sum(p_b*q_a) / sum(p_a*q_a)`
* `P_ab = sum(p_b*q_b) / sum(p_a*q_b)`
* `F_ab = sqrt(L_ab * P_ab)`，记录为 b 相对 a 的价格水平。
* 覆盖率 `coverage_a = 共同产品在 a 的支出 / a 全部有效支出`，b 同理。
* 任一侧覆盖率低于 `coverage_threshold`（含等号保留）或没有共同产品，
  这条对没有可比边。除此之外图边权为
  `sqrt(coverage_a * coverage_b)`（`geometric-mean-coverage`）。
* 正反方向互换单元后 `F_ba = 1/F_ab`。

每个候选边（包括被拒绝的）都输出 `common_products`、`laspeyres`、
`paasche`、`fisher`、两侧覆盖率与拒绝理由。边矩阵给出对数 Fisher 的
上/下三角（下三角为负的上三角）、`accepted` 与 `coverage_min`。

## 3. 多边指数：完整 GEKS 与缺边图

* 请求单元集两两均有可比边时，方法为 `geks-fisher`：
  `log index_j = (1/n) * sum_k (log F_kj - log F_k0)`，基准 0 为 1。
  完整结果同时给出 `complete_graph=true`、`comparison_scope=all-units-relative-to-<base>`。
* 任一对缺边时，方法为 `weighted-graph-least-squares`：
  最小化 `sum_edge w_ab * (z_b - z_a - log F_ab)^2`，固定基准（若基准不在
  该分量则取分量首单元）为 0。结果标记 `complete_graph=false`，绝不称为
  GEKS。
* 图不连通时只在各连通分量内给相对指数：每个分量的锚点（含基准的分量用
  基准，否则用分量首单元）为 1，不跨分量填值，也不跨分量比较；输出
  `comparison_scope=within-component-only` 与 `comparable_with_base`。
  孤立单元没有可比边，其指数为 null。
* 每条使用中的边给出残差（`fitted_log_difference - observed_log_fisher`）、
  权重与目标函数值；非树边给出环路不一致（观测对数差减生成树路径对数
  差），同时报告图的秩 `rank = n - 分量数`、边数与独立环路数。

## 4. 滚窗接续与修订

* 固定窗口 `w = rolling.window`，以 `new_unit` 结尾。
* 窗口内先置首单元为 1，重算局部多边指数；局部图若缺边，局部指数本身
  使用上文缺边图估计并在 `window.local_method` 明示。
* 接续常数 `c = mean(log(旧发布值) - log(新局部值))`，只在窗口内的已发布
  重叠单元上取等权平均。
* `freeze-history` 只发布 `new_unit`（`status=new-publication`）；已发布
  月份保持原值（`status=published-frozen`），`changed_history=false`。
* `revise` 另出完整版本（`revision.version=revision-complete`）：逐窗推进，
  每个窗口用已有覆盖单元上的接续常数，并列出相对旧发布值发生变化的
  `affected_months`。修订版本以首个窗口首单元为锚点；换基期不等于真实
  价格变化，冻结历史不被覆盖。
* 下列情形直接阻断接续并报错，不用最近报价充数：
  窗口单元没有有效报价；窗口末端单元没有可比共同边；窗口共同边图不
  连通；没有任何可接续的已发布重叠单元。

## 5. 不变性与输出

* 所有单元价格同乘一个常数：所有双边 Fisher、GEKS、图估计与接续序列不变。
* 数量按地区乘常数：覆盖率比例、权重与指数不变。
* 审计输出固定包含：交易聚合账（`accepted_cells`）与拒绝账
  （`rejected`）、每条边的共同篮子与覆盖率、边矩阵、图秩/边数/环路、
  残差、接续常数与逐项 `terms`、冻结映射与清单 SHA-256、冻结/修订序列。
* 业务读取指数时必须同时读取 `method` / `complete_graph` /
  `comparison_scope`（以及 `comparable_with_base`），不得只取一串没有
  可比范围说明的百分数。
