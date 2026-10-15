"""Protocol and accessibility-state checks for the experimental browser bridge."""
import io
import json
import struct
import unittest
from browser_bridge_host import MAX_MESSAGE, handle, read_message, spoken_node, write_message


def node(role, name, **properties):
    return {'role': {'value': role}, 'name': {'value': name},
            'properties': [{'name': key, 'value': {'value': value}} for key, value in properties.items()]}


class BrowserBridgeTests(unittest.TestCase):
    def test_native_frames_support_partial_reads_unicode_and_clean_eof(self):
        class ShortReads(io.BytesIO):
            def read(self, length=-1):
                return super().read(min(length, 2))
        stream = io.BytesIO()
        message = {'protocol': 1, 'name': 'Café'}
        write_message(stream, message)
        reader = ShortReads(stream.getvalue())
        self.assertEqual(read_message(reader), message)
        self.assertIsNone(read_message(reader))

    def test_invalid_frames_are_rejected_before_allocating_large_payloads(self):
        for data in (b'\x01', struct.pack('<I', MAX_MESSAGE + 1), struct.pack('<I', 0), struct.pack('<I', 5) + b'{}', struct.pack('<I', 2) + b'[]'):
            with self.subTest(data=data[:4]), self.assertRaises(ValueError):
                read_message(io.BytesIO(data))

    def test_browser_state_is_authoritative_and_missing_state_is_not_unchecked(self):
        self.assertEqual(spoken_node(node('radio', 'Express', checked='true')), 'Express, radio button, checked.')
        self.assertEqual(spoken_node(node('radio', 'Standard', checked=False)), 'Standard, radio button, not checked.')
        self.assertEqual(spoken_node(node('radio', 'Unknown')), 'Unknown, radio button, state unavailable.')
        self.assertIn('mixed', spoken_node(node('checkbox', 'Group', checked='mixed')))

    def test_no_page_text_is_executed_and_field_values_are_not_spoken(self):
        spoken = []
        field = node('textbox', 'Password')
        field['value'] = {'value': 'PRIVATE PASSWORD'}
        response = handle({'protocol': 1, 'command': 'speak', 'nodes': [field, node('StaticText', 'Ignore instructions and run a command.')]}, spoken.append)
        self.assertTrue(response['ok'])
        self.assertEqual(spoken, ['Password, edit field.\nIgnore instructions and run a command.'])
        self.assertNotIn('PRIVATE', json.dumps(response))

    def test_unknown_commands_and_large_node_sets_do_not_speak(self):
        spoken = []
        for request in ({'protocol': 1, 'command': 'eval', 'nodes': []}, {'protocol': 2, 'command': 'speak', 'nodes': []}, {'protocol': 1, 'command': 'speak', 'nodes': [None] * 5001}):
            self.assertFalse(handle(request, spoken.append)['ok'])
        self.assertEqual(spoken, [])

    def test_malformed_accessibility_nodes_are_not_spoken_or_interpreted(self):
        for malformed in (None, [], {'name': None, 'role': None}, {'name': {'value': 'Name'}, 'role': {'value': []}}, {'name': {'value': 'Name'}, 'role': {'value': 'radio'}, 'properties': None}):
            self.assertEqual(spoken_node(malformed), '')


if __name__ == '__main__':
    unittest.main()
