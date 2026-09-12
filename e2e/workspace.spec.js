const { test, expect } = require('@playwright/test');
const path = require('node:path');

test('dashboard, evidence, fix, edit, persist and export', async ({ page }) => {
  const errors=[];
  page.on('pageerror', e => errors.push(e.message));
  await page.goto('/');
  await expect(page.getByRole('heading', { name: '案件工作台', exact: true })).toBeVisible();
  await expect(page.locator('.case-table tbody tr')).toHaveCount(2);
  await page.screenshot({ path: 'test-results/dashboard.png', fullPage: true });
  const [sampleResponse] = await Promise.all([
    page.waitForResponse(r => r.url().endsWith('/api/samples/errors') && r.request().method() === 'POST'),
    page.getByRole('button', { name: '建立錯誤示範' }).click(),
  ]);
  const sampleCase = (await sampleResponse.json()).case;
  const manualTotal = page.locator('.check').filter({has:page.getByRole('heading',{name:'調整百分率絕對值加總',exact:true})});
  await expect(manualTotal).toContainText('未記錄來源頁碼');
  await expect(manualTotal.getByRole('button',{name:/原文 p\./})).toHaveCount(0);
  await expect(page.getByRole('heading', { name: '金山區商業用地｜錯誤示範', exact: true })).toBeVisible();
  const road=page.locator('.check').filter({has:page.getByRole('heading',{name:'面前道路寬度',exact:true})});
  await road.getByRole('button',{name:'基準 p.7'}).click();
  await expect(page.getByRole('dialog')).toContainText('15 m 以上，未滿 20 m');
  await page.getByRole('button',{name:'關閉',exact:true}).click();
  await road.getByRole('button',{name:'採用建議'}).click();
  await expect(road.locator('.pill')).toHaveText('待確認');
  await page.getByRole('button',{name:'資料核對',exact:true}).click();
  await page.locator('[data-factor="road_width"][data-key="confirmed"]').check();
  await page.getByRole('button',{name:'儲存並重新審查'}).click();
  await page.locator('[data-factor="school"][data-key="subject"]').fill('150');
  await expect(page.locator('[data-factor="school"][data-key="confirmed"]')).not.toBeChecked();
  await expect(page.locator('[data-factor="school"][data-key="confirmed"]')).toBeDisabled();
  await page.getByRole('button',{name:'儲存並重新審查'}).click();
  await expect(page.locator('.dirty-indicator')).toHaveText('所有變更已儲存');
  await page.locator('[data-factor="school"][data-key="confirmed"]').check();
  await page.getByRole('button',{name:'儲存並重新審查'}).click();
  await page.getByRole('button',{name:'審查結果',exact:true}).click();
  const school=page.locator('.check').filter({has:page.getByRole('heading',{name:'接近學校之程度',exact:true})});
  await expect(school.locator('.pill')).toHaveText('通過');
  await school.getByRole('button',{name:'原文 p.3'}).click();
  await page.getByRole('button',{name:'文字版',exact:true}).click();
  if (sampleCase.document_id) {
    const document = await (await page.request.get('/api/documents/' + sampleCase.document_id)).json();
    const expectedPage = Math.min(3, document.pages.length);
    const sourcePage = document.pages.find(p => p.page === expectedPage);
    expect(sourcePage.text.trim()).not.toBe('');
    await expect(page.locator('#source-page')).toHaveValue(String(expectedPage));
    await expect(page.locator('.source-text')).toHaveText(sourcePage.text);
  } else {
    await expect(page.locator('#source-content')).toContainText('尚未連結 PDF');
  }
  await page.screenshot({ path: 'test-results/review.png', fullPage: true });
  await page.getByRole('button',{name:'修訂紀錄',exact:true}).click();
  await expect(page.getByRole('dialog')).toContainText('採用建議：面前道路寬度');
  await page.getByRole('button',{name:'關閉',exact:true}).click();
  await page.getByRole('button',{name:'匯出成果',exact:true}).click();
  const href=await page.getByRole('link',{name:'審查報告 · HTML ↗'}).getAttribute('href');
  const report=await page.request.get(href);
  expect(report.status()).toBe(200);
  expect(await report.text()).toContain('尚有疑點或待確認項目');
  expect(await report.text()).toContain('未記錄來源頁碼');
  await page.getByRole('button',{name:'關閉',exact:true}).click();
  await page.getByRole('button',{name:'返回案件工作台',exact:true}).click();
  await page.getByRole('textbox',{name:'搜尋案件'}).fill('錯誤示範');
  await expect(page.locator('.case-table tbody tr')).toHaveCount(2);
  expect(errors).toEqual([]);
});

