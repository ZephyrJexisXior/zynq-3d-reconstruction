# FPGA 建模工作台

Windows 本地运行的照片接收与三维建模工作台。支持 FPGA 以太网收图、已有照片导入、主体分割、四方向 AI 灰模、相机配准、照片颜色贴图和模型导出。

## 当前功能

- 接收现有 FPGA 图像协议，检查整轮采集清单、位置和照片哈希；USB 串口用于连通检查，照片通过 UDP 传输。
- IS-Net 多范围前景识别与 OpenCV GrabCut 原图边缘校正；保留浅色主体，提供主体框与画笔修正。
- 快速预选四方向、手选四方向或先做相机配准。FPGA 主摄默认以倒数第二张为正面；快速预选假定均匀转一圈，不等于实测位姿。
- Hunyuan3D-2mv Turbo 接收四张前景生成灰模；可让 CPU 照片配准与 GPU 形状推理并行，配准失败仍保留灰模。
- 使用 SuperPoint／LightGlue、COLMAP 和几何检查计算可靠照片位姿；用 xatlas 展开 UV，通过遮挡、观察角度与清晰度选取照片颜色。
- 保留灰模、仅照片颜色版、邻近颜色补全的展示版、纹理与覆盖图。补色为网格上的颜色插值，不是生成式纹理模型。
- 网页提供实际处理预览、阶段进度、日志、可旋转模型与 GLB／OBJ／PNG 下载。

## 包含与排除

本仓库包含源码、网页静态资源、安装入口、依赖清单、测试、默认配置、模型下载地址与哈希，以及第三方许可。

不包含照片、生成的模型、模型权重、虚拟环境、COLMAP 二进制、本机 `config/local.json`、运行日志或聊天记录。这是源码包，不是免安装程序。本子目录专注电脑端；FPGA 源码和板端程序分别见仓库根目录的 `fpga/`、`board/`。本子目录的 `vendor/fpga` 是电脑接收端代码。

## 安装与启动

需要 Windows、Python 3.11、兼容的 NVIDIA 驱动和 CUDA GPU。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Install.ps1
```

安装脚本创建 `runtime`，安装依赖，下载并校验 Hunyuan 与 IS-Net 权重并创建桌面入口。首次安装需要网络和数 GB 额外磁盘空间。新机器上的完整安装流程尚未逐台验证。

双击 `启动建模.cmd` 或桌面“FPGA 建模工作台”。默认网页端口为 **8790**，以实际启动提示为准。本机旧部署的 8795 等端口只是历史部署记录。

### 本机配置

`config/default.json` 提供可迁移默认值。按本机情况创建 `config/local.json` 覆盖参数，不要把此文件上传。本整合包默认接收端口为 `5012`，与 board/ 中的固化采集程序一致；使用旧版 `5002` 固件时请同步修改配置。板端目标 IP、电脑网卡地址、串口和接收端口须与实际设备一致。

照片贴图另需安装兼容的 COLMAP，并配置 `colmap` 路径。可使用下面的命令获取经过哈希校验的匹配模型，再配置 `matching_model` 路径：

```powershell
.\runtime\Scripts\python.exe tools\download_models.py --matching-only
```

缺少照片配准组件时，快速预选或手动模式仍可先生成灰模；取消“同时准备照片贴图”可完全跳过配准。上色需要可靠相机配准，失败保留灰模。硬件未接入时，可以离线导入已有照片。

## 工程目录

| 目录或文件 | 用途 |
| --- | --- |
| `server.py`、`studio/` | 本地服务、连接、图片处理、任务与贴图逻辑 |
| `web/`、`vendor/viewer/` | 网页交互和离线三维预览 |
| `tools/` | 分割、相机求解、形状生成、网格导出与权重下载 |
| `vendor/fpga/` | 电脑端 FPGA 图像接收与协议解析 |
| `vendor/hunyuan3d/` | 固定上游版本的形状推理代码与本地修改说明 |
| `config/` | 默认配置、协议说明、外部模型来源和 SHA-256 |
| `tests/` | 协议、任务、前景与纹理等回归测试 |
| `licenses/` | 各第三方组件的许可和声明 |

## 验证与边界

```powershell
.\runtime\Scripts\python.exe -m unittest discover -s tests -v
```

依赖不足的测试可能跳过，须核对测试输出。更多本机实测记录见 `验证说明.md`，历史说明见 `docs/本机使用与历史记录.md`。

最近一组18张主摄照片在 RTX 4060 Laptop GPU 上实测：灰模104.30秒可查看，全部处理237.03秒，导入另3.747秒；18/18照片配准。这里只是一次实验，不保证其他物体的性能和效果。四张直接进入 AI 形状网络，其余照片用于配准和贴图。

模型含 AI 推断，没有实测尺寸精度或自动骨骼绑定保证。细小边缘误分和局部纹理错位仍可能存在。支持双摄照片清单不等于已经实现标定双目深度或 FPGA SGM／TSDF 流程。

## 来源与许可

保留 `licenses/`、上游版权声明、Hunyuan 的 NOTICE 和本地修改记录；具体条款见各组件许可。整合第三方组件并不改变它们的许可，不将整个项目统一标为 MIT 或宣称全部算法为原创。

主要上游项目：
- https://github.com/Tencent-Hunyuan/Hunyuan3D-2
- https://github.com/xuebinqin/DIS
- https://github.com/danielgatis/rembg
- https://github.com/fabio-sim/LightGlue-ONNX
- https://github.com/colmap/colmap
- https://github.com/jpcy/xatlas
- https://github.com/google/model-viewer

上传方法见 `上传说明.md`。本次仅准备源码压缩包，没有执行 GitHub 发布。
