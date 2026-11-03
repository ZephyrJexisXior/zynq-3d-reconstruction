# FPGA源码与构建

目标xc7z020clg400-2，Block Design保存于Vivado2026.1。
设计包括OV5640 DVP接入、视频流桥、VDMA、共享ISP、双视角DDR回放/FAST9/BRIEF256候选匹配、
AXI-Lite控制与V7舵机KEY0停稳拍摄接口。USB协议在PS处理，不是PL直接实现USB Host。

## 先接入外部依赖

`ov5640_capture_data`、`rgb2lcd`是厂家保留版权IP，未包含其源码。
从合法持有的原工程ip_repo导入，脚本逐文件核对已知版本哈希：

```text
python tools/import_private_dependencies.py --ip-repo PATH_TO_AUTHORIZED_IP_REPO
```

对已有运行工程，也可在本地导入已部署位流，避免为了使用采集程序而重新构建：

```text
python tools/import_private_dependencies.py --overlay-dir PATH_TO_VERIFIED_VIVADO_FILES
```

导入文件被.gitignore排除，不应上传到GitHub。获得厂家其他版本并不保证与本配置兼容；
工具会拒绝哈希不一致，避免静默使用错误版本。应另行审查差异，不绕过检查冒充已验收。

## 构建

建议把仓库放在纯英文路径，例如D:/src/zynq-3d-reconstruction。

```text
vivado -mode batch -source fpga/scripts/build.tcl
```

新脚本在仓库build/fpga中建立独立项目，保留原始Block Design、RTL、引脚约束；
标准IP由合法安装的Vivado生成，不附带缓存、生成HDL或许可文件。
报告输出到build/fpga/reports，位流与HWH输出到build/fpga/artifacts。
时序或严重DRC不通过时拒绝输出为可用产物。

本次仅静态核对源码/依赖和离线测试，没有重新完成该移植构建入口的综合实现，
也不承诺重建字节与既有位流一致。第一次使用新构建结果须静止检查2592×1944完整帧、
时钟/PWM计时、USB回放与质量结果，再做KEY0轮次验证。
源码及引脚定义未因本次打包而改变。
