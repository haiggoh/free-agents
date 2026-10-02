#!/usr/bin/env python3
"""tests/test_queue_replay.py — tests for the shared queue replay core."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'bin'))
import queue_replay as qr


class QueueReplayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def write_fixture(self, name: str, records: list[dict]) -> Path:
        path = self.root / name
        path.write_text('\n'.join(json.dumps(r) for r in records) + '\n')
        return path

    def test_build_occurrences(self):
        """Enqueues become occurrences with stable IDs."""
        fixture = self.write_fixture("test.jsonl", [
            {"type": "user", "message": {"role": "user", "content": "turn 1"}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "reply 1"}]}},
            {"type": "queue-operation", "operation": "enqueue", "content": "P1"},
            {"type": "queue-operation", "operation": "enqueue", "content": "P2"},
            {"type": "queue-operation", "operation": "enqueue", "content": "P3"},
        ])
        occs = qr.build_occurrences(str(fixture), "session-123")
        self.assertEqual(len(occs), 3)
        self.assertEqual([o.content for o in occs], ["P1", "P2", "P3"])
        # Occurrence IDs should be stable and distinct
        self.assertNotEqual(occs[0].occurrence_id, occs[1].occurrence_id)
        # Same session + line + content = same ID
        occs2 = qr.build_occurrences(str(fixture), "session-123")
        self.assertEqual(occs[0].occurrence_id, occs2[0].occurrence_id)

    def test_build_drains(self):
        """Drains are parsed in physical order."""
        fixture = self.write_fixture("test.jsonl", [
            {"type": "queue-operation", "operation": "remove", "content": "P1"},
            {"type": "queue-operation", "operation": "dequeue"},
            {"type": "queue-operation", "operation": "popAll"},
        ])
        drains = qr.build_drains(str(fixture))
        self.assertEqual(len(drains), 3)
        self.assertEqual(drains[0].op, "remove")
        self.assertEqual(drains[0].content, "P1")
        self.assertEqual(drains[1].op, "dequeue")
        self.assertIsNone(drains[1].content)
        self.assertEqual(drains[2].op, "popAll")

    def test_pair_fifo_three_prompts(self):
        """Three prompts drained FIFO keep their own content."""
        fixture = self.write_fixture("test.jsonl", [
            {"type": "queue-operation", "operation": "enqueue", "content": "P1"},
            {"type": "queue-operation", "operation": "enqueue", "content": "P2"},
            {"type": "queue-operation", "operation": "enqueue", "content": "P3"},
            {"type": "queue-operation", "operation": "remove", "content": "P1"},
            {"type": "queue-operation", "operation": "remove", "content": "P2"},
            {"type": "queue-operation", "operation": "remove", "content": "P3"},
        ])
        occs = qr.build_occurrences(str(fixture), "s")
        drains = qr.build_drains(str(fixture))
        groups = qr.pair_occurrences_with_drains(occs, drains)
        self.assertEqual(len(groups), 3)
        self.assertEqual([g.occurrence.content for g in groups], ["P1", "P2", "P3"])

    def test_pair_outoforder_by_content(self):
        """Drain matched by content it carries, not position."""
        fixture = self.write_fixture("test.jsonl", [
            {"type": "queue-operation", "operation": "enqueue", "content": "FIRST prompt"},
            {"type": "queue-operation", "operation": "enqueue", "content": "SECOND prompt"},
            {"type": "queue-operation", "operation": "remove", "content": "SECOND prompt"},
            {"type": "queue-operation", "operation": "remove", "content": "FIRST prompt"},
        ])
        occs = qr.build_occurrences(str(fixture), "s")
        drains = qr.build_drains(str(fixture))
        groups = qr.pair_occurrences_with_drains(occs, drains)
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0].occurrence.content, "SECOND prompt")
        self.assertEqual(groups[1].occurrence.content, "FIRST prompt")

    def test_pair_contentless_dequeue_fifo(self):
        """Contentless dequeue falls back to OLDEST pending (FIFO).

        The NEWEST remains undelivered (no drain) as a separate group.
        """
        fixture = self.write_fixture("test.jsonl", [
            {"type": "queue-operation", "operation": "enqueue", "content": "OLDEST"},
            {"type": "queue-operation", "operation": "enqueue", "content": "NEWEST"},
            {"type": "queue-operation", "operation": "remove"},
        ])
        occs = qr.build_occurrences(str(fixture), "s")
        drains = qr.build_drains(str(fixture))
        groups = qr.pair_occurrences_with_drains(occs, drains)
        # Two groups: one drained (OLDEST), one undelivered (NEWEST)
        self.assertEqual(len(groups), 2)
        # Find the drained one
        drained = [g for g in groups if g.drain is not None]
        self.assertEqual(len(drained), 1)
        self.assertEqual(drained[0].occurrence.content, "OLDEST")
        # Find the undelivered one
        undelivered = [g for g in groups if g.drain is None]
        self.assertEqual(len(undelivered), 1)
        self.assertEqual(undelivered[0].occurrence.content, "NEWEST")

    def test_pair_popall_yields_oldest_first(self):
        """popAll yields groups oldest-first."""
        fixture = self.write_fixture("test.jsonl", [
            {"type": "queue-operation", "operation": "enqueue", "content": "A"},
            {"type": "queue-operation", "operation": "enqueue", "content": "B"},
            {"type": "queue-operation", "operation": "enqueue", "content": "C"},
            {"type": "queue-operation", "operation": "popAll"},
        ])

        occs = qr.build_occurrences(str(fixture), "s")
        drains = qr.build_drains(str(fixture))
        groups = qr.pair_occurrences_with_drains(occs, drains)
        self.assertEqual(len(groups), 3)
        self.assertEqual([g.occurrence.content for g in groups], ["A", "B", "C"])

    def test_identical_prompts_distinct_occurrences(self):
        """Two identical 'e' prompts are distinct occurrences."""
        fixture = self.write_fixture("test.jsonl", [
            {"type": "queue-operation", "operation": "enqueue", "content": "e"},
            {"type": "queue-operation", "operation": "enqueue", "content": "e"},
            {"type": "queue-operation", "operation": "remove", "content": "e"},
            {"type": "queue-operation", "operation": "remove", "content": "e"},
        ])
        occs = qr.build_occurrences(str(fixture), "s")
        drains = qr.build_drains(str(fixture))
        groups = qr.pair_occurrences_with_drains(occs, drains)
        self.assertEqual(len(groups), 2)
        # Both have same content but different occurrence_ids
        self.assertNotEqual(groups[0].occurrence.occurrence_id, groups[1].occurrence.occurrence_id)
        # Both have same content_hash
        self.assertEqual(groups[0].occurrence.content_hash, groups[1].occurrence.content_hash)

    def test_repeated_slash_command_distinct(self):
        """Later repeated slash command not cleared by earlier marker."""
        fixture = self.write_fixture("test.jsonl", [
            {"type": "queue-operation", "operation": "enqueue", "content": "/systematic-debugging"},
            {"type": "queue-operation", "operation": "remove", "content": "/systematic-debugging"},
            {"type": "queue-operation", "operation": "enqueue", "content": "/systematic-debugging"},
            {"type": "queue-operation", "operation": "remove", "content": "/systematic-debugging"},
        ])
        occs = qr.build_occurrences(str(fixture), "s")
        drains = qr.build_drains(str(fixture))
        groups = qr.pair_occurrences_with_drains(occs, drains)
        self.assertEqual(len(groups), 2)
        self.assertNotEqual(groups[0].occurrence.occurrence_id, groups[1].occurrence.occurrence_id)

    def test_marker_before_enqueue_rejected(self):
        """Marker before enqueue cannot clear future occurrence."""
        # This is tested via acknowledgment validation - marker in text before enqueue line
        # should not be matched because we only check texts AFTER enqueue_line
        fixture = self.write_fixture("test.jsonl", [
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "[[QUEUE_ANSWERED:abc12345]]"}]}},
            {"type": "queue-operation", "operation": "enqueue", "content": "later prompt"},
        ])
        occs = qr.build_occurrences(str(fixture), "s")
        self.assertEqual(len(occs), 1)
        assistant_entries = qr.extract_assistant_entries(str(fixture))
        self.assertEqual(len(assistant_entries), 1)
        # Assistant entry is at line 1, enqueue at line 2
        # When we attach evidence, only texts with line > enqueue_line count
        groups = qr.pair_occurrences_with_drains(occs, [])
        qr.attach_assistant_evidence(groups, assistant_entries)
        self.assertEqual(len(groups[0].assistant_texts), 0)  # No texts after enqueue

    def test_validate_acknowledgment_marker_only_rejected(self):
        """Marker-only response is insufficient."""
        fixture = self.write_fixture("test.jsonl", [
            {"type": "queue-operation", "operation": "enqueue", "content": "P1"},
        ])
        occs = qr.build_occurrences(str(fixture), "s")
        # Only marker, no substantive text
        texts = ["[[QUEUE_ANSWERED:" + qr.content_hash("P1") + "]]"]
        addressed, disp, ev = qr.validate_acknowledgment(texts, occs[0])
        self.assertFalse(addressed)

    def test_validate_acknowledgment_answered_now(self):
        """Substantive answer with marker is accepted as answered_now."""
        fixture = self.write_fixture("test.jsonl", [
            {"type": "queue-operation", "operation": "enqueue", "content": "P1"},
        ])
        occs = qr.build_occurrences(str(fixture), "s")
        texts = ["Here is the answer to P1 [[QUEUE_ANSWERED:" + qr.content_hash("P1") + "]]"]
        addressed, disp, ev = qr.validate_acknowledgment(texts, occs[0])
        self.assertTrue(addressed)
        self.assertEqual(disp, qr.Disposition.ANSWERED_NOW)

    def test_validate_acknowledgment_answered_earlier(self):
        """Reference to earlier answer is accepted as answered_earlier."""
        fixture = self.write_fixture("test.jsonl", [
            {"type": "queue-operation", "operation": "enqueue", "content": "P1"},
        ])
        occs = qr.build_occurrences(str(fixture), "s")
        texts = ["I already answered this earlier in my previous response [[QUEUE_ANSWERED:" + qr.content_hash("P1") + "]]"]
        addressed, disp, ev = qr.validate_acknowledgment(texts, occs[0])
        self.assertTrue(addressed)
        self.assertEqual(disp, qr.Disposition.ANSWERED_EARLIER)

    def test_validate_acknowledgment_clarification(self):
        """Clarification request is accepted as clarification."""
        fixture = self.write_fixture("test.jsonl", [
            {"type": "queue-operation", "operation": "enqueue", "content": "P1"},
        ])
        occs = qr.build_occurrences(str(fixture), "s")
        texts = ["Could you clarify what you mean by P1? [[QUEUE_ANSWERED:" + qr.content_hash("P1") + "]]"]
        addressed, disp, ev = qr.validate_acknowledgment(texts, occs[0])
        self.assertTrue(addressed)
        self.assertEqual(disp, qr.Disposition.CLARIFICATION)

    def test_validate_acknowledgment_no_marker_rejected(self):
        """Substantive text without marker is rejected."""
        fixture = self.write_fixture("test.jsonl", [
            {"type": "queue-operation", "operation": "enqueue", "content": "P1"},
        ])
        occs = qr.build_occurrences(str(fixture), "s")
        texts = ["Here is the answer to P1"]
        addressed, disp, ev = qr.validate_acknowledgment(texts, occs[0])
        self.assertFalse(addressed)

    def test_validate_acknowledgment_wrong_hash_rejected(self):
        """Marker for different content is rejected."""
        fixture = self.write_fixture("test.jsonl", [
            {"type": "queue-operation", "operation": "enqueue", "content": "P1"},
        ])
        occs = qr.build_occurrences(str(fixture), "s")
        texts = ["Answer [[QUEUE_ANSWERED:" + qr.content_hash("P2") + "]]"]
        addressed, disp, ev = qr.validate_acknowledgment(texts, occs[0])
        self.assertFalse(addressed)

    def test_validate_acknowledgment_stdin_hash(self):
        """Marker in stdin payload (last_assistant_message) is accepted."""
        fixture = self.write_fixture("test.jsonl", [
            {"type": "queue-operation", "operation": "enqueue", "content": "P1"},
        ])
        occs = qr.build_occurrences(str(fixture), "s")
        texts = ["Answer without marker"]
        stdin_hashes = {qr.content_hash("P1")}
        addressed, disp, ev = qr.validate_acknowledgment(texts, occs[0], stdin_hashes)
        self.assertTrue(addressed)

    def test_legacy_migration_unambiguous(self):
        """Legacy marker for single occurrence is migrated."""
        fixture = self.write_fixture("test.jsonl", [
            {"type": "queue-operation", "operation": "enqueue", "content": "P1"},
            {"type": "queue-operation", "operation": "remove", "content": "P1"},
        ])
        occs = qr.build_occurrences(str(fixture), "s")
        drains = qr.build_drains(str(fixture))
        groups = qr.pair_occurrences_with_drains(occs, drains)

        # Assistant text with legacy marker (content hash only)
        assistant_texts = ["Some response [[QUEUE_ANSWERED:" + qr.content_hash("P1") + "]]"]
        qr.migrate_legacy_markers(groups, assistant_texts)

        self.assertTrue(groups[0].acknowledged)
        self.assertEqual(groups[0].acknowledgment_disposition, qr.Disposition.ANSWERED_NOW)

    def test_legacy_migration_ambiguous_not_auto(self):
        """Legacy marker for multiple matching occurrences stays ambiguous."""
        fixture = self.write_fixture("test.jsonl", [
            {"type": "queue-operation", "operation": "enqueue", "content": "P1"},
            {"type": "queue-operation", "operation": "enqueue", "content": "P1"},
            {"type": "queue-operation", "operation": "remove", "content": "P1"},
            {"type": "queue-operation", "operation": "remove", "content": "P1"},
        ])
        occs = qr.build_occurrences(str(fixture), "s")
        drains = qr.build_drains(str(fixture))
        groups = qr.pair_occurrences_with_drains(occs, drains)

        assistant_texts = ["Some response [[QUEUE_ANSWERED:" + qr.content_hash("P1") + "]]"]
        qr.migrate_legacy_markers(groups, assistant_texts)

        # Neither should be auto-acknowledged due to ambiguity
        self.assertFalse(groups[0].acknowledged)
        self.assertFalse(groups[1].acknowledged)

    def test_replay_and_classify_full_pipeline(self):
        """Full replay_and_classify pipeline works end-to-end."""
        fixture = self.write_fixture("test.jsonl", [
            {"type": "user", "message": {"role": "user", "content": "turn 1"}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "reply 1"}]}},
            {"type": "queue-operation", "operation": "enqueue", "content": "P1"},
            {"type": "queue-operation", "operation": "enqueue", "content": "P2"},
            {"type": "queue-operation", "operation": "remove", "content": "P1"},
            {"type": "queue-operation", "operation": "remove", "content": "P2"},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Answered P1 [[QUEUE_ANSWERED:" + qr.content_hash("P1") + "]]"}]}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Answered P2 [[QUEUE_ANSWERED:" + qr.content_hash("P2") + "]]"}]}},
        ])

        result = qr.replay_and_classify(str(fixture), "session-123")
        self.assertEqual(len(result.groups), 2)
        self.assertEqual(len(result.acknowledged), 2)
        self.assertEqual(len(result.unaddressed), 0)
        self.assertEqual(len(result.undelivered), 0)

    def test_replay_and_classify_with_unaddressed(self):
        """Unaddressed prompts are correctly identified."""
        fixture = self.write_fixture("test.jsonl", [
            {"type": "queue-operation", "operation": "enqueue", "content": "P1"},
            {"type": "queue-operation", "operation": "enqueue", "content": "P2"},
            {"type": "queue-operation", "operation": "remove", "content": "P1"},
            {"type": "queue-operation", "operation": "remove", "content": "P2"},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Answered P1 [[QUEUE_ANSWERED:" + qr.content_hash("P1") + "]]"}]}},
            # P2 not addressed
        ])

        result = qr.replay_and_classify(str(fixture), "session-123")
        self.assertEqual(len(result.acknowledged), 1)
        self.assertEqual(len(result.unaddressed), 1)
        self.assertEqual(result.unaddressed[0].occurrence.content, "P2")

    def test_replay_and_classify_undelivered(self):
        """Undelivered (enqueued but not drained) prompts are identified."""
        fixture = self.write_fixture("test.jsonl", [
            {"type": "queue-operation", "operation": "enqueue", "content": "P1"},
            {"type": "queue-operation", "operation": "enqueue", "content": "P2"},
            # No drain
        ])

        result = qr.replay_and_classify(str(fixture), "session-123")
        self.assertEqual(len(result.undelivered), 2)
        self.assertEqual(len(result.unaddressed), 0)
        self.assertEqual(len(result.acknowledged), 0)

    def test_task_notifications_excluded_from_obligations(self):
        """Task notifications are replayed but not treated as user obligations."""
        # This is about classification - task notifications have distinct content patterns
        # They should be paired correctly but not appear in unaddressed if not acknowledged
        # For now, we just verify they don't break replay
        fixture = self.write_fixture("test.jsonl", [
            {"type": "queue-operation", "operation": "enqueue", "content": "user request"},
            {"type": "queue-operation", "operation": "enqueue", "content": "<task-notification>Task started</task-notification>"},
            {"type": "queue-operation", "operation": "remove", "content": "user request"},
            {"type": "queue-operation", "operation": "remove", "content": "<task-notification>Task started</task-notification>"},
        ])
        occs = qr.build_occurrences(str(fixture), "s")
        drains = qr.build_drains(str(fixture))
        groups = qr.pair_occurrences_with_drains(occs, drains)
        self.assertEqual(len(groups), 2)
        contents = [g.occurrence.content for g in groups]
        self.assertIn("user request", contents)
        self.assertIn("<task-notification>Task started</task-notification>", contents)


if __name__ == '__main__':
    import sys
    if '--help' in sys.argv or '-h' in sys.argv:
        print(__doc__)
        sys.exit(0)
    unittest.main()