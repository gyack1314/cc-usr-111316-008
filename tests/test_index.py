"""多边投入价格指数的统计口径测试。

覆盖：交易聚合与拒绝账、共同篮子与覆盖率门槛、完整 GEKS 与缺边图的
区分、三时期环路、图连通分量、滚窗接续与修订，以及价格/数量缩放下的
不变性。
"""
from __future__ import annotations

import math
import unittest
from fractions import Fraction

from analysis_data.exact import AnalysisError, ContractError
from analysis_data.index import GEKS_METHOD, GRAPH_METHOD, compare_units, multilateral
from analysis_data.panel import load_panel
from analysis_data.report import analysis_view, frozen_view, transactions_view
from analysis_data.rolling import rolling
from tests.helpers import make_panel


def _price_and_quantity_rows(rows, *, price_factor="1", quantity_by_unit=None):
    """给所有交易乘同一个价格常数，或按地区乘数量常数。"""
    quantity_by_unit = quantity_by_unit or {}
    result = []
    for trade_id, unit, product, price, quantity in rows:
        scaled_price = Fraction(price) * Fraction(price_factor)
        scaled_quantity = Fraction(quantity) * Fraction(quantity_by_unit.get(unit, "1"))
        result.append((trade_id, unit, product, str(scaled_price), str(scaled_quantity)))
    return result


def _three_period_rows():
    """环形三时期例子：u3 用 new-pump 替代 pump，需冻结映射才可比。"""
    return [
        ("t1", "u1", "pump", "10", "1"),
        ("t2", "u1", "barrier", "20", "2"),
        ("t3", "u2", "pump", "20", "1"),
        ("t4", "u2", "barrier", "40", "2"),
        ("t5", "u3", "new-pump", "30", "1"),
        ("t6", "u3", "barrier", "60", "2"),
    ]


def _three_period_panel(**kwargs):
    return make_panel(
        _three_period_rows(),
        ["u1", "u2", "u3"],
        product_map={"new-pump": "pump"},
        threshold="1/2",
        published={"u1": "1", "u2": "2"},
        window=2,
        new_unit="u3",
        **kwargs,
    )


class FragmentationTests(unittest.TestCase):
    def test_split_trades_aggregate_to_same_unit_value(self):
        """拆分成交不改变 unit×product 的单位值与最终指数。"""
        base = make_panel(
            [
                ("a1", "u1", "p", "10", "1"),
                ("a2", "u1", "q", "4", "1"),
                ("b1", "u2", "p", "20", "1"),
                ("b2", "u2", "q", "8", "1"),
            ],
            ["u1", "u2"],
            threshold="1",
        )
        split = make_panel(
            [
                ("a1", "u1", "p", "10", "1/3"),
                ("a1-rest", "u1", "p", "10", "2/3"),
                ("a2", "u1", "q", "4", "1"),
                ("b1", "u2", "p", "20", "1"),
                ("b2", "u2", "q", "8", "1"),
            ],
            ["u1", "u2"],
            threshold="1",
        )
        self.assertEqual(
            base.products("u1")["p"].unit_value,
            split.products("u1")["p"].unit_value,
        )
        self.assertEqual(
            base.products("u1")["p"].expenditure,
            split.products("u1")["p"].expenditure,
        )
        self.assertEqual(
            set(base.products("u1")["p"].trade_ids),
            {"a1"},
        )
        self.assertEqual(
            split.products("u1")["p"].trade_ids, ("a1", "a1-rest")
        )
        self.assertEqual(multilateral(base)["index"], multilateral(split)["index"])

    def test_negative_and_zero_values_are_rejected_not_indexed(self):
        panel = make_panel(
            [
                ("a", "u1", "p", "10", "1"),
                ("return", "u1", "p", "-10", "1"),
                ("free", "u1", "q", "0", "2"),
                ("zero-qty", "u1", "r", "5", "0"),
                ("b", "u2", "p", "20", "1"),
            ],
            ["u1", "u2"],
            threshold="1/4",
        )
        view = transactions_view(panel)
        self.assertEqual(
            [item["id"] for item in view["rejected"]],
            ["return", "free", "zero-qty"],
        )
        reasons = {item["id"]: item["reason"] for item in view["rejected"]}
        self.assertEqual(reasons["return"], "negative-price")
        self.assertEqual(reasons["free"], "zero-price")
        self.assertEqual(reasons["zero-qty"], "zero-quantity")
        # 有效支出只计正交易；退货与零价不进制数，也不缩小覆盖率分母。
        self.assertEqual(panel.accepted_expenditure("u1"), Fraction(10))
        self.assertNotIn("q", panel.products("u1"))
        self.assertNotIn("r", panel.products("u1"))

    def test_returned_quantity_is_rejected(self):
        panel = make_panel(
            [("a", "u1", "p", "10", "-2"), ("b", "u1", "q", "5", "1")],
            ["u1"],
            threshold="1",
        )
        self.assertEqual(panel.rejected[0].reason, "negative-quantity")


