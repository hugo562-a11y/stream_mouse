# Stream Mouse Overlay

Windows 直播 / 錄影用透明覆蓋層，專為 OBS Studio 設計。  
顯示鍵盤輸入、路徑動畫、放大鏡、遊標呼吸效果，全程不影響底層應用程式。

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-lightgrey)](https://github.com/hugo562-a11y/stream_mouse/releases)

---

## 下載（免安裝）

> **不需要安裝 Python**，下載後直接執行。

👉 **[前往 Releases 頁面下載最新版](https://github.com/hugo562-a11y/stream_mouse/releases/latest)**

| 檔案 | 說明 |
|------|------|
| `StreamMouse.exe` | 單一檔案，下載即用（防毒軟體可能警告，見下方說明） |
| `StreamMouse.zip` | 資料夾版，防毒誤判率較低，解壓縮後執行 `StreamMouse\StreamMouse.exe` |

同頁面也提供 **StreamMouse.streamDeckProfile** — 匯入後直接使用六個預設按鍵（見下方 Stream Deck 說明）。

> **⚠️ 防毒軟體誤判說明**  
> 部分防毒軟體（Windows Defender、Kaspersky 等）可能將此 EXE 標記為威脅並隔離。這是**誤判**，原因：  
> - 使用 PyInstaller 打包，結構與某些惡意程式相似  
> - 需要鍵盤/滑鼠 Hook 與螢幕截圖（正常功能，非惡意行為）  
> - 尚未購買 Microsoft 數位簽章憑證（原始碼完全公開可自行驗證）  
>
> **解決方式：**  
> - **被 Windows Defender 隔離**：「Windows 安全性」→「病毒與威脅防護」→「保護歷程記錄」→ 找到 StreamMouse → 「允許」  
> - **SmartScreen 警告**：點「更多資訊」→「仍要執行」  
> - **其他防毒軟體**：將 `StreamMouse.exe` 加入白名單／例外清單  
> - **自行驗證**：上傳到 [VirusTotal](https://www.virustotal.com) 查看各家引擎結果，或從原始碼自行編譯

---

## 從原始碼執行

**需求：** Python 3.11+、Windows 10/11

```powershell
pip install -r requirements.txt
python stream_mouse.py
```

### 自行編譯 EXE

```powershell
build.bat
```

輸出：`dist\StreamMouse.exe`

---

## 功能總覽

### 鍵盤 HUD
- 浮動視窗顯示最近輸入的按鍵（方向鍵、Enter、Esc 等特殊鍵皆顯示）
- 拖動 HUD 視窗到任意位置；滑鼠移開後背景自動隱藏
- 右上角顯示 OBS 連線狀態：`LIVE` / 場景名稱 / `OFFLINE`

### 路徑錄製與動畫

| 操作 | 說明 |
|------|------|
| 按錄製快速鍵（預設 `Ctrl+F1`） | 開始錄製滑鼠軌跡 |
| 再按一次 | 停止錄製，軌跡自動開始播放動畫 |
| 按中斷點快速鍵（預設 `Ctrl+F2`） | 錄製中插入中斷點 |
| 按重播快速鍵（預設 `Ctrl+F5`） | 所有軌跡從頭重新播放 |

播放效果：
- 彗星軌跡：尾端漸漸透明消失，頭部有方向圖示（飛機 / 箭頭 / 火箭），圖示尖端對齊遊標
- 遇到中斷點時暫停，停頓時間可在設定調整，再繼續下一段
- 中斷點位置顯示彩色編號圓點標記
- 可選擇錄製時同步截取螢幕，播放時呈現當時的完整畫面背景

### 放大鏡

| 操作 | 說明 |
|------|------|
| 放大鏡快速鍵（預設 `Ctrl+F3`） | 凍結畫面並進入放大模式 |
| 滾輪 | 縮放（1x–6x） |
| 滑鼠移動 | 在放大畫面上繪製筆跡 |
| `Ctrl+Z` / `Ctrl+Shift+Z` | 還原 / 重做筆跡 |
| 再按放大鏡快速鍵 | 截圖並儲存到桌面 |
| `Esc` | 退出放大模式 |

兩種放大樣式可選：
- **全螢幕**：整個畫面放大，準心跟著滑鼠移動
- **跟隨鏡頭 (Lens)**：圓形放大鏡跟著滑鼠，其他區域保持原樣

### 遊標呼吸效果
滑鼠游標周圍可顯示動態光圈，樣式可選：
雙圓圈 / 單圓圈 / 十字線 / 點+圓 / 無  
外框色與中心色可分別設定。

---

## 預設快速鍵

| 快速鍵 | 功能 |
|--------|------|
| `Ctrl+F1` | 錄製切換（開始 / 停止） |
| `Ctrl+F2` | 插入中斷點 |
| `Ctrl+F3` | 放大鏡 / 截圖 |
| `Ctrl+F5` | 重播動畫 |
| `Esc` | 返回一般模式（清除軌跡） |
| `Ctrl+Z` | 放大繪圖還原 |
| `Ctrl+Shift+Z` | 放大繪圖復原 |

所有快速鍵可在設定視窗自訂。

---

## 設定

點控制視窗的 **設定** 按鈕開啟，分五個頁籤：

### 一般
- HUD 文字區域大小、背景透明度
- 字型、字體大小、文字顏色/透明度
- 文字自動消失秒數（0 = 永不）
- OBS WebSocket 密碼

### 放大鏡
- 放大鏡樣式（全螢幕 / 跟隨鏡頭）
- 進入時初始縮放倍率、鏡頭半徑
- 縮放步進、閒置自動退出秒數
- 準心樣式、大小、顏色/透明度

### 遊標效果
- 呼吸效果樣式、基礎半徑、速度、外框色、中心色

### 路徑軌跡
**軌跡外觀**
- 頭部圖示：飛機 / 箭頭 / 火箭 / 無圖示
- 圖示大小（8–60 px）
- 軌跡長度（可見尾巴長度，20–2000 px）
- 線條粗細、軌跡顏色

**中斷點**
- 停頓時間（0–5 秒）
- 中斷點圓點大小（0 = 隱藏）、顏色、背景透明度、數字顏色、外框

**播放背景截圖**
- 開關：錄製時同步截取螢幕畫面
- 截圖間隔（0.2–5.0 秒）

### 快速鍵
點按鈕後按下想要的組合鍵即可設定。

---

## Stream Deck

從 Releases 下載 **StreamMouse.streamDeckProfile**，雙擊即可匯入。

| 按鍵位置 | 圖示 | 功能 | 快速鍵 |
|----------|------|------|--------|
| 第一排 1 | 🔴 錄製 | 開始 / 停止錄製 | `Ctrl+F1` |
| 第一排 2 | 📍 中斷點 | 插入中斷點 | `Ctrl+F2` |
| 第一排 3 | 🔍 放大鏡 | 放大鏡 / 截圖 | `Ctrl+F3` |
| 第一排 4 | ▶️ 重播 | 重播動畫 | `Ctrl+F5` |
| 第一排 5 | ↩️ 返回 | 返回一般模式 | `Esc` |
| 第二排 1 | 🖱️ 啟動 | 啟動 StreamMouse.exe | *(需自行設定路徑)* |

> **啟動按鍵**：匯入後在 Stream Deck 軟體中，對「啟動 StreamMouse」按鈕按右鍵 → 編輯 → 填入你的 `StreamMouse.exe` 完整路徑。

---

## OBS WebSocket

OBS Studio 啟用 WebSocket（預設 `127.0.0.1:4455`）後，HUD 自動顯示串流狀態與場景名稱。

若設有密碼，可在 **設定 > 一般 > WebSocket 密碼** 填入，或透過環境變數：

```powershell
$env:OBS_WEBSOCKET_PASSWORD = "your-password"
```

---

## 注意事項

- 僅支援 Windows 10 / 11
- 覆蓋層只套用在啟動時選擇的螢幕
- 鍵盤 HUD 會顯示最近輸入，請勿在輸入密碼時使用
- 截圖儲存至桌面，檔名格式：`stream_mouse_YYYYMMDD_HHMMSS.png`
- 啟用背景截圖功能後，長時間錄製會佔用較多記憶體

---

## License

[MIT](LICENSE)
