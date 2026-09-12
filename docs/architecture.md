# PaddleOCR / Bedrock 估價審查架構

## 領域邊界

本版是一個估價審查 bounded context，以模組化單體部署。

- `Case` 是案件 aggregate，包含因素值、總計、適用基準 ID、文件 ID 與 revision。
- `Factor` 保存比準地與比較標的條件、原填修正率、人工確認狀態及 `Evidence`。
- 基準以不可覆寫版本保存；案件用 ID 綁定當時版本。
- 原始文件獨立保存；案件與文件透過 ID 關聯。
- `review` 是純領域計算，不存取資料庫、不呼叫模型。

保留現有金山商業用地計算規則與單一比較標的範圍。`app/domain/shulin_residential/`
已提供樹林普通住宅用地的純領域計算、分級及矩陣查表，但尚未接入 `Case`、規則版本儲存或
application 流程；多比較標的與完整計算追溯仍屬後續領域擴充。

## 依賴方向

```text
瀏覽器
  → interfaces/http.py
    → application/services.py
      → domain/models.py / engine.py / rule_validation.py
      → application/ports.py
          ← infrastructure/paddle_pdf.py
          ← infrastructure/bedrock.py
          ← infrastructure/persistence.py

bootstrap.py 負責選擇具體 adapter 並注入。
```

領域和應用層不能 import HTTP、Paddle、AWS 或 SQLite；測試會檢查這個界線。HTTP 層處理請求、狀態碼與輸出，應用層負責用例順序與版本衝突，領域層負責估價判定。

## PDF 用例

1. HTTP 串流接收，超過 20 MB 立即停止。
2. Paddle adapter 以 PDF hash 與 OCR 設定查詢快取。
3. 啟動獨立子程序，以 PDFium 將頁面轉成圖片，PaddleOCR 在 CPU 辨識。
4. 保存文字框、信心值、像素座標及頁面尺寸，再按位置重建供解析器使用的文字。
5. 已知版型轉為待確認草稿；原文、PDF 與案件分別保存。

子程序不接受模型工具指令，也不呼叫雲端文件處理服務。Paddle 模型首次從官方來源下載。逾時會終止子程序；程式不會在 OCR 失敗後假裝改用成功的文字層結果。

## AI 用例

1. 確認文件符合競賽上雲規範，核對案件 revision。
2. 讀取此案件的 OCR 文字與選定的基準版本。
3. 以文字、基準內容、模型、區域與 prompt 版本建立快取 key。
4. 取得跨程序檔案鎖，命中快取則直接回傳；否則等待持久化的請求間隔。
5. 呼叫 Bedrock Converse。SDK 自動重試關閉，最多三次 adapter 嘗試，每次都必須經過節流。
6. 模型回傳頁碼與來源行號，程式自行複製引用，檢查 JSON、停止原因、因素 ID 及所述值是否存在於該引用；互相矛盾的同因素候選不直接採用。
7. 再次核對 revision，回傳未確認草稿。此時不修改案件或修訂紀錄。
8. 使用者選擇套用，保留既有等級、備註與免比較設定，更新條件、原填修正率與引用，重設人工確認狀態。

引用與值存在只是機械檢查，不能證明模型選對欄位或比較方向。模型遇到原文指令時仍應視為資料；模型沒有工具或修改規則的權限。

## 儲存與部署

- SQLite 保留原本四張表，新增 `extraction_cache` 與 `request_gate`，舊案件不需要重建。
- 案件保存與 audit snapshot 在同一筆 transaction；過期 revision 回傳 HTTP 409。
- 文件以 UUID 命名，來源檔名只作顯示，避免控制儲存路徑。
- 外部回應錯誤轉成固定訊息，不將 AWS upstream response 或憑證回傳 UI。
- 預設 `us-west-2`、區域內 `qwen.qwen3-32b-v1:0`；區域限制與至少 1.1 秒間隔在設定載入時驗證。
- AWS CLI profile / environment / instance role / Bedrock API key 由 SDK 處理，Git 只保存設定範例。

目前採單機 SQLite 與共用鎖檔，沒有分散式佇列。HTTP 呼叫期間等待 OCR 或 AI，介面顯示處理中；程序重啟後使用者需重新提交，成功快取可重用。

AWS 單機部署由 Nginx 提供限制來源 IP 的 HTTP 入口，管理走 SSM，Bedrock 憑證由 instance role 提供。TLS、應用程式登入、自動備份與多機協調仍未提供；詳見 [AWS 部署](aws-deployment.md)。上雲前必須遵守競賽資料規範；不得因題目由主辦提供，就推定其中價格資料允許上雲。

## 基準文件 RAG

`application/rag.py` 編排上傳來源、唯讀檢索與引用說明；`rag_contracts.py` 定義 DTO。`LocalEvidenceRetriever` 從 repository 精確篩選案件基準 ID／版本、地區、用地與期間後，以中文雙字詞 BM25 排名。`BedrockEvidenceAnswerer` 共用既有 transport、lock 與 gate，只生成可引用本次 hits 的說明。

新增 `evidence_documents` 表與索引，原始 PDF 保存在原有 documents／uploads；案件與 audit 不變。`static/rag.js` 提供來源上傳、查找與說明視窗。查詢不更改基準或確認狀態；v2 整合與 GraphRAG 仍未實作。詳見 [RAG 操作與限制](rag.md)及 [共用契約](contracts.md)。

## 動態 ruleset 與題目匯入

`RulesetImportService` 編排「PaddleOCR → structured ruleset 草稿 → 人工確認矩陣方向與期間 → 保存規則版本及檢索來源」。OCR 草稿不會直接執行；確認時才將通用 `FactorRule` 投影到現行案件審查 JSON 契約，且規則與來源索引在同一筆 repository transaction 保存。

題目 PDF 必須明確帶入已選 ruleset。`application/drafts.py` 只依該 ruleset 提供的因素名稱、標籤、分類值與單位做保守列對應，另擷取文件中明確出現的行政區與詳細地址；不做 geocoding，也不以基準地區冒充題目辨識結果。無法唯一對應的欄位維持待確認。固定表3／表4／表5 renderer 仍是既有版型 adapter；跨地區完整輸出使用不依賴固定因素 ID 的 `review-xlsx`。

## Agentic RAG

`AgenticRagService` 讓模型透過 `AgentModel` 選擇四個已註冊函式：搜尋、來源讀頁、查看規則及 domain 審查。Bedrock adapter 負責原生 toolUse／toolResult；工具由 application 執行，不讓模型直接操作 Python 或資料庫。迴圈受 revision、工具白名單、引用驗證及次數限制。詳見 [Agentic RAG](agentic-rag.md)。


## 計算欄位來源相容性

`Case.total_evidence` 是總計欄位名稱至 `Evidence` 的選用映射，預設為空。
版型抽取保存實際頁碼與引文；domain 審查僅使用已保存來源，不以表號推定頁碼。
修改總計值、替換／移除文件時清除過期引用，引用修改也須重新人工確認。
application 保存時核對引用確實位於連結文件；沒有連結文件的 JSON 匯入不保留總計引用。
舊案件與歷史 JSON 不回填或重寫，數值與確認狀態不變；未記錄來源的總計在 UI／HTML 顯示
「未記錄來源頁碼」，CSV／Excel 頁碼留白。SQLite 仍儲存 JSON，沒有 schema 遷移。
舊版程式不認得新增的 JSON 欄位；回退舊版前須備份並使用升版前資料，或另做保留數值與修訂的欄位相容轉換。
