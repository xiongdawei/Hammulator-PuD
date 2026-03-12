import random
from collections import defaultdict, Counter
from itertools import combinations
import pandas as pd
from typing import Dict, List, Tuple, Optional

# This file takes in trace file and generate a new trace file that later feeds into hammulator 


class TriplePriorityLayoutOptimizer:
    """
    'Triple-priority + anti-sandwich + heat-layering + online remap' layout optimizer.
    """

    def __init__(
        self,
        rows: List[str],
        heat: Dict[str, int],
        triples: List[Tuple[str, str, str]],
        lambda_weight: int = 10,
        K: int = 20,  # percent of rows considered "hot"
        seed: Optional[int] = 42
    ):
        self.rows = rows[:]                         # logical row names
        self.heat = dict(heat)                      # heat per row
        self.triples = [tuple(t) for t in triples]  # list of (a,b,c)
        self.lambda_weight = int(lambda_weight)
        self.K = int(K)
        self.initial_order = rows[:]                # original physical order
        self.weight = None                          # pairwise adjacency weights
        self.final_order = None                     # optimized physical order
        self.mapping = None                         # logical -> physical index
        self._rng = random.Random(seed)

    # -----------------------------
    # Public API
    # -----------------------------
    def optimize(self):
        """Run the optimizer end-to-end."""
        rows = self.initial_order[:]
        self.weight = self._build_weights(rows, self.heat, self.triples, self.lambda_weight)
        hot_rows = self._pick_hot_rows(rows, self.heat, self.K)

        # 1) Greedy placement for hot rows
        hot_sequence = self._greedy_insert(self.weight, hot_rows)

        # 2) Fill remaining rows (preserve original order to minimize movement cost)
        cold_rows = [r for r in rows if r not in hot_sequence]
        final_order = hot_sequence + [r for r in self.initial_order if r in cold_rows]

        # 3) Anti-sandwich local fix
        final_order = self._fix_sandwich_patterns(final_order, self.triples)

        # Save results
        self.final_order = final_order
        self.mapping = {logical: physical for physical, logical in enumerate(self.final_order)}
        return self.final_order, self.mapping

    def diagnostics(self):
        """Compute before/after spans and violation counts."""
        if self.final_order is None:
            raise RuntimeError("Call optimize() before diagnostics().")

        before_order = self.initial_order
        after_order = self.final_order

        before_spans = self._triple_spans(before_order, self.triples)
        after_spans  = self._triple_spans(after_order,  self.triples)
        before_viol = self._count_sandwich_patterns(before_order, self.triples)
        after_viol  = self._count_sandwich_patterns(after_order,  self.triples)

        return {
            "before_order": before_order,
            "after_order": after_order,
            "before_spans": before_spans,
            "after_spans": after_spans,
            "before_violations": before_viol,
            "after_violations": after_viol
        }

    def to_dataframes(self):
        """Return (summary_df, triple_df) like your original script."""
        if self.final_order is None:
            raise RuntimeError("Call optimize() before to_dataframes().")

        before_order = self.initial_order
        after_order = self.final_order

        # Summary per row
        summary_rows = []
        for r in self.rows:
            summary_rows.append({
                "Row": r,
                "Heat": self.heat[r],
                "OriginalIndex": before_order.index(r),
                "NewIndex": after_order.index(r),
                "Moved": before_order.index(r) != after_order.index(r)
            })
        summary_df = (
            pd.DataFrame(summary_rows)
              .sort_values("NewIndex")
              .reset_index(drop=True)
        )

        # Triple spans before/after
        before_spans = self._triple_spans(before_order, self.triples)
        after_spans  = self._triple_spans(after_order,  self.triples)

        triple_df_rows = []
        for t in self.triples:
            triple_df_rows.append({
                "Triple": "-".join(t),
                "BeforeSpan": before_spans[t],
                "AfterSpan": after_spans[t],
                "BeforeAdjacent(≤2)": before_spans[t] <= 2,
                "AfterAdjacent(≤2)": after_spans[t] <= 2
            })
        triple_df = pd.DataFrame(triple_df_rows)

        return summary_df, triple_df

    def print_summary(self):
        """Compact text output similar to your original prints."""
        d = self.diagnostics()
        print("Initial order:", d["before_order"])
        print("Optimized order:", d["after_order"])
        print("Sandwich-violating triples BEFORE:", d["before_violations"])
        print("Sandwich-violating triples AFTER :", d["after_violations"])

    # -----------------------------
    # Internals
    # -----------------------------
    @staticmethod
    def _build_weights(rows, heat, triples, lambda_weight):
        weight = defaultdict(lambda: defaultdict(int))
        for (a,b,c) in triples:
            for x,y in [(a,b),(b,c),(a,c)]:
                weight[x][y] += lambda_weight
                weight[y][x] += lambda_weight
        for r in rows:
            for s in rows:
                if r != s:
                    w = heat[r] + heat[s]
                    weight[r][s] += w
        return weight

    @staticmethod
    def _pick_hot_rows(rows, heat, K_percent):
        n = len(rows)
        k_count = max(1, int(n * K_percent / 100.0))
        return sorted(rows, key=lambda r: heat[r], reverse=True)[:k_count]

    @staticmethod
    def _greedy_insert(weight, candidates):
        placed: List[str] = []
        if not candidates:
            return placed
        start = max(candidates, key=lambda r: sum(weight[r].values()))
        placed.append(start)
        remaining = [r for r in candidates if r != start]
        while remaining:
            best = None
            best_gain = -1
            best_pos = None
            for r in remaining:
                for pos in range(len(placed)+1):
                    gain = 0
                    if pos > 0:
                        gain += weight[r].get(placed[pos-1], 0)
                    if pos < len(placed):
                        gain += weight[r].get(placed[pos], 0)
                    if gain > best_gain:
                        best_gain = gain
                        best = r
                        best_pos = pos
            placed.insert(best_pos, best)
            remaining.remove(best)
        return placed

    @staticmethod
    def _fix_sandwich_patterns(order, triples):
        order = order[:]
        changed = True
        for _ in range(3):
            if not changed: break
            changed = False
            for triple in triples:
                if any(r not in order for r in triple):
                    continue
                idxs = sorted(order.index(r) for r in triple)
                if max(idxs) - min(idxs) > 2:
                    triple_rows = [order[i] for i in idxs]
                    min_idx = min(idxs)
                    for r in triple_rows:
                        order.remove(r)
                    for i, r in enumerate(triple_rows):
                        order.insert(min_idx + i, r)
                    changed = True
        return order

    @staticmethod
    def _triple_spans(order, triples):
        spans = {}
        for t in triples:
            idxs = sorted(order.index(r) for r in t)
            spans[t] = max(idxs) - min(idxs)
        return spans

    @staticmethod
    def _count_sandwich_patterns(order, triples):
        spans = TriplePriorityLayoutOptimizer._triple_spans(order, triples)
        return sum(1 for _, span in spans.items() if span > 2)

    # -----------------------------
    # Build from examples / files
    # -----------------------------
    @classmethod
    def read_trace_file(
        cls,
        file_path: str,
        order: Optional[List[str]] = None,
        infer_triples: bool = True,
        ignore_numeric_rows: bool = False
    ):
        """
        Enhanced read_trace_file to handle both structured micro-programs 
        and raw space-separated trace logs (like ap_trace_00).
        """
        with open(file_path, 'r') as f:
            raw_lines = [ln.strip() for ln in f if ln.strip()]

        heat_counter = Counter()
        triples_set = set()

        for ln in raw_lines:
            # 过滤掉可能的 UI 或系统标签干扰
            if ln.startswith(":
                continue

            # 尝试使用旧的微程序解析器（针对带有 '=' 等标准指令格式的行）
            is_micro_program = False
            if HAS_PARSER and ('=' in ln or 'Type' in ln):
                try:
                    inst = parse_instruction(ln)
                    if inst and inst.get('type') in ['Row Copy', 'Arithmetic']:
                        touched_rows = set()
                        for field in ['dst', 'src']:
                            micro = inst.get(field)
                            content = microRegister2Contents(micro)
                            rows_here = extract_rows(content)
                            for base in rows_here:
                                heat_counter[base] += 1
                            touched_rows.update(rows_here)
                        if infer_triples and len(touched_rows) == 3:
                            triples_set.add(tuple(sorted(touched_rows)))
                        is_micro_program = True
                except Exception:
                    pass
            
            # 如果不是微程序，或者微程序解析失败，使用兼容新 Trace 的空格分隔解析法
            if not is_micro_program:
                parts = ln.split()
                cleaned_parts = []
                
                for p in parts:
                    # 去除前缀 B_
                    base = p[2:] if p.startswith("B_") else p
                    # 如果配置了忽略纯数字行（如 '40', '57'），则跳过
                    if ignore_numeric_rows and base.isdigit():
                        continue
                    cleaned_parts.append(base)
                
                # 更新热度并处理下划线连接的复合名词 (如 T0_T1_T2)
                for p in cleaned_parts:
                    heat_counter[p] += 1
                    if "_" in p:
                        sub_parts = p.split("_")
                        if infer_triples and len(sub_parts) == 3:
                            triples_set.add(tuple(sorted(sub_parts)))

                # 识别当前行是否为三元组 (例如 "T0 T1 T2" 刚好三个元素)
                if infer_triples and len(cleaned_parts) == 3:
                    triples_set.add(tuple(sorted(cleaned_parts)))

        # 整理最终的 Row 列表
        if order:
            rows = order[:]
            # 将 trace 中出现但 order 里没有的行补上
            for r in heat_counter:
                if r not in rows:
                    rows.append(r)
        else:
            rows = [r for r, _ in sorted(heat_counter.items(), key=lambda kv: (-kv[1], kv[0]))]

        heat = {r: heat_counter.get(r, 0) for r in rows}
        triples = [tuple(t) for t in triples_set if all(x in heat for x in t)]

        return rows, heat, triples

    @classmethod
    def from_trace_file(
        cls,
        file_path: str,
        order: Optional[List[str]] = None,
        lambda_weight: int = 12,
        K: int = 40,
        seed: int = 42,
        infer_triples: bool = True,
        ignore_numeric_rows: bool = False
    ):
        rows, heat, triples = cls.read_trace_file(
            file_path=file_path,
            order=order,
            infer_triples=infer_triples,
            ignore_numeric_rows=ignore_numeric_rows
        )
        return cls(rows, heat, triples, lambda_weight=lambda_weight, K=K, seed=seed)

if __name__ == "__main__":
    # 新版 Trace 测试用法
    opt = TriplePriorityLayoutOptimizer.from_trace_file(
        file_path="ap_trace_00_addition-plus-ap_row_summary.txt", # 替换为你的路径
        order=None, 
        lambda_weight=12, 
        K=40, 
        infer_triples=True,
        ignore_numeric_rows=False # 如果不想让 40, 41 参与排序，改为 True
    )
    opt.optimize()
    opt.print_summary()
    summary_df, triple_df = opt.to_dataframes()
    # print(summary_df.head())