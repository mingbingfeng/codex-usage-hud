"""Unit tests for the pre-send base estimator and reading-activity monitor."""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from codex_usage_hud.core.activity_monitor import (
    ReadingActivity,
    _extract_file_from_arguments,
    detect_reading_activity,
)
from codex_usage_hud.core.pre_send_estimator import (
    AttachmentEstimate,
    BaseEstimate,
    PreSendEstimator,
    _estimate_image_tokens,
    estimate_attachments,
)


class BaseEstimateTests(unittest.TestCase):
    def test_short_label_cache_friendly(self) -> None:
        self.assertIn("Cache友好", BaseEstimate(total_tokens=12_500).short_label())

    def test_short_label_large_context(self) -> None:
        self.assertIn("大量上下文", BaseEstimate(total_tokens=150_000).short_label())

    def test_short_label_reports_error(self) -> None:
        self.assertIn("估价异常", BaseEstimate(error="boom").short_label())

    def test_with_session_history_replaces_history_term(self) -> None:
        base = BaseEstimate(
            total_tokens=981,
            input_text_tokens=6,
            session_history_tokens=0,
            context_files_tokens=920,
            mcp_schema_tokens=5,
            padding_tokens=50,
            encoding_used="tiktoken",
        )
        merged = base.with_session_history(48_000)
        self.assertEqual(merged.session_history_tokens, 48_000)
        # 6 + 920 + 5 + 50 + 48000
        self.assertEqual(merged.total_tokens, 48_981)
        self.assertEqual(merged.encoding_used, "tiktoken")

    def test_with_session_history_clamps_negative(self) -> None:
        merged = BaseEstimate(padding_tokens=50).with_session_history(-5)
        self.assertEqual(merged.session_history_tokens, 0)
        self.assertEqual(merged.total_tokens, 50)

    def test_breakdown_rows_use_readable_labels(self) -> None:
        est = BaseEstimate(
            input_text_tokens=6,
            session_history_tokens=48000,
            context_files_tokens=920,
            mcp_schema_tokens=5,
            padding_tokens=50,
        )
        rows = est.breakdown_rows()
        labels = [r["label"] for r in rows]
        # 不再暴露 A/B/C/D/F 代号，改用中文名称。
        self.assertEqual(labels[0], "输入框内容")
        self.assertIn("会话上下文", labels[1])
        self.assertIn("项目规则", labels)
        self.assertIn("工具定义", labels)
        self.assertIn("协议开销", labels)
        self.assertNotIn("A", labels)

    def test_breakdown_rows_override_live_input(self) -> None:
        est = BaseEstimate(input_text_tokens=6, padding_tokens=50)
        rows = est.breakdown_rows(live_input_tokens=123)
        self.assertEqual(rows[0]["label"], "输入框内容")
        self.assertEqual(rows[0]["tokens"], 123)

    def test_breakdown_splits_cache_hit_when_priced(self) -> None:
        est = BaseEstimate(session_history_tokens=10000).with_pricing(
            input_price_per_token=5e-6,
            cached_price_per_token=0.5e-6,
            cache_hit_rate=0.8,
            model_name="gpt-5.5",
        )
        rows = est.breakdown_rows()
        labels = [r["label"] for r in rows]
        self.assertIn("会话上下文·命中缓存", labels)
        self.assertIn("会话上下文·未命中", labels)
        hit = next(r for r in rows if r["label"] == "会话上下文·命中缓存")
        miss = next(r for r in rows if r["label"] == "会话上下文·未命中")
        self.assertEqual(hit["tokens"], 8000)
        self.assertEqual(miss["tokens"], 2000)
        self.assertEqual(hit["kind"], "cached")
        self.assertEqual(miss["kind"], "input")
        # 命中部分用更便宜的 cached 单价。
        self.assertLess(hit["cost"] / hit["tokens"], miss["cost"] / miss["tokens"])

    def test_confirmed_context_uses_measured_input_not_cumulative(self) -> None:
        # 回归测试：会话上下文必须取自上一次真实请求的实测输入，
        # 而非全会话累加（后者会随轮次暴涨到数百万，导致金额虚高）。
        est = BaseEstimate(
            context_files_tokens=396,
            mcp_schema_tokens=1024,
            padding_tokens=50,
        ).with_confirmed_context(cached_tokens=224000, uncached_tokens=56000)
        # C/D/F 已含在实测输入里，必须归零避免重复计费。
        self.assertEqual(est.context_files_tokens, 0)
        self.assertEqual(est.mcp_schema_tokens, 0)
        self.assertEqual(est.padding_tokens, 0)
        self.assertTrue(est.confirmed_context)
        self.assertEqual(est.session_history_tokens, 280000)

        priced = est.with_pricing(
            input_price_per_token=5e-6,
            cached_price_per_token=0.5e-6,
            cache_hit_rate=0.0,  # confirmed 路径忽略此值，用实测拆分
            model_name="gpt-5.5",
        )
        rows = priced.breakdown_rows(live_input_tokens=86)
        labels = [r["label"] for r in rows]
        # confirmed 路径不再出现项目规则/工具定义/协议开销行（已并入实测输入）。
        self.assertNotIn("项目规则", labels)
        self.assertNotIn("工具定义", labels)
        self.assertNotIn("协议开销", labels)
        hit = next(r for r in rows if r["label"] == "会话上下文·命中缓存")
        miss = next(r for r in rows if r["label"] == "会话上下文·未命中")
        self.assertEqual(hit["tokens"], 224000)
        self.assertEqual(miss["tokens"], 56000)
        # 合计应远小于把 14M 全按 input 价算出的天价。
        self.assertLess(priced.total_cost(live_input_tokens=86), 0.5)

    def test_breakdown_no_cache_row_without_prices(self) -> None:
        est = BaseEstimate(session_history_tokens=10000)
        rows = est.breakdown_rows()
        labels = [r["label"] for r in rows]
        self.assertIn("会话上下文", labels)
        self.assertNotIn("会话上下文·命中缓存", labels)
        self.assertIsNone(rows[0]["cost"])

    def test_total_cost_sums_priced_rows(self) -> None:
        est = BaseEstimate(
            session_history_tokens=10000,
            context_files_tokens=1000,
            padding_tokens=50,
        ).with_pricing(
            input_price_per_token=5e-6,
            cached_price_per_token=0.5e-6,
            cache_hit_rate=0.8,
            model_name="gpt-5.5",
        )
        rows = est.breakdown_rows(live_input_tokens=100)
        expected = sum(r["cost"] for r in rows)
        self.assertAlmostEqual(est.total_cost(live_input_tokens=100), expected)

    def test_total_cost_none_without_prices(self) -> None:
        self.assertIsNone(BaseEstimate(session_history_tokens=100).total_cost())


