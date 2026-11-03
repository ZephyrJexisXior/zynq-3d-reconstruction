"""Opt-in local AI shape completion; output is inferred geometry, not a scan."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def main(args):
    sys.stdout.reconfigure(encoding='utf-8')
    os.environ.setdefault('HF_HUB_OFFLINE', '1')
    os.environ.setdefault('TRANSFORMERS_OFFLINE', '1')
    if os.name != 'nt':
        os.environ.setdefault('PYTORCH_CUDA_ALLOC_CONF', 'expandable_segments:True')
    runtime = ROOT / 'vendor/hunyuan3d'
    weight = args.weights
    config = runtime / 'config.yaml'
    source = json.loads((runtime / 'weight_source.json').read_text(encoding='utf-8'))
    if not weight.is_file() or weight.stat().st_size != source['size']:
        raise ValueError('The complete verified model weights are required.')
    print('VERIFY_WEIGHTS', flush=True)
    if digest(weight) != source['sha256']:
        raise ValueError('Model SHA-256 verification failed.')
    if args.output.exists():
        raise ValueError('Use a fresh output directory to preserve every trial.')
    sys.path.insert(0, str(runtime))
    import torch
    from PIL import Image
    from hy3dgen.shapegen import Hunyuan3DDiTFlowMatchingPipeline
    if not torch.cuda.is_available():
        raise RuntimeError('This local trial requires CUDA.')
    torch.set_num_threads(8)
    torch.manual_seed(args.seed)
    torch.cuda.reset_peak_memory_stats()
    names = json.loads((args.inputs / 'views.json').read_text(encoding='utf-8'))['views']
    if set(names) != {'front', 'left', 'back', 'right'}:
        raise ValueError('views.json must specify four ordered camera directions.')
    for name in names.values():
        if not (args.inputs / name).resolve().is_relative_to(args.inputs.resolve()):
            raise ValueError('Each view must be inside the input directory.')
    if args.swap_sides:
        names['left'], names['right'] = names['right'], names['left']
    images = {key: Image.open(args.inputs / name).convert('RGBA') for key, name in names.items()}
    args.output.mkdir(parents=True)
    report = dict(status='loading', accepted=False, method='Hunyuan3D-2mv Turbo',
                  geometry_is_ai_inferred=True, physical_scale_calibrated=False,
                  source=json.loads((runtime / 'upstream.json').read_text(encoding='utf-8')),
                  model_sha256=source['sha256'], config_sha256=digest(config),
                  local_decoder_patches=json.loads((runtime / 'local_patches.json').read_text(encoding='utf-8')),
                  input_images={k: dict(file=str(args.inputs / n), sha256=digest(args.inputs / n))
                                for k, n in names.items()},
                  seed=args.seed, steps=args.steps, guidance=args.guidance,
                  resolution=args.resolution, cpu_offload=True,
                  gpu=torch.cuda.get_device_name(), torch_version=torch.__version__,
                  limitations=['Learned shape completion is not measured depth.',
                               'No animation rig or deformation topology is generated.',
                               'Appearance and geometry require multi-view review.'])
    def save():
        (args.output / 'generation_report.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    save()
    start = time.perf_counter()
    print('LOAD_VERIFIED_MODEL', flush=True)
    pipe = Hunyuan3DDiTFlowMatchingPipeline.from_single_file(
        str(weight), str(config), device='cpu', dtype=torch.float16, use_safetensors=True)
    # This upstream revision exposes the offload method but omits the Diffusers
    # component registry used inside it. Supply only the actual loaded modules.
    if not hasattr(pipe, 'components'):
        pipe.components = {name: getattr(pipe, name) for name in ('conditioner', 'model', 'vae')}
    pipe.enable_model_cpu_offload(device='cuda:0')
    # The upstream CPU loader leaves this at CPU after installing CUDA hooks.
    # Set the execution device without moving all three modules into 8 GB VRAM.
    pipe.device = torch.device('cuda:0')
    pipe.vae.enable_flashvdm_decoder(enabled=True, adaptive_kv_selection=False, mc_algo='mc')
    if args.preview_dir:
        # Show the exact upstream preprocessor output, not an approximation of the input.
        import numpy as np
        preview = args.preview_dir
        preview.mkdir(parents=True, exist_ok=True)
        rgb, alpha = pipe.image_processor.load_image(images['front'], border_ratio=.15, to_tensor=False)
        Image.fromarray(rgb).save(preview/'ai_input.png')
        stages_path = preview/'stages.json'
        stages = json.loads(stages_path.read_text(encoding='utf-8')) if stages_path.exists() else {'stages':[]}
        stages['stages'] = [r for r in stages['stages'] if r['key'] != 'ai_input']
        stages['stages'].append(dict(key='ai_input', label='实际 AI 输入', file='processing/ai_input.png',
            detail='模型实际使用的 512×512 正面输入：主体居中、留边、白底。未做额外锐化或灰度化。'))
        stages_path.write_text(json.dumps(stages, ensure_ascii=False, indent=2), encoding='utf-8')
        print('AI_INPUT_READY', flush=True)
    report.update(status='generating', load_seconds=time.perf_counter() - start)
    save()
    def progress(step, timestep, output):
        print('DIFFUSION_STEP', step + 1, flush=True)
    meshes = pipe(image=images, num_inference_steps=args.steps, guidance_scale=args.guidance,
                  generator=torch.Generator(device='cuda').manual_seed(args.seed),
                  octree_resolution=args.resolution, num_chunks=4096, mc_algo='mc',
                  callback=progress, callback_steps=1)
    print('MESH_EXTRACTED', flush=True)
    mesh = meshes[0]
    import numpy as np
    if mesh is None or not len(mesh.faces) or not np.isfinite(mesh.vertices).all():
        raise RuntimeError('The model produced an empty surface.')
    mesh.export(args.output / 'generated_raw.ply')
    mesh.export(args.output / 'generated_raw.glb')
    report.update(status='generated_pending_review', seconds=time.perf_counter() - start,
                  vertices=len(mesh.vertices), faces=len(mesh.faces),
                  watertight=bool(mesh.is_watertight), bounds=mesh.bounds.tolist(),
                  peak_gpu_allocated_gb=torch.cuda.max_memory_allocated() / 1024**3,
                  raw_ply_sha256=digest(args.output / 'generated_raw.ply'))
    save()
    print('AI_SHAPE_COMPLETE', json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--weights', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--preview-dir', type=Path)
    parser.add_argument('--seed', type=int, default=7)
    parser.add_argument('--steps', type=int, default=8)
    parser.add_argument('--resolution', type=int, default=256)
    parser.add_argument('--guidance', type=float, default=5.0)
    parser.add_argument('--swap-sides', action='store_true')
    args = parser.parse_args()
    output_preexisted = args.output.exists()
    try:
        main(args)
    except Exception as exc:
        path = args.output / 'generation_report.json'
        if not output_preexisted and path.is_file():
            failed = json.loads(path.read_text(encoding='utf-8'))
            failed.update(status='failed', accepted=False, error=repr(exc))
            path.write_text(json.dumps(failed, ensure_ascii=False, indent=2), encoding='utf-8')
        raise