class BilateralTests(unittest.TestCase):
    def test_laspeyres_paasche_fisher_exact(self):
        panel = make_panel(
            [
                ("a", "u1", "p", "1", "2"),
                ("b", "u1", "q", "4", "1"),
                ("c", "u2", "p", "2", "1"),
                ("d", "u2", "q", "8", "1"),
            ],
            ["u1", "u2"],
            threshold="1",
        )
        edge = compare_units(panel, "u1", "u2")
        self.assertEqual(edge.laspeyres, Fraction(2))
        self.assertEqual(edge.paasche, Fraction(2))
        self.assertAlmostEqual(edge.fisher, 2.0)
        reverse = compare_units(panel, "u2", "u1")
        self.assertAlmostEqual(reverse.fisher * edge.fisher, 1.0)
        self.assertEqual(reverse.common_products, edge.common_products)
        self.assertEqual(reverse.coverage_a, edge.coverage_b)

    def test_coverage_threshold_blocks_thin_common_basket(self):
        """共同产品支出占比不足时没有这条可比边。"""
        panel = make_panel(
            [
                ("a", "u1", "p", "10", "1"),
                ("b", "u1", "q", "100", "1"),
                ("c", "u2", "p", "20", "1"),
                ("d", "u2", "r", "100", "1"),
            ],
            ["u1", "u2"],
            threshold="1/2",
        )
        edge = compare_units(panel, "u1", "u2")
        self.assertFalse(edge.accepted)
        self.assertEqual(edge.rejection, "coverage-below-threshold")
        self.assertEqual(edge.common_products, ("p",))
        self.assertEqual(edge.coverage_a, Fraction(10, 110))
        self.assertEqual(edge.coverage_b, Fraction(20, 120))
        self.assertEqual(edge.weight, math.sqrt((10 / 110) * (20 / 120)))

    def test_equal_coverage_threshold_is_inclusive(self):
        """覆盖率恰好等于门槛时保留边。"""
        panel = make_panel(
            [
                ("a", "u1", "p", "10", "1"),
                ("b", "u1", "q", "10", "1"),
                ("c", "u2", "p", "10", "1"),
                ("d", "u2", "r", "10", "1"),
            ],
            ["u1", "u2"],
            threshold="1/2",
        )
        edge = compare_units(panel, "u1", "u2")
        self.assertEqual(edge.coverage_a, Fraction(1, 2))
        self.assertEqual(edge.coverage_b, Fraction(1, 2))
        self.assertTrue(edge.accepted)

    def test_missing_common_product_has_no_edge(self):
        panel = make_panel(
            [("a", "u1", "p", "10", "1"), ("b", "u2", "q", "20", "1")],
            ["u1", "u2"],
        )
        edge = compare_units(panel, "u1", "u2")
        self.assertFalse(edge.accepted)
        self.assertEqual(edge.rejection, "no-common-products")
        self.assertEqual(edge.common_products, ())

    def test_bidirectional_matrix_is_reciprocal(self):
        panel = make_panel(
            [
                ("a", "u1", "p", "10", "1"),
                ("b", "u1", "q", "20", "1"),
                ("c", "u2", "p", "20", "2"),
                ("d", "u2", "q", "60", "1"),
            ],
            ["u1", "u2"],
            threshold="1",
        )
        result = multilateral(panel)
        matrix = result["edge_matrix"]["log_fisher"]
        self.assertAlmostEqual(matrix[0][1] + matrix[1][0], 0.0, places=12)


