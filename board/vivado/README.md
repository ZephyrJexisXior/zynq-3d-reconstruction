# 本地位流放置位置

此目录不随源码分发已编译位流。通过仓库根目录tools/import_private_dependencies.py
导入已部署的camera_5mp_quality.bit及同名.hwh；可选.ltx供ILA使用。
必须成对核对位流与HWH。其SHA-256在fpga/private_dependencies.json中。
重新构建版本须重新做静止帧和板端验收，不能用既有记录代替。
