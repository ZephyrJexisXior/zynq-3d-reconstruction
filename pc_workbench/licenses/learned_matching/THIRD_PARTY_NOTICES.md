# Learned matching research experiment

Runtime model: `superpoint_lightglue_pipeline.onnx`, LightGlue-ONNX release v2.0.
Official release URL and SHA256 are recorded in `model_provenance.json`.

LightGlue and LightGlue-ONNX: Apache License 2.0. Original licenses are preserved
in `licenses/LightGlue-LICENSE.txt` and `licenses/LightGlue-ONNX-LICENSE.txt`.
Upstream code and documentation are unmodified reference files; the local
matching/triangulation/PnP runner is `tools/learned_rig_registration.py`.

SuperPoint is subject to its separate Magic Leap noncommercial research license,
including pretrained weights and inference implementation. See the complete
`licenses/SuperPoint-LICENSE.txt` and the upstream `LightGlue_README.md` license
section. Do not treat the combined ONNX artifact as exclusively Apache-2.0.

Sources:
- https://github.com/cvg/LightGlue
- https://github.com/fabio-sim/LightGlue-ONNX
- https://github.com/magicleap/SuperPointPretrainedNetwork

The experiment uses learned image correspondences only. It does not synthesize
geometry, textures, or evidence for unseen regions.

Desktop integration note (2026-11-02): original experimental notice retained above. In this package, provenance is `config/lightglue_source.json`; license files are beside this notice. The new runtime integration is `studio/learned_directions.py` and `tools/select_directions.py`. No matching model weights are bundled in the source ZIP.
