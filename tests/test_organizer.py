import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import organizer
from llm_classifier import parse_json_object, VisionClassifier


class OrganizerTests(unittest.TestCase):
    def test_qwen_reasoning_wrapper_json_is_parsed(self):
        text = 'analysis first\n</think>\n```json\n{"category":"Travel","confidence":0.91,"reason":"boarding pass"}\n```'
        result = parse_json_object(text)
        self.assertEqual(result["category"], "Travel")

    def test_vision_validation_rejects_unknown_or_low_confidence_categories(self):
        classifier = VisionClassifier(
            {"mode": "unmatched", "minimum_confidence": 0.7},
            ["Travel", "By Type/PDF"],
        )
        category, _ = classifier._validated_result(
            {"category": "Made Up", "confidence": 0.99}, "By Type/PDF", "test"
        )
        self.assertEqual(category, "By Type/PDF")
        category, _ = classifier._validated_result(
            {"category": "Travel", "confidence": 0.4}, "By Type/PDF", "test"
        )
        self.assertEqual(category, "By Type/PDF")
        category, reason = classifier._validated_result(
            {"category": "Travel", "confidence": 0.9, "reason": "flight"},
            "By Type/PDF", "test",
        )
        self.assertEqual(category, "Travel")
        self.assertIn("flight", reason)

    def test_keyword_classification_precedes_type(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "2025-tax-statement.pdf"
            path.write_bytes(b"pdf")
            category, reason = organizer.classify(path, {"Financial": {"keywords": ["tax"]}})
            self.assertEqual(category, "Financial")
            self.assertIn("tax", reason)

    def test_existing_identical_file_goes_to_duplicate_review(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source" / "manual.pdf"
            source.parent.mkdir()
            source.write_bytes(b"same")
            timestamp = time.mktime((2024, 1, 2, 0, 0, 0, 0, 0, -1))
            os.utime(source, (timestamp, timestamp))
            existing = root / "organized" / "Manuals" / "2024" / "manual.pdf"
            existing.parent.mkdir(parents=True)
            existing.write_bytes(b"same")
            action = organizer.action_for(source, root / "organized", {"Manuals": {"keywords": ["manual"]}})
            self.assertEqual(action["category"], "_Review/Duplicates")
            self.assertIn("_Review/Duplicates/2024/manual.pdf", action["destination"])

            review_copy = Path(action["destination"])
            review_copy.parent.mkdir(parents=True)
            review_copy.write_bytes(b"same")
            second_action = organizer.action_for(
                source, root / "organized", {"Manuals": {"keywords": ["manual"]}}
            )
            self.assertTrue(second_action["destination"].endswith("manual-2.pdf"))

    def test_scan_skips_destination_new_and_disallowed_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old_pdf = root / "nested" / "old.pdf"
            old_pdf.parent.mkdir()
            old_pdf.write_bytes(b"old")
            old_time = time.time() - 7200
            os.utime(old_pdf, (old_time, old_time))
            (root / "new.pdf").write_bytes(b"new")
            (root / "ignore.exe").write_bytes(b"exe")
            organized = root / "Organized"
            organized.mkdir()
            archived = organized / "already.pdf"
            archived.write_bytes(b"archived")

            found = list(organizer.iter_files([root], organized, {"pdf"}, time.time() - 3600))
            self.assertEqual(found, [old_pdf])

            found_inside_destination = list(
                organizer.iter_files([organized], organized, {"pdf"}, time.time() + 1)
            )
            self.assertEqual(found_inside_destination, [])

    def test_collision_gets_numeric_suffix(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.txt"
            target = root / "target.txt"
            source.write_text("new", encoding="utf-8")
            target.write_text("old", encoding="utf-8")
            chosen, duplicate = organizer.unique_destination(target, source)
            self.assertEqual(chosen.name, "target-2.txt")
            self.assertFalse(duplicate)

    def test_excluded_tree_is_never_scanned(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            excluded = root / "active-project"
            excluded.mkdir()
            file = excluded / "notes.pdf"
            file.write_bytes(b"keep in place")
            found = list(
                organizer.iter_files(
                    [root], root / "Organized", {"pdf"}, time.time() + 1, [excluded]
                )
            )
            self.assertEqual(found, [])

    def test_apply_run_moves_and_logs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inbox = root / "Inbox"
            inbox.mkdir()
            source = inbox / "flight-itinerary.pdf"
            source.write_bytes(b"trip")
            old_time = time.time() - 7200
            os.utime(source, (old_time, old_time))
            config = root / "config.toml"
            config.write_text(
                f'scan_roots = ["{inbox}"]\n'
                f'organized_root = "{root / "Organized"}"\n'
                'min_age_hours = 1\nallowed_extensions = ["pdf"]\n'
                '[rules.Travel]\nkeywords = ["itinerary"]\n',
                encoding="utf-8",
            )
            args = SimpleNamespace(config=config, apply=True, json=True, min_age_hours=None)
            with patch.dict(os.environ, {"HOME": str(root)}):
                result = organizer.run(args)
            self.assertEqual(result, 0)
            self.assertFalse(source.exists())
            self.assertTrue((root / "Organized" / "Travel" / time.strftime("%Y", time.localtime(old_time)) / source.name).exists())
            self.assertTrue(
                (root / "Library" / "Logs" / "KeepMacsOrganized" / "actions.jsonl").exists()
            )


if __name__ == "__main__":
    unittest.main()
