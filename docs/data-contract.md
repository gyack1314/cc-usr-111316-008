# 可比投入价格

unit为地区时期的稳定名，本包示例同一地区三个月，接口可接受多地区。price、quantity为正有理数，单位值是sum(price*quantity)/sum(quantity)。product_map把原产品映至统一产品，缺项保持原ID，映射不可多目标或形成循环。

F_ab=sqrt(L_ab*P_ab)，L_ab=sum(p_b*q_a)/sum(p_a*q_a)，P_ab=sum(p_b*q_b)/sum(p_a*q_b)，仅共同产品。覆盖率分别为共同产品支出/该单元全部有效支出，图边权为sqrt(coverage_a*coverage_b)。完整GEKS相对基准0：P_j=(prod_k(F_kj/F_k0))^(1/n)。图估计最小化sum_edge w_ab*(z_b-z_a-log(F_ab))^2。

滚窗先将窗口内首单元置1，再在全部已发布重叠单元上平均(log旧值-log新局部值)作为接续常数。仅new_unit入冻结历史，修订另开版本。没有任何重叠可比连接则失败。读取器只核对交易身份和数量，不做聚合或指数。
