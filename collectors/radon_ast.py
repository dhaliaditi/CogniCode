"""
collectors/radon_ast.py
Collects: ClassName, CyclomaticComplexity, ComplexityRank,
          MaintainabilityIndex, LOC, SLOC, LLOC, Comments, Multi, Blanks,
          MethodsCount, PublicMethodsCount, InstanceAttributesCount,
          CommentRatio, HalsteadVolume, HalsteadEffort, HalsteadDifficulty,
          HalsteadVocabulary
"""

import ast
from pathlib import Path

try:
    import radon.complexity as radon_cc
    import radon.metrics   as radon_mi
    import radon.raw       as radon_raw
    HAS_RADON = True
except ImportError:
    HAS_RADON = False


class RadonASTCollector:

    def collect(self, py_file: Path) -> dict:
        try:
            source = py_file.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            print(f"  [radon/ast] Cannot read {py_file.name}: {exc}")
            return {}

        result = {}
        result.update(self._ast_metrics(source))

        if not HAS_RADON:
            return result

        result.update(self._raw_metrics(source, py_file.name))
        result.update(self._cc_metrics(source, py_file.name))
        result.update(self._mi_metrics(source, py_file.name))
        result.update(self._halstead_metrics(source, py_file.name))
        return result

    # ------------------------------------------------------------------
    def _ast_metrics(self, source: str) -> dict:
        result: dict = {
            "ClassName": "",
            "MethodsCount": 0,
            "PublicMethodsCount": 0,
            "InstanceAttributesCount": 0,
        }
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return result

        classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
        result["ClassName"] = "|".join(c.name for c in classes)

        for cls in classes:
            for node in ast.walk(cls):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    result["MethodsCount"] += 1
                    if not node.name.startswith("_"):
                        result["PublicMethodsCount"] += 1
                if isinstance(node, ast.Assign):
                    for t in node.targets:
                        if (isinstance(t, ast.Attribute) and
                                isinstance(t.value, ast.Name) and
                                t.value.id == "self"):
                            result["InstanceAttributesCount"] += 1
        return result

    def _raw_metrics(self, source: str, name: str) -> dict:
        try:
            r = radon_raw.analyze(source)
            return {
                "LOC":          r.loc,
                "SLOC":         r.sloc,
                "LLOC":         r.lloc,
                "Comments":     r.comments,
                "Multi":        r.multi,
                "Blanks":       r.blank,
                "CommentRatio": round(r.comments / r.loc, 4) if r.loc else 0.0,
            }
        except Exception as exc:
            print(f"  [radon-raw] {name}: {exc}")
            return {}

    def _cc_metrics(self, source: str, name: str) -> dict:
        try:
            blocks = radon_cc.cc_visit(source)
            avg_cc = (sum(b.complexity for b in blocks) / len(blocks)
                      if blocks else 1)
            return {
                "CyclomaticComplexity": round(avg_cc, 3),
                "ComplexityRank":       radon_cc.cc_rank(avg_cc),
            }
        except Exception as exc:
            print(f"  [radon-cc] {name}: {exc}")
            return {}

    def _mi_metrics(self, source: str, name: str) -> dict:
        try:
            return {"MaintainabilityIndex": round(radon_mi.mi_visit(source, True), 3)}
        except Exception as exc:
            print(f"  [radon-mi] {name}: {exc}")
            return {}

    def _halstead_metrics(self, source: str, name: str) -> dict:
        try:
            reports = radon_mi.h_visit(source)
            if reports:
                hr = reports[0]
                return {
                    "HalsteadVolume":     round(hr.volume, 3),
                    "HalsteadEffort":     round(hr.effort, 3),
                    "HalsteadDifficulty": round(hr.difficulty, 3),
                    "HalsteadVocabulary": hr.vocabulary,
                }
        except Exception as exc:
            print(f"  [radon-hal] {name}: {exc}")
        return {}
