"""Манифест вложений: подписи и порядок частей как в UI desktop."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from client.attachments import bridge


class BuildMessagePartsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _file(self, name: str, data: bytes = b"x") -> Path:
        path = self.root / name
        path.write_bytes(data)
        return path

    def test_manifest_goes_last_and_lists_every_attachment(self) -> None:
        deck = self._file("deck.pptx", b"pk\x03\x04deck")
        note = self._file("note.txt", "привет".encode("utf-8"))

        parts = bridge.build_message_parts("сделай презентацию", bridge.prepare_attachments([str(deck), str(note)]))

        self.assertEqual(parts[0]["type"], "text")
        self.assertEqual(parts[0]["text"], "сделай презентацию")
        self.assertEqual(
            parts[-1]["text"],
            "\n\n[Приложенные файлы:\n1) deck.pptx — презентация\n2) note.txt — текстовый файл\n]\n",
        )
        self.assertTrue(parts[-1]["synthetic"])
        self.assertEqual([part["type"] for part in parts], ["text", "file", "file", "text"])

    def test_office_labels_match_ui(self) -> None:
        deck = bridge.prepare_attachment(str(self._file("deck.pptx", b"pk\x03\x04deck")))
        doc = bridge.prepare_attachment(str(self._file("doc.docx", b"pk\x03\x04doc")))

        self.assertEqual(deck.manifest_label, "презентация")
        self.assertEqual(doc.manifest_label, "документ Word")

    def test_no_attachments_no_manifest(self) -> None:
        parts = bridge.build_message_parts("привет", [])

        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0]["text"], "привет")


if __name__ == "__main__":
    unittest.main()
