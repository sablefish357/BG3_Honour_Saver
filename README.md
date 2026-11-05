# BG3 Honour Saver

[English](#english) · [简体中文](#简体中文)

## English

### Abstract

BG3 Honour Saver is a Windows utility for recording successive versions of a selected *Baldur’s Gate 3* Honour Mode save. The game maintains a working save under `Story`. The utility observes that save and copies stable versions into a separate backup library. A browser interface presents the captured versions and provides ZIP export and restoration.

### Requirements and use

On Windows with [uv](https://docs.astral.sh/uv/) installed, run the following commands from the repository directory. `uv sync` prepares the project's Python environment and installs its dependencies.

```powershell
uv sync
uv run bg3-honour-saver
```

The program opens the [local dashboard](http://127.0.0.1:8765/) once the server is ready.

1. **Open a backup library.** Select an existing library or an empty folder to initialize one. Neither the library nor the game’s `Story` directory can contain the other.
2. **Select a save.** The application looks for `Story` under `%LOCALAPPDATA%\Larian Studios\Baldur's Gate 3\PlayerProfiles\Public\Savegames\Story`. You can choose a different directory through the Windows folder dialog. A slot appears in the list once it contains `HonourMode.lsv`.
3. **Start watching.** Select one slot and leave the program running while you play. The browser tab can be closed. Each captured version appears in the history with its size, the game file’s last modification time, the backup time, and a thumbnail when the game provides one.
4. **Manage the history.** Set the backup limit to retain the newest N versions across the library. A value of `0` keeps all versions. Saving the limit immediately removes excess versions, oldest first. Backups made before restoration also count toward the limit. **Delete** removes an individual backup in one click. The dashboard refreshes every five seconds and provides a **Refresh** button.
5. **Download or restore a version.** Download exports that version as a ZIP. Before using **Restore**, return to the game’s main menu or close the game, then confirm the destination in the dashboard. If the destination already contains a slot, the application backs it up before replacement.

### Design and data model

FastAPI serves the local dashboard through Uvicorn. Jinja templates render the English and Chinese interface. Watchdog reports changes in `Story`, and the backup worker responds to changes under the selected slot. It waits for a quiet interval and periodically checks the slot to account for missed notifications. Before indexing a version, the worker checks that the source file inventory is stable across the copy and that the copied `HonourMode.lsv` matches the source fingerprint. A SHA-256 comparison suppresses duplicate versions when consecutive observations have identical main save contents.

The library contains the following files and directories.

| Item | Contents |
| --- | --- |
| `settings.json` | The selected `Story` path, the selected slot, whether watching is enabled, and the library's `max_backups` limit. Existing settings without this field use `0` for unlimited. |
| `backups.sqlite3` | The version index, including timestamps, sizes, checksums, and paths relative to the library. It also records the current fingerprint for each slot. |
| `backups/<game-id>/<version-id>/` | The files from one captured version, including the game thumbnail when present. A ZIP is generated on download. |

The restore operation verifies the selected version against its recorded checksum and stages a copy in the target `Story` directory before replacing the live slot. If replacement fails, it attempts to put the original slot back. The library can be moved as a unit because the index uses relative paths. After a move, open the library at its new location and select `Story` again if the saved game path has changed.

### Scope and limitations

The service binds to `127.0.0.1` and watches one selected Honour Mode slot at a time. The history records the stable versions the program observes. If the game overwrites a version before it can be copied, that version will be absent from the history. Deletion and retention remove both the version record and its archived files. The dashboard can use externally hosted fonts and HTMX, with local fonts and a bundled refresh script as fallbacks.

### License

Distributed under the [MIT License](LICENSE).

## 简体中文

### 摘要

BG3 Honour Saver 是一个 Windows 本地工具，用于记录《博德之门 3》指定荣誉模式存档的多个版本。游戏在 `Story` 中维护当前存档，工具监听该存档并将观察到的稳定版本复制到独立资料库。用户可在网页中查看历史、导出 ZIP 或恢复某个版本。

### 运行环境与操作流程

在 Windows 上安装 [uv](https://docs.astral.sh/uv/) 后，在仓库目录运行以下命令。`uv sync` 会准备项目的 Python 环境并安装依赖。

```powershell
uv sync
uv run bg3-honour-saver
```

服务就绪后，程序会自动打开一次[本地控制台](http://127.0.0.1:8765/)。

1. **打开资料库。** 可选择已有资料库，也可选择空文件夹进行初始化。资料库与游戏的 `Story` 目录不能互相包含。
2. **选择存档。** 程序默认查找 `%LOCALAPPDATA%\Larian Studios\Baldur's Gate 3\PlayerProfiles\Public\Savegames\Story`。如有需要，可通过 Windows 文件夹选择器更换路径。档位中出现 `HonourMode.lsv` 后，就可以在列表中选择它。
3. **开始监听。** 选择一个档位，游玩时保持程序运行。浏览器标签页可以关闭。历史记录会显示每个备份的大小、游戏存档文件的最后修改时间、备份时间，以及游戏提供的缩略图。
4. **管理历史。** 可设置整个资料库保留最新 N 个版本，`0` 表示不限。保存上限后立即从最旧版本开始清理，恢复前备份也计入数量。每条备份旁的「删除」按钮可一键删除该版。页面每 5 秒自动刷新，也提供「刷新」按钮。
5. **下载或恢复。** 下载会将指定版本导出为 ZIP。恢复前，请先退到游戏主菜单或退出游戏，再在控制台确认恢复位置。如果目标位置已有该档位，程序会先备份当前档，再进行替换。

### 系统设计与数据组织

本地服务由 Uvicorn 和 FastAPI 提供，网页使用 Jinja 模板渲染中英文界面。Watchdog 接收 `Story` 的文件变化通知，后台任务处理所选档位的变化。程序等文件变化暂时停止后再复制，同时定期检查档位，以补偿可能遗漏的通知。写入版本索引前，程序检查复制期间的文件清单是否稳定，并核对副本中的 `HonourMode.lsv` 与源文件的指纹。主存档的 SHA-256 指纹用于识别内容相同的连续版本。

资料库包含以下文件和目录。

| 项目 | 内容 |
| --- | --- |
| `settings.json` | 保存所选 `Story` 路径、档位、监听开关和资料库的 `max_backups` 上限。旧设置缺少该字段时按 `0` 处理，表示不限。 |
| `backups.sqlite3` | 记录版本时间、大小、校验值和相对资料库的路径，同时保存每个档位的当前指纹。 |
| `backups/<游戏 ID>/<版本 ID>/` | 保存该版本的原始文件，包括游戏提供的缩略图。点击下载时生成 ZIP。 |

恢复时，程序先核对所选版本的校验值，再将副本写入目标 `Story` 下的临时目录，最后替换游戏档位。替换失败时，程序会尝试将原档放回原位。索引使用相对路径，因此资料库可以整体移动。移动后，在网页中打开新位置即可。如果游戏存档路径也发生变化，请重新选择 `Story`。

### 适用范围与限制

服务仅在 `127.0.0.1` 上监听，同一时间处理一个选中的荣誉档。历史记录反映程序实际观察到的稳定版本。如果游戏在复制开始前覆盖了某一版，历史中就不会出现该版本。手动删除和上限清理会同时移除版本记录与对应的备份文件。网页可加载外部字体和 HTMX，加载失败时分别使用本机字体和内置刷新脚本。

### 许可证

本项目采用 [MIT 许可证](LICENSE)。
