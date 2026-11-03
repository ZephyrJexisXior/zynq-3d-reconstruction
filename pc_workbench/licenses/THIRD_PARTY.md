# Third-party components

- Hunyuan3D-2 shape generation: Tencent-Hunyuan/Hunyuan3D-2, commit `f8db63096c8282cb27354314d896feba5ba6ff8a`. Only the shape inference package is included. Retain `Hunyuan3D_LICENSE.txt` and `Hunyuan3D_NOTICE.txt`. Model distribution has its own conditions; this is not an MIT relicensing.
- Local decoder modifications: float32 spacing/origin, trilinear coarse-field preservation, grid-cell normalization in marching cubes. The two modified files carry notices; details and regression results are in `vendor/hunyuan3d/local_patches.json` and `decoder_test.json`. The wrapper also supplies the CPU offload component registry and CUDA execution device.
- IS-Net: https://github.com/xuebinqin/DIS ; ONNX distribution from https://github.com/danielgatis/rembg/releases/download/v0.0.0/isnet-general-use.onnx . Retain IS-Net and rembg license files. Source and checksum are in `config/isnet_source.json`; weights are not in the source distribution.
- model-viewer: https://github.com/google/model-viewer . Retain `model-viewer_LICENSE.txt`; bundled code also contains Lit BSD-3-Clause and Three.js MIT notices, retained in the bundle and accompanying license files. Runtime viewer asset copied unchanged from the working project.
- FPGA packet assembly and receiver: extracted from the user's existing PC receiver. Local changes include package imports, dual-camera metadata validation, late metadata recovery, and complete-round auto-import; the desktop wrapper sends no board commands.

This local tool is supplied by the local project operator. Tencent is not affiliated with, sponsoring, or endorsing this application. If distributing this as a product or service, replace this operator wording with the actual provider's full legal name as required by the upstream agreement. No public publication was performed in this task.

- Optional learned direction matching: SuperPoint + LightGlue ONNX from https://github.com/fabio-sim/LightGlue-ONNX . Model provenance is in `config/lightglue_source.json`; original component notices and licenses are retained in `licenses/learned_matching/`. Weights are external and not included in the source ZIP.
- Optional COLMAP: https://github.com/colmap/colmap . An independently installed executable is invoked for registration; no COLMAP binary is redistributed in this ZIP.