test('scanned PDF upload through PaddleOCR and disabled Bedrock message', async ({ page }) => {
  test.setTimeout(120000);
  await page.goto('/');
  await page.locator('#pdf-input').setInputFiles(path.join(__dirname,'..','tests','fixtures','synthetic-scanned.pdf'));
  await expect(page.getByRole('heading',{name:'synthetic-scanned',exact:true})).toBeVisible({timeout:110000});
  await page.getByRole('button',{name:'文字版',exact:true}).click();
  await page.locator('#source-page').selectOption('1');
  await expect(page.locator('.source-text')).toContainText('寬度');
  await page.getByRole('button',{name:'資料核對',exact:true}).click();
  await expect(page.locator('[data-factor="width"][data-key="confirmed"]')).not.toBeChecked();
  await page.getByRole('button',{name:'AWS AI 抽取',exact:true}).click();
  await expect(page.getByRole('dialog')).toContainText('目前未啟用 Bedrock');
});

test('new rule version validates and can be selected for case', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button',{name:'評價基準庫',exact:true}).click();
  await expect(page.locator('.rule-card')).toHaveCount(47);
  await page.getByRole('button',{name:'建立基準版本',exact:true}).click();
  const rules=JSON.parse(await page.locator('#rule-json').inputValue());
  rules.name='瀏覽器測試基準';rules.version='test-1';
  await page.locator('#rule-json').fill(JSON.stringify(rules));
  await page.getByRole('button',{name:'驗證並建立版本',exact:true}).click();
  await expect(page.getByRole('dialog')).not.toBeVisible();
  await expect(page.locator('.content > .notice')).toContainText('瀏覽器測試基準');
  await page.getByRole('button',{name:/案件工作台/}).click();
  await page.getByRole('button',{name:'建立案件',exact:true}).click();
  await page.getByRole('button',{name:'案件與計算',exact:true}).click();
  await page.locator('[data-case="title"]').fill('持久化測試案件');
  await page.locator('[data-case="ruleset_id"]').selectOption({label:'瀏覽器測試基準 / test-1'});
  await page.getByRole('button',{name:'儲存並重新審查'}).click();
  await expect(page.getByRole('heading',{name:'持久化測試案件',exact:true})).toBeVisible();
  await page.reload();
  await page.getByRole('button',{name:'開啟 持久化測試案件',exact:true}).click();
  await expect(page.getByRole('heading',{name:'持久化測試案件',exact:true})).toBeVisible();
});

