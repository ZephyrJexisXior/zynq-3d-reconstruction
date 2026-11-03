# 第三方来源与许可范围

根目录MIT仅覆盖项目自编贡献，不改变下列组件的原授权。

| 组件 | 分发方式及依据 |
| --- | --- |
| Hunyuan3D-2形状推理 | 保留pc_workbench/vendor/hunyuan3d、上游固定提交、本地修改及licenses/Hunyuan3D_LICENSE.txt、NOTICE；不是MIT重授权 |
| IS-Net / rembg | 保留下载来源、哈希和Apache/MIT原许可；不分发权重 |
| LightGlue / SuperPoint | 原许可保留在pc_workbench/licenses/learned_matching；SuperPoint有单独研究用途限制；不分发匹配权重 |
| model-viewer / Lit / Three.js | 预览资源及其版权、许可原样保留，见pc_workbench/licenses |
| NumPy / SciPy / OpenCV / Pillow / PyTorch / xatlas等 | 通过依赖管理安装，遵守各自原许可，不复制环境和DLL |
| COLMAP | 用户另行安装，未附带二进制；来源https://github.com/colmap/colmap |
| AMD/Xilinx Vivado与标准IP | 由合法安装的Vivado生成；未附带工具、IP缓存、生成HDL或授权文件 |
| 正点原子基础IP | ov5640_capture_data与rgb2lcd源码标有All rights reserved，未发现明确开源授权，因此不随本包复制；使用本地导入脚本接入合法副本 |

FPGA硬件配置以厂家开发板参考设计为基础；本包公开自编RTL及工程连接配置，
不宣称PS7/VDMA等标准IP或厂家摄像头/LCD模块为自行设计。
本次打包未删除或改写上游版权声明。pc_workbench/licenses/THIRD_PARTY.md保留其详细来源。
权重的独立下载不改变其适用条款；本仓库不向第三方授予自己不持有的权利。
