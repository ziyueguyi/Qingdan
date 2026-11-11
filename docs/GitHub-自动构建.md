# GitHub 自动构建 EXE

源码仓库：https://github.com/ziyueguyi/Qingdan

## 触发方式

向 main 推送提交或推送 v 开头的版本标签时，GitHub Actions 自动在 Windows x64 上构建。也可打开仓库 Actions，选择 `build-windows-exe`，点击 Run workflow 手动构建。对 main 的 Pull Request 同样运行检查。

流程依次安装 Python 3.14 与 Node 22、安装开发和桌面依赖、运行全量测试、PyInstaller 打包、检查 42 MB 体积门禁、实测冻结 EXE/CLI 和远程 API，最后上传产物。任一步失败都不会上传不完整的软件包；测试报告会单独留存。

## 下载

打开 https://github.com/ziyueguyi/Qingdan/actions ，进入成功的运行记录，下载底部 Artifacts 中的 `Qingdan-windows-x64`。GitHub 通常要求登录才能下载 Artifact。产物保留 30 天。

下载内容包括带版本号和提交摘要的 `Qingdan-版本-windows-x64-提交.zip`、SHA-256 校验文件及体积报告。先解开 Artifact 的外层包，再解开软件 ZIP，保留整个 Qingdan 文件夹并运行其中的 `Qingdan.exe`。脚本入口为同目录的 `QingdanCLI.exe`；不要只拿走单个 EXE。

仓库不保存数据库、凭据或构建产物；构建包不会包含本地使用数据，也不会指向开发电脑的数据目录。已有用户更新时先退出旧程序，保留现有数据目录。这个流程生成便携目录包，不是安装程序，也不增加代码签名。

工作流位于 `.github/workflows/build.yml`，默认仅有读取源码权限。没有使用账户密码或个人 Token；未来提交仍可使用正常 Git 客户端。

相关 GitHub 官方说明：[Actions 快速入门](https://docs.github.com/en/actions/get-started/quickstart)、[构建产物上传与下载](https://docs.github.com/en/actions/tutorials/store-and-share-data)。