class PreSendEstimatorTests(unittest.TestCase):
    def test_latest_is_non_blocking_before_start(self) -> None:
        estimator = PreSendEstimator()
        self.assertEqual(estimator.latest().total_tokens, 0)

    def test_recompute_sums_all_components(self) -> None:
        estimator = PreSendEstimator(
            input_text_getter=lambda: "hello world",
            session_history_getter=lambda: "history text",
            mcp_schema_getter=lambda: "{}",
        )
        estimator._recompute()
        est = estimator.latest()
        self.assertGreater(est.total_tokens, 0)
        self.assertEqual(est.padding_tokens, 50)
        self.assertIn(est.encoding_used, {"tiktoken", "heuristic"})

    def test_background_thread_produces_estimate(self) -> None:
        estimator = PreSendEstimator(
            input_text_getter=lambda: "refactor ScanClient please",
            debounce_seconds=0.05,
        )
        estimator.start()
        try:
            estimator.invalidate()
            deadline = time.time() + 3.0
            while time.time() < deadline and estimator.latest().total_tokens == 0:
                time.sleep(0.05)
        finally:
            estimator.close()
        self.assertGreater(estimator.latest().total_tokens, 0)

    def test_set_project_roots_only_changes_on_difference(self) -> None:
        estimator = PreSendEstimator(project_roots=["a"])
        estimator._context_cache = "cached"
        estimator.set_project_roots(["a"])  # no change
        self.assertEqual(estimator._context_cache, "cached")
        estimator.set_project_roots(["b"])  # changed -> cache cleared
        self.assertIsNone(estimator._context_cache)

    def test_recompute_captures_getter_error(self) -> None:
        def boom() -> str:
            raise RuntimeError("getter failed")

        estimator = PreSendEstimator(input_text_getter=boom)
        estimator._recompute()
        self.assertIn("getter failed", estimator.latest().error)


