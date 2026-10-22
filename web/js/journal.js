/* Journal workbench: local evidence → human review → saved original plan.
   Drafts stay in this page's memory. Nothing is sent to an external model. */
(() => {
  const el = id => document.getElementById(id);
  const esc = escapeHtml;
  const questions = [
    ['q1_reason', '研究這家公司的理由'], ['q2_growth_from', '成長來源與後續驗證'],
    ['q3_market_view', '市場目前的預期'], ['q4_my_edge', '我的看法或研究優勢'],
    ['q5_priced_in', '價格反映了多少成長'], ['q6_catalyst', '下一個追蹤事件'],
    ['q7_wrong_signal', '判斷失效的訊號'],
  ];
  el('assistant-questions').innerHTML = questions.map(([key, label], i) => `
    <section class="journal-question" aria-labelledby="label-${key}">
      <div class="journal-question-head"><h3 id="label-${key}">${i + 1}. ${label}</h3><span id="state-${key}" class="muted journal-small">尚未整理</span></div>
      <p id="preview-${key}" class="muted">整理草稿後可直接檢視，也可以自行補充。</p>
      <details><summary>補充或修正</summary><label class="sr-only" for="pl-${key}">${label}</label><textarea id="pl-${key}" rows="3"></textarea></details>
    </section>`).join('');
  let draft = null, autoValues = {}, draftGeneration = 0, positionGeneration = 0;
  let plans = [], saving = false, position = null, baseline = '', savedPlanId = null;
  const statusLabel = value => ({open:'進行中', closed:'已結案', cancelled:'已取消'}[value] || value);
  const values = () => Object.fromEntries([...document.querySelectorAll('[id^="pl-"]')].map(field => [field.id.slice(3), field.type === 'number' ? (field.value === '' ? null : Number(field.value)) : field.value.trim()]));
  const fingerprint = () => JSON.stringify(values());
  const dirty = () => fingerprint() !== baseline;
  function markDirty() {
    el('draft-state').textContent = dirty() ? '尚未儲存' : '已儲存／未修改';
  }
  function updateQuestion(key) {
    const text = el(`pl-${key}`).value.trim();
    el(`preview-${key}`).textContent = text || '尚未記錄，可保留空白或補充。';
    el(`preview-${key}`).className = text ? '' : 'muted';
    el(`state-${key}`).textContent = text.startsWith('待查證：') ? '待查證' : text ? '已記錄，待你確認' : '未記錄';
    el(`state-${key}`).className = (text.startsWith('待查證：') ? 'warn' : 'muted') + ' journal-small';
  }
  document.querySelectorAll('[id^="pl-"]').forEach(field => {
    field.addEventListener('input', () => {
      el('assistant-reviewed').checked = false;
      if (questions.some(([key]) => `pl-${key}` === field.id)) updateQuestion(field.id.slice(3));
      markDirty();
    });
    field.addEventListener('change', markDirty);
  });
  el('pl-stock_id').addEventListener('input', () => {
    draftGeneration++;
    el('btn-draft').disabled = false;
    el('btn-draft').textContent = '整理證據與計畫草稿';
    if (draft && draft.stock_id !== el('pl-stock_id').value.trim()) el('assistant-status').textContent = '股票代號已變更。原草稿先保留；請開始另一份計畫，避免混用不同股票的理由。';
  });
  el('assistant-focus').addEventListener('change', () => { el('assistant-reviewed').checked = false; el('assistant-status').textContent = '研究方向已變更，請重新整理草稿；你修改過的文字會保留。'; });
  window.addEventListener('beforeunload', event => { if (dirty()) { event.preventDefault(); event.returnValue = ''; } });

  async function prepareDraft() {
    const sid = el('pl-stock_id').value.trim();
    if (!sid) { el('assistant-status').textContent = '請先輸入股票代號。'; el('pl-stock_id').focus(); return; }
    if (draft && draft.stock_id !== sid) { el('assistant-status').textContent = '請先按「開始另一份計畫」，確認清除原草稿後再整理不同股票。'; return; }
    const generation = ++draftGeneration, focus = el('assistant-focus').value;
    el('btn-draft').disabled = true; el('btn-draft').textContent = '正在整理資料…';
    el('assistant-status').textContent = '正在讀取現有資料並整理證據，尚未儲存計畫。';
    try {
      const next = await api(`/api/journal/draft-plan/${encodeURIComponent(sid)}?focus=${encodeURIComponent(focus)}`);
      if (generation !== draftGeneration || sid !== el('pl-stock_id').value.trim() || focus !== el('assistant-focus').value) return;
      let preserved = 0;
      for (const key of [...questions.map(([key]) => key), 'entry_price', 'stop_price']) {
        const field = el(`pl-${key}`);
        if (field.value.trim() && field.value !== autoValues[key]) { preserved++; continue; }
        field.value = next[key] ?? '';
        autoValues[key] = field.value;
      }
      draft = next;
      el('assistant-reviewed').checked = false;
      questions.forEach(([key]) => updateQuestion(key));
      el('assistant-evidence').hidden = false;
      el('evidence-asof').textContent = `${sid} ${next.name} · 整理於 ${new Date(next.generated_at).toLocaleString('zh-TW')}；各資料日期如下。`;
      el('evidence-facts').innerHTML = next.evidence.map(f => `<div><dt>${esc(f.label)} <span class="muted">${esc(f.as_of || '日期未提供')}</span></dt><dd>${esc(f.value)}<small>${esc(f.source)}</small></dd></div>`).join('') || '<p class="muted">尚無足夠資料；草稿已將缺口標示為待查證。</p>';
      el('evidence-warnings').innerHTML = next.warnings.length ? `<p class="warn">目前警訊：${next.warnings.map(esc).join('；')}</p>` : '<p class="muted">未偵測到現有規則的警訊，不代表沒有風險。</p>';
      el('price-note').textContent = `${next.price_note}${next.quote_date ? ` 收盤日期：${next.quote_date}。` : ''}`;
      el('assistant-status').textContent = `已整理七項草稿。${preserved ? `保留 ${preserved} 個你已輸入或修改的欄位。` : ''}檢視後只需修正需要補充的內容，並確認風險設定。`;
      markDirty(); calcPosition();
    } catch (error) {
      if (generation === draftGeneration) el('assistant-status').textContent = `草稿尚未整理：${error.message}。原輸入已保留，可重試或自行填寫。`;
    } finally {
      if (generation === draftGeneration) { el('btn-draft').disabled = false; el('btn-draft').textContent = '重新整理證據與草稿'; }
    }
  }
  el('btn-draft').addEventListener('click', prepareDraft);
  el('pl-stock_id').addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.isComposing) { event.preventDefault(); prepareDraft(); }
  });
  el('btn-reset-plan').addEventListener('click', () => {
    if (dirty() && !confirm('清除這份尚未儲存的計畫，開始另一份？')) return;
    draftGeneration++; draft = null; autoValues = {}; position = null; savedPlanId = null;
    document.querySelectorAll('[id^="pl-"]').forEach(field => { field.value = field.id === 'pl-horizon' ? 'swing' : ''; });
    el('assistant-focus').value = 'observe'; el('assistant-reviewed').checked = false;
    el('assistant-evidence').hidden = true; el('assistant-status').textContent = ''; el('plan-hint').textContent = '';
    el('btn-draft').disabled = false; el('btn-draft').textContent = '整理證據與計畫草稿';
    questions.forEach(([key]) => updateQuestion(key)); baseline = fingerprint(); markDirty(); calcPosition(); el('pl-stock_id').focus();
  });

  async function calcPosition() {
    const generation = ++positionGeneration, p = values(); position = null;
    const output = el('pos-suggest');
    if (!(p.entry_price > 0 && p.stop_price > 0 && p.q8_max_loss > 0)) { output.textContent = '填入進場價、停損價與最大虧損後，顯示參考部位。'; return; }
    output.textContent = '正在計算參考部位…';
    try {
      const r = await api('/api/journal/position-size', {method:'POST', body:{entry:p.entry_price, stop:p.stop_price, max_loss:p.q8_max_loss}});
      if (generation !== positionGeneration) return;
      if (r.error) { output.textContent = r.error; return; }
      position = r;
      output.textContent = `每股風險 ${fmt.num(r.risk_per_share)} 元；最多 ${fmt.int(r.max_shares)} 股（整張 ${fmt.int(r.suggest_lots)} 張），所需資金約 ${fmt.int(r.capital_needed)} 元。`;
    } catch (error) { if (generation === positionGeneration) output.textContent = `參考部位無法計算：${error.message}`; }
  }
  ['pl-entry_price','pl-stop_price','pl-q8_max_loss'].forEach(id => el(id).addEventListener('input', calcPosition));

  el('btn-save-plan').addEventListener('click', async () => {
    if (saving) return;
    if (savedPlanId && !dirty()) { el('plan-hint').textContent = `相同內容已儲存為計畫 #${savedPlanId}，可在下方回看；開始另一份時請先重設草稿。`; return; }
    const payload = values();
    if (!payload.stock_id) { el('plan-hint').textContent = '請先填股票代號。'; el('pl-stock_id').focus(); return; }
    if (draft && (draft.stock_id !== payload.stock_id || draft.focus !== el('assistant-focus').value)) { el('plan-hint').textContent = '股票或研究方向已變更，請先重新整理或開始另一份計畫。'; return; }
    if (!el('assistant-reviewed').checked) { el('plan-hint').textContent = '請先檢視內容，再勾選確認；可以保留待查證項目。'; el('assistant-reviewed').focus(); return; }
    payload.position_shares = position?.suggest_shares ?? null;
    saving = true; const button = el('btn-save-plan'); button.disabled = true; button.textContent = '正在儲存…';
    const savedFingerprint = fingerprint();
    try {
      const r = await api('/api/journal/plans',{method:'POST',body:payload});
      baseline = savedFingerprint; savedPlanId = r.id; markDirty();
      el('plan-hint').textContent = `計畫 #${r.id} 已儲存。${r.missing.length ? `另有 ${r.missing.length} 項尚未記錄。` : '七項理由及風險金額已記錄；待查證不等於已驗證。'}可在下方展開回看原始內容。${dirty() ? '你之後的修改尚未儲存。' : ''}`;
      el('plan-status-filter').value = 'open'; await loadPlans(r.id);
    } catch(error) { el('plan-hint').textContent = `計畫未儲存：${error.message}。輸入仍保留，可重試。`; }
    finally { saving = false; button.disabled = false; button.textContent = '儲存研究計畫'; }
  });

  function readback(plan) {
    return `<dl class="journal-plan-meta"><div><dt>進場價</dt><dd>${fmt.num(plan.entry_price)}</dd></div><div><dt>停損價</dt><dd>${fmt.num(plan.stop_price)}</dd></div><div><dt>最大虧損</dt><dd>${fmt.int(plan.q8_max_loss)} 元</dd></div><div><dt>目標價</dt><dd>${fmt.num(plan.target_price)}</dd></div></dl><dl class="journal-original-answers">${questions.map(([key,label],i)=>`<div><dt>${i+1}. ${label}${String(plan[key]||'').startsWith('待查證：')?' <span class="warn">待查證</span>':''}</dt><dd>${esc(plan[key]||'尚未記錄')}</dd></div>`).join('')}</dl><p class="muted journal-small">這是計畫儲存時的內容；不會被今天重新整理的證據改寫。</p>`;
  }
  function showPlans(openId) {
    const filter = el('plan-status-filter').value;
    const visible = plans.filter(p => filter === 'all' || p.status === filter);
    el('plans-list').innerHTML = visible.length ? visible.map(p=>`<details class="journal-saved-plan" data-plan="${p.id}" ${p.id===openId?'open':''}><summary><span><strong>#${p.id} ${esc(p.stock_id)} ${esc(p.name||'')}</strong><small>${esc(p.created_at?.slice(0,10)||'')} · ${statusLabel(p.status)}</small></span><span class="muted">${p.missing.length ? `${p.missing.length} 項未記錄` : '已記錄八項'} · 展開回看</span></summary><div class="journal-plan-content">正在讀取原始計畫…</div><div class="journal-plan-actions"><a href="stock.html?id=${encodeURIComponent(p.stock_id)}">查看個股資料</a><button type="button" class="ghost small" data-status="${p.status==='open'?'closed':'open'}">${p.status==='open'?'將計畫結案':'重新開啟計畫'}</button></div></details>`).join('') : `<p class="muted">${filter==='open'?'尚無進行中的計畫，可先由上方研究助手開始。':'此篩選條件下沒有計畫，可切換為「全部」。'}</p>`;
    el('plans-list').querySelectorAll('details[data-plan]').forEach(details=>{
      let loaded = false;
      async function loadDetail() {
        if (loaded || !details.open) return;
        const content=details.querySelector('.journal-plan-content');
        try { const p=await api(`/api/journal/plans/${details.dataset.plan}`); content.innerHTML=readback(p); loaded=true; }
        catch(error){ content.replaceChildren(); const text=document.createElement('p'); text.textContent=`原始計畫無法讀取：${error.message}`; const retry=document.createElement('button'); retry.type='button'; retry.className='ghost'; retry.textContent='重新讀取計畫';retry.onclick=loadDetail;content.append(text,retry); }
      }
      details.addEventListener('toggle',loadDetail); if(details.open)loadDetail();
      details.querySelector('button[data-status]').addEventListener('click', async event=>{
        const button=event.currentTarget;button.disabled=true;
        try { await api(`/api/journal/plans/${details.dataset.plan}/status`,{method:'POST',body:{status:button.dataset.status}}); await loadPlans(); el('review-title').focus(); toast('計畫狀態已更新；結案計畫仍可切換篩選回看。'); }
        catch(error){toastError(`計畫狀態未更新：${error.message}`);button.disabled=false;}
      });
    });
  }
  async function loadPlans(openId) {
    try {
      plans=await api('/api/journal/plans');
      const selected=el('tr-plan_id').value;
      el('tr-plan_id').innerHTML='<option value="">無</option>'+plans.map(p=>`<option value="${p.id}">#${p.id} ${esc(p.stock_id)} ${esc(p.name||'')} · ${statusLabel(p.status)}</option>`).join('');
      if(plans.some(p=>String(p.id)===selected))el('tr-plan_id').value=selected;
      showPlans(openId);
    } catch(error){ showLoadError(el('plans-list'),'計畫',error,()=>loadPlans()); }
  }
  el('plan-status-filter').addEventListener('change',()=>showPlans());
  function showLoadError(container,label,error,retry) {
    container.replaceChildren(); const message=document.createElement('p');message.setAttribute('role','alert');message.textContent=`${label}無法載入：${error.message}`;
    const button=document.createElement('button');button.type='button';button.className='ghost';button.textContent=`重新載入${label}`;button.onclick=retry;container.append(message,button);
  }
  async function loadPerf() {
    try {
      const p=await api('/api/journal/performance');el('performance-status').replaceChildren();
      const hasSample=Number(p.closed_trades)>0;el('journal-performance').hidden=!hasSample;el('journal-perf-empty').hidden=hasSample;
      el('pf-pnl').textContent=fmt.int(p.total_pnl);el('pf-pnl').className='v '+fmt.cls(p.total_pnl);
      el('pf-win').textContent=p.win_rate!=null?fmt.pct(p.win_rate):'—';el('pf-n').textContent=`已平倉 ${p.closed_trades} 筆`;
      el('pf-avg').textContent=`${fmt.int(p.avg_win)} / ${fmt.int(p.avg_loss)}`;el('pf-factor').textContent=p.profit_factor??'—';
    } catch(error){showLoadError(el('performance-status'),'績效',error,loadPerf);}
  }
  async function loadPositions() {
    try {
      const rows=await api('/api/journal/positions');el('positions-status').replaceChildren();
      el('pos-table').querySelector('tbody').innerHTML=rows.length?rows.map(r=>`<tr><td class="l"><a href="stock.html?id=${encodeURIComponent(r.stock_id)}">${esc(r.stock_id)} ${esc(r.name||'')}</a></td><td>${fmt.int(r.shares)}</td><td>${fmt.num(r.avg_cost)}</td><td>${fmt.num(r.last)}</td><td>${fmt.int(r.market_value)}</td><td class="${fmt.cls(r.unrealized)}">${fmt.int(r.unrealized)}</td><td class="${fmt.cls(r.unrealized)}">${fmt.signPct(r.unrealized_pct)}</td></tr>`).join(''):'<tr><td colspan="7" class="l muted">尚無實際持倉紀錄。</td></tr>';
    }catch(error){showLoadError(el('positions-status'),'持倉',error,loadPositions);}
  }
  async function loadTrades() {
    try {
      const rows=await api('/api/journal/trades');el('trades-status').replaceChildren();
      el('trades-table').querySelector('tbody').innerHTML=rows.length?rows.map(t=>`<tr><td class="l">${esc(t.date)}</td><td class="l"><a href="stock.html?id=${encodeURIComponent(t.stock_id)}">${esc(t.stock_id)} ${esc(t.name||'')}</a></td><td>${t.side==='buy'?'買進':'賣出'}</td><td>${fmt.int(t.shares)}</td><td>${fmt.num(t.price)}</td><td>${fmt.int(t.fee)}</td><td>${fmt.int(t.tax)}</td></tr>`).join(''):'<tr><td colspan="7" class="l muted">尚無成交紀錄。</td></tr>';
    }catch(error){showLoadError(el('trades-status'),'成交紀錄',error,loadTrades);}
  }
  el('btn-add-trade').addEventListener('click',async event=>{
    const button=event.currentTarget;if(button.disabled)return;button.disabled=true;
    try {
      await api('/api/journal/trades',{method:'POST',body:{stock_id:el('tr-stock_id').value.trim(),side:el('tr-side').value,date:el('tr-date').value||undefined,shares:Number(el('tr-shares').value),price:Number(el('tr-price').value),plan_id:el('tr-plan_id').value||null}});
      toast('已記錄實際成交');loadTrades();loadPositions();loadPerf();
    }catch(error){toastError(`成交未記錄：${error.message}`);}finally{button.disabled=false;}
  });
  const today=new Date();el('tr-date').value=`${today.getFullYear()}-${String(today.getMonth()+1).padStart(2,'0')}-${String(today.getDate()).padStart(2,'0')}`;
  baseline=fingerprint();
  if(qs('plan')){el('pl-stock_id').value=qs('plan');prepareDraft();}
  loadPerf();loadPositions();loadPlans();loadTrades();
})();