class MultilateralTests(unittest.TestCase):
    def test_complete_geks_three_period_cycle(self):
        """三时期全连通例子：GEKS 与逐对 Fisher 的环路一致。"""
        panel = _three_period_panel()
        result = multilateral(panel)
        self.assertEqual(result["method"], GEKS_METHOD)
        self.assertTrue(result["complete_graph"])
        self.assertEqual(result["rank"], 2)
        self.assertEqual(result["cycle_count"], 1)
        self.assertLess(abs(result["loop_inconsistency"]["max_abs"]), 1e-12)
        self.assertAlmostEqual(result["index"]["u1"], 1.0)
        self.assertAlmostEqual(result["index"]["u2"], 2.0)
        self.assertAlmostEqual(result["index"]["u3"], 3.0)
        for edge in result["edges"]:
            self.assertTrue(edge["accepted"])
            self.assertIn("coverage_a", edge)
        self.assertEqual(result["comparison_scope"], "all-units-relative-to-u1")

    def test_product_replacement_without_map_breaks_comparability(self):
        """产品更替且无人工映射时，缺边图估计必须明确命名，不冒充 GEKS。"""
        rows = [
            ("a", "u1", "pump", "10", "1"),
            ("b", "u1", "barrier", "20", "2"),
            ("c", "u2", "pump", "20", "1"),
            ("d", "u2", "barrier", "40", "2"),
            ("e", "u3", "new-pump", "300", "1"),
            ("f", "u3", "barrier", "60", "2"),
        ]
        panel = make_panel(rows, ["u1", "u2", "u3"], threshold="1/2")
        result = multilateral(panel)
        self.assertEqual(result["method"], GRAPH_METHOD)
        self.assertFalse(result["complete_graph"])
        self.assertNotEqual(result["method"], GEKS_METHOD)
        self.assertIn("非完整 GEKS", result["method_label"])
        self.assertEqual(result["edge_count"], 1)
        # u3 旧产品被高份额替代，只剩 barrier；覆盖率不足 → 无共同边。
        self.assertEqual(
            [record["units"] for record in result["components"]],
            [["u1", "u2"], ["u3"]],
        )
        self.assertIsNone(result["index"]["u3"])
        self.assertEqual(result["comparison_scope"], "within-component-only")
        self.assertTrue(result["warnings"])

    def test_graph_is_connected_but_not_complete(self):
        """四单元五条边：可用图估计，但必须标成非完整 GEKS。"""
        panel = make_panel(
            [
                ("a", "A", "p", "10", "1"),
                ("b", "A", "q", "10", "1"),
                ("c", "A", "r", "10", "1"),
                ("d", "B", "p", "20", "1"),
                ("e", "B", "q", "20", "1"),
                ("f", "C", "q", "30", "1"),
                ("g", "C", "r", "30", "1"),
                ("h", "D", "r", "40", "1"),
            ],
            ["A", "B", "C", "D"],
            threshold="1/4",
        )
        result = multilateral(panel)
        self.assertFalse(result["complete_graph"])
        self.assertEqual(result["method"], GRAPH_METHOD)
        self.assertEqual(result["edge_count"], 5)
        self.assertEqual(result["rank"], 3)
        self.assertEqual(result["cycle_count"], 2)
        self.assertEqual(len(result["components"]), 1)
        self.assertEqual(result["comparison_scope"], "all-units-relative-to-A")
        anchor = result["index"]["A"]
        self.assertAlmostEqual(anchor, 1.0, places=9)

        # 法方程：每个非锚点节点的入射加权残差带符号求和为零。
        objective = 0.0
        for node in ("B", "C", "D"):
            normal = 0.0
            for record in result["residuals"]:
                if record["unit_a"] == node:
                    normal -= record["weight"] * record["residual"]
                elif record["unit_b"] == node:
                    normal += record["weight"] * record["residual"]
            self.assertAlmostEqual(normal, 0.0, places=9, msg=node)
        self.assertGreater(result["objective"], 0.0)
        self.assertLessEqual(result["loop_inconsistency"]["max_abs"], 1.0)

    def test_disconnected_components_do_not_share_levels(self):
        panel = make_panel(
            [
                ("a", "u1", "p", "10", "1"),
                ("b", "u2", "p", "20", "1"),
                ("c", "u3", "q", "30", "1"),
                ("d", "u4", "q", "60", "1"),
            ],
            ["u1", "u2", "u3", "u4"],
        )
        result = multilateral(panel)
        self.assertEqual(result["method"], GRAPH_METHOD)
        self.assertEqual(result["rank"], 2)
        self.assertEqual(
            [record["units"] for record in result["components"]],
            [["u1", "u2"], ["u3", "u4"]],
        )
        # 第二个分量的锚点是 u3=1，u4=2；相对基准不可比。
        self.assertEqual(result["index"]["u3"], 1.0)
        self.assertAlmostEqual(result["index"]["u4"], 2.0)
        self.assertFalse(result["unit_details"]["u3"]["comparable_with_base"])
        self.assertFalse(result["unit_details"]["u4"]["comparable_with_base"])
        self.assertAlmostEqual(result["index"]["u2"], 2.0)
        self.assertEqual(result["comparison_scope"], "within-component-only")

    def test_single_unit_has_no_bilateral(self):
        panel = make_panel([("a", "u1", "p", "10", "1")], ["u1"], threshold="1")
        result = multilateral(panel)
        self.assertEqual(result["index"], {"u1": 1.0})
        self.assertEqual(result["edge_count"], 0)
        self.assertEqual(result["candidate_edge_count"], 0)

    def test_base_outside_request_rejected(self):
        panel = _three_period_panel()
        with self.assertRaises(ContractError):
            multilateral(panel, units=["u1", "u2"], base_unit="u3")
        with self.assertRaises(ContractError):
            multilateral(panel, units=["u1", "u1"])


