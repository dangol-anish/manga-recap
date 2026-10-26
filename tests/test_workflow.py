"""Readiness uses isolated saved fixtures and never runs real processing."""
import unittest
from unittest.mock import patch
from PIL import Image
from app import storage, workflow, detection, panel_preparation as panels
from tests import test_narration_stage5 as story_tests


class WorkflowTests(unittest.TestCase):
    setUp = story_tests.StoryTests.setUp
    tearDown = story_tests.StoryTests.tearDown
    detect = story_tests.StoryTests.detect
    prepare = story_tests.StoryTests.prepare
    ready = story_tests.StoryTests.ready
    status = story_tests.StoryTests.status
    payload = story_tests.StoryTests.payload
    submit = story_tests.StoryTests.submit

    def progress(self):
        mid = detection.catalog()['manga'][0]['id']
        response = self.client.get(f'/api/workflow/{mid}/{self.ident}')
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_detection_complete_points_to_preparation_without_writing(self):
        before = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with patch('app.detector.Detector', side_effect=AssertionError('No model')), \
             patch('app.panel_preparation.generate', side_effect=AssertionError('No crops')):
            value = self.progress()
        self.assertEqual(value['next']['stage'], 4)
        self.assertEqual(value['steps'][2]['state'], 'ready')
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_prepared_and_partial_batches_point_to_exact_narration_batch(self):
        self.ready(21)
        value = self.progress()
        self.assertEqual(value['next']['stage'], 5)
        self.assertIn('batch-001', value['next']['detail'])
        self.assertTrue(self.submit().json()['ok'])
        value = self.progress()
        self.assertIn('batch-002', value['next']['detail'])

    def test_imported_narration_points_to_audio_then_video_and_complete(self):
        self.ready()
        self.assertTrue(self.submit().json()['ok'])
        self.assertEqual(self.progress()['next']['stage'], 6)
        with patch.object(workflow.speech, 'status', return_value={'ready': 1, 'spoken': 1}), \
             patch.object(workflow.cinema, 'status', return_value={'ready': False}):
            value = self.progress()
            self.assertEqual(value['next']['stage'], 7)
            self.assertFalse(value['complete'])
        with patch.object(workflow.speech, 'status', return_value={'ready': 1, 'spoken': 1}), \
             patch.object(workflow.cinema, 'status', return_value={'ready': True}):
            value = self.progress()
            self.assertTrue(value['complete'])
            self.assertIn('manga=', value['next']['url'])

    def test_changed_image_is_detected_before_downstream_work_is_ready(self):
        self.ready()
        self.submit()
        Image.new('RGB', (100, 150), 'green').save(self.source / '0000.png')
        value = self.progress()
        self.assertEqual(value['next']['stage'], 3)
        self.assertNotEqual(value['steps'][4]['state'], 'ready')

    def test_missing_crop_points_to_preparation(self):
        records = self.ready()
        info, _, _ = panels.context(self.ident)
        record = records[0]
        folder = panels.record_path(info, record['page_id']).parent / record['revision']
        next((folder / 'crops').iterdir()).unlink()
        self.assertEqual(self.progress()['next']['stage'], 4)

    def test_numbering_gap_and_interrupted_transaction_are_explained(self):
        (self.source / '0001.png').rename(self.source / '0050.png')
        value = self.progress()
        self.assertEqual(value['next']['stage'], 2)
        storage.write_json(self.source / '.renumbering.json', {'id': 'a'*32, 'status': 'interrupted'})
        self.assertIn('interrupted', self.progress()['next']['detail'])

    def test_current_detections_on_old_filenames_do_not_force_renumbering(self):
        (self.source / '0001.png').rename(self.source / '0050.png')
        self.detect()
        value = self.progress()
        self.assertEqual(value['steps'][1]['state'], 'optional')
        self.assertEqual(value['next']['stage'], 4)

    def test_unknown_chapter_cannot_read_another_path(self):
        response = self.client.get('/api/workflow/unknown/unknown')
        self.assertEqual(response.status_code, 404)

    def test_unfinished_download_points_back_to_downloader(self):
        ident = 'e' * 32
        storage.write_json(self.source / '.download-owner.json', {'job_id': ident})
        storage.write_json(workflow.downloader.record_path('jobs', ident), {'chapters': [
            {'folder': self.source.relative_to(self.root).as_posix(), 'status': 'failed'}]})
        value = self.progress()
        self.assertEqual(value['next']['stage'], 1)
        self.assertIn('unfinished', value['next']['detail'])

    def test_corrupt_saved_data_is_not_reset_or_marked_ready(self):
        self.ready()
        path = workflow.story.state_path(self.mid)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{broken', encoding='utf-8')
        value = self.progress()
        self.assertEqual(value['next']['stage'], 5)
        self.assertIn('Reference:', value['next']['detail'])
        self.assertEqual(path.read_text(), '{broken')
