# GitHub 自动构建 EXE

源码仓库：https://github.com/ziyueguyi/Qingdan

## 触发方式

向 main 推送提交或推送 v 开头的版本标签时，GitHub Actions 自动在 Windows x64 上构建。也可打开仓库 Actions，选择 `build-windows-exe`，点击 Run workflow 手动构建。对 main 的 Pull Request 同样运行检查。

流程依次安装 Python 3.14 与 Node 22、安装开发和桌面依赖、运行全量测试、PyInstaller 打包、检查 42 MB 体积门禁、实测冻结 EXE/CLI 和远程 API，最后上传产物。任一步失败都不会上传不完整的软件包；测试报告会单独留存。

## 下载

正式下载入口为仓库的 Releases 页面：https://github.com/ziyueguyi/Qingdan/releases 。打开最新版本，在 Assets 中选择名称含 windows-x64 的 ZIP；Source code 是源码，不是运行包。

下载内容包括带版本号和提交摘要的 `Qingdan-版本-windows-x64-提交.zip`、SHA-256 校验文件及体积报告。解压 ZIP，保留整个 Qingdan 文件夹并运行其中的 `Qingdan.exe`。脚本入口为同目录的 `QingdanCLI.exe`；不要只拿走单个 EXE。

main 或版本标签构建成功后自动发布应用版本对应的 Release，手动构建 main 也支持发布。首次发布时先建立草稿、上传完整资产，再公开显示；发布阶段失败可重跑。已公开的同版本不会被覆盖，发新版应同时增加 pyproject.toml 和 src/qingdan/__init__.py 中的版本号；若推送标签，其名称必须与应用版本 vX.Y.Z 一致。

Actions 的 Artifacts 仍保留 30 天，作为开发验收备份；正式用户从 Releases 下载，不依赖 Artifact 的保留期限。

仓库不保存数据库、凭据或构建产物；构建包不会包含本地使用数据，也不会指向开发电脑的数据目录。已有用户更新时先退出旧程序，保留现有数据目录。这个流程生成便携目录包，不是安装程序，也不增加代码签名。

工作流位于 `.github/workflows/build.yml`。测试/构建阶段只读源码；独立发布阶段使用 contents: write 和 GitHub 自动提供的临时令牌创建版本及上传资产，不需要账户密码或个人 Token。Pull Request 不执行发布阶段。

相关 GitHub 官方说明：[Actions 快速入门](https://docs.github.com/en/actions/get-started/quickstart)、[构建产物上传与下载](https://docs.github.com/en/actions/tutorials/store-and-share-data)。