class MappingTests(unittest.TestCase):
    def test_mapping_must_be_frozen_terminal(self):
        from analysis_data.panel import _read_mapping

        with self.assertRaises(ContractError):
            _read_mapping({"old": "middle", "middle": "new"})

    def test_mapping_cannot_target_two_products(self):
        from analysis_data.panel import _read_mapping

        with self.assertRaises(ContractError):
            _read_mapping(
                [{"source": "old", "target": "a"}, {"source": "old", "target": "b"}]
            )

    def test_mapping_self_reference_rejected(self):
        from analysis_data.panel import _read_mapping

        with self.assertRaises(ContractError):
            _read_mapping({"old": "old"})

    def test_mapping_collapses_products_before_aggregation(self):
        panel = make_panel(
            [
                ("a", "u1", "old", "10", "1"),
                ("b", "u1", "new", "30", "1"),
                ("c", "u2", "old", "20", "1"),
                ("d", "u2", "new", "60", "1"),
            ],
            ["u1", "u2"],
            product_map={"new": "old"},
            threshold="1",
        )
        self.assertEqual(panel.products("u1")["old"].expenditure, Fraction(40))
        self.assertEqual(panel.products("u1")["old"].quantity, Fraction(2))


class RollingTests(unittest.TestCase):
    def _panel(self, mode="freeze-history", window=2):
        rows = [
            ("a", "2026-01", "p", "10", "1"),
            ("b", "2026-01", "q", "20", "1"),
            ("c", "2026-02", "p", "20", "1"),
            ("d", "2026-02", "q", "40", "1"),
            ("e", "2026-03", "p", "30", "1"),
            ("f", "2026-03", "q", "60", "1"),
        ]
        return make_panel(
            rows,
            ["2026-01", "2026-02", "2026-03"],
            threshold="1",
            window=window,
            published={"2026-01": "1", "2026-02": "2"},
            new_unit="2026-03",
            mode=mode,
        )

    def test_freeze_only_publishes_new_unit(self):
        panel = self._panel()
        result = rolling(panel)
        self.assertEqual(result["frozen"]["mode"], "freeze-history")
        self.assertFalse(result["frozen"]["changed_history"])
        self.assertTrue(result["frozen"]["new_unit_only"])
        series = {point["unit"]: point for point in result["frozen"]["series"]}
        self.assertEqual(series["2026-01"]["value"], 1.0)
        self.assertEqual(series["2026-02"]["value"], 2.0)
        self.assertAlmostEqual(series["2026-03"]["value"], 3.0)
        self.assertEqual(series["2026-03"]["status"], "new-publication")
        self.assertEqual(series["2026-01"]["status"], "published-frozen")
        self.assertEqual(result["overlap_units"], ["2026-02"])

    def test_link_constant_is_average_log_difference(self):
        panel = self._panel(window=3)
        result = rolling(panel)
        terms = result["link_constant"]["terms"]
        self.assertEqual([term["unit"] for term in terms], ["2026-01", "2026-02"])
        expected = sum(term["log_difference"] for term in terms) / 2
        self.assertAlmostEqual(result["link_constant"]["value"], expected, places=12)
        self.assertEqual(result["link_constant"]["averaging"], "equal-weight")
        new_value = result["frozen"]["series"][-1]["value"]
        self.assertAlmostEqual(
            new_value,
            math.exp(expected + math.log(result["window"]["local_index"]["2026-03"])),
            places=9,
        )

    def test_freeze_does_not_change_published_values(self):
        """即使新月使重算历史发生变化，冻结模式也只发布新月。"""
        panel = make_panel(
            [
                ("a", "U1", "p", "10", "1"),
                ("b", "U2", "p", "20", "1"),
                ("c", "U3", "p", "41", "1"),
            ],
            ["U1", "U2", "U3"],
            threshold="1",
            window=2,
            published={"U1": "1", "U2": "2"},
            new_unit="U3",
            mode="freeze-history",
        )
        result = rolling(panel)
        series = {point["unit"]: point["value"] for point in result["frozen"]["series"]}
        self.assertEqual(series["U1"], 1.0)
        self.assertEqual(series["U2"], 2.0)
        self.assertIsNone(result["revision"])

    def test_missing_window_edge_blocks_linking(self):
        panel = make_panel(
            [
                ("a", "U1", "p", "10", "1"),
                ("b", "U2", "q", "20", "1"),
            ],
            ["U1", "U2"],
            published={"U1": "1"},
            new_unit="U2",
        )
        with self.assertRaises(AnalysisError) as caught:
            rolling(panel)
        self.assertIn("阻断", str(caught.exception))

    def test_no_overlapping_published_blocks_linking(self):
        panel = self._panel()
        panel.published = {}
        with self.assertRaises(AnalysisError) as caught:
            rolling(panel)
        self.assertIn("没有可接续", str(caught.exception))

    def test_window_missing_quote_blocks_linking(self):
        panel = make_panel(
            [
                ("a", "U1", "p", "10", "1"),
                ("b", "U3", "p", "30", "1"),
            ],
            ["U1", "U2", "U3"],
            published={"U1": "1", "U2": "2"},
            window=2,
            new_unit="U3",
        )
        with self.assertRaises(AnalysisError) as caught:
            rolling(panel)
        self.assertIn("U2", str(caught.exception))

    def test_revision_is_a_separate_complete_version(self):
        panel = self._panel(mode="revise")
        result = rolling(panel)
        self.assertIsNotNone(result["revision"])
        self.assertEqual(result["revision"]["version"], "revision-complete")
        self.assertFalse(result["frozen"]["changed_history"])
        self.assertEqual(
            [point["unit"] for point in result["revision"]["series"]],
            ["2026-01", "2026-02", "2026-03"],
        )
        for item in result["revision"]["affected_months"]:
            self.assertIn("unit", item)
            self.assertIn("log_difference", item)
        # 冻结序列与修订序列并存，旧发布值不被覆盖。
        frozen = {point["unit"]: point["value"] for point in result["frozen"]["series"]}
        revised = {point["unit"]: point["value"] for point in result["revision"]["series"]}
        self.assertEqual(frozen["2026-01"], 1.0)
        self.assertAlmostEqual(revised["2026-01"], 1.0, places=9)

    def test_revision_reports_affected_months_when_link_shifts(self):
        panel = make_panel(
            [
                ("1", "U1", "a", "10", "1"),
                ("2", "U1", "b", "10", "1"),
                ("3", "U2", "b", "20", "1"),
                ("4", "U2", "c", "20", "1"),
                ("5", "U3", "a", "30", "1"),
                ("6", "U3", "c", "40", "1"),
                ("7", "U4", "a", "50", "1"),
                ("8", "U4", "c", "70", "1"),
                ("9", "U4", "b", "10", "1"),
            ],
            ["U1", "U2", "U3", "U4"],
            threshold="1/2",
            window=3,
            published={"U1": "1", "U2": "2", "U3": "3"},
            new_unit="U4",
            mode="revise",
        )
        result = rolling(panel)
        affected = {item["unit"]: item for item in result["revision"]["affected_months"]}
        # 完整版本在 U2-U3-U4 窗口重算，旧发布 U2/U3 与修订值出现差异。
        self.assertIn("U2", affected)
        self.assertIn("U3", affected)
        self.assertNotEqual(affected["U2"]["frozen"], affected["U2"]["revised"])
        # 冻结序列仍保持旧发布值，修订只存在于另开的版本。
        frozen = {point["unit"]: point["value"] for point in result["frozen"]["series"]}
        self.assertEqual(frozen["U2"], 2.0)
        self.assertEqual(frozen["U3"], 3.0)


