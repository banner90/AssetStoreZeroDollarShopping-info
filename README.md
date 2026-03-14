# Unity AssetStore资源查看器

在本地快速浏览已下载的 Unity 素材包，并一键获取每个包的介绍、版本、更新说明等信息。不用反复打开 Unity 编辑器或网页，就能查阅手头素材的完整说明。

---

## 工具能做什么

### v1.0.0

- **浏览已下载素材**：按下载目录列出所有 `.unitypackage`，支持搜索过滤
- **查看包详情**：选中后直接显示 Overview 描述、版本、出版商、包大小、更新说明等
- **获取包商店信息**：从 Unity API 批量获取包信息并保存到 `metadata` 元数据库目录，之后离线也能查看

### v1.1.0

- **深色主题**：支持明/暗主题切换；点击界面「明/暗」切换
- **筛选**：支持按类型（category）、发行商（publisher）以及「搜索我的资源」过滤列表；需先执行「获取包商店信息」
- **导入**：可在详情区使用「在 Unity 中打开」一键导入当前运行的 Unity 编辑器

### v1.2.0

- **包详情链接跳转**：详情中的超链接点击后使用系统默认浏览器打开，方便访问文档、演示、论坛等（如果使用源码python运行该工具，详情界面只显示简单文本没有跳转链接等内容时，请把工具github文件中的plugin.json文件下载到与执行的py文件同目录，并查看"插件"页签）
- **折叠内容获取**：获取并展示官网的折叠区块，包括概述、可编程渲染管线 (SRP) 兼容性、技术细节等

### v1.3.0

- **Emoji 显示**：包详情中的 emoji 正确展示（如果使用源码python运行该工具，详情界面没有显示emoji内容时，请把工具github文件中的plugin.json文件下载到与执行的py文件同目录，并查看"插件"页签）
- **分类筛选优化**：类型筛选改为可折叠树形结构，支持按父级归类展开子项勾选
- **单个包商店信息获取**：支持单个包单独商店信息获取

### v1.3.3

- **手动映射**：针对包列表未识别到已存在的文件（仍显示为红色），可通过「手动映射」按钮进行人工确认，将 packageId 与真实文件名关联，需用户自行选择对应文件。

### v1.3.5

- **大中华区发行商资源筛选**：筛选面板新增「仅显示大中华区出版商的资源」选项，勾选后优先筛选出大中华区资源（在 assets_removed_march31.json 中的资源，请把工具github文件中的assets_removed_march31.json下载到与exe同目录即可生效该功能。源码python运行该工具，则放置到与py文件同目录即可）
- **发行商列表搜索优化**：发行商筛选列表默认显示前 20 个常见发行商，修复搜索功能，输入关键词后方便快速查找特定发行商

适用场景：整理素材库、选包时快速查说明、备份包元数据等。

获取包商店信息通常 1～2 小时即可完成，请保证已经执行过 unity_assets_downloader.py 的「获取已购买资产列表」阶段。可以优先获取包商店信息提前浏览资源列表，等待 unity_assets_downloader.py 下载结束。

---

## 如何使用

### 1. 获取 exe

从本项目的 Releases 页面下载 `AssetStoreInfo.exe`。

### 2. 放置位置

将 `AssetStoreInfo.exe` 放到 **[AssetStoreZeroDollarShopping](https://github.com/UlyssesWu/AssetStoreZeroDollarShopping) 项目根目录**，与下面这些文件同一级：

```
AssetStoreZeroDollarShopping/
├── asset_store_config.json
├── purchases_snapshot.json
├── cookie.txt
├── unity_assets_downloader.py
├── AssetStoreInfo.exe   ← 放在这里
└── metadata/            ← 包商店信息（运行后自动创建）
```

### 3. 运行

双击 `AssetStoreInfo.exe` 打开。

- **包列表查看**：左侧列出 download_dir 下的素材，可搜索；选中后右侧显示详情

  ![包列表查看](docs/sourcelistpage.png)

- **获取包商店信息**：在第二个标签页设置数量限制（0 表示全部），点击「开始获取」即可

  ![获取包商店信息](docs/metadatagetpage.png)

---

## 依赖说明

本工具需配合 [AssetStoreZeroDollarShopping](https://github.com/UlyssesWu/AssetStoreZeroDollarShopping) 使用，并从其项目目录中读取以下文件：

| 文件 | 说明 |
|------|------|
| `purchases_snapshot.json` | 已购包列表，通常由 `unity_assets_downloader.py` 生成 |
| `asset_store_config.json` | bearer_token、超时等，手动配置或主脚本自动创建 |
| `cookie.txt` | 浏览器登录态 Cookie，需手动从浏览器获取 |

请先 clone [AssetStoreZeroDollarShopping](https://github.com/UlyssesWu/AssetStoreZeroDollarShopping) 并按说明完成配置，再使用本工具。

---

## 致谢

感谢 [@UlyssesWu](https://github.com/UlyssesWu) 及其项目 [AssetStoreZeroDollarShopping](https://github.com/UlyssesWu/AssetStoreZeroDollarShopping)，为本工具提供了配置、已购列表和 Cookie 的数据来源。

本次合作已受到原作者许可。

---

## 声明

本工具仅用于保存和备份用户本人已拥有的库存资源信息，请勿滥用或用于侵害他人权益。

因不当使用本工具所造成的任何后果，由使用者自行承担。