test('ruleset upload wizard confirms source direction before question upload', async ({ page }) => {
  await page.goto('/');
  let releaseExtraction;
  const existing = await (await page.request.get('/api/rulesets')).json();
  const candidate = {
    ...structuredClone(existing[0]),
    name: '測試市甲區住宅用地 · OCR 評價基準',
    version: 'ocr-browser-test',
    locality: '測試市甲區',
    land_use: '住宅用地',
    import_kind: 'ocr-structured',
    requires_confirmation: true,
  };
  delete candidate.id;
  const saved = {...candidate, id: 'browser-confirmed-rule', requires_confirmation: false};
  await page.route('**/api/rulesets', route => route.fulfill({json:[...existing, saved]}));
  await page.route('**/api/ruleset-imports**', async route => {
    if (route.request().url().endsWith('/confirm')) {
      const body = route.request().postDataJSON();
      expect(body.confirmed).toBe(true);
      expect(body.matrix_direction).toBe('benchmark_row_target_column');
      return route.fulfill({json:{ruleset:saved,message:'基準已建立並加入檢索。'}});
    }
    await new Promise(resolve => { releaseExtraction = resolve; });
    return route.fulfill({json:{
      document_id:'browser-source-doc',source_name:'browser-rule.pdf',
      requires_confirmation:true,warnings:[],rulesets:[{title:'合成 structured ruleset'}],
      candidates:[candidate],message:'合成 OCR 草稿',
    }});
  });
  await page.getByRole('button',{name:'上傳評價基準表',exact:true}).click();
  await page.locator('#ruleset-locality').fill('測試市甲區');
  await page.locator('#ruleset-valid-from').fill('2026-01-01');
  await page.locator('#ruleset-valid-to').fill('2026-12-31');
  await page.locator('#ruleset-file').setInputFiles({name:'browser-rule.pdf',mimeType:'application/pdf',buffer:Buffer.from('%PDF-test')});
  await page.getByRole('button',{name:'開始 OCR 與規則轉換'}).click();
  await expect(page.getByRole('dialog')).toBeHidden();
  const conversionStatus = page.getByRole('status');
  await expect(conversionStatus).toHaveText('正在轉換…');
  await expect(conversionStatus).not.toContainText('PaddleOCR');
  await expect.poll(() => Boolean(releaseExtraction)).toBe(true);
  releaseExtraction();
  await expect(page.getByRole('dialog')).toContainText('合成 OCR 草稿');
  await page.locator('#matrix-direction').selectOption('benchmark_row_target_column');
  await page.locator('#ruleset-confirmed').check();
  await page.getByRole('button',{name:'建立基準並加入檢索'}).click();
  await expect(page.getByRole('heading',{name:'上傳題目並開始填表'})).toBeVisible();
  await expect(page.locator('#case-upload-ruleset')).toHaveValue(saved.id);
});

test('mobile navigation and layout fit the viewport', async ({ page }) => {
  await page.setViewportSize({width:390,height:844});
  await page.goto('/');
  await expect(page.getByRole('heading',{name:'案件工作台',exact:true})).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:'test-results/mobile.png',fullPage:true});
  await page.getByRole('button',{name:'使用指南',exact:true}).click();
  await expect(page.getByRole('heading',{name:'使用指南',exact:true})).toBeVisible();
});

test('Bedrock consent, preview and manual confirmation state', async ({ page }) => {
  let cloudRequests = 0;
  await page.route('**/api/health', route => route.fulfill({json:{status:'ok',ai_configured:true,ai_provider:'bedrock',ai_model:'synthetic-test-model',ai_region:'us-west-2'}}));
  await page.route('**/api/cases/*/ai', async route => {
    cloudRequests++;
    const body = route.request().postDataJSON();
    expect(body.cloud_data_approved).toBe(true);
    await route.fulfill({json:{revision:body.revision,message:'合成 AI 草稿',factors:[{id:'width',subject:'5',comparable:'7',entered_rate:0,confirmed:false,evidence:{page:1,quote:'寬度 5 7',method:'bedrock:test'}}]}});
  });
  await page.goto('/');
  await page.getByRole('button',{name:'建立錯誤示範'}).click();
  await page.getByRole('button',{name:'資料核對',exact:true}).click();
  await page.locator('[data-factor="width"][data-key="note"]').fill('保留人工核對備註');
  await page.getByRole('button',{name:'儲存並重新審查'}).click();
  await page.getByRole('button',{name:'AWS AI 抽取',exact:true}).click();
  await expect(page.getByRole('dialog')).toContainText('競賽禁止');
  await page.getByRole('button',{name:'開始抽取',exact:true}).click();
  expect(cloudRequests).toBe(0);
  await page.locator('#cloud-data-approved').check();
  await page.getByRole('button',{name:'開始抽取',exact:true}).click();
  await expect(page.getByRole('dialog')).toContainText('合成 AI 草稿');
  await page.locator('[data-ai-index="0"]').check();
  await page.getByRole('button',{name:'套用勾選草稿',exact:true}).click();
  await expect(page.locator('[data-factor="width"][data-key="confirmed"]')).not.toBeChecked();
  await expect(page.locator('[data-factor="width"][data-key="note"]')).toHaveValue('保留人工核對備註');
  expect(cloudRequests).toBe(1);
  await page.screenshot({path:'test-results/bedrock-draft.png',fullPage:true});
});

