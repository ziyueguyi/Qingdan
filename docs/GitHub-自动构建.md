# GitHub 自动构建 EXE / APK

源码仓库：https://github.com/ziyueguyi/Qingdan

## 触发方式

向 main 推送提交或推送 v 开头的版本标签时，GitHub Actions 自动在 Windows x64 上构建。也可打开仓库 Actions，选择 `build-windows-exe`，点击 Run workflow 手动构建。对 main 的 Pull Request 同样运行检查。

流程依次安装 Python 3.14 与 Node 22、安装开发和桌面依赖、运行全量测试、PyInstaller 打包、检查 42 MB 体积门禁、实测冻结 EXE/CLI 和远程 API，最后上传产物。任一步失败都不会上传不完整的软件包；测试报告会单独留存。

Android 并行使用 JDK 21、Gradle 9.6、AGP 9.5、Chaquopy 17.2 和 Python 3.13，安装稳定版 Android 17 QPR2（SDK 37.2，Build Tools 37.0.0）。
先打包 arm64 发布版及 x86_64 测试版，再在 16 KB 页大小的 Android 17 QPR2 模拟器验证离线业务、Keystore 和 WebView 页面。
模拟器配置 4 GB 内存；这是运行验收，不能用单纯生成 APK 代替。

签名使用仓库 Actions Secrets 中的 `ANDROID_KEYSTORE_BASE64`、`ANDROID_STORE_PASSWORD`、`ANDROID_KEY_ALIAS`、`ANDROID_KEY_PASSWORD`。
私钥在本地 `.private/android-signing` 备份，禁止入 Git 或上传为公开产物；持续复用同一身份以便覆盖升级。
未配置签名时仅做未签名验证，不发布 Release。Windows 导出的 PKCS12 在 CI 仅校正 Java 别名，不更换证书或私钥。

## 下载

正式下载入口为仓库的 Releases 页面：https://github.com/ziyueguyi/Qingdan/releases 。打开最新版本，在 Assets 中选择名称含 windows-x64 的 ZIP；Source code 是源码，不是运行包。

下载内容包括带版本号和提交摘要的 `Qingdan-版本-windows-x64-提交.zip`、SHA-256 校验文件及体积报告。解压 ZIP，保留整个 Qingdan 文件夹并运行其中的 `Qingdan.exe`。脚本入口为同目录的 `QingdanCLI.exe`；不要只拿走单个 EXE。

Android 下载名称含 `android-arm64` 的 APK，在手机安装；旁边提供 SHA-256 校验文件。
Windows 与签名 Android 构建、运行验证均成功后，才会统一发布到 Releases。

main 或版本标签构建成功后自动发布应用版本对应的 Release，手动构建 main 也支持发布。首次发布时先建立草稿、上传完整资产，再公开显示；发布阶段失败可重跑。已公开的同版本不会被覆盖，发新版应同时增加 pyproject.toml 和 src/qingdan/__init__.py 中的版本号；若推送标签，其名称必须与应用版本 vX.Y.Z 一致。

Actions 的 Artifacts 仍保留 30 天，作为开发验收备份；正式用户从 Releases 下载，不依赖 Artifact 的保留期限。

仓库不保存数据库、凭据或构建产物；构建包不会包含本地使用数据，也不会指向开发电脑的数据目录。已有用户更新时先退出旧程序，保留现有数据目录。这个流程生成便携目录包，不是安装程序，也不增加代码签名。

工作流位于 `.github/workflows/build.yml`。测试/构建阶段只读源码；独立发布阶段使用 contents: write 和 GitHub 自动提供的临时令牌创建版本及上传资产，不需要账户密码或个人 Token。Pull Request 不执行发布阶段。

相关 GitHub 官方说明：[Actions 快速入门](https://docs.github.com/en/actions/get-started/quickstart)、[构建产物上传与下载](https://docs.github.com/en/actions/tutorials/store-and-share-data)。