class InvarianceTests(unittest.TestCase):
    def test_global_price_scaling_leaves_index_unchanged(self):
        base = multilateral(_three_period_panel())
        scaled = multilateral(
            make_panel(
                _price_and_quantity_rows(_three_period_rows(), price_factor="7"),
                ["u1", "u2", "u3"],
                product_map={"new-pump": "pump"},
                published={"u1": "1", "u2": "2"},
                window=2,
                new_unit="u3",
            )
        )
        for unit in base["index"]:
            self.assertAlmostEqual(scaled["index"][unit], base["index"][unit], places=9)

    def test_quantity_scaling_by_region_leaves_index_unchanged(self):
        base = multilateral(_three_period_panel())
        scaled = multilateral(
            make_panel(
                _price_and_quantity_rows(
                    _three_period_rows(), quantity_by_unit={"u1": "3", "u2": "1", "u3": "7"}
                ),
                ["u1", "u2", "u3"],
                product_map={"new-pump": "pump"},
                published={"u1": "1", "u2": "2"},
                window=2,
                new_unit="u3",
            )
        )
        for unit in base["index"]:
            self.assertAlmostEqual(scaled["index"][unit], base["index"][unit], places=9)

    def test_quantity_scaling_preserves_coverage_ratios(self):
        base = multilateral(_three_period_panel())
        scaled = multilateral(
            make_panel(
                _price_and_quantity_rows(
                    _three_period_rows(), quantity_by_unit={"u3": "5"}
                ),
                ["u1", "u2", "u3"],
                product_map={"new-pump": "pump"},
                published={"u1": "1", "u2": "2"},
                window=2,
                new_unit="u3",
            )
        )
        self.assertEqual(
            [edge["coverage_min"] for edge in base["edges"]],
            [edge["coverage_min"] for edge in scaled["edges"]],
        )

    def test_freeze_invariance_under_price_and_quantity_scaling(self):
        base_rows = _three_period_rows()
        base = rolling(
            make_panel(
                base_rows,
                ["u1", "u2", "u3"],
                product_map={"new-pump": "pump"},
                published={"u1": "1", "u2": "2"},
                window=2,
                new_unit="u3",
            )
        )
        scaled = rolling(
            make_panel(
                _price_and_quantity_rows(
                    base_rows, price_factor="3", quantity_by_unit={"u1": "2"}
                ),
                ["u1", "u2", "u3"],
                product_map={"new-pump": "pump"},
                published={"u1": "1", "u2": "2"},
                window=2,
                new_unit="u3",
            )
        )
        self.assertAlmostEqual(
            base["frozen"]["series"][-1]["value"],
            scaled["frozen"]["series"][-1]["value"],
            places=9,
        )


