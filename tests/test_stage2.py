from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from fastapi.testclient import TestClient
from app import storage, review, main


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=storage.ROOT)
        self.root=Path(self.tmp.name)
        self.patches=[patch.object(storage,'ROOT',self.root),patch.object(storage,'DATA',self.root/'data'),patch.object(storage,'OUTPUTS',self.root/'outputs')]
        for p in self.patches:p.start()
        self.manga={'id':'test','folder':'manga/Example','name':'Example','direction':'rtl'}
        storage.write_json(storage.DATA/'library.json',[self.manga])
        storage.write_json(storage.DATA/'model-validation.json',{'allowed_manga_id':'test','panel_only_validated':True,'allowed_chapters':['Chapter 1','Chapter 12']})
        self.folder=self.root/'manga/Example/Chapter 1';self.folder.mkdir(parents=True)
        for name in ('001.png','002.png'):
            image=Image.new('RGB',(100,200),'white')
            for x in range(50):
                for y in range(200):image.putpixel((x,y),(255,0,0))
            image.save(self.folder/name)
        self.source={'schema_version':1,'panels':[{'order':2,'box':[0,0,45,200]},{'order':1,'box':[55,0,100,200]}]}
        for page in storage.pages(self.folder):storage.write_json(storage.page_output(self.manga,'Chapter 1',page.name),self.source)
        self.client=TestClient(main.app)

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()

    def test_flag_thresholds_and_actual_intersection(self):
        reasons=review.flag_reasons([[0,0,100,180],[20,20,60,80]],100,200)
        self.assertTrue(any('90%' in r for r in reasons))
        self.assertTrue(any('overlap:' in r for r in reasons))
        self.assertEqual(review.flag_reasons([[0,0,100,200]],100,200),[])
        self.assertFalse(any('overlap:' in r for r in review.flag_reasons([[0,0,100,80],[0,100,100,200]],100,200)))
        self.assertFalse(any('overlap:' in r for r in review.flag_reasons([[0,0,50,100],[45,90,100,200]],100,200)))
        # Thin strips, shared edges, and an exact 5% intersection never qualify.
        for boxes in (
            [[0,0,50,200],[49,0,100,200]],
            [[0,0,100,100],[0,99,100,200]],
            [[0,0,50,200],[50,0,100,200]],
            [[0,0,100,100],[0,100,100,200]],
            [[0,0,50,200],[45,0,100,200]],
            [[0,0,100,100],[0,90,100,200]],
        ):
            self.assertFalse(any('overlap:' in r for r in review.flag_reasons(boxes,100,200)),boxes)
        self.assertTrue(any('overlap:' in r for r in review.flag_reasons([[0,0,60,120],[50,100,100,200]],100,200)))
        self.assertTrue(any('under 10%' in r for r in review.flag_reasons([[0,0,9,200]],100,200)))
        self.assertTrue(any('under 3%' in r for r in review.flag_reasons([[0,0,10,40]],100,200)))
        self.assertTrue(any('Spread' in r for r in review.flag_reasons([[0,0,200,100]],200,100)))

    def test_padding_uses_panel_dimensions_and_clamps(self):
        self.assertEqual(review.padded_crop_box([0,0,100,200],100,200),[0,0,100,200])
        self.assertEqual(review.padded_crop_box([20,50,70,150],100,200),[19,49,71,151])

    def test_prepare_and_edit_regenerate_only_one_page(self):
        first=self.folder/'001.png';second=self.folder/'002.png'
        source_path=storage.page_output(self.manga,'Chapter 1','001.png')
        original=source_path.read_bytes()
        review.prepare_chapter('test','Chapter 1')
        before=storage.read_json(review.record_path(self.manga,'Chapter 1',first))
        other=review.record_path(self.manga,'Chapter 1',second).read_bytes()
        self.assertEqual([p['id'] for p in before['panels']],['p001-1','p001-2'])
        self.assertEqual(before['panels'][0]['box'],[55,0,100,200])
        old_folder=review.record_path(self.manga,'Chapter 1',first).parent/before['revision']
        updated=review.save_page('test','Chapter 1','001.png',before['revision'],[[0,0,40,200]])
        self.assertNotEqual(updated['revision'],before['revision'])
        self.assertFalse(old_folder.exists())
        crop_path=review.asset_path('test','Chapter 1','001.png','crop',updated['revision'],'p001-1')
        with Image.open(crop_path) as crop:self.assertEqual(crop.getpixel((0,0)),(255,0,0))
        self.assertEqual(list(crop_path.parent.glob('*.png')),[crop_path])
        self.assertEqual(review.record_path(self.manga,'Chapter 1',second).read_bytes(),other)
        self.assertEqual(source_path.read_bytes(),original)
        with self.assertRaises(FileExistsError):review.save_page('test','Chapter 1','001.png',before['revision'],[[0,0,100,200]])
        current=review.record_path(self.manga,'Chapter 1',first).read_bytes()
        with self.assertRaises(ValueError):review.save_page('test','Chapter 1','001.png',updated['revision'],[[-1,0,40,200]])
        self.assertEqual(review.record_path(self.manga,'Chapter 1',first).read_bytes(),current)

    def test_zero_panels_fallback_and_flagged_first(self):
        storage.write_json(storage.page_output(self.manga,'Chapter 1','002.png'),{'panels':[]})
        result=review.prepare_chapter('test','Chapter 1')
        self.assertEqual(result['pages'][0]['page'],'002.png')
        record=result['pages'][0]
        self.assertTrue(record['zero_panels_fallback'])
        self.assertEqual(record['panels'][0]['box'],[0,0,100,200])
        self.assertTrue(any('0 panels' in r for r in record['flag_reasons']))
        revised=review.save_page('test','Chapter 1','001.png',result['pages'][1]['revision'],[])
        self.assertTrue(revised['zero_panels_fallback'])
        self.assertEqual(len(revised['panels']),1)

    def test_http_save_assets_and_scope(self):
        review.prepare_chapter('test','Chapter 1')
        url='/api/review/test/Chapter%201'
        response=self.client.get(url);self.assertEqual(response.status_code,200)
        record=response.json()['pages'][0]
        page_url=url+'/'+record['page']
        response=self.client.post(page_url,json={'revision':record['revision'],'boxes':[[0,0,40,200],[55,0,100,100]]})
        self.assertEqual(response.status_code,200,response.text)
        updated=response.json()
        for kind in ('thumbnail','labeled','source'):
            self.assertEqual(self.client.get(page_url+'/image',params={'kind':kind,'revision':updated['revision']}).status_code,200)
        self.assertEqual(self.client.get(page_url+'/image',params={'kind':'crop','panel_id':'../../secrets'}).status_code,404)
        self.assertEqual(self.client.post(page_url,json={'revision':record['revision'],'boxes':[]}).status_code,409)
        self.assertEqual(self.client.get('/api/review/test/Chapter%202').status_code,403)
        self.assertEqual(self.client.get('/review/test/Chapter%202').status_code,403)
        self.assertEqual(self.client.get('/api/review/another/Chapter%201').status_code,403)
        self.assertEqual(self.client.get(url+'/999.png/image?kind=source').status_code,404)

if __name__=='__main__':unittest.main()
