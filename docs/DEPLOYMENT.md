# 板端安装和使用

## 环境与连接

已有PYNQ 2.7系列系统、PYNQ虚拟环境、NumPy与OpenCV。板端Linux USB Host需支持UVC。
不需重刷PYNQ镜像，不改BOOT.BIN。把board目录完整复制到开发板，例如~/qian-vision。
导入已部署位流及同名HWH到board/vivado，保留config下设备树文件。

主摄OV5640保持开发板摄像头接线；高位USB摄像头接开发板USB Host。
舵机V7信号、独立稳定5V，电源负极与FPGA GND共地。KEY0对应PL按键。
本包不恢复A4988/步进电机，也不改动已验证舵机控制。

## 静止检查与启动

先查看计划，不加载硬件、不触发旋转：

```sh
cd ~/qian-vision
python3 pynq_app/main_front18_top4.py --show-plan
```

确认使用板端PYNQ的Python环境。在没有其他采集服务运行时安装服务：

```sh
sudo sh install_front18_top4_service.sh
sudo systemctl enable --now fpga-vision-front18-top4.service
sudo journalctl -u fpga-vision-front18-top4.service -f
```

安装脚本会拒绝覆盖已有同名服务，也会拒绝其他采集服务仍活动的情况；
已有开发板无需重复安装。日志显示Camera ready和waiting for KEY0后再松开并短按KEY0。
每轮18张主摄、4张顶部；舵机停止时仍输出1500µs中性PWM，结束后等待下一次按键。
质量失败会保留不完整轮次并等待新按键，不自动重复旋转。

默认电脑192.168.2.1，开发板192.168.2.99，UDP5012、控制5011。
这些是示例局域网地址，按实际网络更改命令参数与服务ExecStart，电脑接收端保持一致。
原服务安装脚本保留PYNQ Python路径/usr/local/share/pynq-venv/bin/python3；其他镜像须核实环境。

## 数据位置

- captures_front18_top4/front/round_*/images/A0000.png…A0017.png：18张无损主摄。
- 对应front_manifest.json：质量、完整性、位置、SHA-256。
- captures_front18_top4/top/round_*/position_000、004、009、013：顶部原图、PL输出、主摄引用和匹配。
- 对应usb_top_manifest.json：4张顶部清单。

只有capture_complete及质量检查通过的轮次作为正式输入。优先复制完整板端轮次，
不用UDP预览到达数量替代板端无损保存证据。电脑工作台可离线导入主摄images目录。
顶部补拍不是自动补洞成功保证，单独保留并在有可靠位姿时使用。

此源码准备任务没有连接或重烧开发板；所有部署结论均来自2026-11-02的既有记录。
