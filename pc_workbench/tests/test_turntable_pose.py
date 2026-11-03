import tempfile
import unittest
from pathlib import Path
import sqlite3
import numpy as np
try:
    import scipy
    HAS_SCIPY=True
except ImportError:HAS_SCIPY=False


@unittest.skipUnless(HAS_SCIPY,'Processing environment required for pose optimization')
class TurntablePoseTests(unittest.TestCase):
    def make_database(self,root,bad_view=None):
        from studio.turntable_pose import rotations
        n=18;k=140;rng=np.random.default_rng(42)
        angles=(np.arange(n)-16)*2*np.pi/n+.07*np.sin(np.arange(n)*.7)
        angles-=angles[16];R=rotations(angles,.10,.015);t=np.array([-.055,-.08,1.])
        points=rng.uniform([-.12,-.16,-.10],[.12,.16,.10],(k,3));names=[f'camera0/p{i:04}.png' for i in range(n)]
        with sqlite3.connect(root/'db.sqlite') as db:
            db.executescript('CREATE TABLE images(image_id INTEGER,name TEXT);CREATE TABLE keypoints(image_id INTEGER,rows INTEGER,cols INTEGER,data BLOB);CREATE TABLE two_view_geometries(pair_id INTEGER,rows INTEGER,data BLOB);')
            for i in range(n):
                p=points@R[i].T+t;uv=p[:,:2]/p[:,2:]*1250+[640,480]+rng.normal(0,.15,(k,2))
                if i==bad_view:uv[:,1]+=25
                db.execute('insert into images values(?,?)',(i+1,names[i]))
                db.execute('insert into keypoints values(?,?,?,?)',(i+1,k,2,uv.astype(np.float32).tobytes()))
            for i in range(n):
                a,b=sorted([i+1,(i+1)%n+1]);matches=np.c_[np.arange(k),np.arange(k)].astype(np.uint32)
                db.execute('insert into two_view_geometries values(?,?,?)',(a*2147483647+b,k,matches.tobytes()))
        return names,angles

    def test_angles_are_recovered_from_image_not_locked_to_index(self):
        from studio.turntable_pose import solve_turntable
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);names,angles=self.make_database(root)
            report=solve_turntable(root/'db.sqlite',names,names[16],(1280,960),root/'out')
            actual=np.array([v['angle_deg'] for v in report['views']]);expected=np.degrees(angles)%360
            error=np.abs((actual-expected+180)%360-180)
            self.assertLess(float(error.max()),1)
            self.assertEqual(report['registered_images'],18)
            self.assertLess(report['validation_median_px'],1)
            self.assertFalse(report['measured_rotation_angles'])

    def test_inconsistent_view_is_rejected_while_good_views_survive(self):
        from studio.turntable_pose import solve_turntable
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);names,_=self.make_database(root,bad_view=7)
            report=solve_turntable(root/'db.sqlite',names,names[16],(1280,960),root/'out')
            self.assertFalse(report['views'][7]['accepted'])
            self.assertTrue(report['views'][16]['accepted'])
            self.assertGreaterEqual(report['registered_images'],4)


if __name__=='__main__':unittest.main()
