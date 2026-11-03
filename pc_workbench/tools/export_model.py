"""Clean and export the generated geometry; preserve every connected component."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from studio.common import read_json, save_json, sha256


def main(args):
    import numpy as np
    import pymeshlab
    import trimesh
    mesh = trimesh.load(args.run / 'generated_raw.ply', force='mesh', process=False)
    if not np.isfinite(mesh.vertices).all() or len(mesh.faces) < 10:
        raise ValueError('模型为空或包含无效坐标')
    mesh.update_faces(mesh.unique_faces())
    mesh.update_faces(mesh.nondegenerate_faces())
    mesh.remove_unreferenced_vertices()
    output = args.run / 'delivery'
    output.mkdir()
    artifacts = {}
    for count, name in [(80000, 'model_untextured'), (24000, 'model_lod24k')]:
        work = mesh.copy()
        if len(work.faces) > count:
            ms = pymeshlab.MeshSet()
            ms.add_mesh(pymeshlab.Mesh(work.vertices, work.faces))
            ms.apply_filter('meshing_decimation_quadric_edge_collapse', targetfacenum=count,
                preservenormal=True, preservetopology=True, preserveboundary=True, qualitythr=.6, autoclean=True)
            m = ms.current_mesh()
            work = trimesh.Trimesh(m.vertex_matrix(), m.face_matrix(), process=False)
        work.visual = trimesh.visual.TextureVisuals(material=trimesh.visual.material.PBRMaterial(
            baseColorFactor=[184, 194, 209, 255], roughnessFactor=.82, metallicFactor=0))
        path = output / (name + '.glb')
        work.export(path)
        check = trimesh.load(path, force='mesh', process=False)
        if len(check.faces) != len(work.faces) or not np.isfinite(check.vertices).all():
            raise ValueError('GLB 重新读取校验失败')
        artifacts[name] = dict(file=path.name, sha256=sha256(path), faces=len(check.faces),
                              watertight=bool(check.is_watertight), components=len(check.split(only_watertight=False)))
        if name == 'model_untextured':
            work.export(output / 'model.obj')
            work.export(output / 'model.ply')
        print('EXPORT_DONE', name, flush=True)
    save_json(output / 'model_report.json', dict(status='exported_pending_visual_review',
        artifacts=artifacts, geometry_is_ai_inferred=True, scale_calibrated=False,
        rigged=False, texture_generated=False, accepted_for_static_display=False,
        source_generation=read_json(args.run / 'generation_report.json'),
        limitations=['四方向 AI 形状推断；未做本轮照片的相机标定或实测深度融合。',
                     '此快速入口导出无贴图网格，需要检查结构；不是自动验收的动画资产。']))
    print('EXPORT_VALIDATED', flush=True)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    p = argparse.ArgumentParser()
    p.add_argument('--run', type=Path, required=True)
    main(p.parse_args())
