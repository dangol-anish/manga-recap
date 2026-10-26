import unittest
from unittest.mock import patch
from PIL import Image
from app import narration_stage5 as story, panel_preparation as panels
from tests import test_narration_stage5 as story_tests


class StoryPreviewTests(unittest.TestCase):
    setUp = story_tests.StoryTests.setUp
    tearDown = story_tests.StoryTests.tearDown
    detect = story_tests.StoryTests.detect
    prepare = story_tests.StoryTests.prepare
    ready = story_tests.StoryTests.ready
    status = story_tests.StoryTests.status
    payload = story_tests.StoryTests.payload
    submit = story_tests.StoryTests.submit

    def preview_url(self, bid='batch-001'):
        return self.api+'/'+bid+'/preview'

    def imported(self):
        records = self.ready()
        payload = self.payload()
        ids = self.status()['batches'][0]['panel_ids'][:2]
        payload['beats'] = [{'panel_ids': ids,
            'narration': 'Then a new moment begins. Another action follows.',
            'unsure': 'The identity is unclear.',
            'anchors': [{'panel_id': ids[0], 'phrase': 'a new moment'},
                        {'panel_id': ids[1], 'phrase': 'Another action'}]}]
        self.assertTrue(self.submit(payload).json()['ok'])
        return records

    def test_preview_matches_exact_script_spans_order_and_crop_bytes(self):
        records = self.imported()
        response = self.client.get(self.preview_url())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        value = response.json(); beat = value['beats'][0]
        self.assertEqual(beat['number'], 1)
        self.assertEqual(beat['unsure'], 'The identity is unclear.')
        self.assertEqual([p['id'] for p in beat['panels']], ['p0000-1', 'p0000-2'])
        self.assertEqual([p['narration'] for p in beat['panels']],
                         ['Then a new moment begins. ', 'Another action follows.'])
        self.assertEqual(''.join(p['narration'] for p in beat['panels']), beat['narration'])
        image = self.client.get(self.preview_url()+'/p0000-1', params={'token': value['token']})
        self.assertEqual(image.status_code, 200, image.text if image.status_code != 200 else '')
        info, _, _ = panels.context(self.ident)
        original = panels.record_path(info, 'p0000').parent / records[0]['revision'] / 'crops/p0000-1.png'
        self.assertEqual(image.content, original.read_bytes())

    def test_preview_never_modifies_saved_work_or_generates_assets(self):
        self.imported()
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with patch('app.detector.Detector', side_effect=AssertionError('No inference')), \
             patch('app.panel_preparation.generate', side_effect=AssertionError('No cropping')), \
             patch('app.speech_stage6.start', side_effect=AssertionError('No speech')):
            value = self.client.get(self.preview_url()).json()
            self.assertEqual(self.client.get(self.preview_url()+'/p0000-1', params={'token': value['token']}).status_code, 200)
        self.assertEqual(before, {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_unimported_batch_has_no_preview(self):
        self.ready()
        self.assertEqual(self.client.get(self.preview_url()).status_code, 409)

    def test_stale_source_rejects_preview_and_previously_issued_crop_token(self):
        self.imported()
        value = self.client.get(self.preview_url()).json()
        Image.new('RGB', (100, 150), 'green').save(self.source/'0000.png')
        self.assertGreaterEqual(self.client.get(self.preview_url()).status_code, 400)
        self.assertGreaterEqual(self.client.get(self.preview_url()+'/p0000-1', params={'token': value['token']}).status_code, 400)

    def test_old_token_rejected_after_narration_changes_and_unknown_panel_rejected(self):
        self.imported()
        value = self.client.get(self.preview_url()).json()
        response = self.client.get(self.preview_url()+'/p9999-1', params={'token': value['token']})
        self.assertGreaterEqual(response.status_code, 400)
        payload = self.payload(); payload['beats'][0]['narration'] = 'A new moment changes.'
        self.assertTrue(self.submit(payload).json()['ok'])
        response = self.client.get(self.preview_url()+'/p0000-1', params={'token': value['token']})
        self.assertEqual(response.status_code, 409)

    def test_silent_beat_keeps_images_without_inventing_spoken_text(self):
        self.ready(); payload = self.payload()
        payload['beats'][0].update(narration='', anchors=[])
        self.assertTrue(self.submit(payload).json()['ok'])
        beat = self.client.get(self.preview_url()).json()['beats'][0]
        self.assertTrue(beat['silent'])
        self.assertEqual(beat['panels'][0]['narration'], '')

    def test_partial_chapter_allows_only_current_imported_batches(self):
        self.ready(21)
        self.assertTrue(self.submit().json()['ok'])
        self.assertEqual(self.client.get(self.preview_url()).status_code, 200)
        self.assertEqual(self.client.get(self.preview_url('batch-002')).status_code, 409)

    def test_missing_crop_is_blocked_without_recreating_it(self):
        records = self.imported()
        info, _, _ = panels.context(self.ident)
        crop = panels.record_path(info, 'p0000').parent / records[0]['revision'] / 'crops/p0000-1.png'
        crop.unlink()
        self.assertGreaterEqual(self.client.get(self.preview_url()).status_code, 400)
        self.assertFalse(crop.exists())
