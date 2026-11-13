# 适应工程投入价格的多边指数与滚动接续

本项目保存气候行动研究的合成资料与领域口径，并提供一个零依赖的
Python 分析工具：把交易聚合为地区时期单元的可比投入价格，计算
GEKS-Fisher（完整可比图）或明确命名的缺边图最小二乘估计，并按固定
窗口做滚窗接续与冻结/修订版本。地区、家庭、合同、价格及排放数据均为合成，
不代表真实政策评价或投资建议。

Python 3.11 以上，无第三方依赖。项目根目录可运行：

```sh
python -m unittest discover -s tests -v
python -m compileall -q analysis_data tests

# 查看清单身份与冻结版本（映射、门槛、窗口、基准）
python -m analysis_data.cli inspect data/normal.json
# 完整分析：交易聚合/拒绝账、共同篮子、覆盖率、边矩阵、指数与滚窗接续
python -m analysis_data.cli analyze data/normal.json
# 分步查看
python -m analysis_data.cli transactions data/normal.json
python -m analysis_data.cli index data/normal.json
python -m analysis_data.cli rolling data/normal.json
```

`analyze` 的 JSON 顶层固定为：

* `transactions`：`accepted_cells`（每个 unit×product 的 trade_ids、数量、
  支出与单位值）与 `rejected`（退货/零价/零量的原始值与理由）；
* `index`：`method`、`complete_graph`、`comparison_scope`、每个单元的
  `index` 与 `comparable_with_base`、`components`、每条边的共同篮子与
  两侧覆盖率、`edge_matrix`、`residuals`、`rank`/`cycle_count`、
  `loop_inconsistency` 与 `warnings`；
* `rolling`：`window` 内局部指数与方法、`link_constant`（等权平均
  `log(旧发布值) - log(新局部值)` 及逐项 `terms`）、`frozen` 序列，
  修订模式另含 `revision`（完整版本与 `affected_months`）。

方法选择是显式的：请求单元集两两均有可比边才叫完整 GEKS-Fisher；只要
缺一条边，就输出 `weighted-graph-least-squares`，图断开时只在连通分量内
给相对指数，不跨分量填值。业务读取连续序列时必须带上 `method`、
`complete_graph`、`comparison_scope` 与 `comparable_with_base`，而不是
一串没有可比范围说明的百分数。

`invalid-config.json` 含基本配置错误（窗口小于 2），其余清单在身份上完整，
可能包含需领域分析判断的情况：`disconnected-products.json` 的产品更替没有
人工映射，因此缺边且在滚窗处阻断；`split-equivalent-trades.json` 把同一产品
的成交拆成多笔，聚合后与 `normal.json` 一致。有理数以字符串保留，null 不等于
0。详细量纲、时间、聚合、覆盖率、图估计与接续口径见
[资料说明](docs/data-contract.md)。

Dockerfile 固定 Linux 镜像；`docker build -t climate-analysis .` 构建，
`docker run --rm climate-analysis` 显示冻结版本摘要，
`docker run --rm climate-analysis python -m unittest discover -s tests -v`
运行检查。资料均可本地读取，运行不需要线上账户。