class ExtractFileFromArgumentsTests(unittest.TestCase):
    def test_json_path_key(self) -> None:
        path, _ = _extract_file_from_arguments("read_file", '{"path": "src/ScanClient.cs"}')
        self.assertEqual(path, "src/ScanClient.cs")

    def test_shell_cat_command(self) -> None:
        path, _ = _extract_file_from_arguments("shell", '{"command": "cat src/ScanClient.cs"}')
        self.assertEqual(path, "src/ScanClient.cs")

    def test_command_as_list(self) -> None:
        path, _ = _extract_file_from_arguments(
            "shell", {"command": ["cat", "src/ScanClient.cs"]}
        )
        self.assertEqual(path, "src/ScanClient.cs")

    def test_empty_arguments(self) -> None:
        self.assertEqual(_extract_file_from_arguments("read_file", None), ("", ""))


class DetectReadingActivityTests(unittest.TestCase):
    def _snapshot(self, **kwargs: object) -> SimpleNamespace:
        base = dict(
            task_completed_at=None,
            task_aborted_at=None,
            activity=None,
        )
        base.update(kwargs)
        return SimpleNamespace(**base)

    def test_detects_read_file_tool(self) -> None:
        snap = self._snapshot(
            activity=SimpleNamespace(
                kind="tool call",
                detail='read_file {"path": "src/ScanClient.cs"}',
            )
        )
        activity = detect_reading_activity(snap)
        self.assertTrue(activity.active)
        self.assertEqual(activity.file_name, "ScanClient.cs")
        self.assertIn("ScanClient.cs", activity.warning_label())
        self.assertTrue(activity.warning_label().startswith("⚡"))

    def test_non_read_tool_is_inactive(self) -> None:
        snap = self._snapshot(
            activity=SimpleNamespace(
                kind="tool call",
                detail='apply_patch {"path": "x"}',
            )
        )
        self.assertFalse(detect_reading_activity(snap).active)

    def test_completed_task_turns_light_off(self) -> None:
        snap = self._snapshot(
            task_completed_at=object(),
            activity=SimpleNamespace(
                kind="tool call",
                detail='read_file {"path": "src/ScanClient.cs"}',
            ),
        )
        self.assertFalse(detect_reading_activity(snap).active)

    def test_aborted_task_turns_light_off(self) -> None:
        snap = self._snapshot(
            task_aborted_at=object(),
            activity=SimpleNamespace(
                kind="tool call",
                detail='read_file {"path": "src/ScanClient.cs"}',
            ),
        )
        self.assertFalse(detect_reading_activity(snap).active)

    def test_mcp_namespaced_read_tool(self) -> None:
        snap = self._snapshot(
            activity=SimpleNamespace(
                kind="tool call",
                detail='filesystem.read_file {"path": "a/b/Config.cs"}',
            )
        )
        activity = detect_reading_activity(snap)
        self.assertTrue(activity.active)
        self.assertEqual(activity.file_name, "Config.cs")

    def test_idle_activity_is_inactive(self) -> None:
        snap = self._snapshot(activity=SimpleNamespace(kind="idle", detail=""))
        self.assertFalse(detect_reading_activity(snap).active)

    def test_warning_label_empty_when_inactive(self) -> None:
        self.assertEqual(ReadingActivity(active=False).warning_label(), "")


