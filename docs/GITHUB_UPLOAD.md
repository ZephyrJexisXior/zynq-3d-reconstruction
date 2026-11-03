# GitHub上传说明

将本文件夹内容作为一个仓库的根目录；建议仓库名zynq-3d-reconstruction。
不要只上传ZIP，否则GitHub无法直接浏览代码和比较修改。
保留.gitignore、.gitattributes、LICENSE、第三方许可和SOURCE_MANIFEST.json。

在GitHub创建空仓库后，可在本目录执行：

```sh
git init -b main
git add .
git status --short
git commit -m "Publish Zynq multi-view reconstruction source"
git remote add origin YOUR_GITHUB_REPOSITORY_URL
git push -u origin main
```

示例URL是占位符，替换为自己的仓库。也可用GitHub Desktop执行同样操作。
导入的厂家IP、位流、权重、环境和照片均被忽略；本地运行后不要强制添加这些目录。
自编贡献采用范围限定的MIT，上游代码继续保留各自条款，不能把整个混合许可包描述为无限制MIT。

本次仅整理本地发布文件，没有创建远程仓库、上传文件或推送提交。