class MaterialIntegrationTests(unittest.TestCase):
    def test_normal_manifest_full_pipeline(self):
        panel = load_panel("data/normal.json")
        view = analysis_view(panel)
        self.assertEqual(view["index"]["method"], GEKS_METHOD)
        self.assertAlmostEqual(view["index"]["index"]["2026-03"], 3.0, places=9)
        self.assertAlmostEqual(
            view["rolling"]["frozen"]["series"][-1]["value"], 3.0, places=9
        )
        self.assertTrue(view["index"]["complete_graph"])

    def test_split_manifest_matches_unsplit(self):
        split = analysis_view(load_panel("data/split-equivalent-trades.json"))
        normal = analysis_view(load_panel("data/normal.json"))
        self.assertEqual(split["transactions"]["accepted_cell_count"], 6)
        self.assertEqual(split["index"]["index"], normal["index"]["index"])

    def test_disconnected_manifest_blocks_rolling(self):
        panel = load_panel("data/disconnected-products.json")
        self.assertEqual(multilateral(panel)["method"], GRAPH_METHOD)
        with self.assertRaises(AnalysisError):
            rolling(panel)

    def test_frozen_view_reports_mapping_and_threshold(self):
        panel = load_panel("data/normal.json")
        view = frozen_view(panel)
        self.assertEqual(view["coverage_threshold"], "1/2")
        self.assertEqual(view["product_map"], {})
        self.assertEqual(view["manifest_sha256"], panel.manifest_sha256)

    def test_manifest_identity_mismatch_fails(self):
        with self.assertRaises(ValueError):
            load_panel("data/invalid-config.json")


if __name__ == "__main__":
    unittest.main()
