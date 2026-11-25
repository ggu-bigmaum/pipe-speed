import tempfile
from pathlib import Path
import unittest
from package_national_data import package


class PackageTest(unittest.TestCase):
    def test_required_missing_or_empty_roots_cannot_create_ready_archive(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); dest=root/'out'; dest.mkdir()
            missing=root/'backend/data/missing'
            with self.assertRaises(FileNotFoundError): package(root,dest,'missing.tar.gz',[missing])
            self.assertFalse((dest/'missing.tar.gz').exists())
            empty=root/'backend/data/empty'; empty.mkdir(parents=True)
            with self.assertRaises(ValueError): package(root,dest,'empty.tar.gz',[empty])
            self.assertFalse((dest/'empty.tar.gz').exists())

    def test_bundle_retains_relative_path_and_original_bytes(self):
        import tarfile
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); src=root/'backend/data/distance/kict_water_national/11'; src.mkdir(parents=True)
            (src/'sample.bin').write_bytes(b'grid\x00raster\n')
            dest=root/'out'; dest.mkdir()
            report=package(root,dest,'data.tar.gz',[src])
            self.assertEqual(report['files'],1)
            with tarfile.open(dest/'data.tar.gz','r:gz') as tar:
                self.assertEqual(tar.extractfile('backend/data/distance/kict_water_national/11/sample.bin').read(),b'grid\x00raster\n')


if __name__=='__main__': unittest.main()
