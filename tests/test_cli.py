# SPDX-License-Identifier: GPL-3.0-or-later
# Run with: python3 -m unittest discover -s tests

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from gradia.cli import CliError, RecordCommand, ScreenshotMode, parse_args


class ParseArgsTest(unittest.TestCase):
    def test_no_arguments(self):
        opts = parse_args([])
        self.assertIsNone(opts.screenshot)
        self.assertIsNone(opts.record)
        self.assertEqual(opts.files, [])

    def test_screenshot_defaults_to_interactive(self):
        self.assertEqual(parse_args(["--screenshot"]).screenshot, ScreenshotMode.INTERACTIVE)

    def test_screenshot_modes_are_case_insensitive(self):
        for mode in ScreenshotMode:
            self.assertEqual(parse_args([f"--screenshot={mode.value.lower()}"]).screenshot, mode)

    def test_unknown_screenshot_mode(self):
        with self.assertRaises(CliError):
            parse_args(["--screenshot=REGION"])

    def test_delay_in_seconds(self):
        self.assertEqual(parse_args(["--screenshot", "--delay=5"]).delay_seconds, 5)

    def test_invalid_delay(self):
        for bad in (["--delay"], ["--delay=abc"], ["--delay=-1"]):
            with self.assertRaises(CliError):
                parse_args(bad)

    def test_recording_commands(self):
        self.assertEqual(parse_args(["--record"]).record, RecordCommand.START)
        self.assertEqual(parse_args(["--stop-recording"]).record, RecordCommand.STOP)
        self.assertEqual(parse_args(["--toggle-recording"]).record, RecordCommand.TOGGLE)

    def test_conflicting_commands(self):
        with self.assertRaises(CliError):
            parse_args(["--record", "--stop-recording"])
        with self.assertRaises(CliError):
            parse_args(["--record", "--screenshot"])

    def test_unknown_option(self):
        with self.assertRaises(CliError):
            parse_args(["--frobnicate"])

    def test_files_and_pin(self):
        opts = parse_args(["--pin", "a.png", "b.png"])
        self.assertTrue(opts.pin)
        self.assertEqual(opts.files, ["a.png", "b.png"])


if __name__ == "__main__":
    unittest.main()
