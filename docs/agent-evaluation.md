# Agent 小型驗收題集

本次範圍是 DDD-07／08 的單一標的 Agent 驗收切片，不代表多標的、住宅公式或 PDF 產製的完整題目驗收已完成。

## 如何重跑

只檢查合成測資與獨立計算答案，不呼叫 AWS：

```bash
.venv/bin/python -m scripts.evaluate_agent
```

真實 Bedrock 驗收，使用已設定的 AWS profile：

```bash
.venv/bin/python -m scripts.evaluate_agent --bedrock --profile landwise-hackathon
```

僅驗證規則與計算工具：

```bash
.venv/bin/python -m scripts.evaluate_agent --bedrock --profile landwise-hackathon --case rule_and_calculation
```

預設報告為 `.analysis/agent-evaluation.json`，可用 `--report` 指定。任一驗收條件失敗時，live 命令結束碼為 1，仍保留所有題目結果。沒有 --bedrock 時輸出 fixture-validation，不會把測資檢查當成模型通過。

每次使用同一暫存資料庫與 Bedrock gate，題目各綁獨立的合成規則版本；完整執行後刪除暫存案件與來源。生成請求只送合成問題、合成規則與合成文件。PDF 文字層用於隔離 OCR 誤差，這批不是 OCR 品質測試。

## 固定的六題

| 題目 | 驗收重點 | 2026-09-12 最終實測 |
| --- | --- | --- |
| rule_and_calculation | 實際使用 get_rule、review_case；規則 payload 正確，個別因素及矩陣均為獨立答案 2%；完整 review 與直接執行引擎一致 | 通過 |
| no_source | 搜尋後無來源，不生成附會答案 | 通過 |
| wrong_version | 文件綁在其他版本，結果必須為空 | 通過 |
| missing_data | 呼叫 review_case；缺值維持 null、因素為 missing、整案 complete=false | 通過 |
| conflicting_sources | 同期間兩份文件對同一情境規定 2% 與 5%，須找到兩份並拒絕選邊作答 | 通過 |
| grounded_answer | 搜尋、讀頁、附有效來源引用，文字與文件一致 | 通過 |

所有題目另核對案件／audit 不變。引用檢查涵蓋文件 ID、原始 bytes SHA-256、版本、頁碼與精確字元切片；最終說明的 ID 必須在檢索結果中。數學驗收同時使用獨立預期值與引擎完整結果，不能只有模型說「算對了」。

## 實際發現與修正

初始完整測試為 4/6。兩個計算問題的工具其實成功，但模型把 width、norm_individual 或 deterministic-engine 當成文件 citation_ids，被既有嚴格引用驗證拒收。再次重現兩題後，保留驗證不變，只將 prompt 升為 landwise-agent-v2，明訂工具的 rule/check ID 不是文件引用，無文件時回空 statements；程式審查結果本來就會另外保留。

修正後完整批次 6/6，使用 us-west-2 的 qwen.qwen3-32b-v1:0，14 次實際模型請求、18,510 tokens。包含前兩輪診斷，本次共 33 次請求、43,428 tokens。數字為 usage 回傳值，不是費用估算。

人工另核對 grounded_answer：合成情境、2% 修正率、適用日期及「不是真實法規」標示皆與原 PDF 一致；其他五題沒有生成無引用說明。計算題雖回 insufficient_evidence，仍有完整的程式 review，這是設計行為。

完整合成輸出與逐項條件見 [實測 JSON](evaluations/agentic-2026-09-12.json)。題集與文件見 [fixtures](../tests/fixtures/agent_eval/README.md)。

## 適用限制

這是固定六題的一次修正後 smoke 驗收，題目包含明確工具指示，不代表自然提問的廣泛工具選擇成功率，也不保證模型後續每次都相同。尚未量測多次重跑、不同語氣／同義詞、大型語料或正式業務題的泛化能力。

自動條件主要驗證工具、數值、版本隔離、引用與拒答；語意支持仍需人工閱讀。保留失敗而非重試到成功再隱藏紀錄。多標的及專業規則準確性仍依 DDD-01／02／08 後續驗收。


## 2026-09-13：現場失敗排查

快取重播確認兩個失敗：模型將 `width` 當文件引用；或以 `基準ID.width` 呼叫
`get_rule`，失敗後提前結束。真實重測另發現空引用造成格式拒收，以及一次四個合法
工具超過原先單輪三個的限制。

修正使用合法規則 ID enum／錯誤回饋、一次最終格式／引用修正，以及共用八次工具總預算。
仍維持最多五輪，錯誤引文不顯示。即使最終草稿無法修復，已完成的程式審查仍能呈現。
`landwise-agent-v4` 同時使舊 prompt 快取失效。

- Python：1,178 passed、4 skipped；Chrome E2E：11 passed；JS 語法檢查通過。
- 真實 Bedrock 六題：6/6，17 次 Converse、29,220 tokens；使用合成 PDF，
  [驗收摘要](evaluations/agent-source-fix-2026-09-13.json) 不包含模型隱藏推理。
- 以現場合成案件及兩個原問題重測：第一題模型仍產生無效引用，回應為
  `insufficient_evidence`，保留 11 項通過的程式審查；第二題回傳 `draft`，規則與審查工具成功。
  兩題皆未修改案件。這證明錯誤處理有效，不代表模型保證遵循每個工具順序或生成正確說明。
- 舊案件總計沒有逐欄來源資料；介面與 HTML 顯示「未記錄來源頁碼」，CSV／Excel 留白。
  新抽取總計記錄實際頁碼；手動修改數值後清除過期引用。估價數值與公式不變。
