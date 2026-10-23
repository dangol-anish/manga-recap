import hashlib
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import storage
from scripts import install_model as installer


def entry(name, content, lfs=False):
    value={'rfilename':name,'size':len(content),'blobId':hashlib.sha1(f'blob {len(content)}\0'.encode()+content).hexdigest()}
    if lfs:value['lfs']={'sha256':hashlib.sha256(content).hexdigest(),'size':len(content)}
    return value


class ModelInstallerTests(unittest.TestCase):
    def test_exact_size_and_hash_required_for_source_and_weights(self):
        with tempfile.TemporaryDirectory(dir=storage.ROOT) as name:
            path=Path(name)/'fixture'
            for lfs in (False,True):
                path.write_bytes(b'valid');metadata=entry('fixture',b'valid',lfs)
                self.assertTrue(installer.verified(path,metadata))
                path.write_bytes(b'wrong');self.assertFalse(installer.verified(path,metadata))
                path.write_bytes(b'v');self.assertFalse(installer.verified(path,metadata))

    def test_failed_download_preserves_old_file_and_can_be_retried(self):
        with tempfile.TemporaryDirectory(dir=storage.ROOT) as name:
            dest=Path(name);target=dest/'model.safetensors';target.write_bytes(b'old')
            info={'sha':installer.REVISION,'siblings':[entry(target.name,b'correct',True)]}
            def broken(url,**kwargs):return BytesIO(json.dumps(info).encode() if '/api/' in url else b'broken')
            with patch.object(installer,'DEST',dest),patch.object(installer.urllib.request,'urlopen',side_effect=broken),patch.object(installer.time,'sleep'):
                with self.assertRaisesRegex(ValueError,'verification'):installer.install()
            self.assertEqual(target.read_bytes(),b'old');self.assertFalse(target.with_name(target.name+'.part').exists())
            def correct(url,**kwargs):return BytesIO(json.dumps(info).encode() if '/api/' in url else b'correct')
            with patch.object(installer,'DEST',dest),patch.object(installer.urllib.request,'urlopen',side_effect=correct):installer.install()
            self.assertEqual(target.read_bytes(),b'correct')

    def test_nested_sources_and_source_only_do_not_publish_weights(self):
        with tempfile.TemporaryDirectory(dir=storage.ROOT) as name:
            dest=Path(name);info={'sha':installer.REVISION,'siblings':[entry('nested/source.py',b'code'),entry('model.safetensors',b'weight',True)]}
            def response(url,**kwargs):return BytesIO(json.dumps(info).encode() if '/api/' in url else b'code')
            with patch.object(installer,'DEST',dest),patch.object(installer.urllib.request,'urlopen',side_effect=response):installer.install(source_only=True)
            self.assertEqual((dest/'nested/source.py').read_bytes(),b'code');self.assertFalse((dest/'model.safetensors').exists())

    def test_path_escape_or_wrong_revision_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=storage.ROOT) as name:
            for info in ({'sha':'wrong','siblings':[]},{'sha':installer.REVISION,'siblings':[entry('../escape',b'code')]}):
                with patch.object(installer,'DEST',Path(name)),patch.object(installer.urllib.request,'urlopen',return_value=BytesIO(json.dumps(info).encode())):
                    with self.assertRaises(ValueError):installer.install()