class AttachmentEstimateTests(unittest.TestCase):
    @staticmethod
    def _enc(text: str) -> int:
        return len(text or "") // 4

    def test_image_token_formula_scales_with_size(self) -> None:
        small = _estimate_image_tokens(200, 200)
        large = _estimate_image_tokens(2000, 2000)
        self.assertGreater(large, small)
        # 单块小图取 base 85。
        self.assertEqual(_estimate_image_tokens(400, 400), 85)

    def test_image_token_zero_for_invalid(self) -> None:
        self.assertEqual(_estimate_image_tokens(0, 0), 0)

    def test_estimate_counts_images(self) -> None:
        att = estimate_attachments(
            images=[{"width": 136, "height": 82}, {"width": 783, "height": 288}],
            files=None,
            mentions=None,
            project_roots=[],
            encode_fn=self._enc,
        )
        self.assertEqual(att.image_count, 2)
        self.assertGreater(att.image_tokens, 0)
        # 含图必定标近似。
        self.assertTrue(att.approximate)

    def test_estimate_reads_file_from_disk(self) -> None:
        # 用本仓库真实文件验证读盘 tiktoken 路径。
        att = estimate_attachments(
            images=None,
            files=["pyproject.toml"],
            mentions=None,
            project_roots=[str(PROJECT_ROOT)],
            encode_fn=self._enc,
        )
        self.assertEqual(att.file_count, 1)
        self.assertGreater(att.file_tokens, 0)

    def test_unresolvable_mention_marked_approximate(self) -> None:
        att = estimate_attachments(
            images=None,
            files=None,
            mentions=["@某个不存在的技能名xyz"],
            project_roots=[str(PROJECT_ROOT)],
            encode_fn=self._enc,
        )
        self.assertEqual(att.mention_count, 1)
        self.assertTrue(att.approximate)

    def test_mention_resolving_to_file_counts_as_file(self) -> None:
        # cli.py 在仓库中存在，mention 应被识别为文件。
        att = estimate_attachments(
            images=None,
            files=None,
            mentions=["pyproject.toml"],
            project_roots=[str(PROJECT_ROOT)],
            encode_fn=self._enc,
        )
        self.assertEqual(att.file_count, 1)
        self.assertEqual(att.mention_count, 0)

    def test_mention_resolving_relative_path_reads_file_content(self) -> None:
        att = estimate_attachments(
            images=None,
            files=None,
            mentions=["@src/codex_usage_hud/core/pre_send_estimator.py"],
            project_roots=[str(PROJECT_ROOT)],
            encode_fn=self._enc,
        )
        self.assertEqual(att.file_count, 1)
        self.assertEqual(att.mention_count, 0)
        self.assertGreater(att.file_tokens, 100)

    def test_mention_resolving_markdown_link_absolute_path_reads_file_content(self) -> None:
        target = PROJECT_ROOT / "src" / "codex_usage_hud" / "platforms" / "base.py"
        att = estimate_attachments(
            images=None,
            files=None,
            mentions=[f"[base.py]({target.as_posix()})"],
            project_roots=[str(PROJECT_ROOT)],
            encode_fn=self._enc,
        )
        self.assertEqual(att.file_count, 1)
        self.assertEqual(att.mention_count, 0)
        self.assertGreater(att.file_tokens, 100)

    def test_skill_reference_reads_skill_markdown_content(self) -> None:
        skill_root = PROJECT_ROOT / "tmp_test_skills"
        skill_dir = skill_root / "token-skill"
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text("x" * 1200, encoding="utf-8")
        try:
            att = estimate_attachments(
                images=None,
                files=None,
                mentions=None,
                skills=["$token-skill"],
                project_roots=[str(PROJECT_ROOT)],
                skill_roots=[str(skill_root)],
                encode_fn=self._enc,
            )
        finally:
            try:
                skill_file.unlink()
                skill_dir.rmdir()
                skill_root.rmdir()
            except OSError:
                pass
        self.assertEqual(att.skill_count, 1)
        self.assertEqual(att.mention_count, 0)
        self.assertGreater(att.skill_tokens, 100)
        self.assertFalse(att.approximate)

    def test_file_entry_with_absolute_path_reads_content(self) -> None:
        # JS 侧从 React fiber 取到的 {name, path} 绝对路径应直接读盘，
        # 即便文件不在 project_roots 下（如桌面上传的引用文件）。
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp) / "outside.txt"
            outside.write_text("hello world " * 200, encoding="utf-8")
            att = estimate_attachments(
                images=None,
                files=[{"name": "outside.txt", "path": str(outside)}],
                mentions=None,
                project_roots=[str(PROJECT_ROOT)],  # 故意不含该文件所在目录
                encode_fn=self._enc,
            )
        self.assertEqual(att.file_count, 1)
        self.assertGreater(att.file_tokens, 100)
        self.assertFalse(att.approximate)

    def test_file_entry_missing_path_falls_back_to_name_scan(self) -> None:
        att = estimate_attachments(
            images=None,
            files=[{"name": "pyproject.toml", "path": ""}],
            mentions=None,
            project_roots=[str(PROJECT_ROOT)],
            encode_fn=self._enc,
        )
        self.assertEqual(att.file_count, 1)
        self.assertGreater(att.file_tokens, 0)

    def test_mention_entry_with_absolute_path_reads_content(self) -> None:
        # `@文件` chip 的 fiber 也可能带 resourcePath，指向仓库外的绝对路径；
        # 这类 mention 必须直接读盘计入 @引用文件×N，而不是掉进按名估算。
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp) / "Entity.cs"
            outside.write_text("class Entity {}\n" * 500, encoding="utf-8")
            att = estimate_attachments(
                images=None,
                files=None,
                mentions=[{"name": "@Entity.cs", "path": str(outside)}],
                project_roots=[str(PROJECT_ROOT)],
                encode_fn=self._enc,
            )
        self.assertEqual(att.mention_count, 0)
        self.assertEqual(att.reference_file_count, 1)
        self.assertGreater(att.reference_file_tokens, 100)
        self.assertFalse(att.approximate)

    def test_mention_entry_with_relative_path_joined_to_root(self) -> None:
        # ProseMirror atMention 只暴露相对项目根的 path（如 Moon.Core/Entity/Entity.cs）；
        # Python 端应拼到 project_root 上读盘，而不是当成绝对路径失败后回退按名估算。
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sub = root / "Moon.Core" / "Entity"
            sub.mkdir(parents=True)
            target = sub / "Entity.cs"
            target.write_text("class Entity {}\n" * 500, encoding="utf-8")
            att = estimate_attachments(
                images=None,
                files=None,
                mentions=[{"name": "@Entity.cs", "path": "Moon.Core/Entity/Entity.cs"}],
                project_roots=[str(root)],
                encode_fn=self._enc,
            )
        self.assertEqual(att.mention_count, 0)
        self.assertEqual(att.reference_file_count, 1)
        self.assertGreater(att.reference_file_tokens, 100)
        self.assertEqual(att.reference_file_unresolved, 0)
        self.assertFalse(att.approximate)

    def test_image_approximate_does_not_taint_resolved_file_note(self) -> None:
        # 图片本质是分块估算 (approximate=True)，但只要文件附件都已定位到磁盘，
        # 文件行就不该错标 ≈ 部分未定位。
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp) / "SingleFilePublish.cs"
            outside.write_text("class X{}\n" * 200, encoding="utf-8")
            att = estimate_attachments(
                images=[{"width": 179, "height": 120, "name": "image.png"}],
                files=[{"name": "SingleFilePublish.cs", "path": str(outside)}],
                mentions=None,
                project_roots=[str(PROJECT_ROOT)],
                encode_fn=self._enc,
            )
        self.assertTrue(att.approximate)  # 有图片，整体仍是近似
        self.assertEqual(att.file_attachment_unresolved, 0)  # 但文件本身已定位
        est = BaseEstimate(input_text_tokens=0, attachments=att)
        rows = est.breakdown_rows()
        file_row = next(r for r in rows if r["label"].startswith("文件附件"))
        self.assertNotIn("部分未定位", file_row.get("note", ""))

    def test_breakdown_rows_include_attachment_lines(self) -> None:
        att = AttachmentEstimate(
            image_tokens=300, image_count=2,
            file_tokens=5000, file_count=1,
            mention_tokens=10, mention_count=1,
            approximate=True,
        )
        est = BaseEstimate(input_text_tokens=6, attachments=att)
        labels = [r["label"] for r in est.breakdown_rows()]
        self.assertTrue(any("图片" in label for label in labels))
        self.assertTrue(any("引用文件" in label for label in labels))
        self.assertTrue(any("@引用/名称" in label for label in labels))

    def test_breakdown_rows_split_attachment_reference_and_skill_lines(self) -> None:
        att = AttachmentEstimate(
            image_tokens=300,
            image_count=2,
            file_tokens=7000,
            file_count=2,
            file_attachment_tokens=2000,
            file_attachment_count=1,
            reference_file_tokens=5000,
            reference_file_count=1,
            skill_tokens=1200,
            skill_count=1,
            mention_tokens=12,
            mention_count=1,
            approximate=True,
        )
        est = BaseEstimate(input_text_tokens=6, attachments=att)
        labels = [r["label"] for r in est.breakdown_rows()]

        self.assertIn("图片附件×2", labels)
        self.assertIn("文件附件×1", labels)
        self.assertIn("@引用文件×1", labels)
        self.assertIn("$技能×1", labels)
        self.assertIn("@引用/名称×1", labels)
        self.assertNotIn("引用文件×2", labels)

    def test_set_attachments_requests_immediate_recompute(self) -> None:
        updates: list[int] = []
        estimator = PreSendEstimator(
            project_roots=[str(PROJECT_ROOT)],
            input_text_getter=lambda: "hi",
            update_callback=lambda _estimate: updates.append(1),
            debounce_seconds=60.0,
        )
        estimator.start()
        try:
            estimator.set_attachments({
                "images": [{"width": 512, "height": 512}],
                "files": [],
                "mentions": [],
                "skills": [],
            })
            deadline = time.time() + 1.5
            while time.time() < deadline:
                if estimator.latest().attachments.image_count == 1:
                    break
                time.sleep(0.03)
            self.assertEqual(estimator.latest().attachments.image_count, 1)
            self.assertGreaterEqual(len(updates), 1)
        finally:
            estimator.close()

    def test_attachments_counted_in_total_tokens(self) -> None:
        att = AttachmentEstimate(image_tokens=300, image_count=1, file_tokens=5000, file_count=1)
        base = BaseEstimate(input_text_tokens=100, attachments=att)
        merged = base.with_confirmed_context(cached_tokens=1000, uncached_tokens=200)
        # 100(输入) + 1200(上下文) + 5300(附件)
        self.assertEqual(merged.total_tokens, 6600)

    def test_with_attachments_replaces_without_double_count(self) -> None:
        base = BaseEstimate(
            input_text_tokens=100,
            total_tokens=100,
            attachments=AttachmentEstimate(file_tokens=500, file_count=1),
        )
        # total 原含 500 旧附件
        base = base.with_session_history(0)  # 触发 total 重算含附件
        replaced = base.with_attachments(AttachmentEstimate(file_tokens=999, file_count=1))
        # 旧 500 应被剔除，只算新 999
        self.assertEqual(replaced.attachments.file_tokens, 999)

    def test_set_attachments_triggers_recompute(self) -> None:
        estimator = PreSendEstimator(
            project_roots=[str(PROJECT_ROOT)],
            input_text_getter=lambda: "hi",
            debounce_seconds=0.01,
        )
        estimator.start()
        try:
            estimator.set_attachments({
                "images": [{"width": 512, "height": 512}],
                "files": ["pyproject.toml"],
                "mentions": [],
                "skills": [],
            })
            deadline = time.time() + 3.0
            while time.time() < deadline:
                latest = estimator.latest()
                if latest.attachments.has_any:
                    break
                time.sleep(0.05)
            latest = estimator.latest()
            self.assertTrue(latest.attachments.has_any)
            self.assertEqual(latest.attachments.image_count, 1)
        finally:
            estimator.close()


if __name__ == "__main__":
    unittest.main()