test('factor, totals and rule changes invalidate saved confirmations', async ({ page }) => {
  const rules = (await (await page.request.get('/api/rulesets')).json())[0];
  rules.name = '確認失效測試基準';
  const created = await (await page.request.post('/api/rulesets',{data:rules})).json();
  await page.setViewportSize({width:390,height:844});
  await page.goto('/');
  await page.getByRole('button',{name:'建立錯誤示範'}).click();
  await page.getByRole('button',{name:'資料核對',exact:true}).click();
  const width = page.locator('[data-factor="width"][data-key="confirmed"]');
  const depth = page.locator('[data-factor="depth"][data-key="confirmed"]');
  await expect(width).toBeChecked();
  await page.locator('[data-factor="width"][data-key="subject"]').fill('9');
  await expect(width).not.toBeChecked();
  await expect(width).toBeDisabled();
  await expect(depth).toBeChecked();
  await page.getByRole('button',{name:'案件與計算',exact:true}).click();
  const totals = page.locator('[data-case="totals_confirmed"]');
  await expect(totals).not.toBeChecked();
  await expect(totals).toBeDisabled();
  await page.getByRole('button',{name:'儲存並重新審查'}).click();
  await expect(totals).toBeEnabled();
  await totals.check();
  await page.getByRole('button',{name:'儲存並重新審查'}).click();
  await page.locator('[data-total="individual"]').fill('22');
  await expect(totals).not.toBeChecked();
  await expect(totals).toBeDisabled();
  await page.getByRole('button',{name:'儲存並重新審查'}).click();
  await page.locator('[data-case="ruleset_id"]').selectOption(created.id);
  await page.getByRole('button',{name:'資料核對',exact:true}).click();
  await expect(depth).not.toBeChecked();
  await expect(depth).toBeDisabled();
  await page.getByRole('button',{name:'儲存並重新審查'}).click();
  await expect(depth).toBeEnabled();
  await depth.check();
  await page.getByRole('button',{name:'儲存並重新審查'}).click();
  await expect(depth).toBeChecked();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});


test('saving locks edits and a failed save preserves the pending draft', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button',{name:'建立錯誤示範'}).click();
  await page.getByRole('button',{name:'資料核對',exact:true}).click();
  const input = page.locator('[data-factor="width"][data-key="subject"]');
  const confirmation = page.locator('[data-factor="width"][data-key="confirmed"]');
  await input.fill('9');
  let releaseSave;
  const held = new Promise(resolve => { releaseSave = resolve; });
  let intercepted;
  const requestStarted = new Promise(resolve => { intercepted = resolve; });
  const failSave = async route => {
    if (route.request().method() !== 'PUT') return route.continue();
    intercepted();
    await held;
    await route.fulfill({status:503,json:{detail:'合成儲存失敗'}});
  };
  await page.route('**/api/cases/*', failSave);
  await page.getByRole('button',{name:'儲存並重新審查'}).click();
  await requestStarted;
  try {
    await expect(input).toBeDisabled();
    await expect(confirmation).toBeDisabled();
  } finally { releaseSave(); }
  await expect(input).toBeEnabled();
  await expect(input).toHaveValue('9');
  await expect(confirmation).not.toBeChecked();
  await expect(confirmation).toBeDisabled();
  await expect(page.locator('.dirty-indicator')).toContainText('尚未儲存');
  await page.unroute('**/api/cases/*', failSave);
  await page.getByRole('button',{name:'儲存並重新審查'}).click();
  await expect(confirmation).toBeEnabled();
  await expect(input).toHaveValue('9');
  await expect(confirmation).not.toBeChecked();
});


test('RAG uploads a scoped source, retrieves locally and requires cloud consent', async ({ page }) => {
  test.setTimeout(120000);
  await page.setViewportSize({width:390,height:844});
  await page.goto('/');
  await page.getByRole('button',{name:'建立錯誤示範'}).click();
  await page.getByRole('button',{name:'依據問答',exact:true}).click();
  await expect(page.getByRole('dialog')).toContainText('尚未加入來源');
  await page.locator('#rag-question').fill('寬度');
  await page.locator('#rag-search').click();
  await expect(page.locator('#rag-results')).toContainText('找不到符合版本');
  await page.getByText('管理這個基準版本的來源 PDF',{exact:true}).click();
  await page.locator('#rag-from').fill('2025-01-01');
  await page.locator('#rag-to').fill('2025-12-31');
  await page.locator('#rag-file').setInputFiles(path.join(__dirname,'..','tests','fixtures','synthetic-scanned.pdf'));
  await page.locator('#rag-upload').click();
  await expect(page.locator('#rag-results')).toContainText('來源已加入',{timeout:110000});
  const [response] = await Promise.all([
    page.waitForResponse(r=>r.url().endsWith('/evidence')&&r.request().method()==='POST'),
    page.locator('#rag-search').click(),
  ]);
  const result=await response.json();
  expect(result.hits.length).toBeGreaterThan(0);
  await expect(page.locator('#rag-results')).toContainText('寬度');
  await expect(page.locator('#rag-results a').first()).toHaveAttribute('href',/\/api\/documents\/.+\/file#page=1/);
  let calls=0;
  await page.route('**/api/cases/*/evidence',async route=>{
    if(!route.request().postDataJSON().generate)return route.continue();
    calls++;
    expect(route.request().postDataJSON().cloud_data_approved).toBe(true);
    await route.fulfill({json:{...result,status:'draft',statements:[{text:'合成說明 <script>window.ragInjected=true</script>',citation_ids:[result.hits[0].id]}]}});
  });
  await page.locator('#rag-answer').click();
  await expect(page.locator('#rag-results')).toContainText('請先確認');
  expect(calls).toBe(0);
  await page.locator('#rag-consent').check();
  await page.locator('#rag-answer').click();
  await expect(page.locator('#rag-results')).toContainText('合成說明');
  await expect(page.locator('#rag-results a').first()).toHaveAttribute('href','#rag-cite-'+result.hits[0].id);
  expect(await page.evaluate(()=>window.ragInjected)).toBeUndefined();
  expect(calls).toBe(1);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:'test-results/rag-mobile.png',fullPage:true});
});


test('Agent mode shows function trace and separate deterministic review', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button',{name:'建立錯誤示範'}).click();
  await page.getByRole('button',{name:'依據問答',exact:true}).click();
  await page.locator('#rag-question').fill('寬度依據與審查結果');
  let calls=0;
  await page.route('**/api/cases/*/agent-evidence',async route=>{
    calls++;
    expect(route.request().postDataJSON().cloud_data_approved).toBe(true);
    await route.fulfill({json:{case_revision:1,status:'insufficient_evidence',message:'Agent 合成測試',hits:[],statements:[],
      tool_trace:[{tool:'search_evidence',status:'success'},{tool:'review_case',status:'success'}],
      review:{counts:{pass:0,error:0,pending:1,missing:0},checks:[{title:'寬度',message:'等待人工確認'}]}}});
  });
  await page.locator('#rag-agent').click();
  await expect(page.locator('#rag-results')).toContainText('請先確認');
  expect(calls).toBe(0);
  await page.locator('#rag-consent').check();
  await page.locator('#rag-agent').click();
  await expect(page.locator('#rag-results')).toContainText('search_evidence（success） → review_case（success）');
  await page.getByText('程式審查結果（版本 1）',{exact:true}).click();
  await expect(page.locator('#rag-results')).toContainText('等待人工確認');
  expect(calls).toBe(1);
  await page.screenshot({path:'test-results/agentic-rag.png',fullPage:true});
});
